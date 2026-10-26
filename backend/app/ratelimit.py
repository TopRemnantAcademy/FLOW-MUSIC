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


# ── GAP-002 이메일 인증 / 비밀번호 재설정 (IP 레벨 슬라이딩 윈도우) ──
# 목적: verify/forgot/reset 은 **익명** 접근이 가능한 엔드포인트다.
#   · /verify·/reset-password 는 token 추측(무차별 대입) 표면,
#   · /forgot-password 는 메일 폭탄(상대방에게 무한 발송) 표면.
# 계정 레벨 잠금(locked_until)과 **상보적**으로 IP 레벨도 막는다.


def rate_limit_verify(ip: str) -> None:
    _sliding_window(
        f"verify:{ip}",
        60.0,
        settings.verify_rate_limit_per_minute,
        "인증 시도가 너무 잦습니다. 잠시 후 다시 시도하세요",
    )


def rate_limit_resend_verification(ip: str) -> None:
    _sliding_window(
        f"resend:{ip}",
        60.0,
        settings.resend_verification_rate_limit_per_minute,
        "인증 메일 재발송이 너무 잦습니다. 잠시 후 다시 시도하세요",
    )


def rate_limit_forgot_password(ip: str) -> None:
    _sliding_window(
        f"forgot:{ip}",
        60.0,
        settings.forgot_password_rate_limit_per_minute,
        "요청이 너무 잦습니다. 잠시 후 다시 시도하세요",
    )


def rate_limit_reset_password(ip: str) -> None:
    _sliding_window(
        f"reset:{ip}",
        60.0,
        settings.reset_password_rate_limit_per_minute,
        "요청이 너무 잦습니다. 잠시 후 다시 시도하세요",
    )


def rate_limit_export(user_key: str) -> None:
    """GAP-028 批量导出限流（2026-10-12）：一次导出要下载 N 首完整音频并打包，
    带宽/CPU/磁盘都是 O(N)。无限流时单用户脚本循环调用即可把本机打满
    （CWE-400），而它比单曲下载的放大倍数高两个数量级（100 首/次 vs 1 首/次）。"""
    _sliding_window(
        f"export:{user_key}",
        60.0,
        settings.export_rate_limit_per_minute,
        "导出请求过于频繁，请稍后再试",
    )


def rate_limit_notification_subscribe(user_key: str) -> None:
    """GAP-021 订阅写入限流。订阅文件是**整读整写**的（见 notification_store），
    高频写入会把 O(n) 的读改写放大成放大面；同时它也是"任意 endpoint 写入"面，
    需要防脚本刷。"""
    _sliding_window(
        f"notif-sub:{user_key}",
        60.0,
        settings.notifications_subscribe_rate_limit_per_minute,
        "订阅操作过于频繁，请稍后再试",
    )


def rate_limit_notification_test(user_key: str) -> None:
    """GAP-021 自检限流。每次自检会**真的发信 + 发推**：邮件走 SES 配额，
    推送会打到第三方网关（FCM/Mozilla），无限流即可被用来刷爆发信域名信誉
    （后果是正常验证邮件也被判垃圾），属于必须有的护栏。"""
    _sliding_window(
        f"notif-test:{user_key}",
        60.0,
        settings.notifications_test_rate_limit_per_minute,
        "自检请求过于频繁，请稍后再试",
    )


def rate_limit_developer(user_key: str) -> None:
    """GAP-022 开发者配置写入限流（token 创建 / webhook 注册）。

    为什么需要：注册 webhook 会让我们**主动向外部地址发请求**。
    不限流的话，一个账号就能把本服务当 SSRF 探测跳板 ——
    每次注册一个 URL，再等一次投递，就能从"请求有没有发出去/多久超时"里
    读出目标内网服务的存活情况（即使返回 400 被我们挡了，时序本身也是信息）。
    """
    _sliding_window(
        f"developer:{user_key}",
        60.0,
        settings.developer_rate_limit_per_minute,
        "开发者配置操作过于频繁，请稍后再试",
    )
