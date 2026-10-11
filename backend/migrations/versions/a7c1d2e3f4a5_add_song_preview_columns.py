"""add songs.preview_audio_url / preview_seconds (freemium 试听门控)

Revision ID: a7c1d2e3f4a5
Revises: f6a7b8c9d0e1
Create Date: 2026-09-04 08:10:00.000000

业务背景（用户 2026-09-04 确认）：
- 生成始终是**完整歌曲**（Mureka 无 duration 参数，见 orchestrator/mureka.py 注释）。
- 门控只发生在分发环节：未订阅者拿到「前 N 秒试听片段」链接，订阅者拿完整音频且可下载。
- 故 songs 增加两个列：preview_audio_url（片段链接）、preview_seconds（片段时长）。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a7c1d2e3f4a5"
down_revision: Union[str, Sequence[str], None] = "f6a7b8c9d0e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "songs", sa.Column("preview_audio_url", sa.Text(), server_default="", nullable=True)
    )
    op.add_column("songs", sa.Column("preview_seconds", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("songs", "preview_seconds")
    op.drop_column("songs", "preview_audio_url")
