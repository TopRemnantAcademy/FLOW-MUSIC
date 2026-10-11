"""支付/订阅业务逻辑 (GAP-004).

核心: apply_payment_event 把"已验证的支付事件"落库为
  PaymentOrder.paid → 激活 user.tier_key → 写入/更新 Subscription.
幂等: 同一 (provider, provider_order_id) 已 paid 则跳过, 防止 webhook 重投导致重复升档.
安全: 没有预创建的 PaymentOrder 时拒绝直接 webhook 升档(防伪造).
"""

from datetime import datetime, timedelta, timezone
import logging

from sqlalchemy.orm import Session

from app.billing.models import PaymentOrder, Subscription
from app.billing.pg._common import ZERO_DECIMAL_CURRENCIES
from app.billing.provider import get_provider
from app.config import settings
from app.entitlements import FREE_TIER, TIER_LIMITS, VALID_TIERS
from app.models import PricingTier, User


# 이미 종결된 주문 상태 — 재전송(replay) 된 콜백은 절대 다시 승급시키지 않는다.
# ⚠ "refunded" 포함: 2026-09-13 감사로 확인된 결함 — 환불 후 status 가 refunded 로
#   바뀌면 종전 조건(status=="paid") 을 벗어나, 원문+서명을 재전송하면 다시 paid 로
#   되살아나 권한이 복구됐다(환불 클로저 완전 우회, 재전송 공격 CWE-294).
_SETTLED_ORDER_STATUS = ("paid", "refunded")


class AmountMismatchError(ValueError):
    """回调中的实收金额与订单金额不符 —— 官方要求此类通知"务必忽略"。

    支付宝(opendocs.alipay.com/open/02pa44 验签步骤 5):
      "判断 amount 是否确实为本次操作的金额……上述有任何一个验证不通过, 则表明
       本次通知是异常通知, 务必忽略。"
    微信(native_notify.md): 解密后 amount.total 即订单总金额(分)。

    与"订单未预创建"(重投可能修复)不同, 金额不符重投也不会改变 → 路由按 400 拒绝,
    不再让供应商反复重投。
    """


def _utcnow() -> datetime:
    # SQLite 에서는 DateTime(timezone=True) 가 naive 로 왕복되므로, 비교/저장 일관성을 위해
    # naive UTC 로 통일(func.now() 와 동일). aware 와 비교하면 TypeError 발생.
    #
    # ⚠ [2026-10-11 实测, PG 就绪 Ref: #72/#74] 이 "naive 통일"은 **sqlite 전용 가정**이다.
    #   PostgreSQL 의 `timestamp with time zone` 은 UTC 로 저장하되 **연결 세션 타임존으로
    #   回读**하므로, 읽어온 current_period_end 는 aware(예: tzinfo=Asia/Shanghai) 다.
    #   그 상태로 아래 enforce_subscription_validity 의
    #   `sub.current_period_end < _utcnow()` 를 비교하면
    #   TypeError: can't compare offset-naive and offset-aware datetimes →
    #   get_current_user 가 500 (기존 구독자 전원의 모든 인증 요청이 죽는다).
    #   sqlite 에서는 rep 없이 naive 로 왕복돼 이 경로가 드러나지 않는다.
    #   → PG 전환 시에는 비교 전에 tz 를 명시적으로 통일해야 한다(별도 과제).
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _resolve_currency_amount(tier_row: PricingTier, currency: str) -> tuple[str, float, float]:
    """返回 (币种, 该币种金额, CNY等价金额).

    - CNY: 直接取 price_cny.
    - USD/KRW: 优先用套餐显式标价(price_usd/price_krw); 为 0/未设则按配置汇率由 CNY 换算.
    - 不支持币种: 回退 CNY.
    amount_cny(CNY等价) 始终用于结算/账务, amount 为实际收银币种金额.
    """
    cur = currency.upper()
    base_cny = float(tier_row.price_cny or 0)
    if cur == "CNY":
        return "CNY", round(base_cny, 2), round(base_cny, 2)
    rate = {
        "USD": settings.fx_rate_cny_to_usd,
        "KRW": settings.fx_rate_cny_to_krw,
    }.get(cur)
    if rate is None:
        # 不支持币种 → 回退 CNY(不臆造)
        return "CNY", round(base_cny, 2), round(base_cny, 2)
    explicit = getattr(tier_row, f"price_{cur.lower()}", None)
    if explicit is not None and float(explicit) > 0:
        amt = float(explicit)
    else:
        amt = base_cny * float(rate)
    return cur, round(amt, 2), round(base_cny, 2)


