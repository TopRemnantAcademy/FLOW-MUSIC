"""add song price_cny / margin_cny / tier_key + user tier_key (unit economics)

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-09-03 22:10:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "e5f6a7b8c9d0"
down_revision: Union[str, Sequence[str], None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 歌曲级计费三要素：成本已存在(cost_cny)，补 价格/毛利/适用套餐
    op.add_column(
        "songs",
        sa.Column("price_cny", sa.Numeric(10, 4), nullable=True, server_default=sa.text("0")),
    )
    op.add_column(
        "songs",
        sa.Column("margin_cny", sa.Numeric(10, 4), nullable=True, server_default=sa.text("0")),
    )
    op.add_column("songs", sa.Column("tier_key", sa.String(32), nullable=True))
    op.create_index("ix_songs_tier_key", "songs", ["tier_key"])
    # 用户当前套餐（计费归因）
    op.add_column("users", sa.Column("tier_key", sa.String(32), nullable=True))


def downgrade() -> None:
    op.drop_index("ix_songs_tier_key", "songs")
    op.drop_column("songs", "tier_key")
    op.drop_column("songs", "margin_cny")
    op.drop_column("songs", "price_cny")
    op.drop_column("users", "tier_key")
