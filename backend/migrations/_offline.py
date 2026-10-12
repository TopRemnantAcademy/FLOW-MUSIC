"""离线（`--sql`）模式下的迁移辅助。

背景（#106 / #74）
-----------------
`alembic upgrade head --sql` 走 offline 模式：此时 `op.get_bind()` 返回
`MockConnection`，**没有** inspector/反射能力，`sa.inspect()` 直接抛
`NoInspectionAvailable`。历史上有 6 处迁移用 `sa.inspect(op.get_bind())`
做「存在性幂等判断」（补索引 / 删冗余索引 / 幂等建约束），这条路在
offline 渲染下会断在中途，导致 PG 离线渲染产不出可审阅的 SQL。

策略：offline 下**放弃反射**，按「对象尚不存在」渲染 DDL。对幂等判断而言
这是安全默认值：
  · 「不存在才 create_index」→ offline 会渲染 create_index（正是要审阅的目标）；
  · 「存在才 drop_index」→ offline 跳过 drop（不渲染破坏性语句）。
离线 SQL 本就不可盲跑（无真实库可查），其用途是审计「这条链在目标方言上
生成什么」。真正执行走 online，反射照旧。
"""

from __future__ import annotations

from typing import Optional

import sqlalchemy as sa
from alembic import op


def is_offline() -> bool:
    """当前是否处于 offline（`--sql`）渲染模式。"""
    return bool(getattr(op.get_context(), "as_sql", False))


def inspector_or_none() -> Optional[sa.Inspector]:
    """online 返回 Inspector；offline 返回 None（调用方据此走「不存在」分支）。"""
    if is_offline():
        return None
    return sa.inspect(op.get_bind())
