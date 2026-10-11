"""add song lyrics_sync (Mureka 逐字时间轴 JSON 字符串)

2026-09-10：Mureka 衔接考察发现 mureka-9 返回 choices[].lyrics_sections 含 section/line/word
三级 ms 时间戳，是 synced lyrics 现成数据源。先前代码未捕获，付费数据被丢弃。新增列 JSON 存
该结构，详情接口按需曝光；列表接口不返回以控制包体。
"""

from alembic import op
import sqlalchemy as sa

revision = "a1b2c3d4e5f7"
down_revision = "7f0c40c8bae2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "songs",
        sa.Column("lyrics_sync", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("songs", "lyrics_sync")
