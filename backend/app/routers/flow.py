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

import logging
from datetime import timezone
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Song, SyncRecord, User
from app.routers.auth import SYNC_FLOW_SCOPE, get_current_user_scoped
from app.schemas import FlowSongOut, SyncStatusOut
from app.ratelimit import rate_limit_sync
from app.storage import mirror
from app.storage import s3 as storage

log = logging.getLogger(__name__)

# [F6] 계약 버저닝(2026-09-01): 동기화 계약은 /api/v1 로 버저닝한다.
# 컨슈머 주도 계약 진화를 위해 버전 세그먼트를 둔다(설계 §471).
router = APIRouter(prefix="/api/v1", tags=["flow"])


async def _ensure_cdn_url(song: Song, db: Session) -> str:
    """确保歌曲有稳定的 CDN 播放链接；没有则回源。

    ⚠ 回源逻辑本体已抽到 `app.storage.mirror`（GAP-019）：主播放链路按需回填、
    FLOW 同步、历史回填脚本必须共用**同一个对象键 + 同一套重试/降级**，否则
    同曲两份对象、脚本永远认为「没回填过」。

    降级契约（硬指标）：未配对象存储或回源失败 → 返回上游 `audio_url`（可能过期，
    但绝不 500）。prod 未配 `storage_*` 时走的就是这条。
    """
    if song.audio_cdn_url:
        return song.audio_cdn_url
    if not storage.is_configured():
        # MVP 降级：未配置对象存储时直接用上游原始链接（不稳定，仅本地验证）
        log.warning(
            "S3 storage not configured; serving volatile upstream URL (audio_url) "
            "for song %s — unstable/expiring, configure storage for a stable CDN URL (🔴2)",
            song.id,
        )
        return song.audio_url
    if not song.audio_url:
        raise ValueError("歌曲尚无音频链接")
    mirrored = await mirror.mirror_song_audio(song, db)
    if mirrored:
        return mirrored
    # 回源失败：不抛给 APK（宁可给易失链接，也不要歌单整段失败）。
    log.warning(
        "CDN mirror failed for song %s; falling back to volatile upstream URL", song.id
    )
    return song.audio_url


def _mark_synced(song: Song, db: Session, *, play_url: str | None = None) -> SyncRecord:
    """落同步记录。`play_url` = 本次实际下发给 APK 的链接（CDN 优先，降级为上游）。

    只在它确实是**CDN 链接**时才写回 `song.audio_cdn_url`：把易失的上游链接塞进
    「CDN 字段」会让「已回源」这件事变成谎话，回填脚本也永远发现不了它需要重做。
    """
    rec = db.query(SyncRecord).filter_by(user_id=song.user_id, song_id=song.id).first()
    if not rec:
        rec = SyncRecord(user_id=song.user_id, song_id=song.id)
        db.add(rec)
    rec.status = "synced"
    rec.audio_cdn_url = play_url or song.audio_cdn_url
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
        # GAP-019：回源得到的「实际可播放链接」必须**带下去**，不能只依赖
        # `song.audio_cdn_url` —— 未配 storage 的降级路径根本不写这一列（写下游链接进
        # CDN 字段会污染「已回源」的事实），此时丢返回值就等于给 APK 一个空链接。
        play_url = await _ensure_cdn_url(song, db)
    except Exception:
        if rec:
            rec.status = "failed"
            db.commit()
        raise HTTPException(status_code=502, detail="音频回源失败，请重试")

    rec = _mark_synced(song, db, play_url=play_url)
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
