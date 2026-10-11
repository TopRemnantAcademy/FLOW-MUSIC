"""add user_links (FLOW subscriber_id <-> music user bridging table)

Revision ID: d4e5f6a7b8c9
Revises: c2d3e4f5a6b7
Create Date: 2026-08-31 12:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, Sequence[str], None] = "c2d3e4f5a6b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_links",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("flow_subscriber_id", sa.String(255), nullable=False),
        sa.Column("music_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index(
        "ix_user_links_flow_subscriber_id", "user_links", ["flow_subscriber_id"], unique=True
    )
    op.create_index("ix_user_links_music_user_id", "user_links", ["music_user_id"])


def downgrade() -> None:
    op.drop_index("ix_user_links_music_user_id", table_name="user_links")
    op.drop_index("ix_user_links_flow_subscriber_id", table_name="user_links")
    op.drop_table("user_links")
