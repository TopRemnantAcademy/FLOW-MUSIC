"""出站 Webhook 投递：HMAC 签名 + SSRF 复验 + 指数退避重试 + 失败落库。

「投递」在这里有两层含义，别混：
  · `sign_payload` / `verify_signature` —— 纯粹的密码学函数，双方都要能用
    （对方用 verify_signature 验我们的签名；我们用同一函数给自己的测试验签）。
  · `deliver` —— 真正发出 HTTP 请求的链路。

签名头设计（`X-Flow-Signature`）：
    格式 `t=<unix秒>,v1=<hex(hmac_sha256(secret, "t.body"))>`
    - 把时间戳**签进内容**（而不是只当 header 传），是为了让对方能拒绝重放：
      抓包者无法把 5 分钟前的那条请求改个时间戳重发（改了签名就不对）。
    - `v1=` 前缀留版本位：将来换算法（如 sha512）可并存过渡，不必让双方同时升级。
    - 签名原文用 `t.body` 拼接而不是纯 body：否则攻击者能把时间戳和签名拆开重组。

重试策略：最多 5 次尝试（首次 + 4 次重试），退避 1s/2s/4s/8s（指数），
只在「网络错误 / 5xx / 429」时重试；**4xx 不重试**（对方明确说这请求有问题，
再发 4 次是打扰而非韧性 —— 4xx 是配置错误，得让人去改）。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from datetime import datetime, timezone
from typing import Optional

import httpx
from sqlalchemy.orm import Session

from app.models import WebhookDelivery, WebhookEndpoint
from app.security.ssrf import SSRFGuard

log = logging.getLogger(__name__)

SIGNATURE_HEADER = "X-Flow-Signature"
DELIVERY_HEADER = "X-Flow-Delivery"  # 投递 id，给对方做幂等去重
EVENT_HEADER = "X-Flow-Event"

MAX_ATTEMPTS = 5  # 首次 + 4 次重试
BASE_BACKOFF_SECONDS = 1.0
REQUEST_TIMEOUT_SECONDS = 10.0

# 429 与所有 5xx 视为"对方暂时不行"，值得重试。
_RETRYABLE_STATUS = frozenset({429})


def backoff_seconds(attempt: int) -> float:
    """第 `attempt` 次尝试**之前**应等待的秒数（attempt 从 1 计）。

    1, 2, 4, 8 秒。不做 jitter：单机同步投递、重试次数固定为 4，
    thundering herd 不是本场景的风险，而 jitter 会让"秒级断言"变得不可能
    （测试就得改成范围断言，反而降低回归检测力）。
    """
    return BASE_BACKOFF_SECONDS * (2 ** max(0, attempt - 1))


def sign_payload(secret: str, body: bytes, timestamp: Optional[int] = None) -> str:
    """生成 `X-Flow-Signature` 的值。`timestamp` 可注入以便测试确定性。"""
    ts = int(timestamp if timestamp is not None else time.time())
    signed = f"{ts}.".encode("ascii") + body
    digest = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    return f"t={ts},v1={digest}"


def verify_signature(
    secret: str,
    body: bytes,
    header: str,
    *,
    tolerance_seconds: int = 300,
    now: Optional[int] = None,
) -> bool:
    """校验 `X-Flow-Signature`。这是**给对端用的参考实现**（也用于本仓测试）。

    用 `hmac.compare_digest` 而非 `==`：逐字节比较的耗时差可被用来逐位爆破签名
    （时序侧信道）—— 见 routers/share.py 里同一处取舍。

    tolerance 默认 5 分钟：足以吸收对端与我们的时钟漂移，同时把重放窗口压到最小。
    """
    if not header or not secret:
        return False
    parts = dict(
        p.split("=", 1) for p in header.split(",") if "=" in p
    )
    ts_raw, sig = parts.get("t"), parts.get("v1")
    if not ts_raw or not sig:
        return False
    try:
        ts = int(ts_raw)
    except ValueError:
        return False
    current = int(now if now is not None else time.time())
    if abs(current - ts) > tolerance_seconds:
        return False
    expected = hmac.new(
        secret.encode("utf-8"), f"{ts}.".encode("ascii") + body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(sig, expected)


def _is_retryable_status(status: int) -> bool:
    return status >= 500 or status in _RETRYABLE_STATUS


def deliver(
    endpoint: WebhookEndpoint,
    event: str,
    payload: dict,
    *,
    db: Optional[Session] = None,
    delivery: Optional[WebhookDelivery] = None,
    sleep=time.sleep,
    client_factory=None,
    max_attempts: int = MAX_ATTEMPTS,
) -> WebhookDelivery:
    """投递一条事件，返回落库的投递记录（成功/失败都返回，不抛异常）。

    为什么**不抛**：调用点是后台任务（`_finalize_song`），一个坏掉的 webhook
    不该把"生成成功"变成"生成失败"。失败信息全部写进 `WebhookDelivery` 行，
    由开发者自己查 —— 可观测性靠表，不靠异常冒泡。

    `sleep` / `client_factory` 可注入：测试要断言"退了多久、发了几次"，
    真等 1+2+4+8=15 秒的测试套件没人会跑第二次。
    """
    if client_factory is None:
        client_factory = httpx.Client

    body = json.dumps(payload, separators=(",", ":"), sort_keys=True, default=str).encode("utf-8")

    if delivery is None:
        delivery = WebhookDelivery(
            endpoint_id=endpoint.id,
            event=event,
            payload=body.decode("utf-8"),
        )
        if db is not None:
            db.add(delivery)
            db.commit()
            db.refresh(delivery)

    headers = {
        "Content-Type": "application/json",
        "User-Agent": "FlowMusic-Webhook/1.0",
        EVENT_HEADER: event,
        DELIVERY_HEADER: str(delivery.id),
    }

    for attempt in range(1, max_attempts + 1):
        delivery.attempts = attempt
        # ⚠ 每一跳都重新校验 + 固定 IP（DNS-rebinding / TOCTOU，CWE-918）。
        # 注册期校验过的 URL 不代表现在安全：DNS 记录可以在注册之后被改成 169.254.169.254。
        try:
            pinned, host = SSRFGuard().pin(endpoint.url)
        except Exception as e:  # 解析失败 / 解析到内网 → 直接判失败，不重试（重试也不会变）
            delivery.error = f"SSRF 校验失败: {e}"[:500]
            delivery.status_code = None
            if db is not None:
                db.commit()
            return delivery

        headers["Host"] = host
        headers[SIGNATURE_HEADER] = sign_payload(endpoint.secret, body)
        try:
            with client_factory(timeout=REQUEST_TIMEOUT_SECONDS) as client:
                resp = client.post(
                    pinned,
                    content=body,
                    headers=headers,
                    extensions={"sni_hostname": host},
                )
            delivery.status_code = resp.status_code
            if 200 <= resp.status_code < 300:
                delivery.delivered_at = datetime.now(timezone.utc)
                delivery.error = ""
                if db is not None:
                    db.commit()
                return delivery
            delivery.error = f"HTTP {resp.status_code}: {resp.text[:200]}"[:500]
            if not _is_retryable_status(resp.status_code):
                break  # 4xx：对方说请求有问题，重试是打扰
        except Exception as e:
            delivery.status_code = None
            delivery.error = f"{type(e).__name__}: {e}"[:500]

        if attempt < max_attempts:
            sleep(backoff_seconds(attempt))

    if db is not None:
        db.commit()
    return delivery


def dispatch_event(
    db: Session,
    user_id: int,
    event: str,
    payload: dict,
    *,
    sleep=time.sleep,
    client_factory=None,
    max_attempts: int = MAX_ATTEMPTS,
) -> list[WebhookDelivery]:
    """把事件投给该用户所有匹配的活跃端点。

    `events` 为空串 = 订阅全部事件（比"每条都得勾"更符合直觉：新建端点的人
    通常还没想好要哪些）。多端点之间逐个投递、互不影响 —— 一个端点的异常
    不该让另一个端点收不到（所以 deliver 内部吞掉异常）。
    """
    endpoints = (
        db.query(WebhookEndpoint)
        .filter(WebhookEndpoint.user_id == user_id, WebhookEndpoint.active.is_(True))
        .all()
    )
    out: list[WebhookDelivery] = []
    for ep in endpoints:
        subscribed = [s.strip() for s in (ep.events or "").split(",") if s.strip()]
        if subscribed and event not in subscribed:
            continue
        try:
            out.append(
                deliver(
                    ep,
                    event,
                    payload,
                    db=db,
                    sleep=sleep,
                    client_factory=client_factory,
                    max_attempts=max_attempts,
                )
            )
        except Exception:  # deliver 已不抛，这里是"防未来改坏"的兜底
            log.exception("[webhook] 投递意外异常 endpoint_id=%s event=%s", ep.id, event)
    return out
