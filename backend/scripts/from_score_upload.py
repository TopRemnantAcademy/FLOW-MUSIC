"""악보 → 음악 생성 CLI 업로더 (관리자 전용 백엔드 도구).

용도:
  · 소유자가 백엔드에서 직접 악보를 올려 곡을 생성할 때.
  · 어시스턴트(WorkBuddy)가 로컬 악보 이미지/MusicXML 을 올려 생성할 때.
  공개 앱/웹에는 노출하지 않음 (엔드포인트가 require_admin 게이트).

워크플로우:
  1. 로그인(이메일/비번) 또는 --token 으로 사용자 Bearer JWT 발급
  2. POST /api/songs/from-score (multipart, Authorization + X-Admin-Token)
  3. GET /api/songs/{id} 폴링 → completed 되면 audio_url 출력

지원 포맷:
  · MusicXML(.musicxml/.xml/.mxl): OMR 불필요, 즉시 파싱→생성
  · 이미지/PDF: OMR(oemer) 경유. oemer 미설치/비활성 노드에서는 501 로 거부됨

사용법:
  cd backend
  # MusicXML (가장 단순, OMR 불필요)
  python scripts/from_score_upload.py --file score.musicxml --title "내 곡" --style "피아노 발라드"

  # 이미지 악보 (배포 노드에서만: enable_omr=true + oemer 설치)
  python scripts/from_score_upload.py --file photo.jpg --admin-token "$ADMIN_TOKEN" \
      --email me@x.com --password '***'

  # 기존 토큰 재사용
  python scripts/from_score_upload.py --file s.musicxml --token "$USER_TOKEN" --admin-token "$ADMIN_TOKEN"

환경변수(.env 에서 자동 로드, 우선순위 낮음):
  FROM_SCORE_BASE_URL   기본 http://127.0.0.1:8000
  ADMIN_TOKEN           X-Admin-Token 값
  FROM_SCORE_EMAIL / FROM_SCORE_PASSWORD  로그인 기본값

종료코드: 0=생성완료  1=사용법/인증실패  2=업로드거부(400/413/501/502)  3=생성실패(failed)  4=타임아웃
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import time

try:
    import requests
except ImportError:  # pragma: no cover
    print("[FAIL] 이 스크립트는 requests 가 필요합니다: pip install requests")
    sys.exit(1)


def load_env(paths: list[pathlib.Path]) -> None:
    """경량 .env 로더 (key=value, # 주석, 기존 환경변수 미덮어쓰기)."""
    for p in paths:
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())


def login(base: str, email: str, password: str, verify: bool) -> str:
    r = requests.post(
        f"{base}/api/auth/login",
        json={"email": email, "password": password},
        timeout=30, verify=verify,
    )
    if r.status_code != 200:
        print(f"[FAIL] 로그인 실패 {r.status_code}: {r.text[:300]}")
        sys.exit(1)
    return r.json()["access_token"]


def upload(
    base: str,
    token: str,
    admin_token: str,
    filepath: pathlib.Path,
    args: argparse.Namespace,
    verify: bool,
) -> dict:
    with open(filepath, "rb") as f:
        files = {"file": (filepath.name, f, "application/octet-stream")}
        data = {
            "title": args.title,
            "style": args.style,
            "vocal_gender": args.vocal_gender,
            "content_category": args.content_category,
            "license": args.license,
            "make_instrumental": str(args.make_instrumental).lower(),
        }
        headers = {
            "Authorization": f"Bearer {token}",
            "X-Admin-Token": admin_token,
        }
        r = requests.post(
            f"{base}/api/songs/from-score",
            files=files, data=data, headers=headers, timeout=120, verify=verify,
        )
    if r.status_code == 501:
        print("[501] 이 노드에서 OMR 이 비활성/미설치입니다. MusicXML(.musicxml) 을 사용하거나,"
              " 배포 노드에서 enable_omr=true + oemer 를 설정하세요.")
        sys.exit(2)
    if r.status_code >= 400:
        print(f"[FAIL] 업로드 거부 {r.status_code}: {r.text[:500]}")
        sys.exit(2)
    return r.json()


