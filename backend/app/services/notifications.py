"""通知服务（GAP-021）：模板化消息 + 邮件通道 + Web Push 通道。

本模块只做「一件事怎么发出去」，**不含任何 HTTP 端点**（端点见 routers/notifications.py）。
这样拆分是为了让「发信/发推」可被单测直接调用，不必绕 TestClient 走一遭。

## 三条不变量（每条都对应一次会真出事的失败）

1. **未配置 ≠ 成功。** 没凭证时返回 `DeliveryResult(sent=False, reason=...)`，
   由调用方翻译成 202 Accepted-but-not-sent。绝不返回"已发送"——
   用户会永远等一封不会到的信（这正是 GAP-002 email.py 开头写下的原则，此处延续）。

2. **降级不是丢弃。** Web Push 未配 VAPID（或没装 pywebpush）时，把待推送项登记到
   进程内队列（`pending_push_queue()`），运维可在装好依赖/配好密钥后回放。
   队列有硬上限，避免"降级期的内存增长"成为新的故障源。

3. **推送签名不引新依赖。** VAPID 的两件事——ES256 签名、公钥导出为
   base64url(uncompressed point)——都只用 `cryptography`（**主 requirements 里已有**，
   GAP-002 就在用）。真正发 HTTP 才需要 `pywebpush`，故它保持**可选**：
   没装就是「登记待推送」，装了（`PUSH_HTTP=1` 风格的运行环境）就直接发。
   这条线让本 PR 的依赖面为零增长。

## 为什么邮件是薄封装而不是第二套实现

GAP-002 已把「怎么发信」写清楚了：`app/services/email.py:send_email()`
（boto3 → AWS SES、凭证走 boto3 标准链、失败不吞、凭证绝不进日志）。
通知邮件若另写一条发送路径，就会出现「两处超时、两处错误处理」，
且运维要维护两套凭证。这里只**加模板**，投递仍走那一个 replaceable seam。
"""

from __future__ import annotations

import base64
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from app.config import settings
from app.services import email as email_service

logger = logging.getLogger(__name__)

# ── 通知类型（模板键）。用常量而非散落的字符串字面量，避免"模板名拼错即静默无内容"。
NOTIFY_GENERATION_DONE = "generation_done"
NOTIFY_SHARE = "share"
NOTIFY_SYSTEM = "system"

# 进程内待推送队列上限。降级期会持续累积，无上限即内存泄漏。
PENDING_PUSH_MAX = 500


@dataclass
class DeliveryResult:
    """一次投递的结果。`reason` 是可给运维看的短标签（**不含凭证/正文**）。"""

    sent: bool
    reason: str = ""
    detail: str = ""

    @property
    def accepted(self) -> bool:
        """端点是否应答 202。降级（未配置）与失败都算 accepted ——

        但**理由不同**：降级是"没通道，已登记"，失败是"有通道，这次没成"。
        两者都不该给用户 500：通知是旁路，不该把生成本身拖下水。
        """
        return True


def _utc_ts() -> int:
    return int(time.time())


# ─────────────────────────── 模板层（纯函数，可单测） ───────────────────────────


def render_email(kind: str, ctx: dict[str, Any]) -> tuple[str, str]:
    """(subject, body)。**纯函数**：不查库、不发信 —— 测试直接断言产出。

    未知 kind 抛 ValueError 而不是给一段兜底文案：静默的"通用通知"会让
    "模板名写错"这种 bug 一路发到用户邮箱才被发现。
    """
    base = settings.frontend_base_url
    if kind == NOTIFY_GENERATION_DONE:
        title = ctx.get("title") or "未命名作品"
        song_id = ctx.get("song_id")
        link = f"{base}/songs/{song_id}" if song_id is not None else base
        return (
            "FLOW MUSIC — 作品已生成完成",
            "你的作品已生成完成\n"
            "\n"
            f"《{title}》可以播放了。\n"
            f"{link}\n"
            "\n"
            "本邮件由系统自动发送。\n",
        )
    if kind == NOTIFY_SHARE:
        title = ctx.get("title") or "未命名作品"
        link = ctx.get("link") or base
        sender = ctx.get("sender") or "某位用户"
        return (
            "FLOW MUSIC — 有人分享了作品给你",
            f"{sender} 分享了《{title}》给你\n"
            "\n"
            f"{link}\n"
            "\n"
            "本邮件由系统自动发送。\n",
        )
    if kind == NOTIFY_SYSTEM:
        text = ctx.get("message") or ""
        return (
            ctx.get("subject") or "FLOW MUSIC — 系统公告",
            f"{text}\n"
            "\n"
            f"{base}\n"
            "\n"
            "本邮件由系统自动发送。\n",
        )
    raise ValueError(f"未知通知类型: {kind}")


