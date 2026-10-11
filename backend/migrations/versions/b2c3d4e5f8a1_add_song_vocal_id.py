"""add song vocal_id (Mureka 音色克隆 ID)

2026-09-12：阶段 2 补漏。/v1/vocal/clone 产出的 vocal_id 此前没有落库路径，
导致 generate 走不通音色克隆——因为后台任务 `_process_song` 是从 DB 的 Song
重建 GenerateRequest 的，不在库里就取不到（easy/instrumental 属一次性参数，
由路由直接传给后台任务，不占用此列）。

⚠️ mureka-o2 不支持 vocal_id，后端会在构造 payload 时自动剔除并留 warning，
故本列允许为空，且**不应**因为填了就假定一定生效。
"""

from alembic import op
import sqlalchemy as sa

revision = "b2c3d4e5f8a1"
down_revision = "a1b2c3d4e5f7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "songs",
        sa.Column("vocal_id", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("songs", "vocal_id")
