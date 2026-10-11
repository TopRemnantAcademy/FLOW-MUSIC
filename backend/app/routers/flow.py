"""FLOW APP 同步：把已生成的歌曲同步到自有 APK（FLOW APP）的音乐单。

确认设计（co-location 令牌/DB 分离, 2026-08-31）：
- FLOW APP 为自有应用，但 **不复用同一 JWT**（🔴A 已闭合：FLOW 后端是 HMAC, 无法验证音乐 JWT）。
- FLOW APP 持有两枚独立令牌：
  (a) FLOW HMAC 令牌 ——  채팅/오디오（FLOW 自身 검증）。
  (b) sync 전용 **scope 한정(sync:flow) 음악 JWT** —— flow-exchange 로 교환 발급,
      GET /api/sync/flow · POST /api/songs/{id}/sync-flow 전용. 음악 생성/관리/결제 권한 0.
- MVP 采用 Pull：本接口负责「准备」——校验归属/完成态、把音频回源香港 CDN、
  写入 SyncRecord(synced)；APK 端用 (b) 스코프 한정 토큰으로 GET /api/sync/flow 拉取自己歌单。
- 以后若 FLOW APP 有独立后端并要实时推送，可在 _mark_synced 后加 Push 调用（见规划书 14.2/14.3）。
"""

import asyncio
import httpx
import logging
from datetime import timezone
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Song, SyncRecord, User
from app.routers.auth import SYNC_FLOW_SCOPE, get_current_user_scoped
from app.schemas import FlowSongOut, SyncStatusOut
from app.ratelimit import rate_limit_sync
from app.security.ssrf import ssrf_guard
from app.storage import s3 as storage

log = logging.getLogger(__name__)

# [F6] 계약 버저닝(2026-09-01): 동기화 계약은 /api/v1 로 버저닝한다.
# 컨슈머 주도 계약 진화를 위해 버전 세그먼트를 둔다(설계 §471).
router = APIRouter(prefix="/api/v1", tags=["flow"])


async def _ensure_cdn_url(song: Song, db: Session) -> str:
    """确保歌曲有稳定的 CDN 播放链接；没有则回源。"""
    if song.audio_cdn_url:
        return song.audio_cdn_url
    if not storage.is_configured():
        # MVP 降级：未配置对象存储时直接用 Suno 原始链接（不稳定，仅本地验证）
        log.warning(
            "S3 storage not configured; serving volatile Suno original URL (audio_url) "
            "for song %s — unstable/expiring, configure storage for a stable CDN URL (🔴2)",
            song.id,
        )
        return song.audio_url
    if not song.audio_url:
        raise ValueError("歌曲尚无音频链接")
    # [SEC] X6/T6.4b: song.audio_url 은 사용자 영향 값 — 서버 측 fetch 전 내부/링크로컬/
    # 클라우드 메타데이터(169.254.169.254) 주소 인출을 가드로 차단(SSRF, fail-closed).
    ssrf_guard.guard(song.audio_url)
    # [RES] T8.6c — 네트워크 분할(§11.9 6행) 복구용 지수 백오프 재시도.
    # 경계 타임아웃(timeout=60) 단독으로는 무한 대기만 막지, 일시적 분할 복구 시
    # 재시도가 없으면 동기화가 영구 실패한다. 지수 백오프로 복구 시도.
    max_attempts = 4
    backoff_base = 0.5  # 초 — 지수 증가: 0.5, 1, 2, 4 ...
    last_exc = None
    data = None
    # [SEC] H5(2026-09-13): safe_fetch 는 가드 통과 호스트를 **한 번만 해석해 IP 핀ning**
    # 하므로, guard() 후 httpx 가 다시 DNS 를 해석하는 TOCTOU(DNS-rebinding) 창이 없다.
    # 클라이언트는 루프 밖에서 1회 생성해 재사용한다(호출마다 신규 생성 시 DNS/TLS 폭풍).
    async with httpx.AsyncClient(timeout=60) as c:
        for attempt in range(max_attempts):
            try:
                r = await ssrf_guard.safe_fetch(song.audio_url, client=c, timeout=60)
                data = r.content
                break
            except Exception as e:  # 일시적 분할/오류 → 백오프 후 재시도
                last_exc = e
                if attempt < max_attempts - 1:
                    await asyncio.sleep(backoff_base * (2**attempt))
    if data is None:
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("audio fetch failed")
    key = f"songs/{song.user_id}/{song.id}.mp3"
    url = storage.upload_bytes(key, data, content_type="audio/mpeg")
    song.audio_cdn_url = url
    db.commit()
    return url


