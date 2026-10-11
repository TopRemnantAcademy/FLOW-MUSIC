"""add song extend_type (Mureka /v1/song/extend 衔接方向)

2026-10-11：GAP-143/GAP-149 审计挖出的真缺陷修复（缺陷 1）。

Mureka `/v1/song/extend` 的 `extend_type` 是官方字段（tail=从尾部续 / head=从头部续，
model=mureka-8 时必填），`GenerateRequest.extend_type` 也早已有定义，但：
  · `Song` 没有这个列；
  · `SpawnReq` 也没有这个字段（前端传了会被 pydantic 拒绝）。

而后台任务 `_process_song` 是**从 DB 的 Song 重建** `GenerateRequest` 的
（同 `vocal_id` 的落地原因，见 b2c3d4e5f8a1），所以该值永远重建为 None
→ 供应商侧一律按 tail 处理 → **「从头部衔接」在 API 与 UI 上都不存在，
即使传了也会被静默丢弃**。这是比报错更糟的「假功能」形态（GAP-008/149 明令禁止）。

本列可空：NULL = 未指定（由 provider 按默认 tail 处理，保持既有行为不变）。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, Sequence[str], None] = "a7f3c91d4e62"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("songs", sa.Column("extend_type", sa.String(length=16), nullable=True))


def downgrade() -> None:
    op.drop_column("songs", "extend_type")
