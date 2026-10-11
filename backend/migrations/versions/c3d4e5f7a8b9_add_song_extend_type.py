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

[2026-10-11 迁移链收敛] 原 revision id 为 `c3d4e5f6a7b8`，与 GAP-002 的
`add_user_auth_gap002` **完全撞号**（两条迁移同 id 不同文件，alembic 报
"Revision ... is present more than once" 且 heads 里出现重复项）。此处改用
唯一 id `c3d4e5f7a8b9`；同时把 down_revision 从 `a7f3c91d4e62`（旧基线）
重链到 GAP-002 的 head，使 #97/#100/#96 三条并行迁移收敛为单链。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c3d4e5f7a8b9"
down_revision: Union[str, Sequence[str], None] = "c3d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("songs", sa.Column("extend_type", sa.String(length=16), nullable=True))


def downgrade() -> None:
    op.drop_column("songs", "extend_type")
