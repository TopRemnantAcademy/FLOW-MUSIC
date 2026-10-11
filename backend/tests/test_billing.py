"""GAP-004 真实支付基座 E2E.

覆盖:
  · manual provider 全流程: checkout → 属主确认 → 升档 + 订阅写入 + 订单列表
  · 幂等: 重复确认不重复升档
  · 未知档位拒绝
  · 越权确认 404(不泄露存在性) 且属主未被升档
  · Stripe webhook 签名验签(真实 HMAC-SHA256, 无 SDK 依赖)
  · Stripe provider 解析 paid 事件
"""

import hashlib
import hmac
import json
import sys
import time

from app.billing.provider import StripeProvider, get_provider, verify_stripe_signature
from app.billing.service import (
    apply_payment_event,
    apply_refund_event,
    apply_subscription_event,
)
from app.billing.models import PaymentOrder, Subscription
from app.config import settings
from app.db import SessionLocal
from app.models import PricingTier, User


def _register(client, email="bill@test.com"):
    r = client.post("/api/auth/register", json={"email": email, "password": "pw123456"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _seed_tier(
    tier_key: str,
    price_cny: float,
    model_version: str = "",
    price_usd: float = 0,
    price_krw: float = 0,
) -> None:
    """测试库不自带 pricing_tiers 种子, 这里显式锁定价格解析路径(真实 source of truth = DB 行).

    ⚠ upsert: 应用启动(seed_default_config)会按 DEFAULT_TIERS 幂等注入 pricing_tiers,
    故直接用 db.add 会在首个用例触发 UNIQUE(tier_key) 冲突. 与 app 的 멱등 시드 철학保持一致.
    """
    db = SessionLocal()
    try:
        existing = db.query(PricingTier).filter(PricingTier.tier_key == tier_key).first()
        if existing is not None:
            existing.price_cny = price_cny
            existing.price_usd = price_usd
            existing.price_krw = price_krw
            existing.model_version = model_version
            existing.enabled = True
        else:
            db.add(
                PricingTier(
                    tier_key=tier_key,
                    tier_name=tier_key,
                    price_cny=price_cny,
                    price_usd=price_usd,
                    price_krw=price_krw,
                    credits_per_song=1,
                    model_version=model_version,
                    enabled=True,
                )
            )
        db.commit()
    finally:
        db.close()


def test_checkout_and_manual_confirm_activates_tier(client):
    _seed_tier("standard", 0.36)
    h = _register(client)
    r = client.post("/api/billing/checkout", json={"tier_key": "standard"}, headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["tier_key"] == "standard"
    assert body["amount_cny"] == 0.36
    assert body["checkout_url"].endswith(f"/{body['order_id']}/confirm")

    r2 = client.post(f"/api/billing/orders/{body['order_id']}/confirm", headers=h)
    assert r2.status_code == 200, r2.text
    assert r2.json()["tier_key"] == "standard"

    sub = client.get("/api/billing/subscription", headers=h).json()
    assert sub["active"] is True and sub["tier_key"] == "standard"

    orders = client.get("/api/billing/orders", headers=h).json()
    assert len(orders) == 1 and orders[0]["status"] == "paid"


def test_checkout_uses_db_tier_price(client):
    # 价格解析以 DB pricing_tiers 行为准(而非 TIER_LIMITS 兜底值)
    _seed_tier("lite", 0.26, model_version="mureka-7.6")
    h = _register(client, "price@test.com")
    body = client.post("/api/billing/checkout", json={"tier_key": "lite"}, headers=h).json()
    assert body["amount_cny"] == 0.26


def test_manual_confirm_idempotent(client):
    h = _register(client, "bill2@test.com")
    oid = client.post("/api/billing/checkout", json={"tier_key": "lite"}, headers=h).json()[
        "order_id"
    ]
    assert client.post(f"/api/billing/orders/{oid}/confirm", headers=h).status_code == 200
    # 再次确认幂等: 已 paid → 409, 不重复升档/不报错
    assert client.post(f"/api/billing/orders/{oid}/confirm", headers=h).status_code == 409


def test_checkout_unknown_tier_rejected(client):
    h = _register(client, "bill3@test.com")
    r = client.post("/api/billing/checkout", json={"tier_key": "platinum"}, headers=h)
    assert r.status_code == 400


def test_manual_confirm_requires_ownership(client):
    h1 = _register(client, "owner@test.com")
    h2 = _register(client, "other@test.com")
    oid = client.post("/api/billing/checkout", json={"tier_key": "standard"}, headers=h1).json()[
        "order_id"
    ]
    # 他人确认 → 404(不泄露订单存在性)
    assert client.post(f"/api/billing/orders/{oid}/confirm", headers=h2).status_code == 404
    # 属主仍是 free
    assert client.get("/api/billing/subscription", headers=h1).json()["active"] is False


def test_stripe_webhook_signature_verification():
    secret = "whsec_test"
    payload = json.dumps({"id": "evt_1", "type": "checkout.session.completed"}).encode()
    ts = int(time.time())  # [SEC] 서명은 신선해야 한다(5분 창) — 과거 고정값은 이제 거부됨
    sig = hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    header = f"t={ts},v1={sig}"
    assert verify_stripe_signature(payload, header, secret) is True
    # 篡改签名 → 失败
    assert verify_stripe_signature(payload, f"t={ts},v1=deadbeef", secret) is False
    # 缺时间戳 → 失败
    assert verify_stripe_signature(payload, f"v1={sig}", secret) is False


def test_stripe_webhook_rejects_stale_timestamp():
    """2026-09-13 审计：旧实现解析了 t= 却从不校验新鲜度 → 签名永久有效。

    攻击者只要留存（或截获）一条合法回调原文+签名，就能在退款后重放，让订单
    重新变 paid 并复活订阅（重放/重传攻击，CWE-294）。
    """
    secret = "whsec_test"
    payload = b'{"id":"evt_old","type":"checkout.session.completed"}'
    stale = int(time.time()) - 3600  # 1 小时前
    sig = hmac.new(secret.encode(), f"{stale}.".encode() + payload, hashlib.sha256).hexdigest()
    # 同样的算法、同样的密钥、签名本身完全正确 —— 只因时间戳过旧就必须拒绝
    assert verify_stripe_signature(payload, f"t={stale},v1={sig}", secret) is False
    # 容差内的新鲜签名仍然通过
    fresh = int(time.time())
    sig2 = hmac.new(secret.encode(), f"{fresh}.".encode() + payload, hashlib.sha256).hexdigest()
    assert verify_stripe_signature(payload, f"t={fresh},v1={sig2}", secret) is True


def test_stripe_provider_webhook_parses_paid():
    prov = get_provider("stripe")
    secret = "whsec_test"
    payload = json.dumps(
        {
            "id": "evt_1",
            "type": "checkout.session.completed",
            "data": {"object": {"client_reference_id": "ord_123"}},
        }
    ).encode()
    ts = int(time.time())
    sig = hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    evt = prov.verify_webhook(raw_body=payload, signature=f"t={ts},v1={sig}", secret=secret)
    assert evt.provider_order_id == "ord_123" and evt.status == "paid"


def test_stripe_provider_rejects_bad_signature():
    import pytest

    prov = StripeProvider()
    payload = b'{"id":"evt_1"}'
    with pytest.raises(ValueError):
        prov.verify_webhook(raw_body=payload, signature="t=1,v1=bad", secret="whsec_test")


def test_checkout_currency_usd(client):
    # 多币种(USD): 结账金额按 price_usd 解析; amount_cny 保留 CNY 等价(结算/账务用)
    _seed_tier("standard", 0.36, model_version="", price_usd=0.05, price_krw=68)
    h = _register(client, "usd@test.com")
    body = client.post(
        "/api/billing/checkout", json={"tier_key": "standard", "currency": "USD"}, headers=h
    ).json()
    assert body["currency"] == "USD"
    assert body["amount"] == 0.05
    assert body["amount_cny"] == 0.36
    orders = client.get("/api/billing/orders", headers=h).json()
    assert orders[0]["currency"] == "USD" and orders[0]["amount"] == 0.05


# ---- Stripe 真实 checkout(懒加载 SDK) — 以假 stripe 模块命中真实代码路径(无需网络/真实密钥) ----
class _FakeSession:
    def __init__(self, url, id, subscription=None):
        self.url = url
        self.id = id
        self.subscription = subscription


class _CallRecorder:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeSession(
            "https://checkout.stripe.com/c/pay/cs_test_123", "cs_test_123", subscription="sub_123"
        )


class _FakeCheckout:
    def __init__(self):
        self.Session = _CallRecorder()


class _FakeStripe:
    def __init__(self):
        self.checkout = _FakeCheckout()


def test_stripe_create_checkout_mocked(monkeypatch):
    fake = _FakeStripe()
    monkeypatch.setitem(sys.modules, "stripe", fake)
    monkeypatch.setattr(settings, "stripe_secret_key", "sk_test_xxx")
    monkeypatch.setattr(settings, "billing_return_url", "https://app.test/return")

    class _FakeUser:
        email = "buyer@test.com"

    order = type("O", (), {"id": 5, "currency": "USD", "amount": 0.05, "tier_key": "standard"})()

    prov = get_provider("stripe")
    res = prov.create_checkout(order=order, user=_FakeUser(), return_url="https://app.test/return")
    assert res["checkout_url"] == "https://checkout.stripe.com/c/pay/cs_test_123"
    assert res["provider_order_id"] == "cs_test_123"
    calls = fake.checkout.Session.calls
    assert len(calls) == 1
    kw = calls[0]
    assert kw["mode"] == "subscription"
    assert kw["client_reference_id"] == "5"
    price_data = kw["line_items"][0]["price_data"]
    assert price_data["currency"] == "usd"
    assert price_data["unit_amount"] == 5  # 0.05 * 100
    assert price_data["recurring"]["interval"] == "month"
    # 退款/生命周期回溯: metadata 必须同时写到 Subscription 上(subscription_data.metadata),
    # 否则 customer.subscription.* 事件无法回贴 order_id.
    # ⚠ mode="subscription" 时 payment_intent_data 无效, 必须走 subscription_data.
    assert "payment_intent_data" not in kw
    assert kw["subscription_data"]["metadata"] == {"order_id": "5", "tier_key": "standard"}
    assert kw["metadata"]["order_id"] == "5"


def _sign_stripe(payload: bytes, secret: str, ts: int | None = None) -> str:
    """[SEC] ts 를 생략하면 **현재 시각** 으로 서명한다.

    종전 기본값은 2023-11 의 고정값(1700000000) 이었는데, 서명 신선도 검증이 도입되면서
    그런 과거 타임스탬프는 정당하게 거부된다. 테스트는 "지금 막 도착한 콜백" 을 흉내내야
    하므로 기본값을 현재 시각으로 둔다.
    """
    if ts is None:
        ts = int(time.time())
    sig = hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    return f"t={ts},v1={sig}"


def test_stripe_webhook_dunning_past_due():
    # invoice.payment_failed → 订阅置 past_due(dunning); 事件不带订单号, 靠 provider_subscription_id 匹配
    secret = "whsec_test"
    obj = {"id": "in_1", "subscription": "sub_123", "status": "open"}
    payload = json.dumps(
        {"id": "evt_2", "type": "invoice.payment_failed", "data": {"object": obj}}
    ).encode()
    prov = get_provider("stripe")
    evt = prov.verify_webhook(
        raw_body=payload, signature=_sign_stripe(payload, secret), secret=secret
    )
    assert evt.kind == "subscription"
    assert evt.subscription_status == "past_due"
    assert evt.provider_subscription_id == "sub_123"

    db = SessionLocal()
    try:
        u = User(email="dunning@test.com", hashed_password="x")
        db.add(u)
        db.commit()
        db.refresh(u)
        db.add(
            Subscription(
                user_id=u.id,
                tier_key="standard",
                provider="stripe",
                status="active",
                provider_subscription_id="sub_123",
            )
        )
        db.commit()
        res = apply_subscription_event(
            db,
            provider="stripe",
            provider_subscription_id="sub_123",
            status=evt.subscription_status,
        )
        assert res["found"] is True and res["status"] == "past_due"
        sub = (
            db.query(Subscription)
            .filter(Subscription.provider_subscription_id == "sub_123")
            .first()
        )
        assert sub.status == "past_due"
    finally:
        db.close()


def test_stripe_webhook_refund_marks_order_refunded():
    # charge.refunded 分类: status=refunded / kind=order (refunded=true → 全额退款)
    secret = "whsec_test"
    obj = {"id": "ch_1", "refund": "re_1", "refunded": True}
    payload = json.dumps(
        {"id": "evt_3", "type": "charge.refunded", "data": {"object": obj}}
    ).encode()
    prov = get_provider("stripe")
    evt = prov.verify_webhook(
        raw_body=payload, signature=_sign_stripe(payload, secret), secret=secret
    )
    assert evt.status == "refunded" and evt.kind == "order"
    # 退款状态机落库(order 已按 provider_order_id 预创建). 无法回贴订单号时的
    # customer 反查闭环见 test_refund_resolves_by_customer_and_revokes.
    db = SessionLocal()
    try:
        u = User(email="refund@test.com", hashed_password="x")
        db.add(u)
        db.commit()
        db.refresh(u)
        order = PaymentOrder(
            user_id=u.id,
            provider="stripe",
            tier_key="standard",
            amount_cny=0.36,
            amount=0.05,
            currency="USD",
            provider_order_id="ch_1",
        )
        db.add(order)
        db.commit()
        db.refresh(order)
        oid = order.id
        r = apply_payment_event(db, provider="stripe", provider_order_id="ch_1", status="refunded")
        assert r["status"] == "refunded"
        assert db.get(PaymentOrder, oid).status == "refunded"
    finally:
        db.close()


# ---- step-3: Plans API + 订阅自助取消/恢复 + 结账 provider 校验 ----


def test_plans_endpoint_returns_enabled_tiers(client):
    # 真实价格 source of truth = DB pricing_tiers; plans 端点原样返回各币种标价
    _seed_tier("standard", 0.36, model_version="mureka-9", price_usd=0.05, price_krw=68)
    h = _register(client, "plans@test.com")
    r = client.get("/api/billing/plans", headers=h)
    assert r.status_code == 200, r.text
    rows = r.json()
    assert isinstance(rows, list) and len(rows) >= 1
    std = next((x for x in rows if x["tier_key"] == "standard"), None)
    assert std is not None
    assert std["price_cny"] == 0.36
    assert std["price_usd"] == 0.05
    assert std["price_krw"] == 68
    assert std["enabled"] is True


def _activate_subscription(client, email="sub@test.com", tier="standard"):
    _seed_tier(tier, 0.36)
    h = _register(client, email)
    oid = client.post("/api/billing/checkout", json={"tier_key": tier}, headers=h).json()[
        "order_id"
    ]
    client.post(f"/api/billing/orders/{oid}/confirm", headers=h)
    return h


def test_cancel_subscription_sets_flag(client):
    h = _activate_subscription(client, "cancel@test.com")
    r = client.post("/api/billing/subscription/cancel", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["cancel_at_period_end"] is True
    # 当期仍 active(未到周期结束)
    assert body["status"] == "active"
    sub = client.get("/api/billing/subscription", headers=h).json()
    assert sub["cancel_at_period_end"] is True
    assert sub["active"] is True


def test_reactivate_clears_flag(client):
    h = _activate_subscription(client, "react@test.com")
    client.post("/api/billing/subscription/cancel", headers=h)
    r = client.post("/api/billing/subscription/reactivate", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["cancel_at_period_end"] is False
    sub = client.get("/api/billing/subscription", headers=h).json()
    assert sub["cancel_at_period_end"] is False


def test_cancel_without_subscription_returns_404(client):
    h = _register(client, "nosub@test.com")
    r = client.post("/api/billing/subscription/cancel", headers=h)
    assert r.status_code == 404


# ---- 退款连接闭环(charge.refunded → order_id/customer 回贴 → 权限撤销) ----


def _seed_paid_stripe_user(email: str, *, customer_id: str, sub_id: str, order_ref: str):
    """构造一条真实"已支付"链路: user(付费档) + paid PaymentOrder + active Subscription.

    走 apply_payment_event(而非手写行), 以便同时验证 provider_customer_id 落库路径.
    """
    db = SessionLocal()
    try:
        u = User(email=email, hashed_password="x", tier_key="free")
        db.add(u)
        db.commit()
        db.refresh(u)
        order = PaymentOrder(
            user_id=u.id,
            provider="stripe",
            tier_key="standard",
            amount_cny=0.36,
            amount=0.05,
            currency="USD",
            provider_order_id=order_ref,
        )
        db.add(order)
        db.commit()
        db.refresh(order)
        res = apply_payment_event(
            db,
            provider="stripe",
            provider_order_id=order_ref,
            status="paid",
            provider_subscription_id=sub_id,
            provider_customer_id=customer_id,
        )
        assert res["applied"] is True
        db.refresh(u)
        assert u.tier_key == "standard"
        sub = db.query(Subscription).filter(Subscription.user_id == u.id).first()
        assert sub.provider_customer_id == customer_id  # 退款反查前置条件已落库
        return u.id, order.id, sub.id
    finally:
        db.close()


def test_refund_resolves_by_customer_and_revokes():
    """charge.refunded 只带 customer(无 order_id/subscription) 也能闭环回贴 + 撤销权限。

    这是 step-2 遗留局限的正式破解: Charge 对象既无 client_reference_id 也无 subscription,
    但必带 customer; 支付成功时已把它写进 Subscription.provider_customer_id, 故可离线反查.
    """
    uid, oid, sid = _seed_paid_stripe_user(
        "refund.cust@test.com", customer_id="cus_R1", sub_id="sub_R1", order_ref="cs_R1"
    )
    secret = "whsec_test"
    obj = {"id": "ch_R1", "customer": "cus_R1", "refunded": True, "amount": 5, "amount_refunded": 5}
    payload = json.dumps(
        {"id": "evt_R1", "type": "charge.refunded", "data": {"object": obj}}
    ).encode()
    evt = get_provider("stripe").verify_webhook(
        raw_body=payload, signature=_sign_stripe(payload, secret), secret=secret
    )
    assert evt.status == "refunded"
    assert evt.provider_order_id == ""  # Charge 不带订单号
    assert evt.provider_customer_id == "cus_R1"

    db = SessionLocal()
    try:
        res = apply_refund_event(
            db,
            provider="stripe",
            provider_order_id=evt.provider_order_id or None,
            provider_customer_id=evt.provider_customer_id,
            provider_subscription_id=evt.provider_subscription_id,
            raw_event=evt.raw,
        )
        assert res["found"] is True and res["revoked"] is True
        assert res["order_id"] == oid and res["subscription_id"] == sid
        assert db.get(PaymentOrder, oid).status == "refunded"
        sub = db.get(Subscription, sid)
        assert sub.status == "cancelled" and sub.cancel_at_period_end is False
        assert db.get(User, uid).tier_key == "free"  # 权限真实撤销
    finally:
        db.close()


def test_refund_resolves_by_subscription_metadata_order_id():
    """subscription_data.metadata 使订阅事件自带 order_id → 直接按订单号回贴(最优路径)。"""
    uid, oid, sid = _seed_paid_stripe_user(
        "refund.meta@test.com", customer_id="cus_R2", sub_id="sub_R2", order_ref="cs_R2"
    )
    secret = "whsec_test"
    obj = {
        "id": "sub_R2",
        "status": "canceled",
        "metadata": {"order_id": "cs_R2", "tier_key": "standard"},
    }
    payload = json.dumps(
        {"id": "evt_R2", "type": "customer.subscription.deleted", "data": {"object": obj}}
    ).encode()
    evt = get_provider("stripe").verify_webhook(
        raw_body=payload, signature=_sign_stripe(payload, secret), secret=secret
    )
    # metadata.order_id 已可解析(subscription_data.metadata 的收益)
    assert evt.provider_order_id == "cs_R2"
    assert evt.provider_subscription_id == "sub_R2"
    assert evt.kind == "subscription" and evt.subscription_status == "cancelled"

    db = SessionLocal()
    try:
        res = apply_refund_event(
            db,
            provider="stripe",
            provider_order_id=evt.provider_order_id,
            raw_event=evt.raw,
        )
        assert res["found"] is True and res["order_id"] == oid
        assert db.get(PaymentOrder, oid).status == "refunded"
        assert db.get(User, uid).tier_key == "free"
        assert db.get(Subscription, sid).status == "cancelled"
    finally:
        db.close()


def test_partial_refund_is_not_treated_as_revocation():
    """部分退款(善意补偿)绝不能降档: parser 分类为 refund_partial, 状态机不动。"""
    secret = "whsec_test"
    obj = {
        "id": "ch_R3",
        "customer": "cus_R3",
        "refunded": False,
        "amount": 500,
        "amount_refunded": 100,
    }
    payload = json.dumps(
        {"id": "evt_R3", "type": "charge.refunded", "data": {"object": obj}}
    ).encode()
    evt = get_provider("stripe").verify_webhook(
        raw_body=payload, signature=_sign_stripe(payload, secret), secret=secret
    )
    assert evt.status == "refund_partial" and evt.kind == "order"


def test_refund_unmatched_target_is_safe_noop():
    """定位不到目标的退款事件 → found=False, 不抛异常(防伪造事件把 webhook 打成 5xx)。"""
    db = SessionLocal()
    try:
        res = apply_refund_event(db, provider="stripe", provider_customer_id="cus_does_not_exist")
        assert res["found"] is False and res["reason"] == "refund_target_not_found"
    finally:
        db.close()


def test_subscription_deleted_revokes_entitlement():
    """供应商侧订阅终止 → 必须真实降档(否则用户永久停留付费档)。"""
    uid, _oid, sid = _seed_paid_stripe_user(
        "revoke.sub@test.com", customer_id="cus_R4", sub_id="sub_R4", order_ref="cs_R4"
    )
    db = SessionLocal()
    try:
        res = apply_subscription_event(
            db, provider="stripe", provider_subscription_id="sub_R4", status="cancelled"
        )
        assert res["found"] is True and res["revoked"] is True
        assert db.get(Subscription, sid).status == "cancelled"
        assert db.get(User, uid).tier_key == "free"
    finally:
        db.close()


def test_subscription_updated_active_restores_entitlement():
    """供应商侧重新激活(customer.subscription.updated → active) → 必须恢复 user.tier_key.

    回归: 此前 apply_subscription_event 只置 Subscription.status=active, 不触碰 user.tier_key,
    导致用户付费档权限卡在 free(entitlements 以 user.tier_key 为唯一来源).
    """
    uid, _oid, sid = _seed_paid_stripe_user(
        "react.sub2@test.com", customer_id="cus_R5", sub_id="sub_R5", order_ref="cs_R5"
    )
    db = SessionLocal()
    try:
        # 先模拟供应商侧取消 → 降档 free
        apply_subscription_event(
            db, provider="stripe", provider_subscription_id="sub_R5", status="cancelled"
        )
        assert db.get(User, uid).tier_key == "free"
        # 供应商侧重新激活 → 必须恢复 standard
        res = apply_subscription_event(
            db, provider="stripe", provider_subscription_id="sub_R5", status="active"
        )
        assert res["found"] is True
        assert db.get(Subscription, sid).status == "active"
        assert db.get(User, uid).tier_key == "standard"
    finally:
        db.close()


def test_refund_webhook_endpoint_closes_loop(client):
    """端到端: POST /api/billing/webhook/stripe (charge.refunded) → 回贴 + 撤销。"""
    uid, oid, sid = _seed_paid_stripe_user(
        "refund.http@test.com", customer_id="cus_R5", sub_id="sub_R5", order_ref="cs_R5"
    )
    secret = "whsec_endpoint"
    obj = {"id": "ch_R5", "customer": "cus_R5", "refunded": True}
    payload = json.dumps(
        {"id": "evt_R5", "type": "charge.refunded", "data": {"object": obj}}
    ).encode()
    old = settings.stripe_webhook_secret
    settings.stripe_webhook_secret = secret
    try:
        r = client.post(
            "/api/billing/webhook/stripe",
            content=payload,
            headers={"Stripe-Signature": _sign_stripe(payload, secret)},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["received"] is True
        assert body["refund"]["found"] is True and body["refund"]["revoked"] is True
    finally:
        settings.stripe_webhook_secret = old

    db = SessionLocal()
    try:
        assert db.get(PaymentOrder, oid).status == "refunded"
        assert db.get(Subscription, sid).status == "cancelled"
        assert db.get(User, uid).tier_key == "free"
    finally:
        db.close()


def test_checkout_rejects_unknown_provider(client, monkeypatch):
    # billing_provider 误配为未注册名(bitcoin) → 应干净 400, 而非 500(KeyError).
    # 注意: kakao/naver 现已是注册 provider, 不能用它们当"未知名"样本.
    monkeypatch.setattr(settings, "billing_provider", "bitcoin")
    h = _register(client, "badprov@test.com")
    r = client.post("/api/billing/checkout", json={"tier_key": "standard"}, headers=h)
    assert r.status_code == 400
    assert "unsupported billing provider" in (r.json().get("detail") or "")
    # 还原(避免影响后续用例)
    monkeypatch.setattr(settings, "billing_provider", "manual")


# ===========================================================================
# step-4 本地 PG (kakao / naver / wechat / alipay) 单元测试 + approve 端点 E2E
# 零新增依赖: httpx(已装) + cryptography(已装) + 标准库. 通过注入 RSA 密钥 / mock post 命中真实代码路径.
# ===========================================================================
import base64
import os
import types

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from app.billing import service
from app.billing.pg import alipay as alipay_pg
from app.billing.pg import kakao as kakao_pg
from app.billing.pg import naver as naver_pg
from app.billing.pg import wechat as wechat_pg
from app.billing.pg.alipay import build_sign_content, verify_alipay_notify
from app.billing.pg.wechat import _decrypt_resource, _verify_platform_signature
from app.models import User


def _gen_rsa() -> tuple[bytes, bytes]:
    """返回 (private_pem, public_pem)。测试专用, 不落盘。"""
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv_pem = priv.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    pub_pem = (
        priv.public_key()
        .public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    return priv_pem, pub_pem


def _fake_order(**kw):
    """最小替身订单(仅暴露 provider 用到的属性)。"""
    defaults = dict(
        id=7,
        user_id=3,
        tier_key="standard",
        currency="KRW",
        amount=1000.0,
        provider_order_id="",
    )
    defaults.update(kw)
    return types.SimpleNamespace(**defaults)


# ---- Alipay: 签名/验签roundtrip(含真实 RSA2 正确性) ----


def test_alipay_sign_verify_roundtrip(monkeypatch):
    # 真实 RSA 私钥签名 → 支付宝公钥验签必须一致.
    # ⚠ 官方 rsaCheckV1(支付接口/电脑网站支付专用)的待验签串要 **同时剔除 sign 与 sign_type**:
    #   文档 opendocs.alipay.com/open/02pa44 第 1 条 + 官方 SDK
    #   Java AlipaySignature.getSignCheckContentV1 / PHP AopClient.rsaCheckV1.
    priv_pem, pub_pem = _gen_rsa()
    monkeypatch.setattr(settings, "alipay_public_key", pub_pem)
    params = {
        "app_id": "2021xxxx",
        "trade_status": "TRADE_SUCCESS",
        "out_trade_no": "music0000000007",
        "total_amount": "0.36",
        "notify_time": "2026-01-01 00:00:00",
        "sign_type": "RSA2",
    }
    priv = serialization.load_pem_private_key(priv_pem.encode(), password=None)
    content = build_sign_content(
        {k: v for k, v in params.items() if k not in ("sign", "sign_type")}
    )
    params["sign"] = base64.b64encode(
        priv.sign(content.encode("utf-8"), padding.PKCS1v15(), hashes.SHA256())
    ).decode()
    assert verify_alipay_notify(params) is True

    # 回归锁: 若按"只剔除 sign"(旧实现, 与官方相反)计算签名, 则验签必须失败.
    # 真实支付宝通知必带 sign_type, 旧实现会导致 100% 验签失败 → 付款后永不升档.
    wrong_content = build_sign_content({k: v for k, v in params.items() if k != "sign"})
    wrong = dict(params)
    wrong["sign"] = base64.b64encode(
        priv.sign(wrong_content.encode("utf-8"), padding.PKCS1v15(), hashes.SHA256())
    ).decode()
    assert verify_alipay_notify(wrong) is False

    # 篡改金额 → 验签失败
    tampered = dict(params)
    tampered["total_amount"] = "0.01"
    assert verify_alipay_notify(tampered) is False

    # 缺 sign → 直接 False(不抛)
    assert verify_alipay_notify({"trade_status": "TRADE_SUCCESS"}) is False


def test_alipay_create_checkout_produces_signed_url(monkeypatch):
    # 支付宝商户用「自己的」应用密钥对签名+验签 → 私钥+公钥必须来自同一对.
    priv_pem, pub_pem = _gen_rsa()
    monkeypatch.setattr(settings, "alipay_app_id", "2021xxxx")
    monkeypatch.setattr(settings, "alipay_private_key", priv_pem)
    monkeypatch.setattr(settings, "alipay_public_key", pub_pem)
    monkeypatch.setattr(settings, "alipay_sign_type", "RSA2")
    monkeypatch.setattr(settings, "alipay_notify_url", "https://app.test/notify")
    monkeypatch.setattr(settings, "alipay_gateway", "https://openapi.alipay.com/gateway.do")
    monkeypatch.setattr(settings, "billing_return_url", "https://app.test/return")

    prov = get_provider("alipay")
    res = prov.create_checkout(
        order=_fake_order(currency="CNY", amount=0.36), user=None, return_url=""
    )
    assert res["checkout_url"].startswith("https://openapi.alipay.com/gateway.do")
    assert "sign=" in res["checkout_url"]
    # 验签可还原: 按「请求侧」规则(只剔除 sign, sign_type 参与)重新验签必须通过.
    # 注意: 请求签名规则与异步通知验签规则(rsaCheckV1, 剔除 sign+sign_type)不同 ——
    #       官方 SDK 同样是两套: AopClient.getSignContent(签名) vs rsaCheckV1(验签).
    from urllib.parse import parse_qs, urlparse

    q = {k: v[0] for k, v in parse_qs(urlparse(res["checkout_url"]).query).items()}
    sign_b64 = q.pop("sign")
    content = build_sign_content(q).encode("utf-8")
    key = serialization.load_pem_public_key(pub_pem.encode())
    key.verify(base64.b64decode(sign_b64), content, padding.PKCS1v15(), hashes.SHA256())
    assert q["sign_type"] == "RSA2"
    assert res["provider_order_id"] == "music0000000007"


def test_alipay_notify_parses_amount_and_checks_app_id(monkeypatch):
    """官方验签步骤 5: 验签通过后仍须核对金额与收款方, 任一不符"务必忽略"。"""
    priv_pem, pub_pem = _gen_rsa()
    monkeypatch.setattr(settings, "alipay_public_key", pub_pem)
    monkeypatch.setattr(settings, "alipay_app_id", "2021xxxx")
    priv = serialization.load_pem_private_key(priv_pem.encode(), password=None)

    def signed(params: dict) -> bytes:
        p = dict(params, sign_type="RSA2")
        content = build_sign_content({k: v for k, v in p.items() if k not in ("sign", "sign_type")})
        p["sign"] = base64.b64encode(
            priv.sign(content.encode("utf-8"), padding.PKCS1v15(), hashes.SHA256())
        ).decode()
        from urllib.parse import urlencode

        return urlencode(p).encode("utf-8")

    prov = get_provider("alipay")
    evt = prov.verify_webhook(
        raw_body=signed(
            {
                "out_trade_no": "music0000000007",
                "trade_status": "TRADE_SUCCESS",
                "total_amount": "0.36",
                "app_id": "2021xxxx",
            }
        ),
        signature="",
        secret="",
    )
    assert evt.status == "paid"
    # total_amount(元) → 分, 供 service 与订单金额核对
    assert evt.paid_amount_minor == 36

    # 金额异常/缺失 → 不校验(None), 由订单号匹配兜底, 不误拒真实支付
    evt2 = prov.verify_webhook(
        raw_body=signed(
            {
                "out_trade_no": "music0000000007",
                "trade_status": "TRADE_SUCCESS",
                "app_id": "2021xxxx",
            }
        ),
        signature="",
        secret="",
    )
    assert evt2.paid_amount_minor is None

    # app_id 不匹配 → 拒绝(收款方核对)
    try:
        prov.verify_webhook(
            raw_body=signed(
                {
                    "out_trade_no": "music0000000007",
                    "trade_status": "TRADE_SUCCESS",
                    "total_amount": "0.36",
                    "app_id": "other_app",
                }
            ),
            signature="",
            secret="",
        )
        raised = False
    except ValueError:
        raised = True
    assert raised is True, "app_id 不匹配必须被拒绝"


def test_apply_payment_event_rejects_amount_mismatch():
    """官方(支付宝 02pa44 步骤5 / 微信 amount.total): 金额不符的通知"务必忽略" → 不升档。"""
    from app.billing.service import AmountMismatchError, apply_payment_event
    from app.db import SessionLocal

    db = SessionLocal()
    try:
        u = User(email="amt@test.com", hashed_password="x")
        db.add(u)
        db.commit()
        db.refresh(u)
        order = PaymentOrder(
            user_id=u.id,
            provider="alipay",
            tier_key="standard",
            amount_cny=0.36,
            amount=0.36,
            currency="CNY",
            provider_order_id="music0000000007",
        )
        db.add(order)
        db.commit()

        # 实收 1 分 ≠ 订单 36 分 → 拒绝升档
        try:
            apply_payment_event(
                db,
                provider="alipay",
                provider_order_id="music0000000007",
                status="paid",
                paid_amount_minor=1,
            )
            raised = False
        except AmountMismatchError:
            raised = True
        assert raised is True
        db.rollback()

        # 金额一致 → 正常升档
        r = apply_payment_event(
            db,
            provider="alipay",
            provider_order_id="music0000000007",
            status="paid",
            paid_amount_minor=36,
        )
        assert r["status"] == "paid"
        assert db.get(User, u.id).tier_key == "standard"
    finally:
        db.close()


# ---- WeChat: 平台公钥验签 + resource AES-256-GCM 解密 ----


def _wechat_webhook_body(api_v3_key: str, *, platform_priv, out_trade_no: str, ts: str, nonce: str):
    """构造一笔 TRANSACTION.SUCCESS 回调: 用平台私钥按微信规范验签串签名 + 用 APIv3 密钥加密 resource.

    解密后的字段按官方 native_notify.md「resource中ciphertext解密后字段」表构造:
    out_trade_no / trade_state / amount.total(分) / mchid / appid, 均为官方标注必填项.

    返回 (raw_body_bytes, signature_b64).
    """
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    nonce_bytes = nonce.encode("utf-8")
    aad = b"wechat-aad"
    plain = json.dumps(
        {
            "out_trade_no": out_trade_no,
            "trade_state": "SUCCESS",
            "amount": {"total": 36, "currency": "CNY"},
            "mchid": "1900000001",
            "appid": "wxd678efh567hg6787",
            "transaction_id": "1217752501201407033233368018",
        }
    ).encode("utf-8")
    ct = AESGCM(api_v3_key.encode()).encrypt(nonce_bytes, plain, aad)
    resource = {
        "algorithm": "AEAD_AES_256_GCM",
        "ciphertext": base64.b64encode(ct).decode(),
        "nonce": nonce,
        "associated_data": aad.decode(),
    }
    body = json.dumps(
        {"id": "evt_wx_1", "event_type": "TRANSACTION.SUCCESS", "resource": resource},
        ensure_ascii=False,
    ).encode("utf-8")
    message = f"{ts}\n{nonce}\n{body.decode('utf-8')}\n".encode("utf-8")
    sig = platform_priv.sign(message, padding.PKCS1v15(), hashes.SHA256())
    return body, base64.b64encode(sig).decode()


def test_wechat_webhook_verify_and_decrypt(monkeypatch):
    api_v3_key = "a" * 32  # APIv3 密钥必须 32 字节
    _plat_priv_pem, plat_pub_pem = _gen_rsa()
    monkeypatch.setattr(settings, "wechat_pay_api_v3_key", api_v3_key)
    monkeypatch.setattr(settings, "wechat_pay_platform_public_key", plat_pub_pem)
    monkeypatch.setattr(settings, "wechat_pay_pub_key_id", "")  # 平台证书模式
    platform_priv = serialization.load_pem_private_key(_plat_priv_pem.encode(), password=None)

    ts, nonce = "1700000000", "nonce1234567"
    body, sig = _wechat_webhook_body(
        api_v3_key, platform_priv=platform_priv, out_trade_no="music0000000009", ts=ts, nonce=nonce
    )
    evt = get_provider("wechat").verify_webhook(
        raw_body=body,
        signature=sig,
        secret=api_v3_key,
        headers={
            "Wechatpay-Timestamp": ts,
            "Wechatpay-Nonce": nonce,
            # 平台证书模式: 序列号不是 PUB_KEY_ID_ 格式 → 用平台证书验签
            "Wechatpay-Serial": "5157F09EFDC096DE15EBE81A47057A72",
        },
    )
    assert evt.status == "paid" and evt.kind == "order"
    assert evt.provider_order_id == "music0000000009"
    # 官方解密字段 amount.total(分) → 供 service 与订单金额核对
    assert evt.paid_amount_minor == 36


def test_wechat_webhook_bad_signature_rejected(monkeypatch):
    _, plat_pub_pem = _gen_rsa()
    monkeypatch.setattr(settings, "wechat_pay_api_v3_key", "a" * 32)
    monkeypatch.setattr(settings, "wechat_pay_platform_public_key", plat_pub_pem)
    monkeypatch.setattr(settings, "wechat_pay_pub_key_id", "")
    body = json.dumps({"id": "x", "event_type": "TRANSACTION.SUCCESS", "resource": {}}).encode()
    # 用一张无关密钥签名 → 验签失败
    other_priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    sig = base64.b64encode(
        other_priv.sign(b"t\nn\nx\n", padding.PKCS1v15(), hashes.SHA256())
    ).decode()

    try:
        get_provider("wechat").verify_webhook(
            raw_body=body,
            signature=sig,
            secret="a" * 32,
            headers={
                "Wechatpay-Timestamp": "t",
                "Wechatpay-Nonce": "n",
                "Wechatpay-Serial": "5157F09EFDC096DE15EBE81A47057A72",
            },
        )
        raised = False
    except ValueError:
        raised = True
    assert raised is True, "非法签名必须被拒绝(ValueError)"


def test_wechat_serial_is_required(monkeypatch):
    # 官方 native_notify.md 2.1: 用 Wechatpay-Serial 选择验签密钥;
    # 官方 SDK(wechatpay-java NotificationParser)对 serialNumber 缺失直接抛异常.
    _, plat_pub_pem = _gen_rsa()
    monkeypatch.setattr(settings, "wechat_pay_platform_public_key", plat_pub_pem)
    monkeypatch.setattr(settings, "wechat_pay_pub_key_id", "")
    try:
        get_provider("wechat").verify_webhook(
            raw_body=b'{"id":"x"}',
            signature="sig",
            secret="a" * 32,
            headers={"Wechatpay-Timestamp": "t", "Wechatpay-Nonce": "n"},
        )
        raised = False
    except ValueError as e:
        raised = "Wechatpay-Serial" in str(e)
    assert raised is True, "缺少 Wechatpay-Serial 必须被拒绝"


def test_wechat_pub_key_mode_requires_matching_serial(monkeypatch):
    # 公钥模式: 配置了 WECHAT_PAY_PUB_KEY_ID → 回调 serial 必须完全一致, 否则拒绝
    # (防止"拿 A 密钥验 B 密钥的签名"这种无诊断信息的静默失败).
    api_v3_key = "a" * 32
    _plat_priv_pem, plat_pub_pem = _gen_rsa()
    monkeypatch.setattr(settings, "wechat_pay_api_v3_key", api_v3_key)
    monkeypatch.setattr(settings, "wechat_pay_platform_public_key", plat_pub_pem)
    monkeypatch.setattr(settings, "wechat_pay_pub_key_id", "PUB_KEY_ID_3000000001")
    platform_priv = serialization.load_pem_private_key(_plat_priv_pem.encode(), password=None)
    ts, nonce = "1700000000", "nonce1234567"
    body, sig = _wechat_webhook_body(
        api_v3_key, platform_priv=platform_priv, out_trade_no="music0000000009", ts=ts, nonce=nonce
    )
    from app.billing.pg._common import ProviderNotConfigured

    # serial 不一致 → 503 类错误(凭据/配置问题)
    try:
        get_provider("wechat").verify_webhook(
            raw_body=body,
            signature=sig,
            secret=api_v3_key,
            headers={
                "Wechatpay-Timestamp": ts,
                "Wechatpay-Nonce": nonce,
                "Wechatpay-Serial": "PUB_KEY_ID_9999999999",
            },
        )
        raised = False
    except ProviderNotConfigured:
        raised = True
    assert raised is True, "公钥模式下 serial 不一致必须被拒绝"

    # serial 一致 → 正常通过
    evt = get_provider("wechat").verify_webhook(
        raw_body=body,
        signature=sig,
        secret=api_v3_key,
        headers={
            "Wechatpay-Timestamp": ts,
            "Wechatpay-Nonce": nonce,
            "Wechatpay-Serial": "PUB_KEY_ID_3000000001",
        },
    )
    assert evt.status == "paid"


def test_wechat_platform_cert_mode_rejects_pub_key_id_serial(monkeypatch):
    # 平台证书模式下收到 PUB_KEY_ID_ 格式的 serial → 说明微信在用公钥签名而我们配的是
    # 平台证书, 必须明确报错引导配置, 而不是让验签默默失败.
    _, plat_pub_pem = _gen_rsa()
    monkeypatch.setattr(settings, "wechat_pay_platform_public_key", plat_pub_pem)
    monkeypatch.setattr(settings, "wechat_pay_pub_key_id", "")
    from app.billing.pg._common import ProviderNotConfigured

    try:
        get_provider("wechat").verify_webhook(
            raw_body=b'{"id":"x"}',
            signature="sig",
            secret="a" * 32,
            headers={
                "Wechatpay-Timestamp": "t",
                "Wechatpay-Nonce": "n",
                "Wechatpay-Serial": "PUB_KEY_ID_3000000001",
            },
        )
        raised = False
    except ProviderNotConfigured as e:
        raised = "WECHAT_PAY_PUB_KEY_ID" in str(e)
    assert raised is True, "平台证书模式收到公钥 ID serial 必须给出配置指引"


def test_wechat_description_truncated_to_official_limit(monkeypatch):
    # 官方 native_prepay.md: description 为 string(127), "不能超过127个字符"
    priv_pem, _ = _gen_rsa()
    monkeypatch.setattr(settings, "wechat_pay_mch_id", "1900000001")
    monkeypatch.setattr(settings, "wechat_pay_app_id", "wxd678efh567hg6787")
    monkeypatch.setattr(settings, "wechat_pay_cert_serial_no", "SERIAL")
    monkeypatch.setattr(settings, "wechat_pay_private_key", priv_pem)
    monkeypatch.setattr(settings, "wechat_pay_notify_url", "https://app.test/notify")

    captured = {}

    def fake_post(url, *, headers, body):
        captured["payload"] = json.loads(body)
        return {"code_url": "weixin://wxpay/bizpayurl?pr=xxx"}

    monkeypatch.setattr(wechat_pg, "_post_raw", fake_post)
    long_tier = "x" * 500
    get_provider("wechat").create_checkout(
        order=_fake_order(currency="CNY", amount=0.36, tier_key=long_tier),
        user=None,
        return_url="",
    )
    assert len(captured["payload"]["description"]) == 127
    assert captured["payload"]["amount"] == {"total": 36, "currency": "CNY"}


def test_wechat_decrypt_resource_roundtrip():
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    key = "b" * 32
    ct = AESGCM(key.encode()).encrypt(b"nonce1234567", b'{"out_trade_no":"music1"}', b"aad")
    resource = {
        "algorithm": "AEAD_AES_256_GCM",
        "ciphertext": base64.b64encode(ct).decode(),
        "nonce": "nonce1234567",
        "associated_data": "aad",
    }
    dec = _decrypt_resource(resource, key)
    assert dec["out_trade_no"] == "music1"


# ---- KakaoPay: create_checkout(ready 调用) + approve(최종 승인) ----


def test_kakao_create_checkout_ready_handoff(monkeypatch):
    monkeypatch.setattr(settings, "kakao_pay_credential", "SK-dev")
    monkeypatch.setattr(settings, "kakao_pay_cid", "TC0ONETIME")
    monkeypatch.setattr(settings, "kakao_pay_auth_scheme", "SECRET_KEY")
    monkeypatch.setattr(settings, "kakao_pay_api_base", "https://open-api.kakaopay.com")
    monkeypatch.setattr(settings, "billing_return_url", "https://app.test/return")

    captured = {}

    def fake_post(url, *, headers, json_body):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json_body
        return {
            "tid": "T12345",
            "next_redirect_pc_url": "https://kakao/pc",
            "next_redirect_mobile_url": "https://kakao/m",
            "next_redirect_app_url": "kakao://app",
            "android_app_scheme": "a",
            "ios_app_scheme": "i",
        }

    monkeypatch.setattr(kakao_pg, "post", fake_post)
    res = get_provider("kakao").create_checkout(
        order=_fake_order(currency="KRW", amount=1000.0), user=None, return_url=""
    )
    assert res["checkout_url"] == "https://kakao/pc"
    assert res["provider_order_id"] == "T12345"
    assert res["provider_params"]["next_redirect_mobile_url"] == "https://kakao/m"
    # 공식 경로/인증/인코딩(2026-09-04 실증)
    assert captured["url"] == "https://open-api.kakaopay.com/online/v1/payment/ready"
    assert captured["headers"]["Authorization"] == "SECRET_KEY SK-dev"
    assert captured["headers"]["Content-type"].startswith("application/json")
    # vat_amount 는 의도적 미전송 → 공식 자동계산((총액-비과세)/11) 에 맡긴다.
    assert "vat_amount" not in captured["json"]
    assert captured["json"]["total_amount"] == 1000
    assert captured["json"]["approval_url"].endswith("provider=kakao&order_id=7")


def test_kakao_create_checkout_rejects_overlong_url(monkeypatch):
    # 공식: approval_url / cancel_url / fail_url 최대 255자.
    monkeypatch.setattr(settings, "kakao_pay_credential", "SK-dev")
    monkeypatch.setattr(settings, "kakao_pay_cid", "TC0ONETIME")
    monkeypatch.setattr(settings, "kakao_pay_api_base", "https://open-api.kakaopay.com")
    long_url = "https://app.test/" + "r" * 300
    try:
        get_provider("kakao").create_checkout(
            order=_fake_order(currency="KRW", amount=1000.0), user=None, return_url=long_url
        )
        raised = False
    except ValueError:
        raised = True
    assert raised is True


def test_kakao_approve_calls_approve_endpoint(monkeypatch):
    monkeypatch.setattr(settings, "kakao_pay_credential", "SK-dev")
    monkeypatch.setattr(settings, "kakao_pay_cid", "TC0ONETIME")
    monkeypatch.setattr(settings, "kakao_pay_auth_scheme", "SECRET_KEY")
    monkeypatch.setattr(settings, "kakao_pay_api_base", "https://open-api.kakaopay.com")

    captured = {}

    def fake_post(url, *, headers, json_body):
        captured["url"] = url
        captured["json"] = json_body
        return {"aid": "A1", "cid": "TC0ONETIME", "tid": "T12345"}

    monkeypatch.setattr(kakao_pg, "post", fake_post)
    order = _fake_order(currency="KRW", amount=1000.0, provider_order_id="T12345")
    data = get_provider("kakao").approve(order=order, pg_token="pg_tok_xyz")
    assert data["aid"] == "A1"
    assert captured["url"] == "https://open-api.kakaopay.com/online/v1/payment/approve"
    assert captured["json"]["tid"] == "T12345"
    assert captured["json"]["pg_token"] == "pg_tok_xyz"
    # 공식: approve 의 total_amount 는 "결제 준비 API 요청과 일치해야 함" → 서버측 금액 바인딩.
    assert captured["json"]["total_amount"] == 1000


def test_kakao_approve_missing_tid_rejected():
    order = _fake_order(currency="KRW", amount=1000.0, provider_order_id="")
    try:
        get_provider("kakao").approve(order=order, pg_token="x")
        raised = False
    except ValueError:
        raised = True
    assert raised is True


# ---- NaverPay: create_checkout(SDK handoff, 无 HTTP) + approve(서버 승인) ----


def test_naver_create_checkout_sdk_handoff(monkeypatch):
    monkeypatch.setattr(settings, "naver_pay_client_id", "CLIENT")
    monkeypatch.setattr(settings, "naver_pay_chain_id", "CHAIN")
    monkeypatch.setattr(settings, "naver_pay_mode", "development")
    monkeypatch.setattr(settings, "billing_return_url", "https://app.test/return")

    res = get_provider("naver").create_checkout(
        order=_fake_order(currency="KRW", amount=1200.0), user=None, return_url=""
    )
    # SDK handoff: checkout_url 指向自己的落地路由, 真实参数在 provider_params
    assert "provider=naver" in res["checkout_url"] and "order_id=7" in res["checkout_url"]
    assert res["provider_order_id"].startswith("naver")
    pp = res["provider_params"]
    assert pp["sdk"] == "naverpay" and pp["clientId"] == "CLIENT" and pp["chainId"] == "CHAIN"
    assert pp["totalPayAmount"] == 1200
    assert pp["merchantPayKey"] == res["provider_order_id"]
    # 공식 SDK 스크립트 주소(pay.naver.com 아님 — nsp.pay.naver.com)
    assert pp["sdk_script"] == "https://nsp.pay.naver.com/sdk/js/naverpay.min.js"
    # 공식: 일반결제 SDK 는 payType normal
    assert pp["payType"] == "normal"
    # 공식 필수 파라미터(productCount / productItems) —— 누락 시 결제창 미오픈
    assert pp["productCount"] == 1
    items = pp["productItems"]
    assert isinstance(items, list) and len(items) == 1
    it = items[0]
    # 공식 상품유형 표: 디지털 컨텐츠 = PRODUCT / DIGITAL_CONTENT
    assert it["categoryType"] == "PRODUCT"
    assert it["categoryId"] == "DIGITAL_CONTENT"
    assert it["uid"] and it["name"] and it["count"] == 1


def test_naver_approve_requires_secret(monkeypatch):
    # 공식 apply API 는 Client-Secret 헤더가 필수 → 미설정 시 ProviderNotConfigured(절대放行 안 함).
    # (공식 호스트가 확정되었으므로 apply_url 을 비워도 이 단계에서 막힌다.)
    monkeypatch.setattr(settings, "naver_pay_client_id", "CLIENT")
    monkeypatch.setattr(settings, "naver_pay_chain_id", "CHAIN")
    monkeypatch.setattr(settings, "naver_pay_mode", "development")
    monkeypatch.setattr(settings, "naver_pay_apply_url", "")
    monkeypatch.setattr(settings, "naver_pay_secret_key", "")
    from app.billing.pg._common import ProviderNotConfigured

    order = _fake_order(currency="KRW", amount=1200.0, provider_order_id="naver0000000007")
    try:
        get_provider("naver").approve(order=order, payment_id="P1")
        raised = False
    except ProviderNotConfigured:
        raised = True
    assert raised is True


def test_naver_apply_url_defaults_to_official_host(monkeypatch):
    # 공식 호스트(실증): 개발 dev-pay.paygate.naver.com / 운영 pay.paygate.naver.com
    # 경로: /naverpay-partner/naverpay/payments/v2.2/apply/payment
    monkeypatch.setattr(settings, "naver_pay_client_id", "CLIENT")
    monkeypatch.setattr(settings, "naver_pay_chain_id", "CHAIN")
    monkeypatch.setattr(settings, "naver_pay_secret_key", "SECRET")
    monkeypatch.setattr(settings, "naver_pay_apply_url", "")

    captured = {}

    def fake_post(url, *, headers, form, timeout=None):
        captured["url"] = url
        captured["timeout"] = timeout
        return {"code": "Success", "body": {"paymentId": "P1"}}

    monkeypatch.setattr(naver_pg, "post", fake_post)
    order = _fake_order(currency="KRW", amount=1200.0, provider_order_id="naver0000000007")

    monkeypatch.setattr(settings, "naver_pay_mode", "development")
    get_provider("naver").approve(order=order, payment_id="P1")
    assert captured["url"] == (
        "https://dev-pay.paygate.naver.com/naverpay-partner/naverpay/payments/v2.2/apply/payment"
    )
    # 공식 주의: 승인 timeout 60초(기본 30초면 정상 승인 중 클라이언트가 먼저 끊는다).
    assert captured["timeout"].read == 60.0

    monkeypatch.setattr(settings, "naver_pay_mode", "production")
    get_provider("naver").approve(order=order, payment_id="P1")
    assert captured["url"] == (
        "https://pay.paygate.naver.com/naverpay-partner/naverpay/payments/v2.2/apply/payment"
    )


def test_naver_approve_verifies_total_pay_amount(monkeypatch):
    # 공식 보안 가이드 3단계(필수): 응답 totalPayAmount 를 주문 금액과 대조.
    monkeypatch.setattr(settings, "naver_pay_client_id", "CLIENT")
    monkeypatch.setattr(settings, "naver_pay_chain_id", "CHAIN")
    monkeypatch.setattr(settings, "naver_pay_mode", "development")
    monkeypatch.setattr(settings, "naver_pay_secret_key", "SECRET")
    monkeypatch.setattr(settings, "naver_pay_apply_url", "https://naver/pay/apply")

    order = _fake_order(currency="KRW", amount=1200.0, provider_order_id="naver0000000007")

    def fake_post(url, *, headers, form, timeout=None):
        return {"code": "Success", "body": {"paymentId": "P1", "detail": {"totalPayAmount": 100}}}

    monkeypatch.setattr(naver_pg, "post", fake_post)
    try:
        get_provider("naver").approve(order=order, payment_id="P1")
        raised = False
    except ValueError:
        raised = True
    assert raised is True

    # 금액이 일치하면 통과
    def ok_post(url, *, headers, form, timeout=None):
        return {"code": "Success", "body": {"paymentId": "P1", "detail": {"totalPayAmount": 1200}}}

    monkeypatch.setattr(naver_pg, "post", ok_post)
    data = get_provider("naver").approve(order=order, payment_id="P1")
    assert data["body"]["paymentId"] == "P1"

    # 응답에 금액 필드가 없으면 단정하지 않고 통과(모르는 형태를 실패로 단정 금지)
    def bare_post(url, *, headers, form, timeout=None):
        return {"code": "Success"}

    monkeypatch.setattr(naver_pg, "post", bare_post)
    assert get_provider("naver").approve(order=order, payment_id="P1")["code"] == "Success"


def test_naver_approve_calls_apply_url(monkeypatch):
    monkeypatch.setattr(settings, "naver_pay_client_id", "CLIENT")
    monkeypatch.setattr(settings, "naver_pay_chain_id", "CHAIN")
    monkeypatch.setattr(settings, "naver_pay_mode", "development")
    monkeypatch.setattr(settings, "naver_pay_apply_url", "https://naver/pay/apply")
    monkeypatch.setattr(settings, "naver_pay_secret_key", "SECRET")

    captured = {}

    def fake_post(url, *, headers, form, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["form"] = form
        return {"paymentId": "P1", "status": "SUCCESS"}

    monkeypatch.setattr(naver_pg, "post", fake_post)
    order = _fake_order(currency="KRW", amount=1200.0, provider_order_id="naver0000000007")
    data = get_provider("naver").approve(order=order, payment_id="P1")
    assert data["paymentId"] == "P1"
    # 오버라이드가 있으면 공식 호스트보다 우선한다(파트너 계약 도메인 지원).
    assert captured["url"] == "https://naver/pay/apply"
    assert captured["form"] == {"paymentId": "P1"}
    assert captured["headers"]["X-Naver-Client-Id"] == "CLIENT"
    assert captured["headers"]["X-NaverPay-Chain-Id"] == "CHAIN"


# ---- approve 端点 E2E: 服务端同步승인 성공 → 승격 ----


def _set_kakao_creds(monkeypatch):
    monkeypatch.setattr(settings, "kakao_pay_credential", "SK-dev")
    monkeypatch.setattr(settings, "kakao_pay_cid", "TC0ONETIME")
    monkeypatch.setattr(settings, "kakao_pay_auth_scheme", "SECRET_KEY")
    monkeypatch.setattr(settings, "kakao_pay_api_base", "https://open-api.kakaopay.com")


def _set_naver_creds(monkeypatch):
    monkeypatch.setattr(settings, "naver_pay_client_id", "CLIENT")
    monkeypatch.setattr(settings, "naver_pay_chain_id", "CHAIN")
    monkeypatch.setattr(settings, "naver_pay_mode", "development")
    monkeypatch.setattr(settings, "naver_pay_apply_url", "https://naver/pay/apply")
    monkeypatch.setattr(settings, "naver_pay_secret_key", "SECRET")


def _create_paid_provider_order(email, *, provider, tier="standard", currency="KRW", amount=1000.0):
    """在测试库建一个该 provider 的未付订单(带 tid/pay_key), 返回 (user, order_id)。"""
    _seed_tier(tier, 0.36, price_krw=int(amount))
    db = SessionLocal()
    try:
        u = db.query(User).filter(User.email == email).first()
        if u is None:
            u = User(email=email, hashed_password="x", tier_key="free")
            db.add(u)
            db.commit()
            db.refresh(u)
        order = service.create_order(
            db, user=u, tier_key=tier, provider=provider, currency=currency
        )
        # create_order 不走 provider, 这里补上 provider 侧订单号(tid / merchantPayKey)
        order.provider_order_id = "naver0000000007" if provider == "naver" else "kakaoTID123"
        db.commit()
        db.refresh(order)
        return u, order.id
    finally:
        db.close()


def test_kakao_approve_endpoint_upgrades_tier(client, monkeypatch):
    h = _register(client, "kakao_e2e@test.com")
    _u, oid = _create_paid_provider_order(
        "kakao_e2e@test.com", provider="kakao", currency="KRW", amount=1000.0
    )
    _set_kakao_creds(monkeypatch)

    def fake_post(url, *, headers, json_body):
        return {"aid": "A1", "tid": "kakaoTID123", "cid": "TC0ONETIME"}

    monkeypatch.setattr(kakao_pg, "post", fake_post)
    r = client.post(
        "/api/billing/kakao/approve",
        json={"order_id": oid, "pg_token": "pg_tok"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "paid" and body["tier_key"] == "standard"
    sub = client.get("/api/billing/subscription", headers=h).json()
    assert sub["active"] is True and sub["tier_key"] == "standard"


def test_naver_approve_endpoint_upgrades_tier(client, monkeypatch):
    h = _register(client, "naver_e2e@test.com")
    _u, oid = _create_paid_provider_order(
        "naver_e2e@test.com", provider="naver", currency="KRW", amount=1200.0
    )
    _set_naver_creds(monkeypatch)

    def fake_post(url, *, headers, form, timeout=None):
        # 공식 응답 구조(code/body.detail) + 보안 검증을 통과하는 금액(1200원 주문).
        return {
            "code": "Success",
            "body": {"paymentId": "P1", "detail": {"totalPayAmount": 1200}},
        }

    monkeypatch.setattr(naver_pg, "post", fake_post)
    r = client.post(
        "/api/billing/naver/approve",
        json={"order_id": oid, "payment_id": "P1"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "paid" and body["tier_key"] == "standard"
    sub = client.get("/api/billing/subscription", headers=h).json()
    assert sub["active"] is True


def test_approve_endpoint_rejects_other_provider_order(client):
    # 订单属于 kakao, 却打到 naver approve → 干净 400(不泄露存在性)
    h = _register(client, "mismatch@test.com")
    _u, oid = _create_paid_provider_order("mismatch@test.com", provider="kakao", currency="KRW")
    r = client.post(
        "/api/billing/naver/approve", json={"order_id": oid, "payment_id": "P1"}, headers=h
    )
    assert r.status_code == 400
    assert "belongs to provider" in (r.json().get("detail") or "")


def test_providers_endpoint_lists_all_and_flow(client):
    h = _register(client, "prov@test.com")
    r = client.get("/api/billing/providers", headers=h)
    assert r.status_code == 200, r.text
    provs = {p["provider"]: p for p in r.json()["providers"]}
    # 7 家全部登记, flow / settle 各就各位
    assert set(provs) == {
        "manual",
        "stripe",
        "kakao",
        "naver",
        "wechat",
        "alipay",
        "paypal",
    }
    assert provs["kakao"]["flow"] == "redirect" and provs["kakao"]["settle"] == "approve"
    assert provs["naver"]["flow"] == "sdk" and provs["naver"]["settle"] == "approve"
    assert provs["wechat"]["flow"] == "qr" and provs["wechat"]["settle"] == "webhook"
    assert provs["alipay"]["settle"] == "webhook"
    assert provs["stripe"]["settle"] == "webhook"
    assert provs["paypal"]["flow"] == "redirect" and provs["paypal"]["settle"] == "webhook"
    # 单币种 PG 的 currency 是字符串; 多币种 PG(PayPal) 是列表
    assert provs["wechat"]["currency"] == "CNY"
    assert isinstance(provs["paypal"]["currency"], list)
    assert "USD" in provs["paypal"]["currencies"]
    # ⚠ 官方币种表不含 KRW(developer.paypal.com/reference/currency-codes)
    assert "KRW" not in provs["paypal"]["currencies"]
    # 不回显任何密钥/配置状态
    for p in provs.values():
        assert "secret" not in p and "key" not in p


# ===========================================================================
# PayPal: Subscriptions API(v1/billing) + 服务器侧回调验签 单元测试
# 零新增依赖: httpx(已装) + 标准库. 通过 mock paypal.post 命中真实代码路径
#   (OAuth2 取 token / 建订阅 / 回传 PayPal 验签), 覆盖官方实证的关键分支.
# 官方规范取证: developer.paypal.com/*.md(2026-09-05).
# ===========================================================================
from app.billing.pg import paypal as paypal_pg
from app.billing.pg._common import allowed_currencies, require_currency


def _fake_paypal_post(token="TOK", verify_status="SUCCESS", sub_id="I-ABC123"):
    """统一 mock: 按 URL 分支返回 token / 验签结果 / 订阅响应。

    命中 paypal.py 里所有真实 post 调用(OAuth2 / verify-webhook / 建订阅),
    不绕开任何业务代码.
    """

    def fake_post(url, *, headers, json_body=None, form=None, timeout=None):
        if "oauth2/token" in url:
            return {"access_token": token, "expires_in": 3600}
        if "verify-webhook-signature" in url:
            return {"verification_status": verify_status}
        if "billing/subscriptions" in url:
            return {
                "id": sub_id,
                "links": [
                    {
                        "rel": "approve",
                        "href": "https://www.paypal.com/webapps/billing/subscriptions?ba_token=BA-XYZ",
                    },
                ],
            }
        raise AssertionError(f"unexpected PayPal URL: {url}")

    return fake_post


def _paypal_headers(sig="SIG", **overrides):
    h = {
        "paypal-auth-algo": "SHA256withRSA",
        "paypal-cert-url": "https://api.paypal.com/cert.pem",
        "paypal-transmission-id": "TXN-1",
        "paypal-transmission-sig": sig,
        "paypal-transmission-time": "2026-01-01T00:00:00Z",
    }
    h.update(overrides)
    return h


# ---- 币种硬约束(官方币种表不含 KRW; 多币种来自 paypal_currencies) ----


def test_paypal_currency_constraint():
    # 默认 paypal_currencies 含 USD, 不含 KRW(官方 25 币种表无 KRW).
    usd_order = _fake_order(currency="USD", tier_key="standard", amount=9.99)
    assert require_currency("paypal", usd_order) == "USD"
    krw_order = _fake_order(currency="KRW", tier_key="standard", amount=1000.0)
    try:
        require_currency("paypal", krw_order)
        raised = False
    except ValueError:
        raised = True
    assert raised is True, "PayPal 不应受理 KRW(官方币种表无 KRW)"


def test_paypal_currencies_config_override(monkeypatch):
    # paypal_currencies 覆盖默认受理集合(官方: 一个 plan 一种币种, 故需多币种映射).
    monkeypatch.setattr(settings, "paypal_currencies", "EUR,GBP")
    assert allowed_currencies("paypal") == frozenset({"EUR", "GBP"})
    eur = _fake_order(currency="EUR", tier_key="standard", amount=8.0)
    assert require_currency("paypal", eur) == "EUR"
    usd = _fake_order(currency="USD", tier_key="standard", amount=9.0)
    try:
        require_currency("paypal", usd)
        raised = False
    except ValueError:
        raised = True
    assert raised is True, "paypal_currencies 覆盖后 USD 应被拒"


# ---- OAuth2 token 缓存(进程内, 提前 60s 过期) ----


def test_paypal_token_cache(monkeypatch):
    calls = []

    def fake_post(url, *, headers, json_body=None, form=None, timeout=None):
        if "oauth2/token" in url:
            calls.append(1)
            return {"access_token": "TOK", "expires_in": 3600}
        raise AssertionError(url)

    monkeypatch.setattr(settings, "paypal_client_id", "cid")
    monkeypatch.setattr(settings, "paypal_client_secret", "sec")
    monkeypatch.setattr(paypal_pg, "post", fake_post)
    paypal_pg.reset_token_cache()
    assert paypal_pg._access_token() == "TOK"
    assert paypal_pg._access_token() == "TOK"  # 缓存命中
    assert len(calls) == 1, "token 应只取一次(进程内缓存)"


# ---- create_checkout: 返回 approve 链接 + 订阅 ID ----


def test_paypal_create_checkout_returns_approve_link_and_sub_id(monkeypatch):
    monkeypatch.setattr(settings, "paypal_client_id", "cid")
    monkeypatch.setattr(settings, "paypal_client_secret", "sec")
    monkeypatch.setattr(settings, "paypal_mode", "sandbox")
    monkeypatch.setattr(settings, "paypal_plan_ids", json.dumps({"standard": "P-ABC123"}))
    monkeypatch.setattr(settings, "paypal_return_url", "https://app.test/return")
    monkeypatch.setattr(paypal_pg, "post", _fake_paypal_post())
    monkeypatch.setattr(settings, "paypal_client_id", "cid")
    monkeypatch.setattr(settings, "paypal_client_secret", "sec")
    paypal_pg.reset_token_cache()

    res = get_provider("paypal").create_checkout(
        order=_fake_order(currency="USD", tier_key="standard", amount=9.99, id=42),
        user=None,
        return_url="",
    )
    # approve 链接 = 买家批准页(官方 links[].rel=="approve")
    assert res["checkout_url"].startswith("https://www.paypal.com/webapps/billing/subscriptions")
    # 订阅 ID(I-...) 作为订单的 provider 锚点(ACTIVATED 回调的 resource.id 即它)
    assert res["provider_order_id"] == "I-ABC123"
    assert res["provider_params"]["subscription_id"] == "I-ABC123"
    assert res["provider_params"]["plan_id"] == "P-ABC123"
    assert res["provider_params"]["kind"] == "subscription_redirect"
    # custom_id = 商户订单号(回贴本地订单用), 不泄露在返回里
    assert "custom_id" not in res


# ---- verify_webhook: 服务器侧回传验签 + 事件映射 ----


def test_paypal_verify_webhook_activated(monkeypatch):
    monkeypatch.setattr(paypal_pg, "post", _fake_paypal_post())
    monkeypatch.setattr(settings, "paypal_client_id", "cid")
    monkeypatch.setattr(settings, "paypal_client_secret", "sec")
    paypal_pg.reset_token_cache()
    body = json.dumps(
        {
            "event_type": "BILLING.SUBSCRIPTION.ACTIVATED",
            "resource": {
                "id": "I-ABC123",
                "billing_info": {"next_billing_time": "2026-02-01T00:00:00Z"},
            },
        }
    ).encode()
    evt = get_provider("paypal").verify_webhook(
        raw_body=body, signature="SIG", secret="WH-1", headers=_paypal_headers()
    )
    # 首次批准激活 = 用户已完成首次付款 → 订单置 paid + 订阅置 active
    assert evt.kind == "order"
    assert evt.status == "paid"
    assert evt.subscription_status == "active"
    assert evt.provider_subscription_id == "I-ABC123"
    assert evt.period_end is not None


def test_paypal_verify_webhook_cancelled(monkeypatch):
    monkeypatch.setattr(paypal_pg, "post", _fake_paypal_post())
    monkeypatch.setattr(settings, "paypal_client_id", "cid")
    monkeypatch.setattr(settings, "paypal_client_secret", "sec")
    paypal_pg.reset_token_cache()
    body = json.dumps(
        {
            "event_type": "BILLING.SUBSCRIPTION.CANCELLED",
            "resource": {"id": "I-ABC123"},
        }
    ).encode()
    evt = get_provider("paypal").verify_webhook(
        raw_body=body, signature="SIG", secret="WH-1", headers=_paypal_headers()
    )
    assert evt.kind == "subscription"
    assert evt.status == "cancelled"
    assert evt.subscription_status == "cancelled"
    assert evt.provider_subscription_id == "I-ABC123"


def test_paypal_verify_webhook_sale_completed(monkeypatch):
    # PAYMENT.SALE.* 的订阅 ID 字段叫 billing_agreement_id(非 subscription_id)
    monkeypatch.setattr(paypal_pg, "post", _fake_paypal_post())
    monkeypatch.setattr(settings, "paypal_client_id", "cid")
    monkeypatch.setattr(settings, "paypal_client_secret", "sec")
    paypal_pg.reset_token_cache()
    body = json.dumps(
        {
            "event_type": "PAYMENT.SALE.COMPLETED",
            "resource": {
                "billing_agreement_id": "I-ABC123",
                "amount": {"currency_code": "USD", "value": "9.99"},
            },
        }
    ).encode()
    evt = get_provider("paypal").verify_webhook(
        raw_body=body, signature="SIG", secret="WH-1", headers=_paypal_headers()
    )
    assert evt.kind == "subscription"
    assert evt.status == "active"
    assert evt.provider_subscription_id == "I-ABC123"


def test_paypal_verify_webhook_sale_refunded(monkeypatch):
    monkeypatch.setattr(paypal_pg, "post", _fake_paypal_post())
    monkeypatch.setattr(settings, "paypal_client_id", "cid")
    monkeypatch.setattr(settings, "paypal_client_secret", "sec")
    paypal_pg.reset_token_cache()
    body = json.dumps(
        {
            "event_type": "PAYMENT.SALE.REFUNDED",
            "resource": {"billing_agreement_id": "I-ABC123"},
        }
    ).encode()
    evt = get_provider("paypal").verify_webhook(
        raw_body=body, signature="SIG", secret="WH-1", headers=_paypal_headers()
    )
    assert evt.kind == "order"
    assert evt.status == "refunded"
    assert evt.provider_subscription_id == "I-ABC123"


def test_paypal_verify_webhook_bad_signature(monkeypatch):
    # 回传 PayPal 验签, verification_status != SUCCESS → 拒绝
    monkeypatch.setattr(paypal_pg, "post", _fake_paypal_post(verify_status="FAILURE"))
    monkeypatch.setattr(settings, "paypal_client_id", "cid")
    monkeypatch.setattr(settings, "paypal_client_secret", "sec")
    paypal_pg.reset_token_cache()
    body = json.dumps(
        {
            "event_type": "BILLING.SUBSCRIPTION.ACTIVATED",
            "resource": {"id": "I-ABC123"},
        }
    ).encode()
    try:
        get_provider("paypal").verify_webhook(
            raw_body=body, signature="SIG", secret="WH-1", headers=_paypal_headers()
        )
        raised = False
    except ValueError as e:
        raised = "invalid paypal webhook signature" in str(e)
    assert raised is True, "验签失败必须抛 invalid paypal webhook signature"


def test_paypal_verify_webhook_missing_webhook_id(monkeypatch):
    # secret = webhook_id, 缺失即等于允许任何人伪造支付成功 → 必须 fail-closed
    from app.billing.pg._common import ProviderNotConfigured

    monkeypatch.setattr(paypal_pg, "post", _fake_paypal_post())
    monkeypatch.setattr(settings, "paypal_client_id", "cid")
    monkeypatch.setattr(settings, "paypal_client_secret", "sec")
    paypal_pg.reset_token_cache()
    body = json.dumps({"event_type": "X", "resource": {}}).encode()
    try:
        get_provider("paypal").verify_webhook(
            raw_body=body, signature="SIG", secret="", headers=_paypal_headers()
        )
        raised = False
    except ProviderNotConfigured:
        raised = True
    assert raised is True, "webhook_id(secret)缺失必须 ProviderNotConfigured"


def test_paypal_verify_webhook_missing_headers(monkeypatch):
    monkeypatch.setattr(paypal_pg, "post", _fake_paypal_post())
    monkeypatch.setattr(settings, "paypal_client_id", "cid")
    monkeypatch.setattr(settings, "paypal_client_secret", "sec")
    paypal_pg.reset_token_cache()
    body = json.dumps({"event_type": "X", "resource": {}}).encode()
    try:
        get_provider("paypal").verify_webhook(
            raw_body=body, signature="", secret="WH-1", headers={}
        )
        raised = False
    except ValueError:
        raised = True
    assert raised is True, "缺少验签必需头必须 ValueError"