def _mark_synced(song: Song, db: Session) -> SyncRecord:
    rec = db.query(SyncRecord).filter_by(user_id=song.user_id, song_id=song.id).first()
    if not rec:
        rec = SyncRecord(user_id=song.user_id, song_id=song.id)
        db.add(rec)
    rec.status = "synced"
    rec.audio_cdn_url = song.audio_cdn_url
    db.commit()
    db.refresh(rec)
    song.synced_to_flow = True
    db.commit()
    return rec


@router.post("/songs/{song_id}/sync-flow", response_model=SyncStatusOut)
async def sync_to_flow(
    song_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_scoped(SYNC_FLOW_SCOPE)),
    _rl: None = Depends(rate_limit_sync),
):
    song = db.get(Song, song_id)
    if not song or song.user_id != user.id:
        raise HTTPException(status_code=404, detail="歌曲不存在")
    if song.status != "completed":
        raise HTTPException(status_code=400, detail="歌曲尚未生成完成")
    # [SEC] D24: 内容审核被拒(rejected)的歌曲禁止同步到 FLOW APP。
    # moderation_status 与 status 相互独立(songs.py:164 / admin.py:127 仅置 rejected 不动 status)，
    # 仅判 status=="completed" 会放过被拒歌曲。explore 页只暴露 approved(songs.py:294)，同步路径需保持一致。
    if song.moderation_status == "rejected":
        raise HTTPException(status_code=400, detail="歌曲未通过内容审核，无法同步")

    rec = db.query(SyncRecord).filter_by(user_id=user.id, song_id=song.id).first()
    if rec and rec.status == "synced":
        return SyncStatusOut(sync_id=rec.id, status="synced", audio_cdn_url=rec.audio_cdn_url)

    try:
        await _ensure_cdn_url(song, db)
    except Exception:
        if rec:
            rec.status = "failed"
            db.commit()
        raise HTTPException(status_code=502, detail="音频回源失败，请重试")

    rec = _mark_synced(song, db)
    return SyncStatusOut(sync_id=rec.id, status="synced", audio_cdn_url=rec.audio_cdn_url)


@router.get("/sync/flow", response_model=list[FlowSongOut])
def list_flow(
    db: Session = Depends(get_db), user: User = Depends(get_current_user_scoped(SYNC_FLOW_SCOPE))
):
    recs = db.query(SyncRecord).filter_by(user_id=user.id, status="synced").all()
    out: list[FlowSongOut] = []
    for r in recs:
        s = db.get(Song, r.song_id)
        if not s:
            continue
        out.append(
            FlowSongOut(
                song_id=s.id,
                title=s.title,
                audio_cdn_url=r.audio_cdn_url or s.audio_cdn_url or s.audio_url,
                cover_url=s.cover_url or None,
                lyric=s.lyric or None,
                style=s.style or None,
                # [D39] SQLite(naive) · Postgres(tz-aware) 에서 형식이 달라지지 않도록
                # UTC 고정 정규화("...Z"). FLOW APK 파싱 계약 일관성.
                synced_at=(
                    r.updated_at.astimezone(timezone.utc).replace(tzinfo=None).isoformat() + "Z"
                )
                if r.updated_at
                else "",
            )
        )
    return out


@router.get("/songs/{song_id}/sync-flow", response_model=SyncStatusOut)
def get_sync_status(
    song_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_scoped(SYNC_FLOW_SCOPE)),
):
    rec = db.query(SyncRecord).filter_by(user_id=user.id, song_id=song_id).first()
    if not rec:
        return SyncStatusOut(status="not_synced")
    return SyncStatusOut(sync_id=rec.id, status=rec.status, audio_cdn_url=rec.audio_cdn_url)