def render_push_payload(kind: str, ctx: dict[str, Any]) -> dict[str, Any]:
    """Web Push 载荷（JSON，符合 RFC 8030 + 通用 notification 约定）。

    纯**数据**：不含 HTML/脚本，service worker 侧自行渲染 ——
    推送载荷会经过第三方推送网关（FCM / Mozilla autopush），
    塞进可执行内容等于把 XSS 面交给中间人。
    """
    base = settings.frontend_base_url
    if kind == NOTIFY_GENERATION_DONE:
        song_id = ctx.get("song_id")
        return {
            "type": kind,
            "title": "作品已生成完成",
            "body": f"《{ctx.get('title') or '未命名作品'}》可以播放了",
            "url": f"/songs/{song_id}" if song_id is not None else "/",
            "tag": f"gen-{song_id}" if song_id is not None else "gen",
        }
    if kind == NOTIFY_SHARE:
        return {
            "type": kind,
            "title": "有人分享了作品给你",
            "body": f"{ctx.get('sender') or '某位用户'} 分享了《{ctx.get('title') or '未命名作品'}》",
            "url": ctx.get("link") or "/",
            "tag": "share",
        }
    if kind == NOTIFY_SYSTEM:
        return {
            "type": kind,
            "title": ctx.get("subject") or "系统公告",
            "body": ctx.get("message") or "",
            "url": "/",
            "tag": "system",
        }
    raise ValueError(f"未知通知类型: {kind}")


# ─────────────────────────── 邮件通道 ───────────────────────────


def email_configured() -> bool:
    """邮件通道是否可用 —— 直接问既有那道 seam，不在这里复制一遍判断。"""
    return email_service.delivery_configured()


def send_notification_email(kind: str, to: str, ctx: dict[str, Any]) -> DeliveryResult:
    """按模板发一封通知邮件。未配置 SES → 降级（不抛异常）。

    ⚠ 与 GAP-002 的 `ensure_delivery_available()`（fail-closed 503）**有意不同**：
    那条路是"用户点了我忘了密码，你说发了其实没发"——必须响亮失败。
    通知是旁路：生成完成而邮件没发出去，作品仍在库里，用户点开就有，
    因此这里降级到"记日志 + sent=False"，由端点决定返回 202。
    """
    subject, body = render_email(kind, ctx)
    if not email_configured():
        logger.warning("[notify] 邮件通道未配置(SES_SOURCE 空) — 降级未发送 kind=%s", kind)
        return DeliveryResult(sent=False, reason="email_not_configured")
    try:
        ok = email_service.send_email(to, subject, body)
    except Exception as exc:  # noqa: BLE001 — 通知绝不把主流程拖崩
        logger.exception("[notify] 邮件发送异常 kind=%s", kind)
        return DeliveryResult(sent=False, reason="email_error", detail=type(exc).__name__)
    if not ok:
        return DeliveryResult(sent=False, reason="email_send_failed")
    return DeliveryResult(sent=True, reason="email_sent")


# ─────────────────────────── Web Push：VAPID 签名 ───────────────────────────


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _load_private_key():
    """把 `VAPID_PRIVATE_KEY` 解析成 cryptography 的 EC 私钥。

    同时接受两种形态，因为它们**都是真实运维会粘进来的东西**：
      · PEM（`-----BEGIN EC PRIVATE KEY-----`）—— openssl 直接产出；
      · 单行 base64url 的 DER（PKCS#8）—— py_vapid 的 `Vapid.from_file` 输出形态，
        也是各家生成器（含 WebPush 浏览器工具）最常见的复制粘贴格式。
    解析失败返回 None（调用方降级），**不抛** —— 一个配错的环境变量不该让
    生成完成的回调 500。
    """
    raw = (settings.vapid_private_key or "").strip()
    if not raw:
        return None
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    loaders = []
    if "BEGIN" in raw:
        # .env 里换行常被写成字面 \n，与 config._load_pem 的处理一致
        pem = raw.replace("\\n", "\n").encode("utf-8")
        loaders.append(lambda: serialization.load_pem_private_key(pem, password=None))
    else:
        try:
            der = _b64url_decode(raw)
        except Exception:
            return None
        loaders.append(lambda: serialization.load_der_private_key(der, password=None))
    for loader in loaders:
        try:
            key = loader()
        except Exception:  # noqa: BLE001 — 一个配错的环境变量不该让调用方 500
            continue
        if isinstance(key, ec.EllipticCurvePrivateKey):
            return key
    return None