def create_order(
    db: Session,
    *,
    user: User,
    tier_key: str,
    provider: str | None = None,
    amount_cny: float | None = None,
    idempotency_key: str | None = None,
    currency: str | None = None,
) -> PaymentOrder:
    if tier_key not in VALID_TIERS:
        raise ValueError(f"unknown tier_key: {tier_key}")
    if tier_key == "free":
        raise ValueError("free tier needs no payment order")

    prov = provider or settings.billing_provider
    cur = (currency or "CNY").upper()
    if amount_cny is None:
        row = (
            db.query(PricingTier)
            .filter(PricingTier.tier_key == tier_key, PricingTier.enabled == True)  # noqa: E712
            .first()
        )
        if row is not None:
            cur, amount, amount_cny = _resolve_currency_amount(row, cur)
        else:
            amount = 0.0
            amount_cny = float(TIER_LIMITS.get(tier_key, {}).get("price_cny", 0))
    else:
        # 显式金额(兼容旧调用): 视为该币种金额, amount_cny 沿用传入值(CNY 等价)
        amount = float(amount_cny)
    order = PaymentOrder(
        user_id=user.id,
        provider=prov,
        tier_key=tier_key,
        amount_cny=amount_cny,
        amount=amount,
        currency=cur,
        idempotency_key=idempotency_key,
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    return order


def get_order(db: Session, order_id: int) -> PaymentOrder | None:
    return db.get(PaymentOrder, order_id)


def apply_payment_event(
    db: Session,
    *,
    provider: str,
    provider_order_id: str,
    status: str,
    raw_event: str | None = None,
    period_days: int = 30,
    provider_subscription_id: str | None = None,
    provider_customer_id: str | None = None,
    paid_amount_minor: int | None = None,
) -> dict:
    """把已验证的支付事件落库。幂等。返回结果 dict。

    paid_amount_minor: 供应商回调里的实收金额(最小货币单位: 分)。提供时与订单 amount_cny
    核对, 不符则抛 AmountMismatchError(官方要求"务必忽略", 见该类注释)。
    """
    existing = (
        db.query(PaymentOrder)
        .filter(
            PaymentOrder.provider == provider,
            PaymentOrder.provider_order_id == provider_order_id,
        )
        .first()
    )
    if existing is not None and existing.status in _SETTLED_ORDER_STATUS:
        # webhook 重投: 已处理过, 幂等跳过, 不重复升档.
        # ⚠ 환불된 주문도 종결 상태로 본다(위 _SETTLED_ORDER_STATUS 주석 참조).
        sub = db.query(Subscription).filter(Subscription.user_id == existing.user_id).first()
        return {
            "order_id": existing.id,
            "applied": False,
            "tier_key": existing.tier_key,
            "status": existing.status,
            "reason": "already_paid" if existing.status == "paid" else "already_refunded",
            "subscription_id": sub.id if sub else None,
        }

    if existing is None:
        # 没有预创建订单(直接 webhook 到达) → 拒绝, 防止伪造升档
        raise ValueError("no matching payment order for webhook event")

    if status == "paid" and paid_amount_minor is not None:
        # 官方要求: 金额不符的通知"务必忽略"。用最小货币单位比较, 规避浮点误差。
        # 与订单的 **amount(按 order.currency 计)** 比, 而不是 amount_cny ——
        # PayPal 等非 CNY 通道的实收金额是外币, 拿去和人民币等价额比会全部误判。
        cur = (existing.currency or "CNY").upper()
        scale = 1 if cur in ZERO_DECIMAL_CURRENCIES else 100
        expected_minor = int(round(float(existing.amount) * scale))
        if expected_minor > 0 and paid_amount_minor != expected_minor:
            raise AmountMismatchError(
                f"{provider} 回调实收金额 {paid_amount_minor} 分 与订单 {existing.id} 的 "
                f"{expected_minor} 分 不一致"
            )
    elif status == "paid":
        # [SEC] 2026-09-13 审计（MED）：实收金额缺失时金额核对被整段跳过，paid 事件会
        # 无条件升档 —— confirm_manual(billing.py:184) 与 _finalize_approved(billing.py:490)
        # 两条路径都不回传 paid_amount_minor。此处维持既有 fail-open 行为（离线/运维流程依赖），
        # 但补一条服务端告警，供运营对账发现「未核金额的升档」。正解：调用方回传实收金额。
        logging.getLogger(__name__).warning(
            "apply_payment_event: paid 事件未携带 paid_amount_minor，金额核对跳过 "
            "(order=%s, provider=%s) —— 请确认调用方已回传实收金额",
            existing.id,
            provider,
        )

    if status == "paid":
        existing.status = "paid"
        existing.paid_at = _utcnow()
        existing.raw_event = raw_event
        if not existing.provider_order_id:
            existing.provider_order_id = provider_order_id
        user = db.get(User, existing.user_id)
        if user is None:
            db.commit()
            return {
                "order_id": existing.id,
                "applied": False,
                "tier_key": existing.tier_key,
                "status": "paid",
                "reason": "user_missing",
            }
        user.tier_key = existing.tier_key
        sub = db.query(Subscription).filter(Subscription.user_id == user.id).first()
        now = _utcnow()
        end = now + timedelta(days=period_days)
        if sub is None:
            sub = Subscription(
                user_id=user.id,
                tier_key=existing.tier_key,
                provider=existing.provider,
                status="active",
                current_period_start=now,
                current_period_end=end,
            )
            db.add(sub)
        else:
            sub.tier_key = existing.tier_key
            sub.provider = existing.provider
            sub.status = "active"
            sub.current_period_start = now
            sub.current_period_end = end
            sub.cancel_at_period_end = False
        if provider_subscription_id:
            sub.provider_subscription_id = provider_subscription_id
        if provider_customer_id:
            # 退款闭环前置条件: 支付成功时把供应商客户 ID 落库,
            # 后续 charge.refunded(只带 customer) 才能离线反查到用户.
            sub.provider_customer_id = provider_customer_id
        db.commit()
        db.refresh(existing)
        return {
            "order_id": existing.id,
            "applied": True,
            "tier_key": existing.tier_key,
            "status": "paid",
            "subscription_id": sub.id,
        }
    else:
        existing.status = status  # failed / refunded
        existing.raw_event = raw_event
        db.commit()
        return {
            "order_id": existing.id,
            "applied": False,
            "tier_key": existing.tier_key,
            "status": status,
        }


def _revoke_entitlement(db: Session, *, user_id: int | None) -> bool:
    """把用户降回免费档(撤销付费权限)。返回是否发生实际降档。

    退款/订阅终止的共同收尾动作: 只改 user.tier_key(权限判定唯一来源, 见 entitlements.py),
    不删订单/订阅记录(账务与审计需要保留)。
    """
    if user_id is None:
        return False
    user = db.get(User, user_id)
    if user is None or user.tier_key == FREE_TIER:
        return False
    user.tier_key = FREE_TIER
    return True


def apply_refund_event(
    db: Session,
    *,
    provider: str,
    provider_order_id: str | None = None,
    provider_customer_id: str | None = None,
    provider_subscription_id: str | None = None,
    raw_event: str | None = None,
) -> dict:
    """退款事件闭环: 订单置 refunded + 订阅置 cancelled + 权限降回 free。

    定位目标的解析链(全部离线, 不发起任何供应商 API 调用):
      1) provider_order_id       —— client_reference_id / metadata.order_id
         (create_checkout 里的 subscription_data.metadata 使订阅事件也自带 order_id)
      2) provider_subscription_id —— 订阅号反查本地 Subscription → user
      3) provider_customer_id     —— Charge.customer 反查本地 Subscription → user
         Stripe 的 charge.refunded 对象既不带 order_id 也不带 subscription, 只有 customer,
         所以第 3 条是真实退款场景下最常命中的路径(step-2 遗留局限正是缺了它).
    三条都定位不到 → found=False(不抛异常, 防止伪造事件把 webhook 打成 5xx/409 噪音).
    幂等: 订单已 refunded 时重复投递不会二次改写。
    """
    order = None
    sub = None
    if provider_order_id:
        order = (
            db.query(PaymentOrder)
            .filter(
                PaymentOrder.provider == provider,
                PaymentOrder.provider_order_id == provider_order_id,
            )
            .first()
        )
    if order is None and provider_subscription_id:
        sub = (
            db.query(Subscription)
            .filter(
                Subscription.provider == provider,
                Subscription.provider_subscription_id == provider_subscription_id,
            )
            .first()
        )
    if order is None and sub is None and provider_customer_id:
        sub = (
            db.query(Subscription)
            .filter(
                Subscription.provider == provider,
                Subscription.provider_customer_id == provider_customer_id,
            )
            .first()
        )
    if order is None and sub is not None:
        # 由订阅反查到用户后, 取该用户该供应商下最近一笔已支付订单作为退款目标.
        order = (
            db.query(PaymentOrder)
            .filter(
                PaymentOrder.user_id == sub.user_id,
                PaymentOrder.provider == provider,
                PaymentOrder.status == "paid",
            )
            .order_by(PaymentOrder.id.desc())
            .first()
        )
    if order is None and sub is None:
        return {"found": False, "reason": "refund_target_not_found"}

    user_id = order.user_id if order is not None else sub.user_id
    already = order is not None and order.status == "refunded"
    if order is not None:
        order.status = "refunded"
        if raw_event:
            order.raw_event = raw_event
    if sub is None and user_id is not None:
        sub = db.query(Subscription).filter(Subscription.user_id == user_id).first()
    if sub is not None:
        sub.status = "cancelled"
        sub.cancel_at_period_end = False
        # 退款即时失效: 当期结束时间拉回现在, 使 get_active_subscription 不再判为有效期内.
        sub.current_period_end = _utcnow()
    revoked = _revoke_entitlement(db, user_id=user_id)
    db.commit()
    return {
        "found": True,
        "already_refunded": already,
        "order_id": order.id if order is not None else None,
        "subscription_id": sub.id if sub is not None else None,
        "user_id": user_id,
        "revoked": revoked,
        "tier_key": FREE_TIER,
    }


def get_active_subscription(db: Session, user_id: int) -> Subscription | None:
    sub = db.query(Subscription).filter(Subscription.user_id == user_id).first()
    if sub is None:
        return None
    if sub.status != "active":
        return sub
    if sub.current_period_end is not None and sub.current_period_end < _utcnow():
        sub.status = "expired"
        # [SEC] 2026-09-13: 만료 시 **권한도 함께 회수**. 종전에는 여기서 status 만
        # 바꿨고 user.tier_key 는 그대로여서, 한 번 결제하면 영구 유료로 남았다.
        _revoke_entitlement(db, user_id=user_id)
        db.commit()
        db.refresh(sub)
    return sub


def enforce_subscription_validity(db: Session, user) -> bool:
    """만료/취소된 구독의 권한을 **접근 시점에** 회수한다(lazy revocation).

    2026-09-13 감사로 확인된 결함: 권한 판정은 `user.tier_key` 하나만 보는데
    (entitlements.is_subscribed), 자연 만료 시 이를 되돌리는 경로가 **없었다**.
    `get_active_subscription` 은 sub.status 만 expired 로 표시했고 스케줄러도 없었다.

    회수 조건: **구독 레코드가 존재하고** 그것이 유효하지 않을 때만 강등한다.
    레코드 자체가 없으면(mock /api/auth/subscribe · 수동 부여 · 레거시 계정) 건드리지
    않는다 — 그런 계정까지 일괄 강등하면 정당한 권한이 날아간다.

    반환: 실제 강등이 발생했는지. 무료 사용자는 추가 쿼리 없이 즉시 반환(핫패스 비용 0).
    """
    if user is None:
        return False
    tier = (getattr(user, "tier_key", None) or "").strip().lower()
    if not tier or tier == FREE_TIER:
        return False
    sub = db.query(Subscription).filter(Subscription.user_id == user.id).first()
    if sub is None:
        return False
    expired = sub.current_period_end is not None and sub.current_period_end < _utcnow()
    if sub.status == "active" and not expired:
        return False
    changed = False
    if sub.status == "active" and expired:
        sub.status = "expired"
        changed = True
    revoked = _revoke_entitlement(db, user_id=user.id)
    if changed or revoked:
        db.commit()
    return revoked


def provider_for(name: str) -> object:
    return get_provider(name)


def list_plans(db: Session) -> list[dict]:
    """返回已启用的套餐列表(真实价格 source of truth = DB pricing_tiers).

    前端据此展示各币种标价(price_cny/price_usd/price_krw), 使 step-2 的币种选择器
    在选择前即可见价格; 避免前端硬编码价格与后端 seed 不一致.
    """
    rows = (
        db.query(PricingTier)
        .filter(PricingTier.enabled == True)  # noqa: E712
        .order_by(PricingTier.id)
        .all()
    )
    return [
        {
            "tier_key": t.tier_key,
            "tier_name": t.tier_name,
            "price_cny": float(t.price_cny or 0),
            "price_usd": float(t.price_usd or 0),
            "price_krw": float(t.price_krw or 0),
            "credits_per_song": t.credits_per_song or 1,
            "description": t.description,
            "enabled": bool(t.enabled),
        }
        for t in rows
    ]


def cancel_subscription(db: Session, *, user: User, at_period_end: bool = True) -> dict:
    """用户自助取消订阅.

    - at_period_end=True(默认, 订阅语义): 标记 cancel_at_period_end=True, 当期结束前仍可用;
      对应 Stripe 的 Subscription.modify(cancel_at_period_end=True).
    - at_period_end=False(立即): 直接 status=cancelled.
    - 找不到订阅 → ValueError(由路由转 404).
    - provider=stripe 且有 provider_subscription_id 时, 尽力(最佳努力)同步通知真实供应商;
      SDK/密钥缺失或调用失败不阻断本地落库(运维/对账可补).
    """
    sub = db.query(Subscription).filter(Subscription.user_id == user.id).first()
    if sub is None:
        raise ValueError("no active subscription to cancel")
    if sub.status == "cancelled" and sub.cancel_at_period_end:
        # 已处于取消意向/已取消: 幂等返回
        return {
            "cancelled": True,
            "status": sub.status,
            "cancel_at_period_end": bool(sub.cancel_at_period_end),
        }
    if at_period_end:
        sub.cancel_at_period_end = True  # 当期仍可用
    else:
        sub.status = "cancelled"
    db.commit()
    db.refresh(sub)
    if sub.provider == "stripe" and sub.provider_subscription_id:
        _notify_stripe_cancel(sub.provider_subscription_id, at_period_end=at_period_end)
    return {
        "cancelled": True,
        "status": sub.status,
        "cancel_at_period_end": bool(sub.cancel_at_period_end),
        "current_period_end": str(sub.current_period_end) if sub.current_period_end else None,
    }


def reactivate_subscription(db: Session, *, user: User) -> dict:
    """撤销 cancel_at_period_end(当期结束前恢复). 找不到订阅 → ValueError(路由转 404)."""
    sub = db.query(Subscription).filter(Subscription.user_id == user.id).first()
    if sub is None:
        raise ValueError("no subscription to reactivate")
    sub.cancel_at_period_end = False
    if sub.status == "cancelled":
        # 仅当当期未过期才恢复为 active; 已过期则保持 cancelled(get_active_subscription 会判定)
        if sub.current_period_end is None or sub.current_period_end >= _utcnow():
            sub.status = "active"
    db.commit()
    db.refresh(sub)
    if sub.provider == "stripe" and sub.provider_subscription_id:
        _notify_stripe_reactivate(sub.provider_subscription_id)
    return {
        "reactivated": True,
        "status": sub.status,
        "cancel_at_period_end": bool(sub.cancel_at_period_end),
    }


def _notify_stripe_cancel(provider_sub_id: str, *, at_period_end: bool) -> None:
    """最佳努力: 通知 Stripe 取消. SDK/密钥缺失或任何异常 → 静默返回(不影响本地状态)."""
    try:
        import stripe
    except ImportError:
        return
    key = settings.stripe_secret_key
    if not key:
        return
    try:
        stripe.api_key = key
        stripe.Subscription.modify(provider_sub_id, cancel_at_period_end=at_period_end)
    except Exception:
        return


def _notify_stripe_reactivate(provider_sub_id: str) -> None:
    """最佳努力: 通知 Stripe 撤销取消. 同上, 失败静默."""
    try:
        import stripe
    except ImportError:
        return
    key = settings.stripe_secret_key
    if not key:
        return
    try:
        stripe.api_key = key
        stripe.Subscription.modify(provider_sub_id, cancel_at_period_end=False)
    except Exception:
        return


def apply_subscription_event(
    db: Session,
    *,
    provider: str,
    provider_subscription_id: str,
    status: str,
    current_period_end: datetime | None = None,
) -> dict:
    """按供应商订阅 ID 更新 Subscription 状态(dunning/续费/取消对账).

    与 apply_payment_event 解耦: 续费(invoice.paid)与扣款失败(invoice.payment_failed)等
    Stripe 事件不带 client_reference_id(订单号), 只能靠 provider_subscription_id 匹配已存在订阅.
    找不到匹配订阅(未预期的 webhook) → 安全返回 found=False, 不报错(防止伪造事件炸日志).
    """
    if not provider_subscription_id:
        return {"found": False, "reason": "no_provider_subscription_id"}
    sub = (
        db.query(Subscription)
        .filter(
            Subscription.provider == provider,
            Subscription.provider_subscription_id == provider_subscription_id,
        )
        .first()
    )
    if sub is None:
        return {"found": False, "reason": "subscription_not_found"}
    sub.status = status
    if current_period_end is not None:
        sub.current_period_end = current_period_end
    revoked = False
    if status == "cancelled":
        # 供应商侧订阅已终止(customer.subscription.deleted / status=canceled) → 必须真实撤销权限.
        # 否则用户会永久停留在付费档(仅本地 Subscription 变 cancelled, entitlements 仍看 user.tier_key).
        sub.cancel_at_period_end = False
        revoked = _revoke_entitlement(db, user_id=sub.user_id)
    elif status == "active":
        # 供应商侧重新激活(customer.subscription.updated → active / invoice.paid):
        # 必须同步恢复用户权限, 否则仅本地 Subscription 变 active, entitlements 仍看 user.tier_key(卡在 free).
        # 续费场景下 user.tier_key 通常已与 sub.tier_key 一致 → 此分支为 no-op, 安全.
        user = db.get(User, sub.user_id)
        if user is not None and user.tier_key != sub.tier_key:
            user.tier_key = sub.tier_key
    db.commit()
    db.refresh(sub)
    return {
        "found": True,
        "subscription_id": sub.id,
        "status": sub.status,
        "revoked": revoked,
    }
