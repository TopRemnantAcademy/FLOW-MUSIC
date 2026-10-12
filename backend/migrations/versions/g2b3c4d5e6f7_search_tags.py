"""add search/discovery tags: tags + song_tags (GAP-014)

Revision ID: g2b3c4d5e6f7
Revises: g1a2b3c4d5e6
Create Date: 2026-10-12 12:00:00.000000

GAP-014 搜索/发现：结构化标签建模。

设计取舍（详见 app/models.py 的 Tag / SongTag 类注释与 app/search/ 各模块头部）：
- Tag 独立成表 + (category, value) 唯一：标签的价值在多对多与"同义词归并"，
  塞进 Song 的 Text 列会让「按标签浏览」退化成逐行 LIKE。
- SongTag.source（auto|enum|manual）：抽取规则会演进，没有来源就无法安全重算
  （会连用户手写的判断一起冲掉）。
- **不建向量表、不加 embedding 列**：评估结论见 app/search/similar.py 头部
  （1g/1cpu 共置机上向量检索的依赖与成本均不划算，本阶段用标签重合 + BM25）。
  臆造一个空 embedding 列并在接口上声称"支持语义检索"才是真正的坑。

[迁移链]
down_revision 挂 `g1a2b3c4d5e6`（GAP-011，main 收敛后的单链链尾，已在真实 PG 上
25 步升级实测通过）。**不改动任何既有 revision id / down_revision**。
⚠ revision id `g2b3c4d5e6f7` 在 versions/ 全目录仅本文件使用
（上轮 main 上 `c3d4e5f6a7b8` 被两个文件共用 → 双 head、干净库 upgrade 直接 FAILED）。

[方言兼容（#106 教训）]
- 本迁移**不调用** op.get_bind() / exec_driver_sql / batch_alter_table，
  故 offline `--sql` 渲染不受影响（仍需整链渲染通过，见 tests/test_pg_migration_dialect.py）。
- 布尔列不存在（本迁移不新增布尔列）；数值默认值一律 sa.text("0") 无引号写法。

[downgrade]
drop 顺序与建表相反（song_tags → tags）。两张表均为新表，无既有数据受影响。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "g2b3c4d5e6f7"
down_revision: Union[str, Sequence[str], None] = "g1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tags",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("category", sa.String(length=16), nullable=False),
        sa.Column("value", sa.String(length=32), nullable=False),
        sa.Column("label", sa.String(length=64), server_default="", nullable=False),
        sa.Column("aliases", sa.Text(), server_default="", nullable=False),
        sa.Column("usage_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("category", "value", name="uq_tags_category_value"),
    )
    op.create_index("ix_tags_id", "tags", ["id"], unique=False)
    op.create_index("ix_tags_category", "tags", ["category"], unique=False)

    op.create_table(
        "song_tags",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("song_id", sa.Integer(), nullable=False),
        sa.Column("tag_id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=8), server_default="auto", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True
        ),
        sa.ForeignKeyConstraint(["song_id"], ["songs.id"]),
        sa.ForeignKeyConstraint(["tag_id"], ["tags.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("song_id", "tag_id", "source", name="uq_song_tags_song_tag_source"),
    )
    op.create_index("ix_song_tags_id", "song_tags", ["id"], unique=False)
    op.create_index("ix_song_tags_song_id", "song_tags", ["song_id"], unique=False)
    op.create_index("ix_song_tags_tag_id", "song_tags", ["tag_id"], unique=False)
    op.create_index("ix_song_tags_tag_song", "song_tags", ["tag_id", "song_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_song_tags_tag_song", table_name="song_tags")
    op.drop_index("ix_song_tags_tag_id", table_name="song_tags")
    op.drop_index("ix_song_tags_song_id", table_name="song_tags")
    op.drop_index("ix_song_tags_id", table_name="song_tags")
    op.drop_table("song_tags")

    op.drop_index("ix_tags_category", table_name="tags")
    op.drop_index("ix_tags_id", table_name="tags")
    op.drop_table("tags")
