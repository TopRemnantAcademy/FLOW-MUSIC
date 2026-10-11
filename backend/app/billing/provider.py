"""支付供应商抽象 (GAP-004).

设计原则(无幻觉, 可测为第一优先级):
- PaymentProvider ABC: 所有供应商统一接口(create_checkout / verify_webhook).
- ManualProvider: 运维/小批量对账模式 —— 创建订单后由所有者本人确认即视为已付.
  无外部依赖, 全链路可测, 也真实可用(线下收款后后台确认升档).
- StripeProvider: webhook 签名验签已按 Stripe 官方 HMAC-SHA256 方案实现并单测;
  checkout 创建(Stripe SDK 调用) 留作 step-2 seam(需 API key + 安装 stripe SDK, 不臆造 SDK 调用).
"""

from __future__ import annotations

import hashlib
import hmac
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone

from app.config import settings


class ProviderNotConfigured(RuntimeError):
    """凭据/端点/SDK 未就绪 —— 路由据此返回 503(而非 400/500/502)。

    与"支付被拒(400)"、"供应商上游错误(502)" 严格区分: 前者要改配置, 后者要看供应商状态.
    定义放在本模块(而非 pg/_common) 是因为 StripeProvider 也要用它, 而 pg.* 反向依赖本模块;
    pg._common 从这里再导出, 两侧共用同一个类, 使路由只需 catch 一次.
    """


@dataclass
class WebhookEvent:
    provider_order_id: str
    status: str  # paid | failed | refunded | refund_partial (order-kind 事件)
    tier_key: str | None
    user_id: int | None
    raw: str
    # ---- step-2 dunning/订阅对账扩展字段 ----
    kind: str = "order"  # order | subscription
    subscription_status: str | None = None  # active|past_due|cancelled (subscription-kind 事件)
    provider_subscription_id: str | None = None
    period_end: datetime | None = None  # 续费周期结束(invoice.paid 延长用)
    # ---- 退款闭环扩展字段 ----
    # 供应商侧客户 ID. charge.refunded 的 Charge 对象不带 order_id/subscription 但必带 customer,
    # 是退款事件回贴订单/撤销权限的唯一离线锚点(见 service.apply_refund_event).
    provider_customer_id: str | None = None
    # ---- 实收金额(最小货币单位: 分) ----
    # 官方要求回调侧核对金额, 不符即忽略:
    #   支付宝(opendocs.alipay.com/open/02pa44 验签步骤 5): "判断 amount 是否确实为
    #     本次操作的金额……上述有任何一个验证不通过, 则表明本次通知是异常通知, 务必忽略"
    #   微信(native_notify.md): 解密后 amount.total 为订单总金额(分)
    # 为 None 表示该 provider 未提供金额(不校验, 由订单号匹配兜底).
    paid_amount_minor: int | None = None


class PaymentProvider(ABC):
    name: str

    @abstractmethod
    def create_checkout(self, *, order, user, return_url: str) -> dict:
        """返回 {'checkout_url': str, 'provider_order_id': str}。"""

    @abstractmethod
    def verify_webhook(
        self,
        *,
        raw_body: bytes,
        signature: str,
        secret: str,
        headers: dict[str, str] | None = None,
    ) -> WebhookEvent:
        """验签并解析 webhook；验签失败抛 ValueError。

        headers: 部分 PG(微信支付 v3) 的验签串需要多个请求头(时间戳/随机串/证书序列号),
        单个 signature 字符串不足以表达, 故统一下传原始请求头映射; 不需要的实现忽略即可.
        """


class ManualProvider(PaymentProvider):
    name = "manual"

    def create_checkout(self, *, order, user, return_url: str) -> dict:
        # 线下模式: 不需要外部收银台, 返回平台内确认入口(订单属主调用 /confirm).
        return {
            "checkout_url": f"/api/billing/orders/{order.id}/confirm",
            "provider_order_id": f"manual_{order.id}",
        }

    def verify_webhook(
        self,
        *,
        raw_body: bytes,
        signature: str,
        secret: str,
        headers: dict[str, str] | None = None,
    ) -> WebhookEvent:
        raise NotImplementedError("manual provider has no external webhook")


