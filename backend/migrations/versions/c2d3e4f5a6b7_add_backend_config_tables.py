"""add backend config tables: engine_configs / routing_rules / pricing_tiers

Revision ID: c2d3e4f5a6b7
Revises: a1b2c3d4e5f6
Create Date: 2026-08-29 14:50:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c2d3e4f5a6b7"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "engine_configs",
        sa.Column("provider", sa.String(32), primary_key=True),
        sa.Column("display_name", sa.String(64), server_default=""),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("api_base", sa.String(512), server_default=""),
        sa.Column("api_key", sa.String(512), server_default=""),
        sa.Column("cost_per_song_cny", sa.Numeric(10, 4), nullable=False, server_default="0"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("notes", sa.Text(), server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
        ),
    )
    op.create_table(
        "routing_rules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("category", sa.String(32), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_routing_rules_category", "routing_rules", ["category"])
    op.create_table(
        "pricing_tiers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tier_key", sa.String(32), unique=True, nullable=False),
        sa.Column("tier_name", sa.String(64), server_default=""),
        sa.Column("engine_provider", sa.String(32), nullable=True),
        sa.Column("price_cny", sa.Numeric(10, 4), nullable=False, server_default="0"),
        sa.Column("credits_per_song", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("description", sa.Text(), server_default=""),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("pricing_tiers")
    op.drop_index("ix_routing_rules_category")
    op.drop_table("routing_rules")
    op.drop_table("engine_configs")
