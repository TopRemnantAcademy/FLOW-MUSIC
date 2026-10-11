"""设计书 file:line 引用全量实证验证器 (拒绝幻觉)。

对 docs/COLOCATION_PLAN_VERIFIED_2026-08-30.md 中所有 `file:line` 引用:
  1. 在 FLOW / 音乐平台 两侧定位文件
  2. 校验行号是否越界
输出: OK / 越界 / 未找到 / 歧义
"""

import re
import subprocess
import sys
from pathlib import Path

DOC = Path(r"C:\Desktop\Project\FLOW\docs\COLOCATION_PLAN_VERIFIED_2026-08-30.md")
FLOW = Path(r"C:\Desktop\Project\FLOW")
MUSIC = Path(r"C:\Desktop\korean-gospel-ai\ai-music-platform")

CITE = re.compile(
    r"`([A-Za-z_][A-Za-z0-9_/.\-]*\.(?:py|yml|yaml|js|ts|toml|ini|md|sh|json))"
    r"((?::\d+(?:-\d+)?)(?:\s*,\s*(?::?\d+(?:-\d+)?))*)`"
)


SKIP = {"node_modules", "__pycache__", ".git", "venv", "venv313", ".venv", "dist", "build"}

# ── 양측 동명 파일: 어느 쪽 인용인지 **사람이 확인해 확정한** 목록 ──────────
# 검증기는 기본적으로 후보를 순서대로 훑어 "행 범위가 맞는 첫 후보" 를 채택한다.
# 그런데 행 번호는 맞지만 **전혀 다른 파일** 일 수 있다(아래 전부 실제로 그랬다).
# 형식: "파일명:행범위" -> (측, 판단 근거)
AMBIG_RESOLUTION: dict[str, tuple[str, str]] = {
    "README.md:10": (
        "MUSIC",
        "음악 README.md:10 = '香港轻量服务器只做编排...' → 설계서 934행이 "
        "인용한 '홍콩 경량(轻量) 서버' 문구와 일치. FLOW README.md:10 은 LLM 표.",
    ),
    "README.md:53": (
        "MUSIC",
        "음악 README.md:53 = 'uvicorn app.main:app ... --port 8000' → "
        "설계서 1327행이 말한 '음악의 8000 포트 실행 명령'과 일치. "
        "FLOW README.md:53 은 환경변수 안내.",
    ),
    "admin.py:45": (
        "MUSIC",
        "음악 routers/admin.py:45 = 'api_key_set=bool(e.api_key)' → "
        "설계서 126행(F3)의 'GET 응답 마스킹 구현'과 일치. "
        "FLOW backend/app/api/admin.py:45 는 '반환: {' 로 무관.",
    ),
    # ── 아래 7건은 2026-08-31 에 "문장에 '음악' 이 명시돼 있는데 FLOW 로 채택된" 것들.
    #    행 범위 검사만으로는 잡히지 않아(양쪽 파일이 모두 충분히 김) **내용 대조** 로 판정했다.
    "config.py:9": (
        "MUSIC",
        "음악 backend/app/config.py:9 = 'database_url: str = \"sqlite:///./app.db\"' "
        "→ 설계서 651행 '음악측 DB URL(기본 sqlite)' 과 일치. "
        "FLOW config.py:9 는 'from pydantic_settings import ...' 로 무관.",
    ),
    "config.py:11": (
        "MUSIC",
        "음악 config.py:11 = 'jwt_secret: str = \"change-me-please\"' → "
        "설계서 761행 '음악 인증=JWT HS256(jwt_secret)' 과 일치. "
        "FLOW config.py:11 은 ROOT_DIR 정의.",
    ),
    "config.py:29": (
        "MUSIC",
        "음악 config.py:29 = 'music_provider: str = \"suno\"' → "
        "설계서 440행 '음악 MusicProvider 추상화' 와 일치. "
        "FLOW config.py:29 는 google_api_key.",
    ),
    "config.py:59": (
        "MUSIC",
        "음악 config.py:59 = 'admin_token: str = \"\"  # ...为空则禁用 admin 路由' → "
        "설계서 775행 X5 '음악 admin_token 기본 빈값→admin 라우트 비활성' 과 정확히 일치.",
    ),
    "schemas.py:150": (
        "MUSIC",
        "음악 schemas.py:150 = 'api_key_set: bool = False  # 脱敏：仅表示是否配置，不返回明文' "
        "→ 설계서 126·1335행(F3/SC-9)의 'GET 응답 마스킹 구현' 과 일치.",
    ),
    "env.py:34": (
        "MUSIC",
        "음악 env.py:34 = 'target_metadata = Base.metadata' → "
        "설계서 667행이 '음악 env.py:34' 로 명시. (같은 행의 'FLOW env.py:44' 는 별개 키)",
    ),
    "env.py:39-44": (
        "MUSIC",
        "음악 migrations/env.py:39-44 = 'context.configure(url=..., target_metadata=...)' → "
        "설계서 662행이 '음악 migrations/env.py:39-44' 로 명시(X3 version_table 부재 근거).",
    ),
    # ── 2026-08-31 6차(D1/D2) 추가 확정 2건.
    #    양쪽 파일이 모두 충분히 길어 **행 범위 검사만으로는 잡히지 않는** 전형적인 오귀속.
    "admin.py:117-131": (
        "MUSIC",
        "음악 routers/admin.py:117-131 = '@router.post(\"/songs/{song_id}/reject\")' "
        "~ 'db.refresh(song)' → 설계서 1625행(#23/D24)이 인용한 "
        "'수동 reject 는 song.status 를 건드리지 않는다' 의 근거. "
        "FLOW backend/app/api/admin.py:117-131 은 헬스체크 jobs 집계로 무관.",
    ),
    "main.py:11-14": (
        "MUSIC",
        "음악 backend/app/main.py:11-14 = '@asynccontextmanager / async def lifespan / "
        "init_db() / yield' → 설계서 1352행(SC-17)이 '음악은 main.py:11-14(lifespan)' 로 명시. "
        "FLOW backend/app/main.py:11-14 는 임포트 구간으로 무관.",
    ),
}


