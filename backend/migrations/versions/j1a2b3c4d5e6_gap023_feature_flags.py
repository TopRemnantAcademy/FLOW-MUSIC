"""GAP-023 运营：feature_flags 表 + users 封禁列

Revision ID: j1a2b3c4d5e6
Revises: i1a2b3c4d5e6
Create Date: 2026-10-12 17:00:00.000000

GAP-023（admin 运营 UI 扩展）中**唯一**的 schema 变更，两件事：

1. 新表 `feature_flags`（灰度发布的最小机制）。
   [为什么是新建表而不是给某张表加列] 开关是**运维对象**（admin 改、前端读），
   不是歌曲或用户的属性。挂到既有表上要么语义错位（"这首歌是否启用 stem 导出"），
   要么退化成一行的 kv 表 —— 那就是本表。

2. `users` 三个封禁列：`is_banned` / `banned_at` / `ban_reason`。
   [为什么不复用 locked_until] 锁定是**自动、临时**（失败次数触发 → 到期自动解锁），
   封禁是**人工、持续**（运营决定）。复用 locked_until 的后果是"锁定期满自动解除封禁"。
   财务导出（结算/发票 CSV）只用既有 payment_orders / subscriptions，不新增 schema。

[downgrade]
逆序回退：先删 users 三列，再删 feature_flags（不触碰任何既有数据列，回退零风险）。

[方言兼容（#106 教训）]
- 布尔列 `enabled` 的 server_default 用 `sa.false()`，**不写** "0"/"1"/"false"：
  PG 上 `DEFAULT '0'::boolean` 会 DatatypeMismatch，而 SQLite 恰好能容忍，
  这个坑在 sqlite 上永远测不出来。
- `rollout` 的默认值是**整数列**，写字符串 `"0"` 会被 SQLAlchemy 渲染成
  `DEFAULT '0'` 并带类型上下文 → 整数列不受引号影响（models.py 里同样传字符串，
  与仓库既有 `usage_count` 等列写法一致），此处统一为 `sa.text("0")` 以杜绝歧义。
- 不调用 op.get_bind() / exec_driver_sql / batch_alter_table → offline `--sql` 渲染不受影响。

[迁移链]
down_revision 挂 `i1a2b3c4d5e6`（GAP-019，main 当时的唯一 head）。
⚠ revision id `j1a2b3c4d5e6` 在 versions/ 全目录仅本文件使用（全仓已核唯一）。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "j1a2b3c4d5e6"
down_revision: Union[str, Sequence[str], None] = "i1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "feature_flags",
        sa.Column("id", sa.Integer(), primary_key=True, index=True, nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("description", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("rollout", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("allow_user_ids", sa.Text(), nullable=False, server_default=""),
        sa.Column("deny_user_ids", sa.Text(), nullable=False, server_default=""),
        sa.Column("min_tier", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    # ⚠ 索引形态必须与 ORM 声明**逐条对齐**，否则 alembic check 报漂移：
    #   · key       = unique=True + index=True → 一条 UNIQUE INDEX `ix_feature_flags_key`
    #                 （不写 UniqueConstraint，避免 PG 上"约束 + 索引"两条并存的老坑，
    #                  见 models.py:PricingTier.tier_key 注释）
    #   · id        = primary_key=True + index=True → `ix_feature_flags_id`
    #   · updated_at/created_at 无 index=True → 无索引
    # 这三条是 autogenerate 比对的口径，改模型必须同步改这里。
    op.create_index("ix_feature_flags_key", "feature_flags", ["key"], unique=True)

    # ---- users 封禁列（GAP-023 用户管理）----
    # ⚠ 布尔列 server_default 用 sa.false()：PG 上 `DEFAULT '0'::boolean` 会
    #   DatatypeMismatch，而 SQLite 恰好容忍 —— 这个坑在 sqlite 上永远测不出来。
    op.add_column(
        "users",
        sa.Column("is_banned", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("users", sa.Column("banned_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "users",
        sa.Column("ban_reason", sa.String(length=500), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("users", "ban_reason")
    op.drop_column("users", "banned_at")
    op.drop_column("users", "is_banned")
    op.drop_index("ix_feature_flags_key", table_name="feature_flags")
    op.drop_table("feature_flags")
