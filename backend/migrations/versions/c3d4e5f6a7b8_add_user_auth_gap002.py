"""add user auth columns for GAP-002 (email verify / password reset / MFA)

Revision ID: c3d4e5f6a7b8
Revises: a7f3c91d4e62
Create Date: 2026-10-11 12:00:00.000000

GAP-002：邮箱验证 / 密码重置 / MFA / 失败锁定所需的 9 个 User 列。

- 全部列可空或带 server_default，故对既有行**无需数据回填**，升级即安全。
- token 列只存 sha256 摘要（64 位十六进制），明文永不落库。
- `email_verified` / `mfa_enabled` / `failed_logins` 带 server_default，
  既有行升级后即为「未验证 / 未启用 MFA / 0 次失败」—— 与注册新用户的语义一致。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, Sequence[str], None] = "c3d4e5f6a7b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (列名, 类型, 是否可空, server_default) —— 与 app/models.py 严格一一对应，
# 以通过 tests/test_alembic.py 的「模型 ↔ 迁移零漂移」断言。
NEW_COLUMNS: Sequence[tuple[str, sa.types.TypeEngine, bool, object]] = (
    ("email_verified", sa.Boolean(), False, sa.false()),
    ("verify_token", sa.String(64), True, None),
    ("verify_sent_at", sa.DateTime(timezone=True), True, None),
    ("reset_token", sa.String(64), True, None),
    ("reset_sent_at", sa.DateTime(timezone=True), True, None),
    ("mfa_secret", sa.String(64), True, None),
    ("mfa_enabled", sa.Boolean(), False, sa.false()),
    ("failed_logins", sa.Integer(), False, sa.text("0")),
    ("locked_until", sa.DateTime(timezone=True), True, None),
)


def upgrade() -> None:
    for name, type_, nullable, server_default in NEW_COLUMNS:
        op.add_column(
            "users",
            sa.Column(name, type_, nullable=nullable, server_default=server_default),
        )


def downgrade() -> None:
    for name, _type, _nullable, _sd in reversed(NEW_COLUMNS):
        op.drop_column("users", name)
