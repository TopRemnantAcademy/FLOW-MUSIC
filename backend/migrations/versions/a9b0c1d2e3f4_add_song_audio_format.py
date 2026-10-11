"""add songs.audio_format / audio_wav_url / audio_flac_url (GAP-010 音频格式)

Revision ID: a9b0c1d2e3f4
Revises: a8b9c0d1e2f3
Create Date: 2026-10-11 15:00:00.000000

GAP-010：音频质量/格式选择（mp3 / wav / flac）。

**实证前提（拒绝臆造，2026-10-11 逐字核对 platform.mureka.ai OpenAPI spec）**：
Mureka 的 `POST /v1/song/generate` 请求体只有 lyrics/model/n/prompt/gender/
reference_id/vocal_id/melody_id/stream，**没有** quality / format / bitrate / duration
任何入参（全站 spec 中 "bitrate"、"quality" 出现 0 次）。响应 `choices[]` 反而同时回传
`url`(mp3) / `flac_url` / `wav_url` 三种链接。
→ 结论：格式选择**只能落在下载侧**（生成侧无可控参数）。故：

- `audio_format`：下载时选定的格式 mp3|wav|flac（默认 mp3）。不是「生成质量参数」。
- `audio_wav_url` / `audio_flac_url`：上游无损链接，生成完成时落库。
- ⚠️ **不加** `quality_kbps`：上游不能按码率出音频，加列 = 制造假字段（本项目禁项）。

三列皆带默认值/可空，对既有行无需回填，升级即安全。

[2026-10-11 迁移链] down_revision 挂 `a8b9c0d1e2f3`（GAP-003 OAuth，main 收敛后的
单链链尾）。本迁移只动 `songs` 表，与链上各步不相交。

⚠️ 本分支同时修掉 main 上的**重复 revision 撞号**（`c3d4e5f6a7b8` 被
`add_user_auth_gap002.py` 与 `fix_index_unique_drift.py` 两个文件共用 → `alembic heads`
双头、干净库 `upgrade head` 直接报 "Multiple head revisions"）：
把后者改名 `c3d4e5f6a7b9` 并线性并入单链（详见该文件头注释）。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a9b0c1d2e3f4"
down_revision: Union[str, Sequence[str], None] = "a8b9c0d1e2f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "songs",
        sa.Column("audio_format", sa.String(length=8), server_default="mp3", nullable=False),
    )
    op.add_column("songs", sa.Column("audio_wav_url", sa.Text(), server_default="", nullable=True))
    op.add_column("songs", sa.Column("audio_flac_url", sa.Text(), server_default="", nullable=True))


def downgrade() -> None:
    op.drop_column("songs", "audio_flac_url")
    op.drop_column("songs", "audio_wav_url")
    op.drop_column("songs", "audio_format")
