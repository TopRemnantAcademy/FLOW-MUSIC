"""GAP-016 内容权利/审核：songs.license + content_reports + audit_logs 审核动作字段

Revision ID: h1a2b3c4d5e6
Revises: g2b3c4d5e6f7
Create Date: 2026-10-12 15:00:00.000000

GAP-016 内容权利 / 审核强化（三块 schema 变更，一次迁移）：
1. `songs.license`（commercial / non_commercial，默认 non_commercial）：
   生成时选择，随分享/下载（GAP-013/GAP-010）联动展示。
   默认取保守值 non_commercial —— 未显式声明商用的产物不得被下游当可商用素材。
2. `content_reports`：举报 / DMCA 侵权通知（外部权利人可无注册账号，故
   reporter_user_id 可空、reporter_email 单列）。
3. `audit_logs` 增补审核动作结构化列（action / target_type / target_id / detail）：
   批准/驳回（含批量）落一条可独立查询、可追责的记录，而不是只靠访问日志。

[迁移链]
down_revision 挂 `g2b3c4d5e6f7`（GAP-014，main 收敛后的单链链尾，已在真实 PG 上
升级实测通过）。**不改动任何既有 revision id / down_revision**。
⚠ revision id `h1a2b3c4d5e6` 在 versions/ 全目录仅本文件使用（全仓唯一）。

[方言兼容（#106 教训）]
- 本迁移**不调用** op.get_bind() / exec_driver_sql / batch_alter_table，
  故 offline `--sql` 渲染不受影响（整链渲染仍须通过，见 test_pg_migration_dialect.py）。
- 布尔列：本迁移**不新增布尔列**。
- 数值默认值一律 sa.text("0") 无引号写法；字符串默认值用 server_default="..."（varchar 合法）。

[downgrade]
逆序：删 audit_logs 新增列 → drop content_reports → 删 songs.license。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "h1a2b3c4d5e6"
down_revision: Union[str, Sequence[str], None] = "g2b3c4d5e6f7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1) songs.license —— 内容授权类型（保守默认 non_commercial）
    op.add_column(
        "songs",
        sa.Column(
            "license",
            sa.String(length=16),
            server_default="non_commercial",
            nullable=False,
        ),
    )

    # 2) content_reports —— 举报 / DMCA
    op.create_table(
        "content_reports",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("song_id", sa.Integer(), nullable=False),
        sa.Column("reporter_user_id", sa.Integer(), nullable=True),
        sa.Column("reporter_email", sa.String(length=255), server_default="", nullable=True),
        sa.Column("reason", sa.String(length=32), server_default="other", nullable=False),
        sa.Column("detail", sa.Text(), server_default="", nullable=True),
        sa.Column("status", sa.String(length=16), server_default="open", nullable=False),
        sa.Column("resolution", sa.String(length=32), server_default="", nullable=True),
        sa.Column("resolved_by", sa.String(length=64), server_default="", nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True
        ),
        sa.ForeignKeyConstraint(["song_id"], ["songs.id"]),
        sa.ForeignKeyConstraint(["reporter_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_content_reports_id", "content_reports", ["id"], unique=False)
    op.create_index("ix_content_reports_song_id", "content_reports", ["song_id"], unique=False)
    op.create_index(
        "ix_content_reports_reporter_user_id", "content_reports", ["reporter_user_id"], unique=False
    )
    op.create_index("ix_content_reports_status", "content_reports", ["status"], unique=False)

    # 3) audit_logs —— 审核动作结构化字段（全部 nullable，兼容既有访问日志行）
    op.add_column("audit_logs", sa.Column("action", sa.String(length=32), nullable=True))
    op.add_column("audit_logs", sa.Column("target_type", sa.String(length=16), nullable=True))
    op.add_column("audit_logs", sa.Column("target_id", sa.Integer(), nullable=True))
    op.add_column("audit_logs", sa.Column("detail", sa.Text(), nullable=True))
    op.create_index("ix_audit_logs_action", "audit_logs", ["action"], unique=False)
    op.create_index("ix_audit_logs_target_id", "audit_logs", ["target_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_audit_logs_target_id", table_name="audit_logs")
    op.drop_index("ix_audit_logs_action", table_name="audit_logs")
    op.drop_column("audit_logs", "detail")
    op.drop_column("audit_logs", "target_id")
    op.drop_column("audit_logs", "target_type")
    op.drop_column("audit_logs", "action")

    op.drop_index("ix_content_reports_status", table_name="content_reports")
    op.drop_index("ix_content_reports_reporter_user_id", table_name="content_reports")
    op.drop_index("ix_content_reports_song_id", table_name="content_reports")
    op.drop_index("ix_content_reports_id", table_name="content_reports")
    op.drop_table("content_reports")

    op.drop_column("songs", "license")
