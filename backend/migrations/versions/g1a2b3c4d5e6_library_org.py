"""add library org: playlists / playlist_songs / folders / trash_keep_rules + songs.deleted_at (GAP-011)

Revision ID: g1a2b3c4d5e6
Revises: a9b0c1d2e3f4
Create Date: 2026-10-12 10:00:00.000000

GAP-011 库组织：播放列表 / 文件夹 / 回收站。

设计取舍见 app/models.py 对应类注释，结论：
- Playlist 与 Folder **分成两个模型**（不给 Playlist 加 type 字段）：水合方式、
  归属基数、删除语义三处都不同，用 type 区分要在每个使用点加 if。
- PlaylistSong 用**稀疏整数 position**（拖拽只写一行，不整体 +1）。
- 回收站只加 `songs.deleted_at`（软删）+ 一张保留策略表，**不建 trash 副表**。

[迁移链]
down_revision 挂 `a9b0c1d2e3f4`（GAP-010，main 收敛后的单链链尾）。
⚠ revision id 自查：`g1a2b3c4d5e6` 在 versions/ 全目录**仅本文件**使用
（上轮 main 上 `c3d4e5f6a7b8` 被两个文件共用 → 双 head、干净库 upgrade 直接 FAILED）。
落地前用 `grep -rl g1a2b3c4d5e6 migrations/versions | wc -l` == 1 复核。

[兼容性]
- 新增四张表 + songs 一列（可空，无 server_default）；既有行 deleted_at 恒为 NULL
  = 未删除，升级即安全，无需回填。
- `songs.deleted_at` 建索引：列表/广场/回收站查询面都要按它过滤（IS NULL），
  数据量上来后全表扫描会退化，索引是必要开销而非装饰。

[downgrade]
drop 顺序与建表相反。drop_column(deleted_at) 在 sqlite 上由 Alembic 自动走
batch 重建，无需手写 batch_alter_table。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "g1a2b3c4d5e6"
down_revision: Union[str, Sequence[str], None] = "a9b0c1d2e3f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("songs", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_songs_deleted_at", "songs", ["deleted_at"], unique=False)

    op.create_table(
        "playlists",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("cover_url", sa.Text(), nullable=True),
        sa.Column("is_public", sa.Boolean(), server_default="false", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_playlists_id", "playlists", ["id"], unique=False)
    op.create_index("ix_playlists_user_id", "playlists", ["user_id"], unique=False)

    op.create_table(
        "playlist_songs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("playlist_id", sa.Integer(), nullable=False),
        sa.Column("song_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True
        ),
        sa.ForeignKeyConstraint(["playlist_id"], ["playlists.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["song_id"], ["songs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("playlist_id", "song_id", name="uq_playlist_songs_playlist_song"),
    )
    op.create_index("ix_playlist_songs_id", "playlist_songs", ["id"], unique=False)
    op.create_index(
        "ix_playlist_songs_playlist_id", "playlist_songs", ["playlist_id"], unique=False
    )
    op.create_index("ix_playlist_songs_song_id", "playlist_songs", ["song_id"], unique=False)
    op.create_index(
        "ix_playlist_songs_playlist_position",
        "playlist_songs",
        ["playlist_id", "position"],
        unique=False,
    )

    op.create_table(
        "folders",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "name", name="uq_folders_user_name"),
    )
    op.create_index("ix_folders_id", "folders", ["id"], unique=False)
    op.create_index("ix_folders_user_id", "folders", ["user_id"], unique=False)

    op.create_table(
        "folder_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("folder_id", sa.Integer(), nullable=False),
        sa.Column("item_type", sa.String(length=16), nullable=False),
        sa.Column("item_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True
        ),
        sa.ForeignKeyConstraint(["folder_id"], ["folders.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("folder_id", "item_type", "item_id", name="uq_folder_items_member"),
    )
    op.create_index("ix_folder_items_id", "folder_items", ["id"], unique=False)
    op.create_index("ix_folder_items_folder_id", "folder_items", ["folder_id"], unique=False)

    op.create_table(
        "trash_keep_rules",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("keep_days", sa.Integer(), server_default="30", nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_trash_keep_rules_id", "trash_keep_rules", ["id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_trash_keep_rules_id", table_name="trash_keep_rules")
    op.drop_table("trash_keep_rules")

    op.drop_index("ix_folder_items_folder_id", table_name="folder_items")
    op.drop_index("ix_folder_items_id", table_name="folder_items")
    op.drop_table("folder_items")

    op.drop_index("ix_folders_user_id", table_name="folders")
    op.drop_index("ix_folders_id", table_name="folders")
    op.drop_table("folders")

    op.drop_index("ix_playlist_songs_playlist_position", table_name="playlist_songs")
    op.drop_index("ix_playlist_songs_song_id", table_name="playlist_songs")
    op.drop_index("ix_playlist_songs_playlist_id", table_name="playlist_songs")
    op.drop_index("ix_playlist_songs_id", table_name="playlist_songs")
    op.drop_table("playlist_songs")

    op.drop_index("ix_playlists_user_id", table_name="playlists")
    op.drop_index("ix_playlists_id", table_name="playlists")
    op.drop_table("playlists")

    op.drop_index("ix_songs_deleted_at", table_name="songs")
    op.drop_column("songs", "deleted_at")
