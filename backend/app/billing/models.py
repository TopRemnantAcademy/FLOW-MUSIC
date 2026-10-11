"""支付/订阅数据模型 (GAP-004 真实支付基座).

匹配 app/models.py 的 SQLAlchemy 1.x Column 风格.
"""

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, Numeric, String, Text, func

from app.db import Base


class PaymentOrder(Base):
    __tablename__ = "payment_orders"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    provider = Column(
        String(32), nullable=False, index=True
    )  # manual|stripe|kakao|naver|wechat|alipay
    tier_key = Column(String(32), nullable=False, index=True)
    amount_cny = Column(Numeric(10, 2), nullable=False, default=0)
    currency = Column(String(8), nullable=False, default="CNY")
    amount = Column(
        Numeric(10, 2), nullable=False, default=0
    )  # 实际计费金额(按 currency 计); amount_cny 为结算/账务等价人民币
    status = Column(String(16), nullable=False, default="pending")  # pending|paid|failed|refunded
    provider_order_id = Column(String(255), nullable=True, index=True)  # 幂等/去重
    # unique=True 是**幂等契约**：调用方拿它做重试去重（billing.py:97/114 接收该参数）。
    # 迁移 c9d0e1f2a3b4 当初只建了普通索引 → 实测同一 key 能插两次（重试=重复建单）。
    # 迁移 c3d4e5f6a7b8 已把它纠正为 UNIQUE INDEX，schema 与这里的声明自此一致。
    idempotency_key = Column(String(255), nullable=True, unique=True, index=True)
    raw_event = Column(Text, nullable=True)  # 收到的 webhook 原始体(审计)
    paid_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Subscription(Base):
    __tablename__ = "subscriptions"

    id = Column(Integer, primary_key=True, index=True)
    # ⚠ 不要同时写 unique=True + index=True：PG 上会落两条唯一索引（UNIQUE CONSTRAINT
    # + UNIQUE INDEX），与迁移 c9d0e1f2a3b4（仅 UniqConstraint uq_subscriptions_user_id）
    # 不一致 → alembic check 漂移。统一为 unique=True（唯一性由 uq_subscriptions_user_id
    # 承担），nullable=False 与迁移一致（订阅必须挂用户）。
    user_id = Column(
        Integer, ForeignKey("users.id"), unique=True, nullable=False
    )
    tier_key = Column(String(32), nullable=False, index=True)
    provider = Column(String(32), nullable=False, index=True)
    status = Column(
        String(16), nullable=False, default="active"
    )  # active|past_due|cancelled|expired
    provider_subscription_id = Column(String(255), nullable=True, index=True)
    # 供应商侧客户 ID(Stripe Customer 等). 退款闭环的唯一可靠锚点:
    # charge.refunded 事件的 Charge 对象不带 order_id / subscription, 但必带 customer,
    # 故用它离线反查本地订阅 → 用户 → 撤销权限(见 service.apply_refund_event).
    provider_customer_id = Column(String(255), nullable=True, index=True)
    current_period_start = Column(DateTime(timezone=True), nullable=True)
    current_period_end = Column(DateTime(timezone=True), nullable=True)
    cancel_at_period_end = Column(Boolean, default=False, nullable=False, server_default="0")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