def _stripe_sig_timestamp(header: str) -> int | None:
    for part in header.split(","):
        part = part.strip()
        if part.startswith("t="):
            try:
                return int(part[2:])
            except ValueError:
                return None
    return None


# [SEC] 2026-09-13 审计: 서명된 타임스탬프의 허용 창(Stripe 공식 권장 5분).
# 종전 구현은 t= 를 파싱만 하고 신선도를 전혀 검증하지 않아, 한 번 유출/기록된
# 서명이 **영구 유효** 했다 → 환불 후 원문을 재전송(replay) 하면 재승급되는
# 재전송 공격(CWE-294) 경로가 열려 있었다.
STRIPE_SIGNATURE_TOLERANCE_SECONDS = 300


def verify_stripe_signature(
    raw_body: bytes, header: str, secret: str, *, tolerance: int | None = None
) -> bool:
    """Stripe 官方 webhook 验签: HMAC-SHA256(secret, f"{ts}.{raw_body}") == v1。

    来源: https://stripe.com/docs/webhooks/signatures (实时计算, 非缓存).

    ⚠ 官方明确要求「时间戳超出容差即拒绝」—— 这是唯一能挡住重放的机制。
    tolerance<=0 时关闭该检查（仅供测试显式使用）。
    """
    ts = _stripe_sig_timestamp(header)
    if ts is None:
        return False
    tol = STRIPE_SIGNATURE_TOLERANCE_SECONDS if tolerance is None else int(tolerance)
    if tol > 0 and abs(int(time.time()) - ts) > tol:
        return False
    expected = hmac.new(secret.encode(), f"{ts}.".encode() + raw_body, hashlib.sha256).hexdigest()
    got = None
    for part in header.split(","):
        part = part.strip()
        if part.startswith("v1="):
            got = part[3:]
    if got is None:
        return False
    return hmac.compare_digest(expected, got)


def _stripe_id(value) -> str | None:
    """Stripe 关联字段归一化为 id 字符串。

    同一字段(customer / subscription 等)在未 expand 时是 id 字符串, 被 expand 时是对象;
    两种形态都可能出现在 webhook 里, 故统一取 id, 其它类型返回 None(不臆造).
    """
    if isinstance(value, str):
        return value or None
    if isinstance(value, dict):
        got = value.get("id")
        return str(got) if got else None
    return None


def _map_stripe_sub_status(stripe_status: str | None) -> str:
    """Stripe 订阅对象 status → 内部 Subscription.status 映射。"""
    return {
        "active": "active",
        "past_due": "past_due",
        "unpaid": "past_due",
        "incomplete": "past_due",
        "trialing": "active",
        "canceled": "cancelled",
    }.get((stripe_status or "").lower(), "active")


