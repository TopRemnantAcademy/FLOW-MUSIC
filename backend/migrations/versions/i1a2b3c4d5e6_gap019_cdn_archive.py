"""GAP-019 CDN/对象存储归档：songs.archived_at + songs.storage_class

Revision ID: i1a2b3c4d5e6
Revises: h1a2b3c4d5e6
Create Date: 2026-10-12 16:30:00.000000

GAP-019（CDN/对象存储 · 主播放链路接入 + 归档策略）中**唯一**的 schema 变更：
给 songs 增加两个归档/保留元数据列，让「归档」成为可查询、可断言、可回热的事实，
而不是只存在于 S3 控制台里的隐性状态。

1. `songs.archived_at`（timestamptz，可空）
   = 音频对象被迁移到归档/低频存储类别的时刻。NULL = 未归档。
   用时间戳而非布尔：既标记状态、又支持「归档时长」推导（老歌回热策略）。
2. `songs.storage_class`（varchar(32)，非空，默认 ""）
   = 该曲音频对象在桶里的存储类别（STANDARD / STANDARD_IA / ...）。"" = 桶默认。

⚠ 本迁移**不新增布尔列**（故不涉及 sa.true()/sa.false() 方言坑）；
   `storage_class` 的 server_default 是**字符串**默认值（varchar 合法，无引号数字闸门不适用）；
   `archived_at` 无 server_default（NULL = 未归档，正是期望的初始态）。

[迁移链]
down_revision 挂 `h1a2b3c4d5e6`（GAP-016，main 收敛后的单链链尾，已在真实 PG 上
升级实测通过）。**不改动任何既有 revision id / down_revision**。
⚠ revision id `i1a2b3c4d5e6` 在 versions/ 全目录仅本文件使用（全仓唯一）。

[方言兼容（#106 教训）]
- 不调用 op.get_bind() / exec_driver_sql / batch_alter_table → offline `--sql` 渲染不受影响。
- 不写带引号数值默认值、不写 SQLite-only 布尔默认值字面量。

[downgrade]
逆序删两列。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "i1a2b3c4d5e6"
down_revision: Union[str, Sequence[str], None] = "h1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("songs", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "songs",
        sa.Column("storage_class", sa.String(length=32), server_default="", nullable=True),
    )


def downgrade() -> None:
    op.drop_column("songs", "storage_class")
    op.drop_column("songs", "archived_at")
