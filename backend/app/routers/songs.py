"""歌曲生成路由：提交任务 + 轮询状态 + 我的作品 + 续写/翻唱/Remix。

生成异步：提交后建 Song(pending)，后台任务调编排层（Suno API），
轮询直到完成，写回 audio_url 等。前端轮询 GET /api/songs/{id}。
任务类型（task）对齐 Suno 真实产品能力：generate/custom/extend/cover/remix。
"""

import asyncio
import datetime
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Request, UploadFile

from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from typing import Optional
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

from app.config import settings
from app.db import SessionLocal, get_db
from app.models import (
    Song,
    SongFavorite,
    SongLike,
    SongRating,
    SyncRecord,
    User,
)
import app.moderation as moderation
from app.orchestrator.base import GenerateRequest, SongResult
from app.orchestrator.client import generate, get_orchestrator
from app.orchestrator.mureka import MurekaProvider
from app.orchestrator.selection import (
    resolve_engine,
    resolve_cost_cny,
    resolve_price_cny,
    compute_margin_cny,
    resolve_engine_chain,
    resolve_tier_model_version,
    allowed_model_versions,
)
from app.quota import enforce_generate_limits, daily_generated_count, rate_limit_generate
from app.ratelimit import (
    rate_limit_play,
    rate_limit_assist,
    rate_limit_preview,
    rate_limit_download,
    rate_limit_vocal_clone,
)
from app.entitlements import (
    resolve_limits,
    can_play_full,
    can_download,
    preview_seconds,
)
from app.routers.auth import get_current_user, get_optional_user
from app.security.ssrf import ssrf_guard
from app import assist_llm
from app.assist_llm import LLMError
from app.wallet_track import (
    hold_wallet_track,
    capture_wallet_track,
    release_wallet_track,
    get_held_reservation_by_job,
)
from app.services.prompt_assist import optimize_lyrics, optimize_style
from app.schemas import (
    GenerateReq,
    GenerateResp,
    SongOut,
    SpawnReq,
    CropReq,
    SpeedReq,
    AlignedLyricsReq,
    SoundReq,
    UploadReq,
    UploadResp,
    RegionEditReq,
    StemResp,
    VocalCloneResp,
    EasyGenerateReq,
    InstrumentalReq,
)

router = APIRouter(prefix="/api/songs", tags=["songs"])


class VisibilityReq(BaseModel):
    """PATCH /{song_id} 修改公开状态。"""

    is_public: bool


def _interaction_stats(db: Session, song_ids: list[int], user_id: Optional[int]) -> dict[int, dict]:
    """批量聚合互动统计（赞/收藏/评分），避免逐首查询的 N+1。

    返回 {song_id: {like_count, favorite_count, rating_count, rating_avg,
                    liked_by_me, favorited_by_me, my_rating}}
    """
    if not song_ids:
        return {}
    stats: dict[int, dict] = {
        sid: {
            "like_count": 0,
            "favorite_count": 0,
            "rating_count": 0,
            "rating_avg": None,
            "liked_by_me": False,
            "favorited_by_me": False,
            "my_rating": None,
        }
        for sid in song_ids
    }
    like_rows = (
        db.query(SongLike.song_id, func.count(SongLike.id))
        .filter(SongLike.song_id.in_(song_ids))
        .group_by(SongLike.song_id)
        .all()
    )
    for sid, c in like_rows:
        stats[sid]["like_count"] = int(c or 0)

    fav_rows = (
        db.query(SongFavorite.song_id, func.count(SongFavorite.id))
        .filter(SongFavorite.song_id.in_(song_ids))
        .group_by(SongFavorite.song_id)
        .all()
    )
    for sid, c in fav_rows:
        stats[sid]["favorite_count"] = int(c or 0)

    rate_rows = (
        db.query(
            SongRating.song_id,
            func.count(SongRating.id),
            func.avg(SongRating.score),
        )
        .filter(SongRating.song_id.in_(song_ids))
        .group_by(SongRating.song_id)
        .all()
    )
    for sid, c, avg in rate_rows:
        stats[sid]["rating_count"] = int(c or 0)
        stats[sid]["rating_avg"] = round(float(avg), 2) if avg is not None else None

    if user_id is not None:
        for (sid,) in (
            db.query(SongLike.song_id)
            .filter(SongLike.song_id.in_(song_ids), SongLike.user_id == user_id)
            .all()
        ):
            stats[sid]["liked_by_me"] = True
        for (sid,) in (
            db.query(SongFavorite.song_id)
            .filter(SongFavorite.song_id.in_(song_ids), SongFavorite.user_id == user_id)
            .all()
        ):
            stats[sid]["favorited_by_me"] = True
        for sid, sc in (
            db.query(SongRating.song_id, SongRating.score)
            .filter(SongRating.song_id.in_(song_ids), SongRating.user_id == user_id)
            .all()
        ):
            stats[sid]["my_rating"] = int(sc)
    return stats


def _author_names(db: Session, user_ids: list[int]) -> dict[int, str]:
    """广场展示发布者名：邮箱局部名（**不输出完整邮箱**，避免暴露 PII）。"""
    ids = [u for u in user_ids if u is not None]
    if not ids:
        return {}
    rows = db.query(User.id, User.email).filter(User.id.in_(ids)).all()
    out: dict[int, str] = {}
    for uid, email in rows:
        local = (email or "").split("@")[0]
        out[uid] = local[:2] + "***" if len(local) > 2 else (local or "匿名")
    return out


def _to_out(
    song: Song,
    user: Optional[User] = None,
    stats: Optional[dict] = None,
    *,
    include_sync: bool = False,
) -> SongOut:
    """序列化歌曲。未订阅者只拿到「试听片段」链接，完整音频 URL 不泄露。

    ⚠ 실패-닫힘(fail-closed): 프리뷰 자산이 아직 없으면 미구독자에게는 audio_url=None 을 준다.
      완곡 URL 로 되돌리면 게이트가 무력화되므로 절대 그렇게 하지 않는다
      (프리뷰 생성 실패 시에만 발생 — 그 땐 재생 불가가 정답).
    """
    user_id = user.id if user is not None else None
    owner = bool(user_id is not None and song.user_id == user_id)
    full = can_play_full(user)
    audio = (song.audio_url or None) if full else (song.preview_audio_url or None)
    st = stats or {}
    # 逐字时间轴仅详情接口按需曝光（list/plaza 等高频列表不返——单首 ~10KB JSON 会撑大包体）。
    lyrics_sync_out = song.lyrics_sync if include_sync else None
    return SongOut(
        id=song.id,
        title=song.title,
        custom_id=song.custom_id or None,
        style=song.style or None,
        prompt=song.prompt or None,
        lyric=song.lyric or None,
        audio_url=audio,
        cover_url=song.cover_url or None,
        status=song.status or "pending",
        make_instrumental=bool(song.make_instrumental),
        # [fix 2026-09-10] songs.task_type 컬럼은 NULL 허용 + SongOut.task_type 은 비-Option[str].
        # pydantic v2 는 "키가 없을 때만" 기본값을 쓰므로 None 명시 전달 시 500 ValidationError.
        # 구버전 행/우회 삽입 경로에서 NULL 이 들어와도 직렬화가 깨지지 않도록 여기서 강제한다.
        task_type=song.task_type or "generate",
        model_version=song.model_version or None,
        duration=song.duration,
        parent_song_id=song.parent_song_id,
        extend_type=song.extend_type or None,
        moderation_status=song.moderation_status or "pending",
        is_public=bool(song.is_public),
        play_count=song.play_count or 0,
        is_owner=owner,
        like_count=st.get("like_count", 0),
        favorite_count=st.get("favorite_count", 0),
        rating_avg=st.get("rating_avg"),
        rating_count=st.get("rating_count", 0),
        liked_by_me=st.get("liked_by_me", False),
        favorited_by_me=st.get("favorited_by_me", False),
        my_rating=st.get("my_rating"),
        author_name=st.get("author_name"),
        content_category=song.content_category or None,
        lyrics_sync=lyrics_sync_out,
        # [SEC] 2026-09-13: 원가(cost_cny) 는 **소유자에게만** 노출.
        #   기존엔 익명 광장(/api/plaza, /explore) 응답에도 실려 나가 경쟁사/사용자가
        #   공급자 단가 구조를 그대로 읽을 수 있었다(영업 기밀 유출, CWE-200).
        cost_cny=float(song.cost_cny) if (owner and song.cost_cny is not None) else None,
        preview_audio_url=song.preview_audio_url or None,
        preview_seconds=song.preview_seconds,
        can_play_full=full,
        can_download=can_download(user),
        created_at=song.created_at.isoformat() if song.created_at else "",
    )


def enforce_or_hold(user, db: Session, song: Song, allow_wallet: bool = False) -> None:
    """生成前置：配额(402) 或 钱包轨 hold（按次扣 FLOW credit）。

    在 Song 已建（有 id + price_cny）后调用。免费额度内 → 直接放行；
    超配额 + **用户显式同意**(allow_wallet) + 开关开 + 已关联 FLOW → hold 放行；
    否则 → 402（调用方负责回滚已建的 Song 行，避免孤儿）。

    ⚠️ allow_wallet 必须来自请求体的 `use_flow_credit`（用户主动勾选），
    不能默认 True —— 未经知情同意自动扣款 = 业务逻辑缺陷 + 合规风险(CWE-840)。
    """
    limits = resolve_limits(db, user.tier_key)
    # 排除「刚建好的本曲」再统计今日已生成数，避免把自己算进配额
    prior_today = daily_generated_count(db, user.id) - 1
    if prior_today < limits["daily_quota"]:
        return
    if allow_wallet and settings.enable_flow_wallet_track:
        try:
            # 超额扣费的单首价：与用户 tier price_cny 解耦。
            # free 档 price_cny=0（loss-leader），不能作为扣费额（cny_to_krw(0) 会拒），
            # 故价格≤0 时回退到 flow_wallet_per_song_cny 固定单价。
            price_cny = float(song.price_cny or 0)
            if price_cny <= 0:
                price_cny = settings.flow_wallet_per_song_cny
            hold_wallet_track(
                db,
                music_user_id=user.id,
                song_id=song.id,
                price_cny=price_cny,
            )
            # hold 必须落盘：hold_wallet_track 仅 flush，而 get_db 会话在响应后关闭会回滚
            # 未提交事务，导致 reservation 丢失（见 P1 接线回归 #1）。
            db.commit()
            return
        except (ValueError, RuntimeError):
            # 未关联 / 开关关 / 套利下限违例 → 回退 402
            pass
    raise HTTPException(
        status_code=402,
        detail="今日生成额度已用完，请升级订阅或用 FLOW credit 支付以继续",
    )


