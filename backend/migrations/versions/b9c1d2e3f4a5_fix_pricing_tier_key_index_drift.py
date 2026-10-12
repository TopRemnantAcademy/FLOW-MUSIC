"""drop redundant unique index on pricing_tiers.tier_key (drift fix)

Revision ID: b9c1d2e3f4a5
Revises: a7f3c91d4e62
Create Date: 2026-10-11 12:00:00.000000

背景（PG 驱动就绪实测暴露，Ref: #72 / #74）
--------------------------------------------
`pricing_tiers.tier_key` 在 `app/models.py` 曾写为
`Column(String(32), unique=True, nullable=False, index=True)`。两个 flag 同时给，
SQLAlchemy 会建 **两条唯一索引**：

  · `pricing_tiers_tier_key_key`  ← unique=True 产生的 UNIQUE CONSTRAINT
  · `ix_pricing_tiers_tier_key`   ← index=True 产生的 UNIQUE INDEX

而真正建表的迁移 `c2d3e4f5a6b7_add_backend_config_tables.py:55` 只写了
`unique=True`，随后的 `a7f3c91d4e62` 又按模型补建了 `ix_pricing_tiers_tier_key`。
于是一个唯一性被两条索引重复承载。

为什么以前没发现
----------------
sqlite 把 `CREATE UNIQUE INDEX` 折叠进 `sqlite_autoindex_pricing_tiers_1`，
`inspect()` 不把它当独立索引回读 → `alembic check` 在 sqlite 上恒"无漂移"。
PG 上两条索引都独立存在 → 暴露。CI 只跑 sqlite，故该漂移长期隐身。

本迁移的做法
------------
保留 **UNIQUE CONSTRAINT**（`pricing_tiers_tier_key_key`，由 unique=True 语义拥有，
也是唯一性约束的规范载体），删除冗余的独立唯一索引
`ix_pricing_tiers_tier_key`，与 `app/models.py` 同步收敛为 `unique=True`。

⚠ 为什么不是反过来（删 constraint 保 index）：
  删除 UNIQUE CONSTRAINT 需要 `op.drop_constraint`，而 sqlite 不支持
  ALTER TABLE DROP CONSTRAINT（需 batch 重建表，动到已有数据的风险更大）。
  删索引在两种方言上都是同一条 ALTER 级安全操作，且**不削弱唯一性**——
  唯一性由保留下来的 constraint 继续保证，查询性能由该 constraint 自带的
  btree 索引承担（无需额外索引）。
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from migrations._offline import inspector_or_none

# revision identifiers, used by Alembic.
revision: str = "b9c1d2e3f4a5"
down_revision: Union[str, Sequence[str], None] = "a7f3c91d4e62"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_REDUNDANT_INDEX = "ix_pricing_tiers_tier_key"
_TABLE = "pricing_tiers"


def _has_index(name: str) -> bool:
    """该索引是否已存在。

    offline（`--sql`）下无反射 → 视为「不存在」，从而渲染出 create_index
    而跳过 drop_index（不产出破坏性语句）。
    """
    inspector = inspector_or_none()
    if inspector is None:
        return False
    return any(ix["name"] == name for ix in inspector.get_indexes(_TABLE))


def upgrade() -> None:
    # 幂等：若目标库（例如 create_all 建库）本就没有该索引，直接跳过
    if _has_index(_REDUNDANT_INDEX):
        op.drop_index(_REDUNDANT_INDEX, table_name=_TABLE)


def downgrade() -> None:
    # 还原为历史状态（两条索引并存）。unique=True 语义由 constraint 承担，
    # 此索引纯粹是回滚兼容，故同样幂等。
    if not _has_index(_REDUNDANT_INDEX):
        op.create_index(_REDUNDANT_INDEX, _TABLE, ["tier_key"], unique=True)
