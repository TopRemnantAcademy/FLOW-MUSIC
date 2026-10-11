"""add pricing_tiers.model_version (저가 티어 lite — 실질 원가절감용 모델 바인딩)

Revision ID: b8d2e3f4a5b6
Revises: a7c1d2e3f4a5
Create Date: 2026-09-04 09:00:00.000000

배경:
  저가 티어(lite) 를 만들려면 "가격 인하" 만으로는 부족하다 — 생성 원가가 그대로면
  마진이 음수가 된다. Mureka 공식 단가(2026-09-04 실측, platform.mureka.ai/pricing):
    · V9 / V8 / O2  = $0.045/song
    · V7.6          = $0.03/song   ← 33% 저렴
    · 9.5           = $0.15/song
  → lite 티어에 mureka-7.6 을 바인딩해 **실제 원가**를 낮춘다.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b8d2e3f4a5b6"
down_revision: Union[str, Sequence[str], None] = "a7c1d2e3f4a5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "pricing_tiers",
        sa.Column("model_version", sa.String(64), nullable=True, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("pricing_tiers", "model_version")
