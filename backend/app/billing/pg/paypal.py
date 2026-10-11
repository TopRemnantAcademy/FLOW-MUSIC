"""PayPal provider — Subscriptions API(v1/billing) + 服务器侧回调验签。

官方规范(2026-09-05 实证, 全部取自 developer.paypal.com 的 .md 原文):

  Host    : sandbox = https://api-m.sandbox.paypal.com
            live    = https://api-m.paypal.com
  取 token : POST /v1/oauth2/token
            Basic Base64(client_id:client_secret)
            Content-Type: application/x-www-form-urlencoded
            body: grant_type=client_credentials        → {"access_token": "...", "expires_in": N}

  建订阅   : POST /v1/billing/subscriptions
            必填字段只有 plan_id(26 位, ^P-[A-Z0-9]*$)。本实现额外传:
              custom_id = 本地商户订单号(1~127 位可打印 ASCII, 用于回调回贴)
              application_context.{return_url, cancel_url, shipping_preference, user_action}
            ⚠ user_action=SUBSCRIBE_NOW 时买家批准后**自动激活**; 否则须再调
              POST /v1/billing/subscriptions/{id}/activate(官方 platforms/subscriptions/integrate)
            响应 links[] 中 rel=="approve" 的 href 即买家批准页:
              https://www.paypal.com/webapps/billing/subscriptions?ba_token=BA-...

  回调验签 : ⚠ PayPal 与所有其它 PG 都不同 —— **不是本地 HMAC**, 而是把回调原样回传给
            PayPal 服务器, 由它核验:
              POST /v1/notifications/verify-webhook-signature
              {
                "auth_algo":         <PAYPAL-AUTH-ALGO 头>,
                "cert_url":         <PAYPAL-CERT-URL 头>,
                "transmission_id":  <PAYPAL-TRANSMISSION-ID 头>,
                "transmission_sig": <PAYPAL-TRANSMISSION-SIG 头>,
                "transmission_time":<PAYPAL-TRANSMISSION-TIME 头>,
                "webhook_id":       <Developer Portal 里该 webhook 的 ID>,
                "webhook_event":    <回调 JSON 原文对象>
              }
              成功 = HTTP 200 + {"verification_status": "SUCCESS"}
            官方列出两种验签方式(CRC32 自验 / 回传 PayPal), 本实现采用**回传**方式:
            不依赖本地证书链与算法名称映射, 只信 PayPal 自己的判定, 最不易出错.
            应答: 2xx 视为收到; 非 2xx 时 PayPal 最多重试 25 次 / 3 天.

  订阅事件 : BILLING.SUBSCRIPTION.{CREATED,ACTIVATED,UPDATED,EXPIRED,CANCELLED,SUSPENDED,PAYMENT.FAILED}
            PAYMENT.SALE.{COMPLETED,REFUNDED,REVERSED}
            ⚠ PAYMENT.SALE.* 的订阅 ID 字段叫 **billing_agreement_id**(不是 subscription_id)
              —— 官方事件表把它列在 Subscriptions 段下; 多个真实回调 payload 均为此字段名.

  币种     : 官方币种表(developer.paypal.com/reference/currency-codes)共 25 种, **不含 KRW**。
            CNY/BRL/MYR 官方脚注 "supported ... only for in-country PayPal accounts"。
            HUF/JPY/TWD 为 "Zero-digit currency — no decimal places or fractions"。

  计划(plan): 本实现**不在运行时建 plan**(需先建 product, 且 plan 与币种/价格强绑定)。
            plan 由运维在 PayPal 后台建好后, 通过 PAYPAL_PLAN_IDS 映射 tier_key → plan_id。
            官方原话: "Only one currency_code is allowed per subscription plan. Make a new
            subscription plan to offer a subscription in another currency."
            → 因此映射支持 "tier:CCY" 粒度。

零新增依赖: httpx(已在 requirements) + 标准库。
"""

from __future__ import annotations

import base64
import json
import time
from datetime import datetime, timezone
from typing import Any

from app.billing.pg._common import (
    ZERO_DECIMAL_CURRENCIES,
    ProviderNotConfigured,
    out_trade_no,
    post,
    require_currency,
)
from app.billing.provider import PaymentProvider, WebhookEvent
from app.config import settings

__all__ = ["PayPalProvider"]

# 官方 host(见 /api/rest 与 /subscriptions/integrate 的示例请求)
_API_HOSTS = {
    "sandbox": "https://api-m.sandbox.paypal.com",
    "live": "https://api-m.paypal.com",
}
TOKEN_PATH = "/v1/oauth2/token"
SUBSCRIPTIONS_PATH = "/v1/billing/subscriptions"
VERIFY_WEBHOOK_PATH = "/v1/notifications/verify-webhook-signature"

