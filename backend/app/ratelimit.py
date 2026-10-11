"""简易内存限流（MVP）。

生产建议用 Redis + 滑动窗口（多实例共享计数）。这里用进程内 deque，
足够单实例 MVP 与 mock 测试（测试时 settings.enable_rate_limit=False 关闭）。
"""

import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request

from app.config import settings

_hits: dict[str, deque[float]] = defaultdict(deque)


def _sliding_window(key: str, window: float, limit: int, detail: str) -> None:
    """滑动窗口限流：key 在 window 秒内的请求数超过 limit 则抛 429。"""
    if not settings.enable_rate_limit:
        return
    now = time.monotonic()
    dq = _hits[key]
    while dq and dq[0] <= now - window:
        dq.popleft()
    if len(dq) >= limit:
        raise HTTPException(status_code=429, detail=detail)
    dq.append(now)


async def rate_limit_sync(request: Request) -> None:
    ip = request.client.host if request.client else "unknown"
    _sliding_window(
        f"sync:{ip}", 60.0, settings.sync_rate_limit_per_minute, "同步过于频繁，请稍后再试"
    )


def rate_limit_generate(user_key: str) -> None:
    _sliding_window(
        f"gen:{user_key}", 60.0, settings.generate_rate_limit_per_minute, "生成过于频繁，请稍后再试"
    )


def rate_limit_login(ip: str) -> None:
    _sliding_window(
        f"login:{ip}", 60.0, settings.login_rate_limit_per_minute, "登录尝试过于频繁，请稍后再试"
    )


def rate_limit_register(ip: str) -> None:
    """注册限流（2026-09-13 新增）。

    注册是**匿名**端点，原本完全无限制：脚本可批量注册账号，每个账号都拿到独立的
    每日免费配额 → 免费额度被放大 N 倍，同时把上游 Mureka 的调用成本打满。
    """
    _sliding_window(
        f"reg:{ip}", 60.0, settings.register_rate_limit_per_minute, "注册过于频繁，请稍后再试"
    )


def rate_limit_play(ip: str) -> None:
    """播放计数限流（2026-09-13 审计，MED）：/api/songs/{id}/play 每次 +1，
    无限流可被单 IP 刷量/滥用，污染 play_count 指标。"""
    _sliding_window(
        f"play:{ip}", 60.0, settings.play_rate_limit_per_minute, "播放请求过于频繁，请稍后再试"
    )


def rate_limit_assist(ip: str) -> None:
    """AI 辅助限流（2026-09-13 审计，MED）：/assist/lyrics 等走按量计费的上游
    LLM，无限制可被脚本高频调用烧掉 token 成本（CWE-913 资源管理）。"""
    _sliding_window(
        f"assist:{ip}",
        60.0,
        settings.assist_rate_limit_per_minute,
        "AI 辅助请求过于频繁，请稍后再试",
    )


def rate_limit_preview(user_key: str) -> None:
    """试听片段生成限流（2026-09-13 审计，MED）：缓存未命中时每次都会跑一次 ffmpeg 裁剪，
    无限流可被单用户高频触发 → 本机 CPU/内存耗尽（CWE-400 资源耗尽）。"""
    _sliding_window(
        f"preview:{user_key}",
        60.0,
        settings.preview_rate_limit_per_minute,
        "试听请求过于频繁，请稍后再试",
    )


def rate_limit_download(user_key: str) -> None:
    """完整下载限流（2026-09-13 审计，MED）：每次都从对象存储取回完整音频再下发，
    无限流可被订阅者脚本高频刷下载 → 带宽/对象存储成本被放大。"""
    _sliding_window(
        f"dl:{user_key}",
        60.0,
        settings.download_rate_limit_per_minute,
        "下载请求过于频繁，请稍后再试",
    )


def rate_limit_vocal_clone(user_key: str) -> None:
    """音色克隆限流（2026-09-13 审计，MED）：上游按调用量计费且占供应商额度，
    单用户高频克隆会烧钱。enforce_generate_limits 是「每日配额」慢速护栏，此处补「每分钟突发」护栏。"""
    _sliding_window(
        f"vclone:{user_key}",
        60.0,
        settings.vocal_clone_rate_limit_per_minute,
        "音色克隆请求过于频繁，请稍后再试",
    )
