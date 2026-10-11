"""add vocal_gender and clip_seconds

Revision ID: b3f7a1c2d4e5
Revises: 659f128c2856
Create Date: 2026-08-29 12:55:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b3f7a1c2d4e5"
down_revision: Union[str, Sequence[str], None] = "659f128c2856"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("songs", sa.Column("vocal_gender", sa.String(16), nullable=True))
    op.add_column("songs", sa.Column("clip_seconds", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("songs", "clip_seconds")
    op.drop_column("songs", "vocal_gender")