# 回调验签所需的 5 个头(官方 verify-webhook-signature 请求体字段的取值来源)
_H_ALGO = "paypal-auth-algo"
_H_CERT = "paypal-cert-url"
_H_TXN_ID = "paypal-transmission-id"
_H_TXN_SIG = "paypal-transmission-sig"
_H_TXN_TIME = "paypal-transmission-time"

# 零小数位币种见 _common.ZERO_DECIMAL_CURRENCIES(官方 PayPal 币种表标注)。

# 订阅事件 → 本地订阅状态(官方 event-names 表的 Subscriptions 段)
_SUB_STATUS_BY_EVENT = {
    "BILLING.SUBSCRIPTION.ACTIVATED": "active",
    "BILLING.SUBSCRIPTION.CANCELLED": "cancelled",
    "BILLING.SUBSCRIPTION.EXPIRED": "cancelled",
    "BILLING.SUBSCRIPTION.SUSPENDED": "past_due",
    "BILLING.SUBSCRIPTION.PAYMENT.FAILED": "past_due",
}

# 进程内 token 缓存(PayPal access_token 有有效期, 官方响应含 expires_in)
_token_cache: dict[str, Any] = {"token": "", "expires_at": 0.0}


def _base() -> str:
    mode = (settings.paypal_mode or "sandbox").strip().lower()
    host = _API_HOSTS.get(mode)
    if host is None:
        raise ProviderNotConfigured(f"PAYPAL_MODE 只能为 sandbox 或 live, 当前为 {mode!r}")
    return host


def _require_credentials() -> tuple[str, str]:
    cid = (settings.paypal_client_id or "").strip()
    secret = (settings.paypal_client_secret or "").strip()
    missing = [n for n, v in (("PAYPAL_CLIENT_ID", cid), ("PAYPAL_CLIENT_SECRET", secret)) if not v]
    if missing:
        raise ProviderNotConfigured(f"PayPal 未配置: {', '.join(missing)}")
    return cid, secret


def _access_token() -> str:
    """OAuth2 client_credentials 取 token, 进程内缓存(提前 60 秒过期)。"""
    now = time.monotonic()
    cached = _token_cache.get("token") or ""
    if cached and float(_token_cache.get("expires_at") or 0.0) > now + 60:
        return str(cached)

    cid, secret = _require_credentials()
    raw = f"{cid}:{secret}".encode("utf-8")
    data = post(
        f"{_base()}{TOKEN_PATH}",
        headers={
            "Authorization": f"Basic {base64.b64encode(raw).decode()}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        form={"grant_type": "client_credentials"},
    )
    token = data.get("access_token")
    if not token:
        raise RuntimeError(f"PayPal 取 token 响应缺少 access_token: {data}")
    try:
        expires_in = int(data.get("expires_in") or 0)
    except (TypeError, ValueError):
        expires_in = 0
    _token_cache["token"] = str(token)
    _token_cache["expires_at"] = now + max(expires_in, 0)
    return str(token)


def reset_token_cache() -> None:
    """仅用于测试/凭据变更后强制重新取 token。"""
    _token_cache["token"] = ""
    _token_cache["expires_at"] = 0.0


def _auth_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_access_token()}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _plan_id_for(tier_key: str, currency: str) -> str:
    """从 PAYPAL_PLAN_IDS 查 plan_id: 先查 "tier:CCY", 再查 "tier"。

    plan 与币种强绑定(官方: 一个 plan 只能有一种 currency_code), 故必须支持按币种取。
    """
    raw = (settings.paypal_plan_ids or "").strip()
    if not raw:
        raise ProviderNotConfigured(
            "PAYPAL_PLAN_IDS 未配置: 需要 tier_key → plan_id 的 JSON 映射"
            '(例: {"standard": "P-XXXX", "pro:USD": "P-YYYY"})。'
            "plan 需在 PayPal 后台预先建好(见官方 /subscriptions/integrate 第 1~2 步)。"
        )
    try:
        mapping = json.loads(raw)
    except (json.JSONDecodeError, ValueError) as e:
        raise ProviderNotConfigured(f"PAYPAL_PLAN_IDS 不是合法 JSON: {e}")
    if not isinstance(mapping, dict):
        raise ProviderNotConfigured("PAYPAL_PLAN_IDS 必须是 JSON 对象")
    for key in (f"{tier_key}:{currency}", tier_key):
        val = mapping.get(key)
        if val:
            return str(val)
    raise ProviderNotConfigured(
        f"PAYPAL_PLAN_IDS 中缺少 tier={tier_key!r} 币种={currency} 对应的 plan_id"
        f"(查过 '{tier_key}:{currency}' 与 '{tier_key}')"
    )