def public_key_b64() -> str:
    """应用服务器公钥（base64url?uncompressed point），供前端 `applicationServerKey`。

    优先用配置的 `VAPID_PUBLIC_KEY`；未配但私钥可解析时**从私钥推导** ——
    公钥是私钥的纯函数，让运维少维护一个"必须与私钥配对否则推送静默失败"的变量。
    """
    configured = (settings.vapid_public_key or "").strip()
    if configured:
        return configured
    key = _load_private_key()
    if key is None:
        return ""
    from cryptography.hazmat.primitives import serialization

    raw = key.public_key().public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    return _b64url(raw)


def vapid_configured() -> bool:
    """VAPID 是否已配置。公钥能拿到（配置值或私钥推导）且 subject 合法才算数。"""
    return bool(public_key_b64()) and bool(_load_private_key()) and _subject_valid()


def _subject_valid() -> bool:
    sub = (settings.vapid_subject or "").strip()
    return sub.startswith("mailto:") or sub.startswith("https://")


def vapid_headers(endpoint: str, now: Optional[int] = None) -> dict[str, str]:
    """生成 VAPID 授权头（RFC 8292）：`Authorization: vapid t=<JWT>, k=<public key>`。

    签名内容是 `{"aud": <endpoint origin>, "exp": < 24h 内>, "sub": <subject>}`，
    ES256（P-256 + SHA-256）。`aud` 必须是**推送服务 origin**而非具体端点路径
    （网关按 origin 校验），`exp` 上限 24 小时 —— 两者都是网关会直接 4xx 的硬约束。
    """
    key = _load_private_key()
    if key is None:
        raise ValueError("VAPID_PRIVATE_KEY 未配置或不可解析")
    if not _subject_valid():
        raise ValueError("VAPID_SUBJECT 必须是 mailto: 或 https:// 开头")
    from urllib.parse import urlsplit

    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec, utils

    parsed = urlsplit(endpoint)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError("推送端点不是合法 URL")
    aud = f"{parsed.scheme}://{parsed.netloc}"

    ts = int(now if now is not None else time.time())
    exp = ts + 12 * 3600  # 12h < 24h 上限：给时钟漂移留余量，别贴着红线发
    header = {"typ": "JWT", "alg": "ES256"}
    claims = {"aud": aud, "exp": exp, "sub": settings.vapid_subject.strip()}
    signing_input = (
        _b64url(json.dumps(header, separators=(",", ":")).encode())
        + "."
        + _b64url(json.dumps(claims, separators=(",", ":")).encode())
    )
    der_sig = key.sign(signing_input.encode("ascii"), ec.ECDSA(hashes.SHA256()))
    r, s = utils.decode_dss_signature(der_sig)
    # JWS 要求固定长度 r||s（各 32 字节），**不是** DER
    raw_sig = r.to_bytes(32, "big") + s.to_bytes(32, "big")
    jwt_str = f"{signing_input}.{_b64url(raw_sig)}"
    return {
        "Authorization": f"vapid t={jwt_str}, k={public_key_b64()}",
        "TTL": str(int(settings.web_push_ttl_seconds)),
        "Content-Type": "application/json",
    }


# ─────────────────────────── Web Push：投递 ───────────────────────────


@dataclass
class _Pending:
    endpoint: str
    payload: dict[str, Any]
    user_id: int
    created_at: int = field(default_factory=_utc_ts)


_pending_lock = threading.Lock()
_pending: list[_Pending] = []


def pending_push_queue() -> list[dict[str, Any]]:
    """待推送队列快照（**副本**，调用方改不到内部状态）。"""
    with _pending_lock:
        return [
            {"endpoint": p.endpoint, "user_id": p.user_id, "created_at": p.created_at, "payload": p.payload}
            for p in _pending
        ]


def _enqueue(endpoint: str, payload: dict[str, Any], user_id: int) -> None:
    """降级登记。超上限时丢**最旧**的一条并记 warning ——

    这是刻意的取舍：降级期无界累积会让"通知通道故障"升级成"进程 OOM"，
    而通知本身是可丢的旁路。丢最旧的（而非最新的）保证队列里始终是"最近的待办"。
    """
    with _pending_lock:
        _pending.append(_Pending(endpoint=endpoint, payload=payload, user_id=user_id))
        while len(_pending) > PENDING_PUSH_MAX:
            dropped = _pending.pop(0)
            logger.warning("[notify] 待推送队列超上限，丢弃最旧一条 user=%s", dropped.user_id)