def locate(basename: str):
    """在两侧定位文件, 返回 (FLOW候选列表, 音乐候选列表)。用原生遍历, 不依赖外部 find。"""
    out = {}
    for name, root in (("FLOW", FLOW), ("MUSIC", MUSIC)):
        hits = []
        if root.exists():
            try:
                for p in root.rglob(basename):
                    if any(s in p.parts for s in SKIP):
                        continue
                    hits.append(str(p))
                    if len(hits) >= 6:
                        break
            except Exception:
                pass
        out[name] = hits
    return out["FLOW"], out["MUSIC"]


def parse_lines(spec: str):
    """把 ':12-34, :56' 解析为 [(12,34),(56,56)]。"""
    nums = []
    for tok in spec.replace("`", "").split(","):
        tok = tok.strip().lstrip(":").strip()
        if not tok:
            continue
        m = re.fullmatch(r"(\d+)(?:-(\d+))?", tok)
        if m:
            a = int(m.group(1))
            b = int(m.group(2)) if m.group(2) else a
            nums.append((a, b))
    return nums


def main():
    text = DOC.read_text(encoding="utf-8", errors="replace")
    doc_lines = text.split("\n")

    # 취소선(~~...~~) 안의 인용은 "폐기된 인용(기록용)"이므로 검증 대상에서 제외
    STRIKE = re.compile(r"~~.*?~~")

    seen = {}
    for i, line in enumerate(doc_lines, 1):
        clean = STRIKE.sub("", line)
        for m in CITE.finditer(clean):
            key = (m.group(1), m.group(2).strip())
            seen.setdefault(key, i)

    cache = {}
    rows = []
    for (fname, spec), docline in sorted(seen.items()):
        base = Path(fname).name
        if base not in cache:
            cache[base] = locate(base)
        f_cands, m_cands = cache[base]

        # 限定: 路径中含子目录的用后缀匹配
        if "/" in fname:
            suffix = fname
            f_cands = [p for p in f_cands if p.replace("\\", "/").endswith(suffix)]
            m_cands = [p for p in m_cands if p.replace("\\", "/").endswith(suffix)]

        # 후보(양측 동명 파일)를 **모두** 시도해 하나라도 행 범위를 만족하면 유효로 본다.
        #   배경(2026-08-31): `auth.py:229-233` 이 `admin/lib/auth.py`(120줄) 로 오해석돼
        #   OVERFLOW 로 잘못 보고됐다. 실제 대상은 `backend/app/api/auth.py`(961줄) 였고
        #   인용 내용(503 raise)도 정확히 일치했다. 동명 파일은 "어느 쪽이든 맞으면 맞다"가
        #   옳은 판정이며, 그래도 모호하면 AMBIG 으로 남겨 사람이 확인하게 한다.
        # 한 쪽 저장소 안에도 동명 파일이 여러 개 있을 수 있다(예: FLOW 의 auth.py 는
        # admin/lib/ 와 backend/app/api/ 두 곳). **모든 후보** 를 시도해야 한다.
        candidates: list[tuple[str, str]] = [("FLOW", p) for p in f_cands]
        candidates += [("MUSIC", p) for p in m_cands]

        # 사람이 확정한 귀속이 있으면 그쪽 후보를 **맨 앞** 으로 올린다.
        resolved_note = ""
        key = f"{base}{spec}"
        if key in AMBIG_RESOLUTION:
            want_side, why = AMBIG_RESOLUTION[key]
            candidates.sort(key=lambda c: 0 if c[0] == want_side else 1)
            resolved_note = f"귀속확정={want_side} ({why})"
        if not candidates:
            rows.append(("NOTFOUND", fname, spec, docline, "-", ""))
            continue

        ok_side, ok_path, ok_total = None, None, 0
        overflow_note = ""
        for side, path in candidates:
            try:
                total = len(Path(path).read_text(encoding="utf-8", errors="replace").split("\n"))
            except Exception as e:
                rows.append(("READERR", fname, spec, docline, f"{side}:{path}", str(e)))
                continue
            bad = [f"{a}-{b}" for a, b in parse_lines(spec) if a < 1 or b > total or a > b]
            if not bad:
                ok_side, ok_path, ok_total = side, path, total
                break
            if not overflow_note:
                overflow_note = f"파일{total}줄 / 초과={','.join(bad)}"

        if ok_path is None:
            rows.append(
                (
                    "OVERFLOW",
                    fname,
                    spec,
                    docline,
                    f"{candidates[0][0]}:{candidates[0][1]}",
                    overflow_note,
                )
            )
            continue

        chosen, total = ok_path, ok_total

        # 양측에 동명 파일이 있으면 어떤 쪽을 채택했는지 표시만 한다.
        # ( ※ 이 분기에서 rows.append 를 빼먹으면 집계에서 누락된다 — 실제로 그랬다.
        #   검증 도구 자체가 인용을 삼키면 검증 결과가 거짓으로 깨끗해진다. )
        label = f"AMBIG→{ok_side}" if (f_cands and m_cands) else ok_side
        note = f"파일{total}줄"
        if resolved_note:
            note += f" | {resolved_note}"
        rows.append(("OK", fname, spec, docline, f"{label}:{chosen}", note))

    buckets = {}
    for r in rows:
        buckets.setdefault(r[0], []).append(r)

    print(f"총 인용 {len(rows)}건 (중복제거)")
    for k in ("OK", "OVERFLOW", "NOTFOUND", "READERR"):
        print(f"  {k:9s}: {len(buckets.get(k, []))}")

    print("\n===== 문제 인용 =====")
    for k in ("OVERFLOW", "NOTFOUND", "READERR"):
        for r in buckets.get(k, []):
            print(f"[{k}] 설계서:{r[3]}행  `{r[1]}{r[2]}`\n      위치={r[4]}\n      비고={r[5]}")

    print("\n===== 양측 중복(AMBIG) — 귀속 확인 대상 =====")
    for r in rows:
        if r[4].startswith("AMBIG"):
            print(f"  설계서:{r[3]}행  `{r[1]}{r[2]}`")
            print(f"      채택={r[4]}  {r[5]}")

    print("\n===== 귀속 확정(AMBIG_RESOLUTION 적용) =====")
    hit = [r for r in rows if "귀속확정" in r[5]]
    if not hit:
        print("  (해당 없음)")
    for r in hit:
        print(f"  설계서:{r[3]}행  `{r[1]}{r[2]}`  → {r[5]}")


if __name__ == "__main__":
    main()
