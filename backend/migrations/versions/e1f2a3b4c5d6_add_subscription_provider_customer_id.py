"""add subscriptions.provider_customer_id (GAP-004 환불 폐쇄 루프)

Revision ID: e1f2a3b4c5d6
Revises: d1e2f3a4b5c6
Create Date: 2026-09-03 10:20:00.000000

배경:
  Stripe `charge.refunded` 이벤트의 data.object 는 Charge 이며
  order_id / subscription 을 담지 않는다(구독 모드에서는 payment_intent_data 도 사용 불가).
  Charge 가 항상 담는 필드는 `customer` 이므로, checkout.session.completed 시점에
  Session.customer 를 subscriptions.provider_customer_id 로 보관해 두면
  환불 이벤트를 네트워크 호출 없이 오프라인으로 사용자까지 역추적할 수 있다.
  (app/billing/service.py:apply_refund_event)

  test_alembic 의 metadata↔migration 일치 검사를 통과시키기 위해 동봉 마이그레이션.
  nullable=True 이므로 SQLite ALTER ADD COLUMN 에 server_default 불필요.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "e1f2a3b4c5d6"
down_revision: Union[str, Sequence[str], None] = "d1e2f3a4b5c6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "subscriptions",
        sa.Column("provider_customer_id", sa.String(255), nullable=True),
    )
    op.create_index(
        "ix_subscriptions_provider_customer_id",
        "subscriptions",
        ["provider_customer_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_subscriptions_provider_customer_id", table_name="subscriptions")
    op.drop_column("subscriptions", "provider_customer_id")