class StripeProvider(PaymentProvider):
    name = "stripe"

    def create_checkout(self, *, order, user, return_url: str) -> dict:
        """创建 Stripe Checkout 会话(真实 SDK 调用, 月付订阅模式).

        安全性/可测性:
        - 懒加载 `import stripe`, 未安装 SDK 时给出明确 RuntimeError(不影响 manual/离线流程与单测).
        - 金额单位换算为最小货币单位(分); 币种取自 order.currency(CNY/USD/KRW, Stripe 用小写三字母).
        - client_reference_id = 订单 id, 使 webhook 事件可幂等回贴到预创建 PaymentOrder(防伪造升档).
        - 单测以 sys.modules 注入假 stripe 模块即可命中真实代码路径(无需网络/真实密钥).
        """
        try:
            import stripe
        except ImportError:
            raise ProviderNotConfigured(
                "stripe SDK 未安装。启用 stripe provider 前请 `pip install stripe`(见 requirements.txt)."
            )
        key = settings.stripe_secret_key
        if not key:
            raise ProviderNotConfigured("STRIPE_SECRET_KEY 未配置; 无法创建 Stripe Checkout 会话.")
        stripe.api_key = key

        cur = (order.currency or "cny").lower()
        try:
            unit = int(round(float(order.amount) * 100))
        except (TypeError, ValueError):
            raise ValueError("order.amount 无效, 无法创建 Stripe 收银台")
        if unit <= 0:
            raise ValueError("订单金额为 0, 无需走 Stripe 收银台")

        success = (return_url or settings.billing_return_url or "").rstrip("/")
        session = stripe.checkout.Session.create(
            mode="subscription",
            client_reference_id=str(order.id),
            success_url=f"{success}?session_id={{CHECKOUT_SESSION_ID}}&order_id={order.id}",
            cancel_url=f"{success}?canceled=1&order_id={order.id}",
            customer_email=getattr(user, "email", None) or None,
            line_items=[
                {
                    "quantity": 1,
                    "price_data": {
                        "currency": cur,
                        "unit_amount": unit,
                        "recurring": {"interval": settings.stripe_recurring_interval},
                        "product_data": {"name": f"订阅套餐 · {order.tier_key}"},
                    },
                }
            ],
            metadata={"order_id": str(order.id), "tier_key": order.tier_key},
            # 退款/订阅生命周期回溯: subscription_data.metadata 会写到 Stripe Subscription 对象上,
            # 因此后续 customer.subscription.* 事件(data.object = Subscription) 自带 order_id,
            # 无需额外 API 调用即可回贴到本地 PaymentOrder.
            # ⚠ 这里不能用 payment_intent_data.metadata —— 该参数仅在 mode="payment" 有效,
            #   本会话为 mode="subscription"(见上方), 传入会被 Stripe 拒绝.
            subscription_data={
                "metadata": {"order_id": str(order.id), "tier_key": order.tier_key},
            },
        )
        return {
            "checkout_url": session.url,
            "provider_order_id": session.id,
            # 会话创建时通常还没有 Customer(结账完成才生成), 除非调用方预先绑定了既有客户.
            # 因此这里是"有则带上"的尽力值; 退款闭环真正依赖的是
            # checkout.session.completed webhook 里的 customer(见 service.apply_payment_event).
            "provider_customer_id": _stripe_id(getattr(session, "customer", None)),
        }

    def verify_webhook(
        self,
        *,
        raw_body: bytes,
        signature: str,
        secret: str,
        headers: dict[str, str] | None = None,
    ) -> WebhookEvent:
        if not verify_stripe_signature(raw_body, signature, secret):
            raise ValueError("invalid stripe signature")
        import json

        evt = json.loads(raw_body.decode("utf-8"))
        et = evt.get("type", "")
        obj = evt.get("data", {}).get("object", {})
        # 订单号(order_id) 解析链 —— 全部离线, 逐级降级, 任一层缺失都安全退化为 None:
        #   1) client_reference_id     : Checkout Session 事件(我们在 create_checkout 里写入)
        #   2) metadata.order_id       : Session / Subscription 事件(subscription_data.metadata)
        #   3) subscription_details.metadata.order_id : Invoice 事件上的订阅 metadata 拷贝
        #      (字段不存在时 .get 链直接为 None, 不假设其必然存在)
        ref = (
            obj.get("client_reference_id")
            or (obj.get("metadata") or {}).get("order_id")
            or ((obj.get("subscription_details") or {}).get("metadata") or {}).get("order_id")
        )
        sub_id = _stripe_id(obj.get("subscription"))
        if et.startswith("customer.subscription"):
            sub_id = sub_id or _stripe_id(obj.get("id"))
        cust_id = _stripe_id(obj.get("customer"))

        # 续费周期结束(invoice.paid 延长用)
        period_end = None
        pe = obj.get("period_end") or obj.get("current_period_end")
        if isinstance(pe, (int, float)):
            try:
                period_end = datetime.fromtimestamp(pe, tz=timezone.utc).replace(tzinfo=None)
            except (OverflowError, OSError, ValueError):
                period_end = None

        # 事件分类: order-kind(带 client_reference_id) vs subscription-kind(dunning/续费/取消)
        if et in ("checkout.session.completed", "checkout.session.async_payment_succeeded"):
            status, kind, sub_status = "paid", "order", "active"
        elif et in ("checkout.session.async_payment_failed", "checkout.session.expired"):
            status, kind, sub_status = "failed", "order", None
        elif et == "invoice.paid":
            # 续费成功: 不带订单号, 靠 provider_subscription_id 匹配
            status, kind, sub_status = "paid", "subscription", "active"
        elif et == "invoice.payment_failed":
            # 扣款失败 → dunning: 订阅置 past_due
            status, kind, sub_status = "failed", "subscription", "past_due"
        elif et == "customer.subscription.deleted":
            status, kind, sub_status = "failed", "subscription", "cancelled"
        elif et == "customer.subscription.updated":
            status, kind, sub_status = (
                "paid",
                "subscription",
                _map_stripe_sub_status(obj.get("status")),
            )
        elif et == "charge.refunded":
            # 全额退款 → 撤销权限(降回 free); 部分退款(善意补偿) 绝不能降档, 单独分类让路由 ack 后不动状态机.
            # Charge.refunded(bool) 仅在全额退款时为 true; 兜底再按 amount_refunded/amount 判定.
            full = bool(obj.get("refunded"))
            if not full:
                amt, ref_amt = obj.get("amount"), obj.get("amount_refunded")
                if isinstance(amt, (int, float)) and isinstance(ref_amt, (int, float)) and amt > 0:
                    full = ref_amt >= amt
            status = "refunded" if full else "refund_partial"
            kind, sub_status = "order", None
        else:
            # 未识别事件 → 保守按失败 order 事件处理, 不升档
            status, kind, sub_status = "failed", "order", None

        return WebhookEvent(
            provider_order_id=str(ref) if ref is not None else "",
            status=status,
            tier_key=None,  # tier 以 order 为准, 不在 webhook 里取
            user_id=None,
            raw=raw_body.decode("utf-8", "replace"),
            kind=kind,
            subscription_status=sub_status,
            provider_subscription_id=str(sub_id) if sub_id is not None else None,
            period_end=period_end,
            provider_customer_id=cust_id,
        )