def poll(base: str, token: str, song_id: int, timeout: float, interval: float, verify: bool) -> dict:
    headers = {"Authorization": f"Bearer {token}"}
    waited = 0.0
    while waited < timeout:
        r = requests.get(f"{base}/api/songs/{song_id}", headers=headers, timeout=30, verify=verify)
        if r.status_code == 200:
            song = r.json()
            st = song.get("status")
            if st == "completed":
                return song
            if st == "failed":
                print(f"[FAIL] 생성 실패: {song.get('moderation_note', '')[:500]}")
                sys.exit(3)
            print(f"  … status={st} ({waited:.0f}s)")
        else:
            print(f"[WARN] 상태 조회 {r.status_code}: {r.text[:200]}")
        time.sleep(interval)
        waited += interval
    print(f"[TIMEOUT] {timeout:.0f}s 내 미완료")
    sys.exit(4)


def main() -> int:
    here = pathlib.Path(__file__).resolve().parent          # backend/scripts
    backend_dir = here.parent                                # backend
    load_env([backend_dir / ".env", backend_dir.parent / ".env"])

    ap = argparse.ArgumentParser(description="악보 → 음악 생성 (관리자 전용)")
    ap.add_argument("--file", required=True, help="악보 파일 (MusicXML 또는 이미지/PDF)")
    ap.add_argument("--base-url", default=os.environ.get("FROM_SCORE_BASE_URL", "http://127.0.0.1:8000"))
    ap.add_argument("--token", default=None, help="사용자 Bearer JWT (생략 시 로그인)")
    ap.add_argument("--admin-token", default=os.environ.get("ADMIN_TOKEN", ""), help="X-Admin-Token")
    ap.add_argument("--email", default=os.environ.get("FROM_SCORE_EMAIL", ""))
    ap.add_argument("--password", default=os.environ.get("FROM_SCORE_PASSWORD", ""))
    ap.add_argument("--title", default="")
    ap.add_argument("--style", default="")
    ap.add_argument("--vocal-gender", default="")
    ap.add_argument("--content-category", default="general")
    ap.add_argument("--license", default="")
    ap.add_argument("--make-instrumental", action="store_true")
    ap.add_argument("--no-wait", action="store_true", help="제출만 하고 완료 대기 안 함")
    ap.add_argument("--timeout", type=float, default=600.0, help="완료 대기 최대 초")
    ap.add_argument("--interval", type=float, default=5.0, help="폴링 간격 초")
    ap.add_argument("--insecure", action="store_true", help="TLS 검증 비활성 (self-signed 용)")
    ap.add_argument("--json", action="store_true", help="결과를 JSON 으로 출력")
    args = ap.parse_args()

    if not args.admin_token:
        print("[FAIL] 관리자 토큰이 필요합니다 (--admin-token 또는 env ADMIN_TOKEN)")
        return 1
    fp = pathlib.Path(args.file)
    if not fp.is_file():
        print(f"[FAIL] 파일 없음: {fp}")
        return 1
    verify = not args.insecure

    # 1) 사용자 토큰
    if args.token:
        token = args.token
    else:
        if not args.email or not args.password:
            print("[FAIL] --token 또는 --email/--password 로 인증하세요")
            return 1
        print(f"[AUTH] 로그인 {args.email} …")
        token = login(args.base_url.rstrip("/"), args.email, args.password, verify)

    # 2) 업로드
    print(f"[UPLOAD] {fp.name} → {args.base_url}/api/songs/from-score")
    resp = upload(args.base_url.rstrip("/"), token, args.admin_token, fp, args, verify)
    song_id = resp.get("song_id")
    print(f"[OK] 접수됨 song_id={song_id} status={resp.get('status')}")
    if args.no_wait or args.json:
        if args.json:
            print(json.dumps(resp, ensure_ascii=False))
        return 0

    # 3) 폴링
    print(f"[POLL] song_id={song_id} 최대 {args.timeout:.0f}s")
    song = poll(args.base_url.rstrip("/"), token, song_id, args.timeout, args.interval, verify)
    print("[DONE] 생성 완료")
    print(f"  audio_url  : {song.get('audio_url')}")
    if song.get("audio_wav_url"):
        print(f"  wav_url    : {song.get('audio_wav_url')}")
    if song.get("audio_flac_url"):
        print(f"  flac_url   : {song.get('audio_flac_url')}")
    if song.get("cover_url"):
        print(f"  cover_url  : {song.get('cover_url')}")
    if song.get("duration"):
        print(f"  duration   : {song.get('duration')}s")
    if args.json:
        print(json.dumps(song, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