def _approve_link(data: dict) -> str:
    for link in data.get("links") or []:
        if isinstance(link, dict) and link.get("rel") == "approve":
            href = link.get("href")
            if href:
                return str(href)
    raise RuntimeError(f"PayPal 建订阅响应缺少 rel=approve 的链接: {data}")


def _parse_rfc3339(value: str) -> datetime | None:
    """官方时间字段为 RFC3339(如 2020-01-22T00:00:00Z)。解析失败 → None(不阻塞流程)。"""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def _amount_minor(amount: Any) -> int | None:
    """把 PayPal 的 Money 对象({"currency_code","value"})换算为最小货币单位。

    零小数位币种(HUF/JPY/TWD)不乘 100 —— 官方币种表明确标注。
    缺失/无法解析 → None(不校验), 避免"用户已付却被判失败"。
    """
    if not isinstance(amount, dict):
        return None
    cur = str(amount.get("currency_code") or "").upper()
    raw = amount.get("value")
    if raw is None or str(raw).strip() == "":
        return None
    try:
        major = float(str(raw).strip())
    except (TypeError, ValueError):
        return None
    scale = 1 if cur in ZERO_DECIMAL_CURRENCIES else 100
    minor = int(round(major * scale))
    return minor if minor > 0 else None


class PayPalProvider(PaymentProvider):
    name = "paypal"

    def create_checkout(self, *, order, user, return_url: str) -> dict:
        currency = require_currency("paypal", order)
        plan_id = _plan_id_for(str(order.tier_key), currency)
        ret = (
            (return_url or "").strip()
            or (settings.paypal_return_url or "").strip()
            or (settings.billing_return_url or "").strip()
        )
        payload = {
            "plan_id": plan_id,
            # custom_id: 官方字段, 1~127 位可打印 ASCII, 用于回调把订阅回贴到本地订单.
            "custom_id": out_trade_no(order),
            "application_context": {
                "return_url": ret,
                "cancel_url": ret,
                # 买家批准后自动激活, 免去再调 /activate(官方 user_action 说明).
                "user_action": "SUBSCRIBE_NOW",
                # 数字商品无需收货地址(官方 platforms 集成指引建议项).
                "shipping_preference": "NO_SHIPPING",
            },
        }
        data = post(
            f"{_base()}{SUBSCRIPTIONS_PATH}",
            headers=_auth_headers(),
            json_body=payload,
        )
        sub_id = data.get("id") or ""
        if not sub_id:
            raise RuntimeError(f"PayPal 建订阅响应缺少 id: {data}")
        return {
            "checkout_url": _approve_link(data),
            # 订阅 ID(I-...) 作为订单的 provider 锚点: ACTIVATED 回调的 resource.id 即为它.
            "provider_order_id": str(sub_id),
            "provider_params": {
                "subscription_id": str(sub_id),
                "plan_id": plan_id,
                "kind": "subscription_redirect",
            },
        }

    def verify_webhook(
        self,
        *,
        raw_body: bytes,
        signature: str,
        secret: str,
        headers: dict[str, str] | None = None,
    ) -> WebhookEvent:
        h = {k.lower(): v for k, v in (headers or {}).items()}
        body = raw_body.decode("utf-8", "replace")
        if not (secret or "").strip():
            raise ProviderNotConfigured(
                "PAYPAL_WEBHOOK_ID 未配置: 回调验签必须携带它(官方 verify-webhook-signature 必填字段)。"
                "未验签即接受回调等于允许任意人伪造支付成功, 绝不放行."
            )
        algo = h.get(_H_ALGO, "")
        cert = h.get(_H_CERT, "")
        txn_id = h.get(_H_TXN_ID, "")
        txn_time = h.get(_H_TXN_TIME, "")
        if not all([algo, cert, txn_id, txn_time, signature]):
            raise ValueError(
                "PayPal 回调缺少验签必需头(PAYPAL-AUTH-ALGO/CERT-URL/"
                "TRANSMISSION-ID/TRANSMISSION-TIME/TRANSMISSION-SIG)"
            )
        try:
            event = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            raise ValueError("PayPal 回调 body 不是合法 JSON")

        # 官方: 把回调回传给 PayPal 由它核验(见模块顶部说明)。任何异常都必须 fail-closed。
        try:
            result = post(
                f"{_base()}{VERIFY_WEBHOOK_PATH}",
                headers=_auth_headers(),
                json_body={
                    "auth_algo": algo,
                    "cert_url": cert,
                    "transmission_id": txn_id,
                    "transmission_sig": signature,
                    "transmission_time": txn_time,
                    "webhook_id": secret,
                    "webhook_event": event,
                },
            )
        except ProviderNotConfigured:
            raise
        except Exception as e:  # 网络/上游异常 —— 一律当作验签失败, 绝不放行
            raise ValueError(f"PayPal 回调验签请求失败: {e}")
        if str(result.get("verification_status") or "").upper() != "SUCCESS":
            raise ValueError("invalid paypal webhook signature")

        event_type = str(event.get("event_type") or "").upper()
        resource = event.get("resource")
        resource = resource if isinstance(resource, dict) else {}

        # 订阅 ID: 订阅类事件在 resource.id, 扣款/退款类(sale)在 resource.billing_agreement_id
        sub_id = str(resource.get("id") or "")
        if event_type.startswith("PAYMENT."):
            sub_id = str(resource.get("billing_agreement_id") or "")

        period_end = None
        billing_info = resource.get("billing_info")
        if isinstance(billing_info, dict):
            period_end = _parse_rfc3339(str(billing_info.get("next_billing_time") or ""))

        # ---------- 事件映射 ----------
        if event_type in _SUB_STATUS_BY_EVENT:
            status = _SUB_STATUS_BY_EVENT[event_type]
            if event_type == "BILLING.SUBSCRIPTION.ACTIVATED":
                # 首次批准激活 = 用户已完成首次付款 → 订单置 paid + 订阅置 active.
                return WebhookEvent(
                    provider_order_id=sub_id,
                    status="paid",
                    tier_key=None,
                    user_id=None,
                    raw=body,
                    kind="order",
                    subscription_status="active",
                    provider_subscription_id=sub_id or None,
                    period_end=period_end,
                )
            # 取消/暂停/扣款失败 → 走订阅状态机(降档/dunning), 与订单状态机无关.
            return WebhookEvent(
                provider_order_id=sub_id,
                status=status,
                tier_key=None,
                user_id=None,
                raw=body,
                kind="subscription",
                subscription_status=status,
                provider_subscription_id=sub_id or None,
                period_end=period_end,
            )

        if event_type == "PAYMENT.SALE.COMPLETED":
            # 订阅扣款成功(首期/续费)。有订阅 ID → 续费对账; 无 → 按一次性付款处理.
            if sub_id:
                return WebhookEvent(
                    provider_order_id=sub_id,
                    status="active",
                    tier_key=None,
                    user_id=None,
                    raw=body,
                    kind="subscription",
                    subscription_status="active",
                    provider_subscription_id=sub_id,
                    period_end=period_end,
                )
            return WebhookEvent(
                provider_order_id="",
                status="paid",
                tier_key=None,
                user_id=None,
                raw=body,
                kind="order",
                paid_amount_minor=_amount_minor(resource.get("amount")),
            )

        if event_type == "PAYMENT.SALE.DENIED":
            return WebhookEvent(
                provider_order_id=sub_id,
                status="past_due",
                tier_key=None,
                user_id=None,
                raw=body,
                kind="subscription",
                subscription_status="past_due",
                provider_subscription_id=sub_id or None,
            )

        if event_type in ("PAYMENT.SALE.REFUNDED", "PAYMENT.SALE.REVERSED"):
            # 退款/撤销: sale 事件只带账单协议(订阅)ID 与本次退款额, **不带原交易总额**,
            # 因此无法从该事件本身区分全额/部分 → 按全额处理并撤销权限.
            # ⚠ 已知限制(拒绝幻觉: 不臆造字段): 官方未在该事件中提供原 sale 金额;
            #    若业务上会做部分退款, 需运营对账后人工恢复, 或改为回查 /v1/payments/sale/{id}.
            return WebhookEvent(
                provider_order_id="",
                status="refunded",
                tier_key=None,
                user_id=None,
                raw=body,
                kind="order",
                provider_subscription_id=sub_id or None,
            )

        # 未识别事件(含 BILLING.SUBSCRIPTION.CREATED/UPDATED): 保守按失败订单事件, 不升档.
        return WebhookEvent(
            provider_order_id=sub_id,
            status="failed",
            tier_key=None,
            user_id=None,
            raw=body,
            kind="order",
        )
