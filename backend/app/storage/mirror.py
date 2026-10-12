"""CDN 回源（mirror）：把上游易失音频链接搬进自有桶，落 songs.audio_cdn_url。

**唯一实现**：「回源 + SSRF 守卫 + 指数退避 + 幂等落库」在这里只写一份，供
  ① 主播放链路按需回填（SongOut 下发前的 write-behind 任务）
  ② FLOW sync-flow（APP 歌单必须拿到稳定链接）
  ③ 历史数据回填脚本（scripts/backfill_cdn.py）
  ④ 归档脚本（scripts/archive_cdn.py）
共用。三处各写一份的结果必然是「键不一致 / 重试策略不一致 / 降级行为不一致」。

优雅降级是硬指标：**任何失败都不抛给调用方**，返回 None，调用方继续用上游
audio_url（prod 未配 storage_* 时整条路径就是这样，零回归、零 500）。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import tempfile
import time

from app.config import settings
from app.security.ssrf import ssrf_guard
from app.storage import s3 as storage
from app.storage.cdn import default_storage_class, media_type_for, song_key

log = logging.getLogger(__name__)

#: 回源下载的整体上墙（秒）。上游卡住时不允许把后台任务/请求线程无限期占住。
FETCH_TIMEOUT_SECONDS = float(getattr(settings, "preview_fetch_timeout", 25.0) or 25.0)
#: 单曲音频体积上限（MB）——异常大文件（或恶意 URL 指向大文件）不落桶，避免桶被灌爆。
FETCH_MAX_MB = float(getattr(settings, "preview_fetch_max_mb", 80) or 80)
#: 指数退避重试次数与基数。3 次 / 0.5s → 0.5s、1s、2s（累计 3.5s，够扛一次瞬时抖动）。
MAX_ATTEMPTS = 3
BACKOFF_BASE = 0.5


def can_mirror(source_url: str | None) -> bool:
    """能否对该链接做回源（未配存储 / 无源链接 → False，调用方直接降级）。"""
    return bool(storage.is_configured() and source_url)


async def mirror_song_audio(song, db, *, source_url: str | None = None) -> str | None:
    """确保 `song.audio_cdn_url` 可用并返回它；失败/不可用返回 None（调用方降级）。

    幂等：已有 audio_cdn_url 直接返回，不重复下载上传。
    副作用：成功时写 song.audio_cdn_url + 提交（调用方传入的 session 由调用方掌管生命周期）。

    ⚠ 调用方必须是「允许阻塞 IO 的上下文」：本函数会去网络取音频。请求线程里请用
      `asyncio.create_task`（asyncio.to_thread 包住同步上传）触发，不要 await 等它。
    """
    if getattr(song, "audio_cdn_url", None):
        return song.audio_cdn_url
    src = source_url or getattr(song, "audio_url", None)
    if not can_mirror(src):
        # 未配存储 = 预期内的 MVP 降级，debug 级别；不 warn（避免 prod 未配时刷屏）。
        log.debug(
            "CDN mirror skipped for song %s (storage_configured=%s, has_source=%s)",
            getattr(song, "id", "?"),
            storage.is_configured(),
            bool(src),
        )
        return None
    try:
        ssrf_guard.guard(src)  # [SEC] 上游链接是用户影响值 → 取回前先挡内网/元数据地址
    except Exception as e:
        log.warning("CDN mirror refused by SSRF guard (song_id=%s): %s", getattr(song, "id", "?"), e)
        return None

    key = song_key(getattr(song, "user_id", None), getattr(song, "id", 0), src)
    storage_class = default_storage_class()
    url = await asyncio.to_thread(
        _fetch_and_upload, src, key, media_type_for(src), storage_class, getattr(song, "id", "?")
    )
    if not url:
        return None
    try:
        song.audio_cdn_url = url
        db.commit()
    except Exception as e:  # 落库失败不能让调用方 500（链接仍可用，只是下次会重做）
        with contextlib.suppress(Exception):
            db.rollback()
        log.warning("CDN mirror persist failed (song_id=%s): %s", getattr(song, "id", "?"), e)
    return url


def _fetch_and_upload(
    src: str, key: str, content_type: str, storage_class: str, song_ref
) -> str | None:
    """同步下载 → 上传桶（在 to_thread 里跑）。带指数退避；彻底失败返回 None。"""
    max_bytes = int(FETCH_MAX_MB * 1024 * 1024)
    last_exc: Exception | None = None
    for attempt in range(MAX_ATTEMPTS):
        if attempt:
            # 退避必须真的睡（旧实现把 sleep 写在 if 分支里，重试等于连打三次）。
            time.sleep(BACKOFF_BASE * (2 ** (attempt - 1)))
        try:
            local = _download_with_cap(src, max_bytes)
            try:
                return storage.upload_file(
                    local, key, content_type=content_type, storage_class=storage_class or None
                )
            finally:
                with contextlib.suppress(OSError):
                    os.unlink(local)
        except Exception as e:  # 网络抖动 / 5xx / 体积超限 → 退避重试
            last_exc = e
            log.warning(
                "CDN mirror attempt %s/%s failed (song=%s): %s",
                attempt + 1,
                MAX_ATTEMPTS,
                song_ref,
                e,
            )
    log.warning("CDN mirror gave up for song %s: %s", song_ref, last_exc)
    return None


def _download_with_cap(url: str, max_bytes: int) -> str:
    """下载到临时文件，超上限即中断（不落桶）。

    为什么不用 storage.download_to_temp：那里已有 SSRF 逐跳校验 + 全墙，但**只在
    超限时抛异常前把已写入的数据留给临时文件清理**；此处要一个明确的「上限即停」语义，
    且复用它会让两个模块互相 import。故这里只做「流式 + 计数 + 上限」这一件事，
    SSRF 逐跳校验由 guarded_get_stream 提供（同一个守卫对象）。
    """
    suffix = os.path.splitext(url.split("?")[0])[1] or ".mp3"
    fd, path = tempfile.mkstemp(suffix=suffix)
    written = 0
    try:
        with os.fdopen(fd, "wb") as f:
            with ssrf_guard.guarded_get_stream(url, timeout=30, max_hops=5) as resp:
                resp.raise_for_status()
                for chunk in resp.iter_bytes(65536):
                    written += len(chunk)
                    if written > max_bytes:
                        raise ValueError(f"音频超过上限 {max_bytes} bytes: {url}")
                    f.write(chunk)
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(path)
        raise
    return path


async def mirror_in_background(song_id: int, db) -> None:
    """请求路径用的 write-behind 回填：本轮响应**不等**它，下一次请求即命中 CDN。

    捕获一切异常：这是 fire-and-forget 任务，抛出去只会变成 "Task exception was
    never retrieved" 噪音日志，且对用户没有任何补救意义。
    """
    try:
        from app.models import Song  # 延迟 import，避免 storage → models 循环

        song = db.get(Song, song_id)
        if song is None or song.audio_cdn_url:
            return
        await mirror_song_audio(song, db)
    except Exception as e:
        log.debug("background CDN mirror skipped (song_id=%s): %s", song_id, e)
    finally:
        with contextlib.suppress(Exception):
            db.close()