def _settle_wallet_track(db: Session, song: Song | None) -> None:
    """后台任务结束按本曲最终状态结算钱包轨：completed→capture / 其余→release。

    幂等：无 held 预约（免费额度内生成 / post-process）直接早退。仅当开关开启时生效。
    """
    if song is None or not settings.enable_flow_wallet_track:
        return
    resv = get_held_reservation_by_job(db, f"song:{song.id}")
    if resv is None:
        return
    if song.status == "completed":
        capture_wallet_track(db, resv.reservation_id)
    else:
        release_wallet_track(db, resv.reservation_id)
    db.commit()


def _resolve_model_version(
    db: Session, requested: str | None, tier_key: str | None, engine_name: str
) -> str:
    """确定本次生成使用的模型：① 用户显式指定 ② 档位绑定 ③ 引擎默认。

    [SEC] 2026-09-13 审计：① 此前**无任何白名单**。而单位经济里
    「成本由 model_version 决定（`resolve_cost_cny`）、价格只由档位决定」，
    于是免费档用户可以指定最贵的模型（如 mureka-9.5，¥1.10/首 vs 7.6 的 ¥0.22），
    在每日配额内把我们的成本放大 5 倍且收入恒为 0 —— 单位经济直接倒挂(CWE-840)。

    只在**客户端显式指定**时校验：留空走档位绑定/引擎默认时不做限制，
    否则 `resolve_tier_model_version` 返回空串（=引擎默认）的档位会被误杀。
    """
    requested = (requested or "").strip()
    if not requested:
        return (resolve_tier_model_version(db, tier_key) or "").strip()
    allowed = allowed_model_versions(engine_name)
    # 원가 표가 없는 엔진(예: suno) 은 검증할 근거가 없음 → 통과
    if allowed and requested.lower() not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的 model_version：{requested}（可选：{', '.join(sorted(allowed))}）",
        )
    return requested


def _create_song(req: GenerateReq, user: User, db: Session) -> Song:
    # 单位经济核算：成本=引擎(模型)单价×首数；价格=适用套餐标准价；毛利=价格-成本
    engine_name = resolve_engine(db, req.content_category)
    n = int(settings.mureka_n or 1) if engine_name == "mureka" else 1  # Mureka 按首计费
    tier_key = user.tier_key
    # 모델 결정 우선순위: ① 사용자가 명시한 model_version ② 티어 바인딩 모델(lite→mureka-7.6) ③ 엔진 기본
    # ⚠ ② 가 빠지면 저가 티어가 "가격만 낮고 원가는 그대로" → 마진 음수(절감 효과 0).
    model_version = (req.model_version or "").strip() or resolve_tier_model_version(db, tier_key)
    cost = resolve_cost_cny(db, req.content_category, n=n, model_version=model_version or None)
    price = resolve_price_cny(db, tier_key, req.content_category)
    margin = compute_margin_cny(price, cost)
    song = Song(
        user_id=user.id,
        title=req.title or "未命名",
        style=req.style or "",
        prompt=req.prompt or "",
        lyric=req.lyric or "",
        make_instrumental=req.make_instrumental,
        status="pending",
        task_type=req.task,
        model_version=model_version,
        parent_song_id=req.source_song_id,
        is_public=bool(req.is_public),
        vocal_gender=req.vocal_gender,
        vocal_id=req.vocal_id,
        clip_seconds=req.clip_seconds,
        extend_type=_normalize_extend_type(req.extend_type),
        content_category=req.content_category or "general",
        engine_name=engine_name,
        cost_cny=cost,
        price_cny=price,
        margin_cny=margin,
        tier_key=tier_key,
    )
    db.add(song)
    db.commit()
    db.refresh(song)
    try:
        enforce_or_hold(user, db, song, bool(getattr(req, "use_flow_credit", False)))
    except HTTPException:
        db.delete(song)
        db.commit()
        raise
    return song


def _apply_postprocess(remote_url: str) -> str:
    """下载远程音频 -> 本地后处理 -> 回传，返回新 URL；任意失败返回原 URL。"""
    import logging
    import os

    from app import postprocess
    from app.storage.s3 import download_to_temp, upload

    try:
        local_in = download_to_temp(remote_url)
        local_out = local_in + ".pp.wav"
        postprocess.run_postprocess(local_in, local_out)
        key = f"postprocess/{os.path.basename(local_out)}"
        return upload(local_out, key)
    except Exception as e:  # 后处理非关键路径，失败保留原音频
        logging.getLogger(__name__).warning("音频后处理跳过（保留原音频）：%s", e)
        return remote_url


def _preview_cache_dir() -> str:
    """试听片段本地缓存目录（相对路径按 CWD=backend 解析）。"""
    import os

    d = settings.preview_cache_dir or ".cache/preview"
    return d if os.path.isabs(d) else os.path.join(os.getcwd(), d)


def _preview_cache_path(song_id: int) -> str:
    import os

    return os.path.join(_preview_cache_dir(), f"{int(song_id)}.preview.mp3")


def _store_preview_local(song_id: int, src_path: str) -> str:
    """把裁剪好的片段放进本地缓存，返回可访问的端点路径（幂等：重复覆盖无副作用）。"""
    import os
    import shutil

    dst = _preview_cache_path(song_id)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copyfile(src_path, dst)
    return f"/api/songs/{song_id}/preview"


def _make_preview(remote_url: str, song_id: int) -> str:
    """완곡 URL → 앞 N초만 자른「试听片段」을 저장하고 그 URL 을 반환.

    저장 위치는 2가지:
      · storage_* 설정됨  → 객체 스토리지에 업로드(운영 권장, CDN 경유)
      · 미설정            → 로컬 캐시 + GET /api/songs/{id}/preview 로按需 생성·서빙
        (⚠ 이 폴백이 없으면 미구독자는 프리뷰 생성 실패로 "아무 소리도 못 듣게" 된다.
          게이트가 기능정지로 변하므로 절대 빼먹지 말 것.)

    실패 시 빈 문자열. 이때 미구독자는 `_to_out` 에서 audio_url=None 이 되어 재생 불가
    (fail-closed) — 완곡으로 폴백하면 게이트가 무력화되므로 절대 그렇게 하지 않는다.
    """
    import logging
    import os

    from app import postprocess
    from app.storage.s3 import download_to_temp, upload, is_configured

    try:
        local_in = download_to_temp(
            remote_url,
            timeout_total=settings.preview_fetch_timeout,
            max_bytes=int(settings.preview_fetch_max_mb * 1024 * 1024),
        )
        local_out = local_in + ".preview.mp3"
        postprocess.trim(local_in, local_out, preview_seconds())
        if is_configured():
            key = f"preview/{song_id}_{os.path.basename(local_out)}"
            return upload(local_out, key)
        return _store_preview_local(song_id, local_out)
    except Exception as e:  # 프리뷰 실패는 게이트 유지로 처리(완곡 노출 금지)
        logging.getLogger(__name__).warning("试听片段生成失败（未订阅者将无法播放）：%s", e)
        return ""


async def _finalize_song(song: Song, db: Session, result) -> None:
    """生成完成后的**统一收尾**：后处理 → 试听片段 → 音频机审 → 自动过审 → 拦截提示。

    ⚠️ 阶段 2 的 instrumental / easy-generate / region-edit 也必须走这里，不能各写各的：
      · 少了 preview_audio_url → 未订阅者在 premium gate 下「什么都听不到」（门控变功能停摆）；
      · 少了 moderation_status=approved → 作品永远进不了 explore / sync（门控变永久不可见）；
      · 少了音频机审 → 新端点成为审核绕过路径。
    抽出来而不是复制，正是为了让新增端点天然继承既有门控。
    """
    # 音频后处理（F1/F2/F3/F4）：下载 -> 本地处理 -> 回传。默认关；失败不影响主流程。
    if settings.enable_audio_postprocess and song.audio_url:
        # [D32] 동기 블로킹 후처리(ffmpeg + 파일 IO) 를 이벤트루프 밖 스레드풀로.
        # 단일 호스트 공존 1대에서 블라스트 레이디어스 확대 차단(§21.3).
        song.audio_url = await run_in_threadpool(_apply_postprocess, song.audio_url)
    # 프리미엄 게이트: 완곡은 그대로 두고, 미구독자용「앞 N초」프리뷰를 별도 자산으로 생성.
    if (
        settings.enable_preview_clip
        and result.status == "completed"
        and song.audio_url
        and not song.preview_audio_url
    ):
        preview_url = await run_in_threadpool(_make_preview, song.audio_url, song.id)
        if preview_url:
            song.preview_audio_url = preview_url
            song.preview_seconds = preview_seconds()
    # P3 音频机审：生成完成后对最终音频做内容审核；未通过标记 rejected。
    # 仅在 pending（未人工审核）时生效，不覆盖 admin 已 approved/rejected。
    if (
        result.status == "completed"
        and settings.enable_audio_moderation
        and song.moderation_status == "pending"
        and song.audio_url
        and not moderation.moderate_audio(song.audio_url)
    ):
        song.moderation_status = "rejected"
    # E4 自动流转：文本已通过 check_lyric（generate_song / _moderate_spawn），
    # 音频通过（或未启用音频审核）→ 生成成功后自动置已审核。不覆盖 admin 已审核结论。
    # [D33] 심의가 설정된 경로일 때만 자동 approved 로 승격.
    # enable_moderation=False(미설정) 면 미심의 상태(pending) 로 유지 —
    # explore/sync 가 명시적 approved 만 통과시키므로 무심의 노출을 막는다.
    elif (
        result.status == "completed"
        and settings.enable_moderation
        and song.moderation_status == "pending"
    ):
        song.moderation_status = "approved"
    # P2 明确审核提示：最终 failed 且判定为内容拦截 → 标记 rejected + 原因（前端可见）
    if song.status == "failed" and getattr(result, "moderation_blocked", False):
        song.moderation_status = "rejected"
        if result.moderation_note:
            song.moderation_note = result.moderation_note


