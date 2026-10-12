"""add payment_orders + subscriptions (GAP-004 真实支付基座)

Revision Id: c9d0e1f2a3b4
Revises: b8d2e3f4a5b6
Create Date: 2026-09-04 10:00:00.000000

배경:
  실 PG 결제(Stripe/Kakao/Naver/WeChat/Alipay) 연동을 위한 데이터 토대.
  PaymentOrder = 결제 시도/영수증(멱등 키 + provider_order_id 로 중복 방지),
  Subscription = 활성 구독 기록(티어·기간·상태). 두 테이블 모두 create_all 폴백과 공존.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c9d0e1f2a3b4"
down_revision: Union[str, Sequence[str], None] = "b8d2e3f4a5b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "payment_orders",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("tier_key", sa.String(32), nullable=False),
        sa.Column("amount_cny", sa.Numeric(10, 2), nullable=False, server_default=sa.text("0")),
        sa.Column("currency", sa.String(8), nullable=False, server_default="CNY"),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("provider_order_id", sa.String(255), nullable=True),
        sa.Column("idempotency_key", sa.String(255), nullable=True),
        sa.Column("raw_event", sa.Text(), nullable=True),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
        ),
    )
    op.create_index("ix_payment_orders_user_id", "payment_orders", ["user_id"])
    op.create_index("ix_payment_orders_provider", "payment_orders", ["provider"])
    op.create_index("ix_payment_orders_tier_key", "payment_orders", ["tier_key"])
    op.create_index("ix_payment_orders_provider_order_id", "payment_orders", ["provider_order_id"])
    op.create_index("ix_payment_orders_idempotency_key", "payment_orders", ["idempotency_key"])

    op.create_table(
        "subscriptions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("tier_key", sa.String(32), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("provider_subscription_id", sa.String(255), nullable=True),
        sa.Column("current_period_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_at_period_end", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
        ),
        # SQLite 에서 ALTER ADD CONSTRAINT 미지원 → 테이블 생성 시점에 인라인 UniqueConstraint 로 정의
        sa.UniqueConstraint("user_id", name="uq_subscriptions_user_id"),
    )
    op.create_index("ix_subscriptions_user_id", "subscriptions", ["user_id"])
    op.create_index("ix_subscriptions_tier_key", "subscriptions", ["tier_key"])
    op.create_index("ix_subscriptions_provider", "subscriptions", ["provider"])
    op.create_index(
        "ix_subscriptions_provider_subscription_id", "subscriptions", ["provider_subscription_id"]
    )


def downgrade() -> None:
    op.drop_table("subscriptions")
    op.drop_table("payment_orders")
