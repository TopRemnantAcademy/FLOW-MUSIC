"""add missing primary-key indexes (alembic drift cleanup)

Revision ID: a7f3c91d4e62
Revises: b2c3d4e5f8a1
Create Date: 2026-09-25 10:00:00.000000

本迁移补齐 4 张表的 5 个缺失索引。这些索引来自 `app/models.py` 中各
`Column(..., primary_key=True, index=True)` / `Column(..., index=True, unique=True)`
声明，但创建这些表的迁移（`d4e5f6a7b8c9_add_user_links.py` /
`f6a7b8c9d0e1_add_audit_logs.py` / 多币种计费相关迁移）是 autogenerate 产物，
未生成对应的 `op.create_index()`，导致 `alembic check` 恒报
"New upgrade operations detected"。

最初在 `7f0c40c8bae2_add_plaza_interactions_song_likes_song_.py:107-111` 的注释里
明确选择「不在该迁移中补、留待单独的清理迁移」，但该清理迁移一直缺失，
故在此补齐（该问题与本文件无关的历史遗留）。

注意：若目标库曾用 `Base.metadata.create_all()` 建表，这些索引可能已存在，
再 create_index 会报 "index already exists"，因此本迁移按存在性**幂等跳过**。
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from migrations._offline import inspector_or_none

# revision identifiers, used by Alembic.
revision: str = "a7f3c91d4e62"
down_revision: Union[str, Sequence[str], None] = "b2c3d4e5f8a1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (索引名, 表名, 列名, 是否唯一) —— 与 app/models.py 的声明一一对应
MISSING_INDEXES: Sequence[tuple[str, str, str, bool]] = (
    ("ix_audit_logs_id", "audit_logs", "id", False),
    ("ix_pricing_tiers_id", "pricing_tiers", "id", False),
    ("ix_pricing_tiers_tier_key", "pricing_tiers", "tier_key", True),
    ("ix_routing_rules_id", "routing_rules", "id", False),
    ("ix_user_links_id", "user_links", "id", False),
)


def _existing_indexes() -> set[tuple[str, str]]:
    """返回 {(表名, 索引名)} 现状，用于幂等跳过已存在的索引。

    offline（`--sql`）下无反射能力 → 返回空集，即「按都不存在渲染」，
    使 create_index 出现在离线 SQL 里（#106 验收 b）。
    """
    inspector = inspector_or_none()
    if inspector is None:
        return set()
    found: set[tuple[str, str]] = set()
    for table in inspector.get_table_names():
        for index in inspector.get_indexes(table):
            found.add((table, index["name"]))
    return found


def upgrade() -> None:
    existing = _existing_indexes()
    for name, table, column, unique in MISSING_INDEXES:
        if (table, name) in existing:
            continue
        op.create_index(op.f(name), table, [column], unique=unique)


def downgrade() -> None:
    existing = _existing_indexes()
    for name, table, _column, _unique in MISSING_INDEXES:
        if (table, name) not in existing:
            continue
        op.drop_index(op.f(name), table_name=table)