async def _process_song(song_id: int):
    db = SessionLocal()
    song = None
    try:
        song = db.get(Song, song_id)
        if not song:
            return
        song.status = "processing"
        db.commit()

        # 引擎由下方 generate(req, song.engine_name) 按后台路由构造
        continue_id = None
        if song.task_type in ("extend", "cover", "remix") and song.parent_song_id:
            parent = db.get(Song, song.parent_song_id)
            if parent:
                # 续写/翻唱基于父歌 custom_id（UUID）；无则回退 external_id（task_id）
                continue_id = parent.custom_id or parent.external_id
        req = GenerateRequest(
            prompt=song.prompt or None,
            style=song.style or None,
            lyric=song.lyric or None,
            title=song.title,
            vocal_gender=song.vocal_gender,
            vocal_id=song.vocal_id,
            make_instrumental=song.make_instrumental,
            task=song.task_type,
            model_version=song.model_version or None,
            continue_song_id=continue_id,
            clip_seconds=song.clip_seconds,
            # 从 DB 带回衔接方向，否则这里永远是 None → provider 一律 tail，
            # 「从头部衔接」被静默丢弃（本缺陷的根因）。
            extend_type=song.extend_type,
        )
        try:
            # P2 Failover：按内容分类构造引擎链（首要 + 其余已启用按优先级），一引擎拦截即换下一引擎
            chain = resolve_engine_chain(db, song.content_category)
            result = await generate(req, engine_chain=chain)
        except ValueError as e:
            song.status = "failed"
            song.moderation_note = f"引擎未接入: {e}"
            db.commit()
            return
        song.status = result.status
        song.external_id = result.external_id
        song.custom_id = result.custom_id or ""
        song.audio_url = result.audio_url or ""
        song.cover_url = result.cover_url or ""
        song.lyric = result.lyric or song.lyric
        song.lyrics_sync = result.lyrics_sync or song.lyrics_sync
        song.title = result.title or song.title
        song.model_version = result.model_version or song.model_version
        song.duration = result.duration or 0
        await _finalize_song(song, db, result)
        db.commit()
    except Exception as e:
        if song is not None:
            song.status = "failed"
            # 保留底层失败原因（如 Mureka 403 地域封锁、422 参数错误、网络超时），
            # 不再静默丢弃。该原因落到 moderation_note，前端「生成失败」详情可直接展示，
            # 便于用户/运维区分「地域封锁 / 密钥失效 / 参数错误 / 限流」等根因。
            try:
                song.moderation_note = f"生成提交失败: {type(e).__name__}: {e}"[:2000]
            except Exception:
                pass
            log.warning("歌曲 %s 生成提交失败: %s", getattr(song, "id", "?"), e)
        db.commit()
    finally:
        _settle_wallet_track(db, song)
        db.close()


# ---- 阶段 0 止血：引擎能力 fail-closed（规划 B，PLAN_H5_AND_MUREKA_GAPS.md B.3 阶段 0）----
# 背景：路由层 songs.py 整体是 Suno API 形状，但线上引擎是 Mureka，两者能力集不同。
# 原缺失能力时的两种坏味道：
#   ① 后期处理（crop/speed/whole_song/aligned_lyrics/sound/upload）：方法只存在于
#      SunoProvider，Mureka 下 getattr 抛 AttributeError，且发生在后台任务里 →
#      被吞成 HTTP 500，而用户已先收到误导性的 200。
#   ② extend/cover/remix：MurekaProvider.create() 忽略 req.task → 静默产出
#      「与父歌毫无关联的新歌」假功能，用户拿到似是而非的结果。
# 现统一改为：派发**前**探测能力，缺失 → 501 Not Implemented（明确、可观测、前端可降级）。
# 阶段 1 接通真实 Mureka 端点后（/song/extend、/song/remix、files/upload 等）再放开。


def _require_capability(capability: str) -> None:
    """当前引擎未实现该能力方法 → 501（而非后台 AttributeError → 500）。"""
    provider = get_orchestrator()
    if not provider.supports(capability):
        raise HTTPException(
            status_code=501,
            detail=(
                f"当前引擎「{type(provider).__name__}」未实现「{capability}」能力，该功能暂不可用"
            ),
        )


def _require_task_support(task: str, db: Session, category: str | None) -> None:
    """engine chain 中无任何引擎真正支持该 task → 501（避免退化成假续写/假翻唱/假 Remix）。"""
    chain = resolve_engine_chain(db, category)
    for name in chain:
        if not name:
            continue
        try:
            provider = get_orchestrator(name)
        except ValueError:  # 引擎已注册但未接入 provider → 跳过（client.generate 同逻辑）
            continue
        if task in provider.supported_tasks:
            return
    raise HTTPException(
        status_code=501,
        detail=(
            f"当前引擎（{', '.join(chain) or (settings.music_provider or 'suno')}）"
            f"不支持「{task}」，该功能暂不可用"
        ),
    )


# extend 衔接方向白名单。非白名单值一律 400 fail-closed，**不静默改写为 tail**：
# 静默改写会让用户以为「从头部衔接」生效了，实际拿到的是尾部续写 —— 正是 GAP-008/149
# 禁止的假功能形态。白名单与 mureka.py:_create_extend 的取值保持一致。
_EXTEND_TYPES = frozenset({"tail", "head"})


def _normalize_extend_type(raw: str | None) -> str | None:
    """校验并归一化 extend_type；None/空串 = 未指定（交 provider 默认 tail）。

    非法值 → 400，不降级。大小写不敏感（"HEAD" 归一为 "head"）。
    """
    if raw is None:
        return None
    value = str(raw).strip().lower()
    if not value:
        return None
    if value not in _EXTEND_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"extend_type 只支持 tail（从尾部衔接）或 head（从头部衔接），收到: {raw}",
        )
    return value


def _load_spawn_parent(
    song_id: int, user: User, require_owner: bool = False, *, db: Session
) -> Song:
    """取父歌并做血缘访问控制（extend 需本人，cover/remix 允许他人公开歌）。

    抽成独立函数的原因：机审必须在**建曲之前**拿到父歌以计算回退生效值，
    而访问控制一旦写两份就会漂移（机审用一份、落库用另一份 → 绕过口子再现）。
    """
    parent = db.get(Song, song_id)
    if not parent:
        raise HTTPException(status_code=404, detail="源歌曲不存在")
    if require_owner and parent.user_id != user.id:
        # 不区分「不存在」与「非本人」，避免探测他人歌曲 id 是否存在
        raise HTTPException(status_code=404, detail="源歌曲不存在")
    if not require_owner and not parent.is_public and parent.user_id != user.id:
        raise HTTPException(status_code=403, detail="仅可对公开歌曲或自己的歌曲翻唱或 Remix")
    if parent.status != "completed":
        raise HTTPException(status_code=400, detail="源歌曲尚未生成完成")
    return parent


def _spawn_from_parent(
    song_id: int,
    task: str,
    body: SpawnReq,
    db: Session,
    user: User,
    require_owner: bool = False,
) -> Song:
    parent = _load_spawn_parent(song_id, user, require_owner, db=db)
    # 阶段 0 止血：task 未被链路中任何引擎真正支持 → 501，不再静默退化成「假功能」
    _require_task_support(task, db, parent.content_category)
    # Mureka /song/extend 与 /song/remix 硬性要求 lyrics（官方文档标注「Required」）；
    # 未填时回退父歌歌词（remix=换风格沿用歌词、extend=续写原词），仍为空则明确 400，
    # 而不是退化成「与父歌无关的新歌」假功能（规划 B 阶段 0 原则）。
    lyric, prompt, style = _resolve_spawn_values(parent, body)
    if task in ("extend", "remix", "cover") and not (lyric or "").strip():
        raise HTTPException(
            status_code=400,
            detail=f"「{task}」需要歌词内容（Mureka 该能力要求歌词为必填项），请填写歌词后重试",
        )
    # 衔接方向：仅 extend 有意义；cover/remix 传了也不该带进 payload。
    extend_type = _normalize_extend_type(body.extend_type) if task == "extend" else None
    req = GenerateReq(
        task=task,
        source_song_id=parent.id,
        prompt=prompt,
        style=style,
        lyric=lyric,
        title=body.title or f"{parent.title} · {task}",
        make_instrumental=body.make_instrumental
        if body.make_instrumental is not None
        else parent.make_instrumental,
        model_version=body.model_version,
        clip_seconds=body.clip_seconds,
        extend_type=extend_type,
        content_category=parent.content_category,
        use_flow_credit=bool(getattr(body, "use_flow_credit", False)),
    )
    return _create_song(req, user, db)


def _moderate_spawn_text(*texts: str | None) -> None:
    """续写/翻唱机审：逐项校验**即将送进引擎的生效值**。

    ⚠️ 与旧实现（`_moderate_spawn(body)`）的关键差异 —— 旧实现只看请求体，
    而 `_spawn_from_parent` 在 body 留空时会**回退到父歌的 lyric/prompt/style**。
    cover/remix 允许对**他人的公开歌**发起，父歌未必审过（机审可能是后来才开的、
    或该歌是更早版本生成的）→ 「拿一首公开歌当载体，把未审文本送进引擎」的审核绕过路径。

    因此改由调用方传入**回退后的生效值**（见 _effective_spawn_texts），逐个校验：
      · 逐项而非拼接：拼接会产生假阴性（两段各自合法、拼起来命中敏感词）
        以及假阳性（拼接引入跨字段上下文误伤）；
      · 逐项也覆盖了「只改 prompt 沿用父歌 lyric」这类混合场景 —— 每一项都过闸。

    空值跳过：未提供且父歌也没有 = 没有文本要审。
    """
    if not settings.enable_moderation:
        return
    for text in texts:
        if text and not moderation.check_lyric(text):
            raise HTTPException(status_code=400, detail="内容未通过审核，请修改后重试")