_PROVIDERS: dict[str, type[PaymentProvider]] = {
    "manual": ManualProvider,
    "stripe": StripeProvider,
}

# 亚洲本地 PG 的实现类放在 app.billing.pg.*, 而这些模块又要 import 本模块的 ABC/WebhookEvent,
# 顶层直接 import 会形成循环依赖 → 用「模块路径, 类名」延迟解析(首次使用后缓存).
_LAZY_PROVIDERS: dict[str, tuple[str, str]] = {
    "kakao": ("app.billing.pg.kakao", "KakaoPayProvider"),
    "naver": ("app.billing.pg.naver", "NaverPayProvider"),
    "wechat": ("app.billing.pg.wechat", "WeChatPayProvider"),
    "alipay": ("app.billing.pg.alipay", "AlipayProvider"),
    "paypal": ("app.billing.pg.paypal", "PayPalProvider"),
}

# 已注册、可安全用于结账的 provider 名称列表(用于请求期校验, 防止未知 provider → 500).
SUPPORTED_PROVIDERS: list[str] = list(_PROVIDERS.keys()) + list(_LAZY_PROVIDERS.keys())

# 结算终态的取得方式不同, 路由据此分流:
#   webhook 型 —— 供应商异步回调(需验签)才是升档依据.
#   approve 型 —— 无 webhook, 服务端同步 approve 调用成功才是升档依据.
WEBHOOK_PROVIDERS: frozenset[str] = frozenset({"stripe", "wechat", "alipay", "paypal"})
APPROVE_PROVIDERS: frozenset[str] = frozenset({"kakao", "naver"})


def get_provider(name: str) -> PaymentProvider:
    cls = _PROVIDERS.get(name)
    if cls is None:
        spec = _LAZY_PROVIDERS.get(name)
        if spec is None:
            raise KeyError(f"unknown billing provider: {name}")
        import importlib

        cls = getattr(importlib.import_module(spec[0]), spec[1])
        _PROVIDERS[name] = cls  # 缓存: 后续调用零导入开销
    return cls()
