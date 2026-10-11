"""add content_category / engine_name / cost_cny

Revision ID: a1b2c3d4e5f6
Revises: b3f7a1c2d4e5
Create Date: 2026-08-29 13:56:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "b3f7a1c2d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "songs",
        sa.Column("content_category", sa.String(32), nullable=True, server_default="general"),
    )
    op.add_column("songs", sa.Column("engine_name", sa.String(64), nullable=True))
    op.add_column(
        "songs",
        sa.Column("cost_cny", sa.Numeric(10, 4), nullable=True, server_default=sa.text("0")),
    )


def downgrade() -> None:
    op.drop_column("songs", "cost_cny")
    op.drop_column("songs", "engine_name")
    op.drop_column("songs", "content_category")
