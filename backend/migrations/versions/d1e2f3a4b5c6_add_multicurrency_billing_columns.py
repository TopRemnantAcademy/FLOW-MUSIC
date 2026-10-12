"""add multi-currency billing columns (GAP-004 step-2 provider 子轨)

Revision ID: d1e2f3a4b5c6
Revises: c9d0e1f2a3b4
Create Date: 2026-09-04 11:00:00.000000

배경:
  step-2 provider 子轨(Stripe 실 checkout / 다통화 / dunning) 구현으로 모델에 컬럼이 추가됨:
  - payment_orders.amount : 실제 과금 금액(통화 단위), amount_cny 는 결제/장부 CNY 등가.
  - pricing_tiers.price_usd / price_krw : USD/KRW 표시가(미설정 시 FX 로 CNY 환산).
  메타데이터-마이그레이션 불일치(test_alembic) 를 막기 위해 동봉 마이그레이션 추가.
  SQLite ALTER ADD COLUMN NOT NULL 는 기존 행 기본값이 필요 → server_default 사용.
  값은 sa.text("0") 로 둔다 — 문자열 '0' 은 PG 에서 DEFAULT '0'(text 타입) 로 렌더되어
  NUMERIC/INTEGER 열에 붙으면 지저분한 캐스트에 의존하게 된다.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "d1e2f3a4b5c6"
down_revision: Union[str, Sequence[str], None] = "c9d0e1f2a3b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "payment_orders",
        sa.Column("amount", sa.Numeric(10, 2), nullable=False, server_default=sa.text("0")),
    )

    op.add_column(
        "pricing_tiers",
        sa.Column("price_usd", sa.Numeric(10, 4), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "pricing_tiers",
        sa.Column("price_krw", sa.Numeric(10, 4), nullable=False, server_default=sa.text("0")),
    )


def downgrade() -> None:
    op.drop_column("pricing_tiers", "price_krw")
    op.drop_column("pricing_tiers", "price_usd")
    op.drop_column("payment_orders", "amount")