def clear_pending_push_queue() -> None:
    """清空队列（端点回放 / 测试隔离用）。"""
    with _pending_lock:
        _pending.clear()


def _http_push(
    endpoint: str,
    payload: dict[str, Any],
    keys: dict[str, str],
    headers: dict[str, str],
) -> DeliveryResult:
    """真正发一次推送（需 `pywebpush`）。

    `pywebpush` 是**可选**依赖：它会连带 pinch 一组加密库，而本 PR 的约束是
    "不引入未授权第三方依赖"。故此处 lazy import —— 没装就降级为待推送队列，
    装了就正常工作（安装方式写在 .env.example 与 PR 说明里）。

    `keys` 与 `payload` 分开传，是因为 `subscription_info` 需要的是订阅的
    加密密钥（p256dh/auth），而 `data` 才是给 service worker 看的载荷 ——
    早先版本把 keys 塞进 payload 再 pop 出来，那是把两个不同生命周期的东西
    混在一个 dict 里，任何一次 fail 分支都会把 keys 留在待推送队列里。
    """
    try:
        from pywebpush import WebPushException, webpush  # type: ignore[import-not-found]
    except ImportError:
        return DeliveryResult(sent=False, reason="pywebpush_not_installed")

    import os

    # webpush() 自己解析 VAPID 私钥需要**文件路径**形态；我们把已算好的
    # Authorization 头直接交出去（headers=...），只让它做载荷加密 + POST。
    # 这样两种私钥形态（PEM 单行 / base64url DER）都不必落盘成临时文件。
    try:
        resp = webpush(
            subscription_info={"endpoint": endpoint, "keys": keys},
            data=json.dumps(payload, ensure_ascii=False),
            headers=headers,
            timeout=float(os.environ.get("WEB_PUSH_TIMEOUT", "10")),
        )
    except WebPushException as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        # 404/410 = 端点已注销（换设备、卸载 PWA）→ 调用方应删除该订阅。
        logger.warning("[notify] 推送失败 status=%s endpoint_host=%s", status, _host(endpoint))
        return DeliveryResult(sent=False, reason="push_gone" if status in (404, 410) else "push_failed")
    except Exception as exc:  # noqa: BLE001 — 网络/加密异常一律降级
        logger.warning("[notify] 推送异常 %s", type(exc).__name__)
        return DeliveryResult(sent=False, reason="push_error", detail=type(exc).__name__)
    return DeliveryResult(sent=bool(getattr(resp, "status_code", 0) in range(200, 300)), reason="push_sent")


def _host(endpoint: str) -> str:
    """日志只留推送服务主机名 —— endpoint 本身是**凭据**（拿到即可向该设备推消息）。"""
    from urllib.parse import urlsplit

    try:
        return urlsplit(endpoint).netloc
    except Exception:  # noqa: BLE001
        return "?"


def send_web_push(kind: str, endpoint: str, keys: dict[str, str], user_id: int, ctx: dict[str, Any]) -> DeliveryResult:
    """向单个订阅端点推送。未配置/未装依赖 → 登记待推送并降级。"""
    payload = render_push_payload(kind, ctx)
    if not vapid_configured():
        logger.warning("[notify] VAPID 未配置 — 推送降级为待推送登记 kind=%s user=%s", kind, user_id)
        _enqueue(endpoint, payload, user_id)
        return DeliveryResult(sent=False, reason="vapid_not_configured")

    try:
        headers = vapid_headers(endpoint)
    except ValueError as exc:
        _enqueue(endpoint, payload, user_id)
        return DeliveryResult(sent=False, reason="vapid_invalid", detail=str(exc))

    result = _http_push(endpoint, payload, keys or {}, headers)
    if result.reason == "pywebpush_not_installed":
        # 已配置但没装发送器：登记，等装上后回放。
        _enqueue(endpoint, payload, user_id)
        return DeliveryResult(sent=False, reason=result.reason, detail=result.detail)
    if result.reason == "push_gone":
        # 端点已注销（换设备/卸载 PWA）：**删除**而不是登记 ——
        # 一个不会复活的端点留在待推送队列里只会永远失败。
        return DeliveryResult(sent=False, reason=result.reason, detail=result.detail)
    return DeliveryResult(sent=result.sent, reason=result.reason, detail=result.detail)


def send_self_test_email(to: str) -> DeliveryResult:
    """`POST /api/notifications/test` 的邮件分支：走 system 模板（明确标注是自测）。"""
    return send_notification_email(
        NOTIFY_SYSTEM,
        to,
        {"subject": "FLOW MUSIC — 通知通道自检", "message": "这是一封自检邮件：通知通道已按当前配置接通。"},
    )
