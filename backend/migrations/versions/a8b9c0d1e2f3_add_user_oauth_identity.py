"""add users.oauth_provider / oauth_sub + unique identity constraint (GAP-003)

Revision ID: a8b9c0d1e2f3
Revises: b8e4f1a2c3d7
Create Date: 2026-09-26 10:00:00.000000

GAP-003（Google / Kakao / Naver 授权码登录）需要把 provider 侧的稳定身份
落库，否则每次登录都要靠邮箱猜账号 —— 而邮箱在 provider 侧可变，且
未验证邮箱本就不该被当作身份（见 app/oauth/service.py 的合并策略）。

两列皆 nullable：既有邮箱账号不受影响（双 NULL 即「未绑定 OAuth」）。

`uq_users_oauth_identity` 唯一约束是**必要的**，不是锦上添花：
并发首登时两个请求会同时通过「按 (provider,sub) 查不到 → 插入」的应用层检查，
唯一约束是最后一道防线（service 捕获 IntegrityError 后重查即得胜者）。
SQLite/Postgres 的唯一索引忽略 NULL，故不影响纯邮箱账号。

[2026-10-11 迁移链收敛] `down_revision` 由 `c3d4e5f6a7b8`（GAP-002）重链到
`b8e4f1a2c3d7`（本轮收敛后的链尾），使本迁移并入 main 的**单链**而非新增一个 head。
本迁移只动 `users` 表，与链上各步操作的表不相交；`batch_alter_table("users")` 在
SQLite 下会重建 `users`，而前一步 `b8e4f1a2c3d7` 已先移除 `user_links → users`
的 FK，故重建不会遗留悬空引用（升级/回滚往返已实测）。
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a8b9c0d1e2f3"
down_revision: Union[str, Sequence[str], None] = "b8e4f1a2c3d7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ⚠ 用 batch_alter_table 而非裸 op.add_column/create_unique_constraint：
    #   SQLite 方言**不支持** ALTER 约束（原报错 "No support for ALTER of constraints
    #   in SQLite dialect"），batch 模式以「建新表→拷数据→换名」实现，SQLite/Postgres 通用。
    #   （测试库与开发默认库都是 SQLite，这条不是可选项。）
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {uc["name"] for uc in inspector.get_unique_constraints("users")}
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(sa.Column("oauth_provider", sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column("oauth_sub", sa.String(length=255), nullable=True))
        # 幂等：若目标库曾用 Base.metadata.create_all() 建表（开发兜底路径，见 app/db.py），
        # 该约束可能已存在，重复创建会报 "already exists" → 先查再建。
        if "uq_users_oauth_identity" not in existing:
            batch_op.create_unique_constraint(
                "uq_users_oauth_identity", ["oauth_provider", "oauth_sub"]
            )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    existing = {uc["name"] for uc in inspector.get_unique_constraints("users")}
    with op.batch_alter_table("users", schema=None) as batch_op:
        if "uq_users_oauth_identity" in existing:
            batch_op.drop_constraint("uq_users_oauth_identity", type_="unique")
        batch_op.drop_column("oauth_sub")
        batch_op.drop_column("oauth_provider")
