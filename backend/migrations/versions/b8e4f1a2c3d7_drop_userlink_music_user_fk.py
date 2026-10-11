"""drop cross-db FK on user_links.music_user_id (#72 결정 1: 독립 DB + 좁은 브리지)

Revision ID: b8e4f1a2c3d7
Revises: a7f3c91d4e62
Create Date: 2026-10-11 12:00:00.000000

배경 (ISSUE #72 결정 1, 2026-10-11 사용자 확정):
  `UserLink.music_user_id` 의 `ForeignKey("users.id")` 는 **공유 DB 전제**
  (MUSIC_PLATFORM_MIGRATION_2026-09-10 「数据库合一」) 잔재다. 결정은 A안
  「독립 DB + 좁은 브리지」로 수렴했으며, 두 서비스가 각자 DB 주권을 갖는 이상
  cross-database FK 는 성립하지 않는다(FLOW 측 DB 에는 `users` 테이블이 없다).

  → `music_user_id` 를 **일반 컬럼**으로 강등(값·인덱스는 유지, FK 제약만 제거).

정합성 책임 이동:
  FK 가 사라진 자리는 애플리케이션 계층이 진다 — `auth.py` 의 flow-link upsert
  (subscriber_id → music user 매핑) + `flow-exchange` 토큰 교환. 본 마이그레이션은
  스키마 제약만 바꾸며 **데이터는 삭제하지 않는다**(값 보존).

구현 노트 (왜 수동 재작성인가):
  SQLite 는 `ALTER TABLE ... DROP CONSTRAINT` 를 지원하지 않고, 원 마이그레이션
  (d4e5f6a7b8c9)이 FK 를 **익명**으로 만들었기 때문에 `batch.drop_constraint(<name>)`
  로는 이름을 짚을 수 없다(실측: sqlite_master 에서 FK 이름 = NULL).
  → 테이블을 임시 이름으로 再작성 후 원본을 대체하는 방식으로 FK 를 제거한다.
  값은 INSERT ... SELECT 로 전부 이관되며, 인덱스는 수동으로 재생성한다.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b8e4f1a2c3d7"
down_revision: Union[str, Sequence[str], None] = "a7f3c91d4e62"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 앱(db.py)은 naming_convention 을 쓰지 않으므로 FK 는 익명이다. 아래는 이관용 이름.
_COLS = ("id", "flow_subscriber_id", "music_user_id", "created_at")


def _is_sqlite() -> bool:
    return op.get_bind().dialect.name == "sqlite"


def _rebuild_user_links(*, drop_fk: bool) -> None:
    """user_links 를 再작성하며 FK 유무만 바꾼다(데이터 보존)."""
    bind = op.get_bind()
    # 1) 원본을 새 스키마로 재작성 (FK 없음 / 있음)
    if drop_fk:
        new_table_sql = """
            CREATE TABLE user_links_new (
                id INTEGER NOT NULL,
                flow_subscriber_id VARCHAR(255) NOT NULL,
                music_user_id INTEGER,
                created_at DATETIME DEFAULT (CURRENT_TIMESTAMP),
                PRIMARY KEY (id)
            )
        """
    else:
        new_table_sql = """
            CREATE TABLE user_links_new (
                id INTEGER NOT NULL,
                flow_subscriber_id VARCHAR(255) NOT NULL,
                music_user_id INTEGER,
                created_at DATETIME DEFAULT (CURRENT_TIMESTAMP),
                PRIMARY KEY (id),
                FOREIGN KEY(music_user_id) REFERENCES users (id)
            )
        """
    bind.exec_driver_sql(new_table_sql)
    cols = ", ".join(_COLS)
    bind.exec_driver_sql(f"INSERT INTO user_links_new ({cols}) SELECT {cols} FROM user_links")
    bind.exec_driver_sql("DROP TABLE user_links")
    bind.exec_driver_sql("ALTER TABLE user_links_new RENAME TO user_links")
    # 2) 인덱스 재생성 (원 마이그레이션 d4e5f6a7b8c9 와 동일한 이름/성질)
    bind.exec_driver_sql(
        "CREATE UNIQUE INDEX ix_user_links_flow_subscriber_id "
        "ON user_links (flow_subscriber_id)"
    )
    bind.exec_driver_sql("CREATE INDEX ix_user_links_music_user_id ON user_links (music_user_id)")
    bind.exec_driver_sql("CREATE INDEX ix_user_links_id ON user_links (id)")


def upgrade() -> None:
    if _is_sqlite():
        _rebuild_user_links(drop_fk=True)
        return
    # Postgres 등: FK 를 이름으로 제거할 수 있다(d4e5f6a7b8c9 가 자동 명명).
    for fk in sa.inspect(op.get_bind()).get_foreign_keys("user_links"):
        if fk.get("referred_table") == "users" and fk.get("constrained_columns") == ["music_user_id"]:
            if fk.get("name"):
                op.drop_constraint(fk["name"], "user_links", type_="foreignkey")
            break


def downgrade() -> None:
    if _is_sqlite():
        _rebuild_user_links(drop_fk=False)
        return
    op.create_foreign_key(
        "fk_user_links_music_user_id_users", "user_links", "users", ["music_user_id"], ["id"]
    )
