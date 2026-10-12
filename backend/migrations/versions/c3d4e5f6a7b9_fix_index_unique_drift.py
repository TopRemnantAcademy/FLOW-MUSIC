"""fix index/unique drift: payment_orders & subscriptions (model ↔ migration 对齐)

Revision ID: c3d4e5f6a7b9
Revises: b9c1d2e3f4a5
Create Date: 2026-10-11 13:00:00.000000

背景（PG 驱动就绪实测暴露，Ref: #72 / #74）
--------------------------------------------
`alembic check` 在**全新库**上恒报漂移。根因不在新迁移，而在两处历史
「模型声明」与「建表迁移」互相不一致。sqlite 上漂移被两件事掩盖：
  · sqlite 把 UNIQUE INDEX 折叠进 autoindex（inspect 不回读）；
  · CI 只跑 sqlite（.github/workflows/ci.yml），`alembic check` 也就只在 sqlite 上跑。
PG 上两条索引都独立存在 → 立刻暴露。

本迁移修的三处
--------------
① `subscriptions.user_id`
   迁移 c9d0e1f2a3b4:73 建的是 **唯一约束** `uq_subscriptions_user_id`；
   c9d0e1f2a3b4:75 又建普通索引 `ix_subscriptions_user_id`；
   而模型 `unique=True, index=True` 期望的是「**唯一索引**」。
   → 与 `pricing_tiers.tier_key` 同一个病：unique + index 双写。
   收敛为「保留唯一约束、删除冗余普通索引」：
     · 与模型语义等价（唯一约束自带 btree，无需额外索引）；
     · sqlite 不支持 ALTER DROP CONSTRAINT（batch 重建表动数据，风险大），
       而 DROP INDEX 是安全操作 —— 故删除的必须是索引一侧。

② `subscriptions.user_id` 可空性
   迁移写 `nullable=False`，模型未写 nullable（默认 True）→ check 报 modify_nullable。
   业务上订阅必须挂用户 → 以迁移为准，模型补 `nullable=False`。

③ `payment_orders.idempotency_key` —— ⚠ **这不只是漂移，是真实缺陷**
   模型 `unique=True`，迁移只建了**普通索引**。实测：同一个 idempotency_key
   能被插入两次（sqlite 实测 count=2）。而 checkout 端点本来就接收
   `idempotency_key` 参数（billing.py:97/114）—— 调用方以为重试安全，实际
   **每次都建新订单**。本迁移补上唯一约束（改索引为 UNIQUE INDEX）。
   ⚠ 补唯一性前先做防护：若目标库已存在重复值，**响亮失败并给出修复指引**，
     绝不静默跳过（静默跳过 = 带着缺陷继续跑），也绝不静默删数据。

④ `payment_orders.id` / `subscriptions.id` 索引缺失
   模型写 `primary_key=True, index=True`，建表迁移未生成 create_index；
   同批次的 a7f3c91d4e62 统一补过其它表，但漏了这两张。

幂等性
------
全部操作按「存在性 / 现状」判断后才执行，可重复跑，且兼容 create_all 建的库。
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from migrations._offline import inspector_or_none, is_offline

# revision identifiers, used by Alembic.
revision: str = "c3d4e5f6a7b9"
down_revision: Union[str, Sequence[str], None] = "b9c1d2e3f4a5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_FILE_ANCHOR_NOTE = "本迁移与 b9c1d2e3f4a5 共同收敛「unique+index 双写」这一类漂移。"


def _indexes(table: str) -> dict[str, dict]:
    """现存索引 {名: 描述}。offline（`--sql`）下无反射 → 空 dict。

    空 dict 的效果：一律走「索引不存在」分支 → 渲染 create_index、
    跳过 drop_index（不产出破坏性语句），符合离线审计的语义。
    """
    insp = inspector_or_none()
    if insp is None:
        return {}
    return {ix["name"]: ix for ix in insp.get_indexes(table)}


def _unique_constraints(table: str) -> list[dict]:
    insp = inspector_or_none()
    if insp is None:
        return []
    getter = getattr(insp, "get_unique_constraints", None)
    return getter(table) if getter else []


def _assert_no_duplicate_idempotency_keys() -> None:
    """补唯一性之前先验数据。有重复 → 显式报错，不做静默处理。

    offline（`--sql`）无法查数据：离线渲染的产物不直接执行（由人在真实库上
    跑 online），故此处跳过数据校验 —— 校验仍会在真实 online 升级时生效。
    """
    if is_offline():
        return
    bind = op.get_bind()
    dup = bind.execute(
        sa.text(
            "SELECT idempotency_key, COUNT(*) AS n FROM payment_orders "
            "WHERE idempotency_key IS NOT NULL "
            "GROUP BY idempotency_key HAVING COUNT(*) > 1"
        )
    ).fetchall()
    if dup:
        detail = ", ".join(f"{k!r}×{n}" for k, n in dup[:5])
        raise RuntimeError(
            "payment_orders.idempotency_key 存在重复值，无法安全加唯一约束："
            f"{detail}（共 {len(dup)} 组）。请先人工去重（保留每组最早一条、其余改判为 "
            "failed 或归档），再重跑本迁移。**不要**用自动删数据的方式绕过。"
        )


def _add_missing_id_indexes() -> None:
    """④ 补 `payment_orders.id` / `subscriptions.id` 的索引。

    模型写的是 `Column(Integer, primary_key=True, index=True)`，而建表迁移
    c9d0e1f2a3b4 未生成对应 create_index。同批次的其它表由 a7f3c91d4e62
    「add missing primary-key indexes」统一补齐，但**漏了这两张** →
    alembic check 恒报这两条 add_index（同一类漂移的第 4 处）。
    """
    needed = (
        ("ix_payment_orders_id", "payment_orders"),
        ("ix_subscriptions_id", "subscriptions"),
    )
    for index_name, table in needed:
        if index_name not in _indexes(table):
            op.create_index(index_name, table, ["id"])


def upgrade() -> None:
    # ① + ③：先校验幂等键数据，避免带缺陷（或静默跳过）加约束
    _assert_no_duplicate_idempotency_keys()

    # ① subscriptions：删冗余普通索引，收敛到唯一约束
    #    （唯一约束 uq_subscriptions_user_id 由迁移 c9d0e1f2a3b4 建立，保留）
    subs_indexes = _indexes("subscriptions")
    if "ix_subscriptions_user_id" in subs_indexes:
        op.drop_index("ix_subscriptions_user_id", table_name="subscriptions")
    # 若该库没有唯一约束（例如某些历史库），用唯一索引兜住唯一性语义
    has_unique_sub = any(
        uc.get("column_names") == ["user_id"] for uc in _unique_constraints("subscriptions")
    )
    if not has_unique_sub and "ix_subscriptions_user_id" not in _indexes("subscriptions"):
        op.create_index(
            "ix_subscriptions_user_id", "subscriptions", ["user_id"], unique=True
        )

    # ③ payment_orders：普通索引 → 唯一索引（真正修掉重复建单缺陷）
    po_indexes = _indexes("payment_orders")
    existing = po_indexes.get("ix_payment_orders_idempotency_key")
    if existing is None:
        op.create_index(
            "ix_payment_orders_idempotency_key",
            "payment_orders",
            ["idempotency_key"],
            unique=True,
        )
    elif not existing.get("unique"):
        op.drop_index("ix_payment_orders_idempotency_key", table_name="payment_orders")
        op.create_index(
            "ix_payment_orders_idempotency_key",
            "payment_orders",
            ["idempotency_key"],
            unique=True,
        )

    # ④ 补两张表漏掉的 id 索引
    _add_missing_id_indexes()


def downgrade() -> None:
    po_indexes = _indexes("payment_orders")
    existing = po_indexes.get("ix_payment_orders_idempotency_key")
    if existing is not None and existing.get("unique"):
        op.drop_index("ix_payment_orders_idempotency_key", table_name="payment_orders")
        op.create_index(
            "ix_payment_orders_idempotency_key",
            "payment_orders",
            ["idempotency_key"],
            unique=False,
        )

    subs_indexes = _indexes("subscriptions")
    if "ix_subscriptions_user_id" not in subs_indexes:
        op.create_index("ix_subscriptions_user_id", "subscriptions", ["user_id"])

    for index_name, table in (
        ("ix_payment_orders_id", "payment_orders"),
        ("ix_subscriptions_id", "subscriptions"),
    ):
        if index_name in _indexes(table):
            op.drop_index(index_name, table_name=table)