def _resolve_spawn_values(parent: Song, body: SpawnReq) -> tuple[str, str, str]:
    """父歌回退的唯一实现：算出 spawn 后真正会送进引擎的 (lyric, prompt, style)。

    ⚠️ 单一来源是刻意的。回退逻辑此前只写在 `_spawn_from_parent` 里，
    机审看不到它 → 「请求体留空 + 父歌带未审文本」就能绕过机审。
    现在建曲与机审都从这里取值，二者不可能再各写一套而悄悄漂移。
    """
    return (
        body.lyric if body.lyric is not None else (parent.lyric or ""),
        body.prompt if body.prompt is not None else (parent.prompt or ""),
        body.style if body.style is not None else (parent.style or ""),
    )


def _effective_spawn_texts(task: str, parent: Song, body: SpawnReq) -> list[str]:
    """待机审的生效文本项（按 task 只取真会进 payload 的字段）。"""
    lyric, prompt, style = _resolve_spawn_values(parent, body)
    if task == "extend":
        # extend 只送 lyrics（mureka._create_extend 的 payload 无 prompt/style），
        # 审多余的字段会拦下根本不会送进引擎的内容 —— 机审也要精确。
        return [lyric]
    return [lyric, prompt, style]


@router.post("/generate", response_model=GenerateResp)
async def generate_song(
    req: GenerateReq,
    bg: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    # 生成前置：突发限流 + 每日配额（429 / 402）
    rate_limit_generate(str(user.id))
    # 歌词机审（MVP：敏感词；生产接 ASR + 审核模型，见 PROMPT.md）
    text_to_check = req.lyric or req.prompt or ""
    if settings.enable_moderation and not moderation.check_lyric(text_to_check):
        raise HTTPException(status_code=400, detail="内容未通过审核，请修改后重试")
    # 长度闸：Mureka /song/generate 官方约束 lyrics ≤5000 字符（mureka.py:8 注释 + 实测）；
    # /assist/lyrics 已截 4500 避免越界，但用户手填仍可能超。提前 400 拦截，
    # 比让 Mureka 返回 400 + 记入 moderation_note（无法解读为「歌词过长」）更友好。
    if req.lyric and len(req.lyric) > 5000:
        raise HTTPException(
            status_code=400,
            detail=f"歌词超过 5000 字上限（当前 {len(req.lyric)} 字），请精简后重试",
        )

    song = _create_song(req, user, db)
    bg.add_task(_process_song, song.id)
    return GenerateResp(song_id=song.id, status=song.status)


@router.post("/{song_id}/extend", response_model=GenerateResp)
async def extend_song(
    song_id: int,
    body: SpawnReq,
    bg: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    rate_limit_generate(str(user.id))
    # 机审改判「生效值」：先按父歌回退算出真正会送进引擎的文本，再逐项校验。
    # 见 _moderate_spawn_text 文档 —— 这是封堵「父歌回退绕过机审」的关键。
    parent = _load_spawn_parent(song_id, user, require_owner=True, db=db)
    _moderate_spawn_text(*_effective_spawn_texts("extend", parent, body))
    song = _spawn_from_parent(song_id, "extend", body, db, user, require_owner=True)
    bg.add_task(_process_song, song.id)
    return GenerateResp(song_id=song.id, status=song.status)


@router.post("/{song_id}/cover", response_model=GenerateResp)
async def cover_song(
    song_id: int,
    body: SpawnReq,
    bg: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    rate_limit_generate(str(user.id))
    # 机审改判「生效值」：先按父歌回退算出真正会送进引擎的文本，再逐项校验。
    # 见 _moderate_spawn_text 文档 —— 这是封堵「父歌回退绕过机审」的关键。
    parent = _load_spawn_parent(song_id, user, require_owner=False, db=db)
    _moderate_spawn_text(*_effective_spawn_texts("cover", parent, body))
    song = _spawn_from_parent(song_id, "cover", body, db, user, require_owner=False)
    bg.add_task(_process_song, song.id)
    return GenerateResp(song_id=song.id, status=song.status)


@router.post("/{song_id}/remix", response_model=GenerateResp)
async def remix_song(
    song_id: int,
    body: SpawnReq,
    bg: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    rate_limit_generate(str(user.id))
    # 机审改判「生效值」：先按父歌回退算出真正会送进引擎的文本，再逐项校验。
    # 见 _moderate_spawn_text 文档 —— 这是封堵「父歌回退绕过机审」的关键。
    parent = _load_spawn_parent(song_id, user, require_owner=False, db=db)
    _moderate_spawn_text(*_effective_spawn_texts("remix", parent, body))
    song = _spawn_from_parent(song_id, "remix", body, db, user, require_owner=False)
    bg.add_task(_process_song, song.id)
    return GenerateResp(song_id=song.id, status=song.status)


@router.get("/explore", response_model=list[SongOut])
def explore_songs(
    limit: int = 50,
    offset: int = 0,
    q: str | None = None,
    db: Session = Depends(get_db),
):
    """发现页公开流：已公开 + 已审核通过 + 生成完成，按播放量 / 时间排序。匿名可访问。

    q 为可选关键字，按标题 / 歌词做不区分大小写匹配（参数化 ilike，防注入）。
    """
    filters = [
        Song.is_public.is_(True),
        Song.moderation_status == "approved",
        Song.status == "completed",
    ]
    if q:
        like = f"%{q}%"
        filters.append(or_(Song.title.ilike(like), Song.lyric.ilike(like)))
    rows = (
        db.query(Song)
        .filter(*filters)
        .order_by(Song.play_count.desc(), Song.id.desc())
        .limit(limit)
        .offset(offset)
        .all()
    )
    return [_to_out(s) for s in rows]


@router.post("/sound", response_model=GenerateResp)
async def create_sound(
    req: SoundReq,
    bg: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    rate_limit_generate(str(user.id))
    # 阶段 0 止血：Mureka 无 sound 能力 → 501（原为后台 AttributeError → 500）
    _require_capability("sound")
    engine_name = settings.music_provider or "mureka"
    cost = resolve_cost_cny(db, None, n=1)
    tier_key = user.tier_key
    price = resolve_price_cny(db, tier_key, None)
    margin = compute_margin_cny(price, cost)
    song = Song(
        user_id=user.id,
        title=req.title,
        style=req.tags or "",
        task_type="sound",
        make_instrumental=True,
        status="pending",
        model_version=req.model_version or "",
        engine_name=engine_name,
        cost_cny=cost,
        price_cny=price,
        margin_cny=margin,
        tier_key=tier_key,
    )
    db.add(song)
    db.commit()
    db.refresh(song)
    try:
        enforce_or_hold(user, db, song, bool(getattr(req, "use_flow_credit", False)))
    except HTTPException:
        db.delete(song)
        db.commit()
        raise
    bg.add_task(_process_sound, song.id, req)
    return GenerateResp(song_id=song.id, status=song.status)


@router.post("/upload", response_model=UploadResp)
async def upload_audio(
    req: UploadReq,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """上传参考音频，返回 custom_id（供 extend/cover 使用）。"""
    # [SEC] H6(2026-09-13): 이 URL 은 **우리 서버가 아니라 Mureka 가 fetch** 한다
    # (orchestrator/mureka.py:431 — multipart form 의 `url` 필드로 전달). 즉 우리는
    # 제3자 SSRF 의 confused deputy 다. 내부망/링크로컬/클라우드 메타데이터로의 인출을
    # 본 단에서 차단해야 하므로 전달 전 공인 주소 여부를 검증한다.
    # ⚠ ValueError 를 그대로 두면 500 — 사용자 입력 오류이므로 400 으로 변환.
    # ⚠ 능력치 검사(_require_capability)보다 **먼저** 둔다: 입력 검증이 선행되어야
    #   엔진 미지원(501) 여부와 무관하게 항상 차단된다.
    try:
        ssrf_guard.guard(req.audio_url)
    except ValueError as e:
        raise HTTPException(400, f"audio_url 不在允许的公网范围内：{e}")
    # 阶段 0 止血：Mureka 无 upload 方法 → 501（原为 AttributeError → 500）
    _require_capability("upload")
    provider = get_orchestrator()
    custom_id = await provider.upload(req.audio_url)
    return UploadResp(custom_id=custom_id)


# ---------------------------------------------------------------------------
# 阶段 2：Mureka 新增能力的 REST 端点
# 端点规格以官方文档为准（PLAN_H5_AND_MUREKA_GAPS.md B.5.3），全部先过
# `_require_capability` → 引擎未实现即 501（阶段 0 的 fail-closed 约定）。
# ---------------------------------------------------------------------------
_STEM_MODELS = ("audio-separation-1", "audio-separation-2", "audio-separation-3")
_VOCAL_CLONE_MAX_BYTES = 10 * 1024 * 1024  # 官方：file 必须 <10MB
_VOCAL_CLONE_EXTS = (".mp3", ".m4a")  # 官方：仅 mp3 / m4a


def _VOCAL_CLONE_MAGIC_OK(data: bytes) -> bool:
    """真实音频格式校验（CWE-434 纵深防御）：仅看扩展名可被改名绕过，
    故读取内容后额外校验文件头魔数。支持 MP3(ID3/MPEG 帧同步)、M4A/MP4(ftyp)、WAV(RIFF)。"""
    if not data or len(data) < 12:
        return False
    if data[:3] == b"ID3":  # ID3 标签
        return True
    if data[0] == 0xFF and (data[1] & 0xE0) == 0xE0:  # MPEG 音频帧同步(11 位全 1)
        return True
    if data[4:8] == b"ftyp":  # ISO BMFF / MP4 / M4A
        return True
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":  # WAV
        return True
    return False


def _safe_download_filename(title: str | None) -> str:
    """Content-Disposition 头注入防护（CWE-113）：去掉引号/控制字符/路径分隔符并限长，
    避免恶意歌名破坏附件头或注入。"""
    bad = set('"\r\n\t\\/')
    cleaned = "".join(ch for ch in (title or "") if ch not in bad and ord(ch) >= 0x20)
    cleaned = cleaned.strip().replace(" ", "_")[:80]
    return cleaned or "song"


def _new_song_row(
    user: User,
    db: Session,
    *,
    title: str,
    task: str,
    prompt: str = "",
    style: str = "",
    lyric: str = "",
    model_version: str = "",
    content_category: str | None = None,
    parent_song_id: int | None = None,
    make_instrumental: bool = False,
    allow_wallet: bool = False,
) -> Song:
    """新建 Song + 单位经济核算（成本/价格/毛利），供阶段 2 各新端点复用。

    不复用 `_create_song` 的原因：那些端点不是 GenerateReq（instrumental 无 lyrics、
    easy-generate 无歌词字段、region-edit 无 prompt），字段形状对不上，硬塞会污染语义。

    ⚠️ is_public **一律 False**：2026-09-10 产品修正要求「发布必须后置」——
    只有 status=completed 且审核通过的作品才允许公开（见 update_visibility）。
    """
    engine_name = resolve_engine(db, content_category)
    n = int(settings.mureka_n or 1) if engine_name == "mureka" else 1  # Mureka 按首计费
    model_version = _resolve_model_version(db, model_version, user.tier_key, engine_name)
    cost = resolve_cost_cny(db, content_category, n=n, model_version=model_version or None)
    price = resolve_price_cny(db, user.tier_key, content_category)
    song = Song(
        user_id=user.id,
        title=title or "未命名",
        style=style or "",
        prompt=prompt or "",
        lyric=lyric or "",
        task_type=task,
        status="pending",
        model_version=model_version,
        make_instrumental=make_instrumental,
        parent_song_id=parent_song_id,
        is_public=False,
        content_category=content_category or "general",
        engine_name=engine_name,
        cost_cny=cost,
        price_cny=price,
        margin_cny=compute_margin_cny(price, cost),
        tier_key=user.tier_key,
    )
    db.add(song)
    db.commit()
    db.refresh(song)
    # [SEC] 2026-09-13 审计（MED，CWE-367 TOCTOU 先查后放行竞争）：
    # 入口的 enforce_generate_limits 是「读计数 → 放行」，与这里的插入之间存在并发窗口：
    # 并发请求各自读到未超限的旧计数而全部放行 → 超额作品照样派发供应商（Mureka 按首计费 = 真金白银）。
    # 插入后**复查**（此时计数已含本行），超额即撤销；撤销发生在调用方 bg.add_task(派发) 之前，
    # 故超额作品不会被计费，闭掉这条烧钱路径。单实例下确定收敛；多实例仍有窄窗口
    # （彻底消除需 Redis/DB 级串行化，属架构项）。
    tier_limits = resolve_limits(db, user.tier_key)
    over_quota = daily_generated_count(db, user.id) > int(tier_limits["daily_quota"])
    try:
        if over_quota:
            raise HTTPException(
                status_code=402,
                detail="今日生成额度已用完，请升级订阅以继续",
            )
        enforce_or_hold(user, db, song, allow_wallet)
    except HTTPException:
        db.delete(song)
        db.commit()
        raise
    return song


@router.post("/vocal-clone", response_model=VocalCloneResp)
async def clone_vocal(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """POST /v1/vocal/clone — 音色克隆，**同步**返回 vocal_id。

    官方约束：mp3/m4a、单文件 <10MB。vocal_id 可用于 song/generate 与
    song/easy-generate；⚠️ **mureka-o2 不支持 vocal_id**。

    不创建 Song（产出是音色素材而非作品），但仍计入限流/配额：它会消耗供应商额度。

    ⏸ **产品暂停（2026-09-13 决策）**：默认 `settings.enable_vocal_clone=False` → 入口
    直接 501 fail-closed。⚠️ 本函数及其依赖（MurekaProvider.vocal_clone、文件头魔数校验
    `_VOCAL_CLONE_MAGIC_OK`、限流器 `rate_limit_vocal_clone`、相关用例）**全部保留、禁止删除**；
    重新上线只需把该开关置 True（.env: ENABLE_VOCAL_CLONE=true）。
    """
    if not settings.enable_vocal_clone:
        raise HTTPException(status_code=501, detail="音色克隆功能已暂停")
    _require_capability("vocal_clone")
    enforce_generate_limits(user, db)
    rate_limit_vocal_clone(str(user.id))
    name = (file.filename or "").lower()
    if not name.endswith(_VOCAL_CLONE_EXTS):
        raise HTTPException(
            status_code=400,
            detail=f"音色克隆仅支持 {' / '.join(_VOCAL_CLONE_EXTS)} 格式（Mureka 官方约束）",
        )
    # 预检大小：file.size 在多数网关下可用（multipart Content-Length）。
    # 提前 413 比「先读满 10MB+ 再判超」更省带宽、更友好；为 None 时回退到读取后判断。
    if file.size is not None and file.size > _VOCAL_CLONE_MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"音频超过 10MB 上限（当前 {file.size / 1024 / 1024:.1f}MB）",
        )
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="上传文件为空")
    if len(data) > _VOCAL_CLONE_MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"音频超过 10MB 上限（当前 {len(data) / 1024 / 1024:.1f}MB）",
        )
    provider = get_orchestrator()
    try:
        vocal_id = await provider.vocal_clone(data, filename=file.filename or "vocal.m4a")
    except Exception as e:  # 供应商报错不裸奔（可能含内部 URL / 请求体）
        log.warning("vocal-clone 失败（user_id=%s）: %s", user.id, e)
        raise HTTPException(status_code=502, detail="音色克隆失败，请稍后重试") from None
    return VocalCloneResp(vocal_id=vocal_id)


@router.post("/instrumental", response_model=GenerateResp)
async def create_instrumental(
    req: InstrumentalReq,
    bg: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """POST /v1/instrumental/generate — 纯器乐（prompt-only，无歌词）。

    ⚠️ 轮询必须用 /instrumental/query/{id}（**不是** /song/query），见 _process_instrumental。
    """
    rate_limit_generate(str(user.id))
    _require_capability("instrumental")
    if settings.enable_moderation and not moderation.check_lyric(req.prompt):
        raise HTTPException(status_code=400, detail="内容未通过审核，请修改后重试")
    if len(req.prompt) > 1024:
        raise HTTPException(
            status_code=400,
            detail=f"描述超过 1024 字上限（当前 {len(req.prompt)} 字），请精简后重试",
        )
    song = _new_song_row(
        user,
        db,
        title=req.title or "未命名",
        task="instrumental",
        prompt=req.prompt,
        model_version=req.model_version or "",
        content_category=req.content_category,
        make_instrumental=True,
        allow_wallet=req.use_flow_credit,
    )
    bg.add_task(_process_instrumental, song.id, req.prompt, req.model_version, req.n)
    return GenerateResp(song_id=song.id, status=song.status)


@router.post("/easy-generate", response_model=GenerateResp)
async def create_easy(
    req: EasyGenerateReq,
    bg: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """POST /v1/song/easy-generate — Prompt to song（**无歌词**，低门槛创作入口）。

    官方 styles 枚举含 gospel（见 mureka.MurekaProvider.EASY_STYLES）。prompt ≤2000
    （与 /song/generate 的 1024 不同，勿混用）。
    """
    rate_limit_generate(str(user.id))
    _require_capability("easy_generate")
    if settings.enable_moderation and not moderation.check_lyric(req.prompt):
        raise HTTPException(status_code=400, detail="内容未通过审核，请修改后重试")
    if len(req.prompt) > 2000:
        raise HTTPException(
            status_code=400,
            detail=f"描述超过 2000 字上限（当前 {len(req.prompt)} 字），请精简后重试",
        )
    # styles 校验：**仅接受官方枚举**，非法值直接 400 而非静默忽略（否则用户以为生效实则没生效）。
    cleaned = []
    if req.styles:
        for s in req.styles:
            key = s.strip().lower()
            if key in MurekaProvider.EASY_STYLES:
                cleaned.append(key)
            else:
                raise HTTPException(
                    status_code=400,
                    detail=f"不支持的风格「{s}」。可选：{', '.join(sorted(MurekaProvider.EASY_STYLES))}",
                )
    song = _new_song_row(
        user,
        db,
        title=req.title or "未命名",
        task="easy",
        prompt=req.prompt,
        style=", ".join(cleaned),
        model_version=req.model_version or "",
        content_category=req.content_category,
        allow_wallet=req.use_flow_credit,
    )
    bg.add_task(
        _process_easy, song.id, req.prompt, cleaned or None, req.model_version, req.n, req.vocal_id
    )
    return GenerateResp(song_id=song.id, status=song.status)


@router.post("/{song_id}/region-edit", response_model=GenerateResp)
async def region_edit_song(
    song_id: int,
    body: RegionEditReq,
    bg: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """POST /v1/song/region-edit — 局部改词（生成**子曲**，原曲保留）。

    官方约束：改写区间 edit_end - edit_start **≥3000ms**；lyrics ≤3000。
    """
    rate_limit_generate(str(user.id))
    _require_capability("region_edit")
    parent = _owned_completed(song_id, db, user)
    source_ref = parent.custom_id or parent.external_id
    if not source_ref:
        raise HTTPException(status_code=400, detail="源歌曲缺少供应商 ID，无法局部改词")
    if body.edit_end <= body.edit_start:
        raise HTTPException(
            status_code=400,
            detail="改写区间非法：结束时间须大于开始时间",
        )
    if body.edit_end - body.edit_start < 3000:
        # 提前 400：否则会在后台任务里抛 ValueError，前端只看到「生成失败」
        raise HTTPException(
            status_code=400,
            detail=f"改写区间须 ≥3000ms（当前 {body.edit_end - body.edit_start}ms）",
        )
    if len(body.lyrics) > 3000:
        raise HTTPException(
            status_code=400,
            detail=f"歌词超过 3000 字上限（当前 {len(body.lyrics)} 字），请精简后重试",
        )
    if settings.enable_moderation and not moderation.check_lyric(body.lyrics):
        raise HTTPException(status_code=400, detail="内容未通过审核，请修改后重试")
    song = _new_song_row(
        user,
        db,
        title=parent.title or "未命名",
        task="region_edit",
        style=parent.style or "",
        lyric=body.lyrics,
        parent_song_id=parent.id,
        content_category=parent.content_category,
        allow_wallet=body.use_flow_credit,
    )
    bg.add_task(
        _process_region_edit, song.id, source_ref, body.lyrics, body.edit_start, body.edit_end
    )
    return GenerateResp(song_id=song.id, status=song.status)


@router.post("/{song_id}/stem", response_model=StemResp)
async def export_stem(
    song_id: int,
    model: str = "audio-separation-2",
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """POST /v1/song/stem — 分轨导出（**同步**接口，立即返回 zip_url + midi_zip_url）。

    ⚠️ zip_url **会过期**（expires_at 为秒级 Unix 时间戳）。前端应在到期前转存到自有
    存储，或提示用户重新导出——它是「导出」而非「生成新歌」，故**不创建 Song 行**。
    不占每日生成配额（未新建作品），但仍校验所有权 + 完成状态。
    """
    _require_capability("stem")
    song = _owned_completed(song_id, db, user)
    if not song.audio_url:
        raise HTTPException(status_code=400, detail="歌曲尚无音频，无法分轨")
    if model not in _STEM_MODELS:
        raise HTTPException(
            status_code=400,
            detail=f"分轨模型须为 {' / '.join(_STEM_MODELS)}（当前 {model}）",
        )
    provider = get_orchestrator()
    try:
        data = await provider.stem(url=song.audio_url, model=model)
    except Exception as e:
        log.warning("stem 失败（song_id=%s）: %s", song_id, e)
        raise HTTPException(status_code=502, detail="分轨导出失败，请稍后重试") from None
    expires_at = data.get("expires_at")
    expires_at_iso = None
    if expires_at is not None:
        # 官方 expires_at 为秒级 Unix 时间戳（非毫秒），直接用 fromtimestamp。
        try:
            expires_at_iso = datetime.datetime.fromtimestamp(
                float(expires_at), tz=datetime.timezone.utc
            ).isoformat()
        except (TypeError, ValueError, OverflowError):
            expires_at_iso = None
    return StemResp(
        zip_url=data.get("zip_url"),
        midi_zip_url=data.get("midi_zip_url"),
        expires_at=expires_at,
        expires_at_iso=expires_at_iso,
    )


@router.get("/{song_id}", response_model=SongOut)
def get_song(
    song_id: int, db: Session = Depends(get_db), user: Optional[User] = Depends(get_optional_user)
):
    """读取单首歌曲详情（含逐字时间轴 lyrics_sync）。

    选择 인증: 토큰 없/유효하지 않으면 익명(None) → 공개(is_public) 곡만 조회 가능.
    비공개 곡은 소유자 본인만. 오디오 URL 은 _to_out 에서 can_play_full(user) 에 따라
    미가입자=시청 프리뷰로 fail-closed 게이팅됨(전체 URL 노출 안 함).
    """
    song = db.get(Song, song_id)
    if not song:
        raise HTTPException(status_code=404, detail="歌曲不存在")
    owner_id = user.id if user is not None else None
    if song.user_id != owner_id and not song.is_public:
        raise HTTPException(status_code=404, detail="歌曲不存在")
    return _to_out(song, user, include_sync=True)


@router.delete("/{song_id}", status_code=200)
def delete_song(
    song_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    """删除自己的歌曲；子歌血缘解绑（parent_song_id 置 NULL）避免外键悬挂。

    [F5] 删除传播(2026-09-01): 关联 SyncRecord 를 ``revoked`` 로 마킹한다.
    这样 FLOW APP 在 ``GET /api/v1/sync/flow`` 拉取歌单时会排除已删除歌曲
    (list_flow 仅返回 status=="synced")，避免孤儿残留(설계 §11.4).
    """
    song = db.get(Song, song_id)
    if not song or song.user_id != user.id:
        raise HTTPException(status_code=404, detail="歌曲不存在")
    # [F5] 删除传播 — 关联同步记录标记 revoked
    rec = db.query(SyncRecord).filter_by(song_id=song_id).first()
    if rec:
        rec.status = "revoked"
        db.commit()
    db.query(Song).filter(Song.parent_song_id == song.id).update({Song.parent_song_id: None})
    db.delete(song)
    db.commit()
    return {"detail": "deleted", "id": song_id}


@router.patch("/{song_id}", response_model=SongOut)
def update_visibility(
    song_id: int,
    body: VisibilityReq,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """发布到广场 / 取消公开（is_public）。仅所有者可改。

    ⚠ 发布**必须后置**（2026-09-10 产品修正）：
      · 只有 status == completed 才允许公开 —— 生成中/失败的作品不能进广场；
      · moderation_status == rejected 的作品禁止公开（已驳回内容不得上架）；
      · 取消公开（is_public=False）不受上述限制，任何时候都可下架。
    创建流程中的 is_public 勾选框已移除，公开动作统一走这里。
    """
    song = db.get(Song, song_id)
    if not song or song.user_id != user.id:
        raise HTTPException(status_code=404, detail="歌曲不存在")

    if body.is_public:
        if song.status != "completed":
            raise HTTPException(
                status_code=400,
                detail=f"歌曲尚未生成完成（当前状态：{song.status}），完成后才能发布到广场",
            )
        if song.moderation_status == "rejected":
            raise HTTPException(
                status_code=403,
                detail=f"该作品未通过内容审核，无法发布：{song.moderation_note or '已被驳回'}",
            )

    song.is_public = bool(body.is_public)
    db.commit()
    db.refresh(song)
    return _to_out(song, user, include_sync=True)


def _owned_completed(song_id: int, db: Session, user: User) -> Song:
    """取歌曲并校验：存在 + 所有者 + 生成完成。否则抛 404/403/400。"""
    song = db.get(Song, song_id)
    if not song:
        raise HTTPException(status_code=404, detail="歌曲不存在")
    if song.user_id != user.id:
        raise HTTPException(status_code=404, detail="歌曲不存在")
    if song.status != "completed":
        raise HTTPException(status_code=400, detail="源歌曲尚未生成完成")
    return song


async def _process_post(song_id: int, op: str, **params) -> None:
    """后期处理后台任务：调供应商 op 方法，按操作类型写回结果到原 Song。

    op ∈ crop / speed / whole_song（写回 audio_url）/ aligned_lyrics（写回 lyric）。
    供应商方法不是 MusicProvider 抽象契约，由各实现自行提供（见 orchestrator/*）。
    """
    db = SessionLocal()
    song = None
    try:
        song = db.get(Song, song_id)
        if not song:
            return
        song.status = "processing"
        db.commit()
        provider = get_orchestrator()
        result = await getattr(provider, op)(**params)
        song.status = result.status
        if op in ("crop", "speed", "whole_song"):
            song.audio_url = result.audio_url or song.audio_url
        elif op == "aligned_lyrics":
            song.lyric = result.lyric or song.lyric
        if result.duration:
            song.duration = result.duration
        db.commit()
    except Exception:
        if song is not None:
            song.status = "failed"
            db.commit()
    finally:
        db.close()


async def _process_sound(song_id: int, req: SoundReq) -> None:
    """生成音效后台任务：调供应商 sound 方法，写回新 Song。"""
    db = SessionLocal()
    song = None
    try:
        song = db.get(Song, song_id)
        if not song:
            return
        song.status = "processing"
        db.commit()
        provider = get_orchestrator()
        result = await provider.sound(
            title=req.title,
            tags=req.tags or "",
            mv=req.model_version or "chirp-crow",
            tempo=req.tempo,
            key=req.key,
            loop=req.loop,
        )
        song.status = result.status
        song.audio_url = result.audio_url or ""
        song.custom_id = result.custom_id or ""
        song.model_version = result.model_version or song.model_version
        song.duration = result.duration or 0
        db.commit()
    except Exception:
        if song is not None:
            song.status = "failed"
            db.commit()
    finally:
        _settle_wallet_track(db, song)
        db.close()


# ---- 阶段 2：Mureka 新增能力的后台任务（端点规格见 PLAN_H5_AND_MUREKA_GAPS.md B.5.3）----
async def _poll_task(poll_fn, external_id: str):
    """轮询直到完成 / 失败 / 超时。

    instrumental 与 song 的轮询端点**不同**（/instrumental/query vs /song/query），
    故由调用方传入 poll_fn，避免混用导致查不到任务。
    """
    # ⚠️ interval 取下限 0.05s：测试配置 song_poll_interval=0，若直接累加会导致
    # 「waited 恒为 0 → while 永不退出」的死循环（client.generate 里是同一隐患，
    # 那里靠首次轮询即 completed 提前 return 才没暴露）。
    interval = max(float(settings.song_poll_interval or 0), 0.05)
    waited = 0.0
    last = None
    while waited < settings.song_poll_timeout:
        await asyncio.sleep(interval)
        waited += interval
        last = await poll_fn(external_id)
        if last.status in ("completed", "failed"):
            return last
    return SongResult(
        external_id=external_id,
        status="failed",
        moderation_note="轮询超时" if last is not None else "未开始轮询（poll_timeout 过小）",
    )


def _write_back(song: Song, result) -> None:
    """把 SongResult 写回 Song（字段集与 _process_song 保持一致）。

    ⚠️ external_id **必须写回**：否则这首歌无法再作为父曲被 extend/remix/region-edit
    使用（_spawn_from_parent 与 _process_region_edit 都取 `song.custom_id or
    song.external_id` 作为供应商侧 ID）。_process_song 也是这么做的。
    ⚠️ moderation_note **仅在 failed 时**写回：给用户一个可读的失败原因（轮询超时/
    官方报错原文摘要），否则前端只显示「生成失败」而无从排查。
    """
    song.status = result.status
    if result.external_id:
        song.external_id = result.external_id
    if getattr(result, "custom_id", None):
        song.custom_id = result.custom_id
    song.audio_url = result.audio_url or song.audio_url
    song.cover_url = result.cover_url or song.cover_url
    song.lyric = result.lyric or song.lyric
    song.lyrics_sync = result.lyrics_sync or song.lyrics_sync
    song.title = result.title or song.title
    song.model_version = result.model_version or song.model_version
    if result.duration:
        song.duration = result.duration
    note = getattr(result, "moderation_note", None)
    if note and song.status == "failed":
        song.moderation_note = note


def _resolve_provider_for(db: Session, song: Song, capability: str):
    """按内容分类的引擎链选出**真正支持该能力**的 provider。

    ⚠️ 不能用裸 get_orchestrator()：那是 settings.music_provider（默认引擎），可能与
    song.engine_name（按 routing_rules 解析）不同 —— 一旦不同，后台任务会拿着 Mureka 的
    Song 去调 SunoProvider.instrumental()，AttributeError 被 except 吞掉后只留一个「生成失败」，
    极难定位。这里按 song.content_category 的 engine chain 逐一试探，返回首个真支持的。
    """
    chain = resolve_engine_chain(db, song.content_category)
    for name in chain:
        if not name:
            continue
        try:
            provider = get_orchestrator(name)
        except ValueError:
            continue
        if provider.supports(capability):
            return provider
    return None


async def _process_region_edit(
    song_id: int, source_ref: str, lyrics: str, edit_start: int, edit_end: int
) -> None:
    """局部改词后台任务。

    ⚠️ source_ref 由路由层显式传入（父曲的供应商 song_id）：本任务写回的是**新建的子曲**，
    子曲自身没有供应商 ID，若沿用 `song.custom_id or song.external_id` 会拿到空值。
    写成子曲而非原地改写，是为了保留原曲（与 extend/cover/remix 一致）。
    """
    db = SessionLocal()
    song = None
    try:
        song = db.get(Song, song_id)
        if not song:
            return
        song.status = "processing"
        db.commit()
        provider = _resolve_provider_for(db, song, "region_edit")
        if provider is None:
            song.status = "failed"
            song.moderation_note = "当前引擎不支持「局部改词」"
            db.commit()
            return
        created = await provider.region_edit(
            song_id=source_ref,
            lyrics=lyrics,
            edit_start=edit_start,
            edit_end=edit_end,
        )
        result = await _poll_task(provider.get, created.external_id)
        _write_back(song, result)
        await _finalize_song(song, db, result)
        db.commit()
    except Exception as e:  # 失败不冒泡（后台任务），落 failed 供前端展示
        log.warning("region-edit 失败（song_id=%s）: %s", song_id, e)
        if song is not None:
            song.status = "failed"
            db.commit()
    finally:
        _settle_wallet_track(db, song)
        db.close()


async def _process_instrumental(
    song_id: int, prompt: str, model: str | None, n: int | None
) -> None:
    db = SessionLocal()
    song = None
    try:
        song = db.get(Song, song_id)
        if not song:
            return
        song.status = "processing"
        db.commit()
        provider = _resolve_provider_for(db, song, "instrumental")
        if provider is None:
            song.status = "failed"
            song.moderation_note = "当前引擎不支持「纯器乐」"
            db.commit()
            return
        created = await provider.instrumental(prompt=prompt, model=model, n=n)
        song.external_id = created.external_id
        db.commit()
        # ⚠️ instrumental 必须用**独立**轮询端点，复用 /song/query 会查不到任务
        result = await _poll_task(provider.get_instrumental, created.external_id)
        _write_back(song, result)
        await _finalize_song(song, db, result)
        db.commit()
    except Exception as e:
        log.warning("instrumental 失败（song_id=%s）: %s", song_id, e)
        if song is not None:
            song.status = "failed"
            db.commit()
    finally:
        _settle_wallet_track(db, song)
        db.close()


async def _process_easy(
    song_id: int,
    prompt: str,
    styles: list[str] | None,
    model: str | None,
    n: int | None,
    vocal_id: str | None = None,
) -> None:
    db = SessionLocal()
    song = None
    try:
        song = db.get(Song, song_id)
        if not song:
            return
        song.status = "processing"
        db.commit()
        provider = _resolve_provider_for(db, song, "easy_generate")
        if provider is None:
            song.status = "failed"
            song.moderation_note = "当前引擎不支持「灵感创作」"
            db.commit()
            return
        created = await provider.easy_generate(
            prompt=prompt, styles=styles, model=model, n=n, vocal_id=vocal_id
        )
        song.external_id = created.external_id
        db.commit()
        result = await _poll_task(provider.get, created.external_id)
        _write_back(song, result)
        await _finalize_song(song, db, result)
        db.commit()
    except Exception as e:
        log.warning("easy-generate 失败（song_id=%s）: %s", song_id, e)
        if song is not None:
            song.status = "failed"
            db.commit()
    finally:
        _settle_wallet_track(db, song)
        db.close()


@router.post("/{song_id}/crop", response_model=GenerateResp)
async def crop_song(
    song_id: int,
    body: CropReq,
    bg: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    song = _owned_completed(song_id, db, user)
    _require_capability("crop")  # 阶段 0 止血：Mureka 无 crop → 501（原为后台 500）
    clip_id = song.custom_id or song.external_id
    bg.add_task(
        _process_post,
        song.id,
        "crop",
        clip_id=clip_id,
        start_time=body.start_time,
        end_time=body.end_time,
    )
    return GenerateResp(song_id=song.id, status="processing")


@router.post("/{song_id}/speed", response_model=GenerateResp)
async def speed_song(
    song_id: int,
    body: SpeedReq,
    bg: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    song = _owned_completed(song_id, db, user)
    _require_capability("speed")  # 阶段 0 止血：Mureka 无 speed → 501（原为后台 500）
    clip_id = song.custom_id or song.external_id
    bg.add_task(_process_post, song.id, "speed", clip_id=clip_id, speed=body.speed)
    return GenerateResp(song_id=song.id, status="processing")


@router.post("/{song_id}/aligned-lyrics", response_model=GenerateResp)
async def aligned_lyrics_song(
    song_id: int,
    body: AlignedLyricsReq,
    bg: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    song = _owned_completed(song_id, db, user)
    # 阶段 0 止血：Mureka 在 generate 直接回传 lyrics_sections，无独立对齐端点 → 501
    _require_capability("aligned_lyrics")
    suno_id = song.custom_id or song.external_id
    bg.add_task(_process_post, song.id, "aligned_lyrics", lyrics=body.lyrics, suno_id=suno_id)
    return GenerateResp(song_id=song.id, status="processing")


@router.post("/{song_id}/whole-song", response_model=GenerateResp)
async def whole_song_endpoint(
    song_id: int,
    bg: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    song = _owned_completed(song_id, db, user)
    _require_capability("whole_song")  # 阶段 0 止血：Mureka 无 whole_song → 501（原为后台 500）
    clip_id = song.custom_id or song.external_id
    bg.add_task(_process_post, song.id, "whole_song", clip_id=clip_id)
    return GenerateResp(song_id=song.id, status="processing")


@router.get("", response_model=list[SongOut])
def list_songs(
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    songs = (
        db.query(Song)
        .filter(Song.user_id == user.id)
        .order_by(Song.id.desc())
        .limit(limit)
        .offset(offset)
        .all()
    )
    return [_to_out(s, user) for s in songs]


@router.get("/me/billing")
def my_billing(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """사용자 본인 사용량 원장 + 단위경제 요약(GAP-005).

    Req6 에서 관리자용(config_dashboard/stats)으로만 집계했던
    cost_cny/price_cny/margin_cny 를 사용자도 **자기 곡 범위**에서 확인할 수 있게 한다.
    (공개 SongOut 에는 미노출 — 타인 곡 경제정보 유출 방지)
    """
    songs = db.query(Song).filter(Song.user_id == user.id).all()
    total_cost = sum(float(s.cost_cny or 0) for s in songs)
    total_price = sum(float(s.price_cny or 0) for s in songs)
    total_margin = round(total_price - total_cost, 4)
    daily_used = daily_generated_count(db, user.id)
    return {
        "tier_key": resolve_limits(db, user.tier_key)["tier_key"],
        "total_songs": len(songs),
        "total_cost_cny": round(total_cost, 4),
        "total_revenue_cny": round(total_price, 4),
        "total_margin_cny": total_margin,
        "daily_quota_used": daily_used,
        "daily_quota_limit": resolve_limits(db, user.tier_key)["daily_quota"],
        "recent": [
            {
                "id": s.id,
                "title": s.title,
                "status": s.status,
                "cost_cny": float(s.cost_cny or 0),
                "price_cny": float(s.price_cny or 0),
                "margin_cny": float(s.margin_cny or 0),
                "tier_key": s.tier_key,
                "created_at": s.created_at.isoformat() if s.created_at else None,
            }
            for s in sorted(
                songs, key=lambda x: (x.created_at is None, x.created_at or ""), reverse=True
            )[:20]
        ],
    }


@router.post("/{song_id}/play", response_model=SongOut)
def play_song(
    song_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """播放计数 +1（供前端播放时调用）。自己或已公开的歌均可计数。"""
    rate_limit_play(request.client.host if request.client else "unknown")
    song = db.get(Song, song_id)
    if not song or (song.user_id != user.id and not song.is_public):
        raise HTTPException(status_code=404, detail="歌曲不存在")
    song.play_count = (song.play_count or 0) + 1
    db.commit()
    db.refresh(song)
    return _to_out(song, user, include_sync=True)


@router.get("/{song_id}/download")
def download_song(
    song_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """订阅者下载完整音频；未订阅 403。

    S3 公开 URL 直接重定向无法设置 Content-Disposition（浏览器会「播放」而非「下载」），
    故后端取回临时文件后以 attachment 下发（代价：占用本机带宽，MVP 可接受）。
    ⚠ 这里给的是**完整音频**；未订阅者在 `_to_out` 侧连试听片段以外的链接都拿不到。
    """
    from fastapi.responses import FileResponse

    from app.storage.s3 import download_to_temp

    song = db.get(Song, song_id)
    if not song or (song.user_id != user.id and not song.is_public):
        raise HTTPException(status_code=404, detail="歌曲不存在")
    if not can_download(user):
        raise HTTPException(status_code=403, detail="订阅后可下载完整音频")
    if not song.audio_url:
        raise HTTPException(status_code=404, detail="音频不可用")
    try:
        local = download_to_temp(song.audio_url)
    except Exception as e:
        raise HTTPException(status_code=502, detail="音频下载失败") from e
    filename = f"{(song.title or '').strip() or 'song'}.mp3"
    return FileResponse(local, media_type="audio/mpeg", filename=filename)


@router.get("/{song_id}/preview")
def preview_audio(
    song_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """未订阅者的试听片段（前 N 秒）——对象存储未配置时的按需生成回退。

    · 命中本地缓存直接返回；未命中则下载完整音频 → ffmpeg 裁剪 → 入缓存 → 返回。
    · [SEC] 어떤 경우에도 완곡을 스트리밍하지 않는다(길이 = preview_seconds 로 고정).
    · [SEC] 소유자 또는 공개 곡만 접근 가능(미소유·비공개 → 404, 존재 여부 노출 금지).
    · 생성 실패 시 404(fail-closed) — 완곡으로 폴백하지 않는다.
    · ⚠ 인증이 필요하므로 <audio src> 로 직접 걸 수 없다 → 프런트는 fetch+blob 으로 재생.
    """
    import logging
    import os

    from fastapi.responses import FileResponse

    from app import postprocess
    from app.storage.s3 import download_to_temp

    rate_limit_preview(str(user.id))
    song = db.get(Song, song_id)
    if not song or (song.user_id != user.id and not song.is_public):
        raise HTTPException(status_code=404, detail="试听片段不可用")
    if not song.audio_url:
        raise HTTPException(status_code=404, detail="试听片段不可用")

    cached = _preview_cache_path(song_id)
    if os.path.exists(cached):
        return FileResponse(cached, media_type="audio/mpeg", filename=f"{song_id}_preview.mp3")

    try:
        # [REL] 사용자 대면 경로: 업스트림이 느리면 요청이 무한정 붙잡히므로 전체 상한을 건다
        #       (httpx timeout 은 읽기 간 유휴 기준 → "천천히 꾸준히" 흐르는 응답은 못 막음).
        local_in = download_to_temp(
            song.audio_url,
            timeout_total=settings.preview_fetch_timeout,
            max_bytes=int(settings.preview_fetch_max_mb * 1024 * 1024),
        )
        local_out = local_in + ".preview.mp3"
        postprocess.trim(local_in, local_out, preview_seconds())
        _store_preview_local(song_id, local_out)
    except Exception as e:  # fail-closed: 완곡 대신 404
        logging.getLogger(__name__).warning("试听片段按需生成失败（song_id=%s）：%s", song_id, e)
        raise HTTPException(status_code=404, detail="试听片段生成失败") from e
    return FileResponse(cached, media_type="audio/mpeg", filename=f"{song_id}_preview.mp3")


# ═══════════════════════════════════════════════════════════════════════════════
# AI 一键优化助手（2026-09-10 新增）
#
# 官方事实（已核对 platform.mureka.cn/docs）：
#   · POST /v1/lyrics/generate —— Mureka **唯一**的 AI 辅助端点：{prompt} → {title, lyrics}
#   · 官方**没有**风格/提示词优化端点 → 风格规范化由本地确定性规则实现
#     （app/services/prompt_assist.py：不调 LLM，故无幻觉、零成本、可复现）
#   · /v1/song/generate 的 prompt 是**自由文本** ≤1024，官方示例
#     "r&b, slow, passionate, male vocal" ⇒ 风格**越结构化越好**，不是越长越好
# ═══════════════════════════════════════════════════════════════════════════════


class AssistLyricsReq(BaseModel):
    prompt: str
    engine: Optional[str] = "mureka"


class AssistLyricsResp(BaseModel):
    title: str = ""
    lyrics: str = ""
    engine: str = ""


class AssistTextReq(BaseModel):
    text: str
    force_gospel: bool = False
    mode: str = "style"  # style=风格标签整理 | prompt=主题/情绪扩写（两按钮各自语义）
    lyrics: str | None = None  # 可选：供「从歌词生成风格」参考（隐式自动优化的显式化）


class AssistTextResp(BaseModel):
    optimized: str = ""
    notes: list[str] = []


@router.post("/assist/lyrics", response_model=AssistLyricsResp)
async def assist_generate_lyrics(
    body: AssistLyricsReq,
    request: Request,
    user: User = Depends(get_current_user),
):
    """AI 生成歌词（含标题）—— 两级链路：

    ① 文案 LLM（ASSIST_LLM_* 配置，OpenAI 兼容协议；默认腾讯混元免费档，
       与音乐引擎解耦、无音乐额度依赖）—— 已配置则优先；
    ② 降级：当前音乐引擎的官方 AI 歌词端点（原有链路，原样保留）。
    两级都不可用 → 4xx/502 中性文案，绝不 5xx 悬空。
    """
    # [SEC] 2026-09-13 审计（MED）：上游文案 LLM 按量计费，无限制可被脚本烧 token → 限流。
    rate_limit_assist(request.client.host if request.client else "unknown")
    prompt = (body.prompt or "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="请先输入主题或描述，再自动生成歌词")
    if len(prompt) > 500:
        prompt = prompt[:500]

    # ① 文案 LLM 优先（输出：首行「标题：XX」+ [Verse] 等结构标记 + 中文歌词）
    if assist_llm.llm_enabled():
        try:
            # max_tokens=2000：推理模型 reasoning 预算 + 完整歌词（结构标记+正文）约需 1500+
            out = await assist_llm.chat(
                assist_llm.ASSIST_LYRICS_SYSTEM, prompt, max_tokens=2000, temperature=0.8
            )
            title, lyrics = assist_llm.split_lyrics_output(out)
            if lyrics:
                return AssistLyricsResp(title=title[:60], lyrics=lyrics[:4500], engine="llm")
            log.warning("assist llm returned empty lyrics, fallback to engine")
        except LLMError as e:
            # 上游错误细节只进服务端日志（可能含第三方品牌词），用户走降级链路
            log.warning("assist llm lyrics failed, fallback to engine: %s", e)

    # ② 降级：音乐引擎官方 AI 歌词端点（原有链路）
    provider = get_orchestrator(body.engine or "mureka")
    gen = getattr(provider, "generate_lyrics", None)
    if gen is None:
        raise HTTPException(
            status_code=400,
            # [品牌规范] 第三方引擎名（mureka/suno 等）不得出现在用户可见文案里
            detail="当前引擎不支持自动生成歌词，请稍后再试或联系管理员",
        )
    try:
        data = await gen(prompt)
    except Exception as e:
        # 上游错误原文只在服务端日志保留（可能含第三方品牌名），对用户输出中性文案
        log.warning("lyrics generate failed: %s", e)
        raise HTTPException(status_code=502, detail="自动生成歌词失败，请稍后再试") from e
    return AssistLyricsResp(
        title=data.get("title") or "",
        lyrics=data.get("lyrics") or "",
        engine=body.engine or "mureka",
    )


@router.post("/assist/style", response_model=AssistTextResp)
async def assist_optimize_style(
    body: AssistTextReq,
    user: User = Depends(get_current_user),
):
    """一键优化（mode 区分两个按钮）：AI 优先，未配置/失败自动降级，按钮永不因 AI 中断。

    mode="style"  → 风格标签整理（AI 输出中文标签串；降级 = 本地确定性规则）
    mode="prompt" → 主题/情绪扩写成歌意描述（AI 输出中文描述；降级 = 原样返回）

    🔒 安全加固（2026-09-13 审计，CWE-306 缺失认证）：原为**匿名**端点，
    可被脚本高频调用烧掉按量计费的上游 LLM token（成本放大）。现要求登录 + 生成级限流。
    """
    rate_limit_generate(str(user.id))
    text = (body.text or "").strip()
    use_prompt_mode = body.mode == "prompt"
    lyrics = (body.lyrics or "").strip()
    if text and assist_llm.llm_enabled():
        system = (
            assist_llm.ASSIST_PROMPT_SYSTEM if use_prompt_mode else assist_llm.ASSIST_STYLE_SYSTEM
        )
        # 风格模式且给出歌词时，把歌词注入用户上下文，让 LLM 基于歌词情绪/主题提炼风格
        # （行业「留空由模型隐式推断风格」的显式化、可编辑版本）。
        user_msg = text
        if lyrics and not use_prompt_mode:
            user_msg = f"【风格】{text}\n【歌词】{lyrics}" if text else f"【歌词】{lyrics}"
        try:
            # max_tokens=800：hy3 为推理模型（先 reasoning_content 后 content，实测推理消耗
            # 200-400 token）——预算太小会 finish=length 且 content 为空 → 触发降级。
            out = await assist_llm.chat(system, user_msg, max_tokens=800, temperature=0.7)
            return AssistTextResp(optimized=out[:2000], notes=["已按 AI 优化"])
        except LLMError as e:
            # 上游细节只进日志；按钮体验不因 AI 故障中断
            log.warning("assist llm (mode=%s) failed, fallback: %s", body.mode, e)
    if use_prompt_mode:
        # 主题扩写没有安全的本地等价物（旧本地规则会把散文压碎成标签）→ 原样返回
        return AssistTextResp(optimized=body.text or "", notes=["AI 暂未启用，主题描述保持原样"])
    optimized, notes = optimize_style(
        body.text or "", lyrics=lyrics, force_gospel=bool(body.force_gospel)
    )
    return AssistTextResp(optimized=optimized, notes=notes)


@router.post("/assist/lyrics/format", response_model=AssistTextResp)
def assist_format_lyrics(
    body: AssistTextReq,
    user: User = Depends(get_current_user),
):
    """歌词一键优化：结构标签对齐 + 去空行 + 超限截断（本地规则，不改写语义；刻意不走 AI，避免改写用户原词）。

    🔒 安全加固（2026-09-13 审计，CWE-306）：原为匿名端点，现要求登录 + 限流。
    """
    rate_limit_generate(str(user.id))
    optimized, notes = optimize_lyrics(body.text or "")
    return AssistTextResp(optimized=optimized, notes=notes)
