"""GAP-022 开放平台：developer_tokens + webhook_endpoints + webhook_deliveries

Revision ID: k1a2b3c4d5e6
Revises: j1a2b3c4d5e6
Create Date: 2026-10-13 10:00:00.000000

GAP-022（开发者 API / Webhook / SDK）中**唯一**的 schema 变更：三张新表，
**零改动既有表**。开放平台是增量能力，既有 JWT / songs / billing 路径完全不受影响。

1. `developer_tokens` —— 开发者 API 凭证。
   [为什么不复用 users 上的列] 一个用户要能同时持有多枚凭证（CI 一枚、本地脚本一枚），
   且必须能**单独吊销**、单独记 `last_used_at`。做成列 = 一个用户只能有一枚，
   吊销就是全吊销（见 models.py:DeveloperToken 注释）。
   [为什么 token_hint 存 sha256 前 8 位] 列表页要可辨认但不泄露明文熵。

2. `webhook_endpoints` —— 出站事件订阅端点。
   URL 在注册与投递**两处**做 SSRF 校验（models.py:WebhookEndpoint 注释）。

3. `webhook_deliveries` —— 投递日志（成败 + 重试依据）。
   [为什么必须落表] 失败可观测 + 重试对象是入库记录；日志会滚掉，表不会。

[索引形态 —— 必须与 ORM 声明逐条对齐，否则 `alembic check` 报漂移]
  · developer_tokens.token_hash            → UNIQUE INDEX ix_developer_tokens_token_hash
                                             （unique=True + index=True → 唯一索引，
                                              不另写 UniqueConstraint，避免 PG 上"约束+索引"并存的坑）
  · developer_tokens.user_id               → ix_developer_tokens_user_id（index=True）
  · developer_tokens(user_id, created_at)  → ix_developer_tokens_user_created（显式 Index）
  · webhook_endpoints.user_id              → ix_webhook_endpoints_user_id
  · webhook_endpoints(user_id, created_at) → ix_webhook_endpoints_user_created
  · webhook_endpoints(user_id, url)        → UNIQUE CONSTRAINT uq_webhook_endpoints_user_url
                                             （这里是 UniqueConstraint 而非唯一索引：
                                              复合唯一 + 无需单独 lookup 路径）
  · webhook_deliveries.endpoint_id         → ix_webhook_deliveries_endpoint_id
  · webhook_deliveries(delivered_at, created_at) → ix_webhook_deliveries_status

[方言兼容（#106 教训）]
- 布尔列 `revoked` / `active` 的 server_default 用 `sa.false()` / `sa.true()`，
  **不写** "0"/"1"/"false" 字符串：PG 上 `DEFAULT '0'::boolean` 会 DatatypeMismatch，
  而 SQLite 恰好容忍 —— 这个坑在 sqlite 上永远测不出来。
- 整数列 `attempts` 的默认值写 `sa.text("0")`（不是字符串 "0"，避免渲染成 `DEFAULT '0'`）。
- 不调用 op.get_bind() / exec_driver_sql / batch_alter_table → offline `--sql` 渲染不受影响。

[downgrade]
逆序删表（先 deliveries，再 endpoints，最后 tokens；外键依赖方向要求此顺序）。
本迁移不触碰任何既有表，回退零风险。
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "k1a2b3c4d5e6"
down_revision: Union[str, Sequence[str], None] = "j1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ---- 1. developer_tokens ----
    op.create_table(
        "developer_tokens",
        sa.Column("id", sa.Integer(), primary_key=True, index=True, nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("name", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("token_hint", sa.String(length=8), nullable=False, server_default=""),
        sa.Column("scopes", sa.Text(), nullable=False, server_default=""),
        sa.Column("revoked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index(
        "ix_developer_tokens_token_hash", "developer_tokens", ["token_hash"], unique=True
    )
    op.create_index(
        "ix_developer_tokens_user_created", "developer_tokens", ["user_id", "created_at"]
    )

    # ---- 2. webhook_endpoints ----
    op.create_table(
        "webhook_endpoints",
        sa.Column("id", sa.Integer(), primary_key=True, index=True, nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("url", sa.String(length=512), nullable=False),
        sa.Column("secret", sa.String(length=64), nullable=False),
        sa.Column("events", sa.Text(), nullable=False, server_default=""),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "url", name="uq_webhook_endpoints_user_url"),
    )
    op.create_index(
        "ix_webhook_endpoints_user_created", "webhook_endpoints", ["user_id", "created_at"]
    )

    # ---- 3. webhook_deliveries ----
    op.create_table(
        "webhook_deliveries",
        sa.Column("id", sa.Integer(), primary_key=True, index=True, nullable=False),
        sa.Column(
            "endpoint_id",
            sa.Integer(),
            sa.ForeignKey("webhook_endpoints.id"),
            nullable=False,
            index=True,
        ),
        sa.Column("event", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False, server_default=""),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("error", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index(
        "ix_webhook_deliveries_status", "webhook_deliveries", ["delivered_at", "created_at"]
    )
    op.create_index(
        "ix_webhook_deliveries_endpoint", "webhook_deliveries", ["endpoint_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_webhook_deliveries_endpoint", table_name="webhook_deliveries")
    op.drop_index("ix_webhook_deliveries_status", table_name="webhook_deliveries")
    op.drop_table("webhook_deliveries")

    op.drop_index("ix_webhook_endpoints_user_created", table_name="webhook_endpoints")
    op.drop_table("webhook_endpoints")

    op.drop_index("ix_developer_tokens_user_created", table_name="developer_tokens")
    op.drop_index("ix_developer_tokens_token_hash", table_name="developer_tokens")
    op.drop_table("developer_tokens")
