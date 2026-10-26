"""GAP-021 生成完成通知：users.notify_generation_done_enabled 偏好开关

Revision ID: l1a2b3c4d5e6
Revises: k1a2b3c4d5e6
Create Date: 2026-10-26 09:00:00.000000

GAP-021（通知通道）收尾：在「已具备发送能力」（notify_generation_done /
notify_share / notify_system 三函数 + 订阅/自检端点）之后，补上「是否愿意被通知」
的**用户级偏好开关**，并把它接线到生成完成回调（routers/songs.py:_finalize_song）。

为什么是 users 上的一个布尔列，而不是新表 / FeatureFlag：
  · 这是「账户是否愿意收生成完成通知」的个人偏好，与具体用户强绑定、单行即够；
  · 它**不是**「Web Push 订阅端点」（设备凭据）——后者才是 GAP-021 刻意不入业务表、
    由 test_store_does_not_touch_orm_metadata 锁死的东西。两者语义不同，特此澄清，
    以免日后误读那条断言为「通知相关字段一律不许进 User」。
  · 也不用 FeatureFlag：FeatureFlag 是**运维灰度**（按百分比/套餐对全体生效），
    这里是**个人意图**，二者维度正交。

默认关（opt-in）：避免无条件邮件骚扰 + 未配置 SES 时的无效发信成本。

[方言兼容（沿用 j1a2b3c4d5e6 GAP-023 教训）]
  · 布尔列 server_default 用 `sa.false()`，**不写** "0"/"1"/"false"：PG 上
    `DEFAULT '0'::boolean` 会 DatatypeMismatch，而 SQLite 恰好容忍 → 这个坑在
    sqlite 上永远测不出来，必须以 sa.false() 写。
  · 不调用 op.get_bind() / exec_driver_sql / batch_alter_table → offline `--sql`
    渲染不受影响。

[迁移链]
down_revision 挂 `k1a2b3c4d5e6`（GAP-022，main 当时的唯一 head）。
⚠ revision id `l1a2b3c4d5e6` 在 versions/ 全目录仅本文件使用（已核唯一）。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "l1a2b3c4d5e6"
down_revision: Union[str, Sequence[str], None] = "k1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ⚠ 布尔列 server_default 用 sa.false()：PG 上 `DEFAULT '0'::boolean` 会
    #   DatatypeMismatch，而 SQLite 恰好容忍 —— 这个坑在 sqlite 上永远测不出来。
    op.add_column(
        "users",
        sa.Column(
            "notify_generation_done_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "notify_generation_done_enabled")
