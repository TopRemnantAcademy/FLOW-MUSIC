"""Alembic 环境：对接项目 Base.metadata 与 settings.database_url。

设计要点（长远）：
- 以项目统一元数据 Base.metadata 作为 autogenerate 的真相来源；
  `import app.models` 确保 User / Song / SyncRecord 全部注册到 metadata，
  否则自动生成的迁移会漏表。
- 数据库 URL 优先级：环境变量 ALEMBIC_DB_URL（CI / 测试隔离库）
  > settings.database_url（开发 / 生产默认）。便于在临时库验证迁移、
  不污染开发库（12-factor 配置外置）。
- 生产用 `alembic upgrade head` 演进 schema；开发兜底 create_all
  仍保留（幂等、无害，见 app/db.py）。
- 方言：sqlite（默认）与 postgresql（`postgresql+psycopg://...`）均可直接
  `upgrade head`。schema 本身已避开方言专属类型（全表 Integer/String/Numeric/
  DateTime/Text，自增主键走 SQLAlchemy 的 autoincrement 语义，PG 侧落 BIGSERIAL/
  SERIAL，sqlite 侧落 INTEGER PRIMARY KEY）—— 无需为 PG 另写一套迁移。
"""

from logging.config import fileConfig
import os

from sqlalchemy import create_engine
from sqlalchemy import pool
from sqlalchemy.engine import make_url

from alembic import context

from app.db import Base
import app.models  # 确保模型注册到 Base.metadata（autogenerate 必需）
from app.config import settings

config = context.config

# 动态设置数据库 URL（环境变量优先，便于测试 / CI 用临时库）
db_url = os.environ.get("ALEMBIC_DB_URL") or settings.database_url
config.set_main_option("sqlalchemy.url", db_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _include_object(object, name, type_, reflected, compare_to):
    """공유 커널 DB 에서 상대 서비스(FLOW) 테이블을 drop 하지 않도록 격리(X4, §21).

    autogenerate 시 DB 에 존재하지만 본 서비스 메타데이터에 없는 테이블
    (예: FLOW subscriber/interaction 등) 은 무시한다.
    """
    if type_ == "table" and reflected and name not in target_metadata.tables:
        return False
    return True


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table="alembic_version_music",  # X3: FLOW 과 버전 테이블 분리
        include_object=_include_object,  # X4: 상대 테이블 drop 방지
    )

    with context.begin_transaction():
        context.run_migrations()


def _migration_connect_args(db_url: str) -> dict:
    """迁移期 connect_args —— 必须与 app/db.py 一样按方言条件化。

    背景（PG 就绪，Ref: #72 / #74）：sqlite3 DBAPI 专属的 `check_same_thread`
    一旦传给 psycopg 就是 TypeError。原实现走 engine_from_config 把 ini 段落里的
    参数照单全收，迁移连接与 ORM 连接因此在「换 DATABASE_URL」时各行其是 →
    迁移能跑但应用崩（或反之）。这里显式只放该方言认识的参数。
    """
    dialect = ""
    try:
        dialect = make_url(db_url).get_backend_name()
    except Exception:  # 非法 URL 交给 create_engine 报原始错误
        return {}
    if dialect == "sqlite":
        return {"check_same_thread": False}
    if dialect in ("postgresql", "postgres"):
        # 建表/加列可能耗时，给足握手时间避免误判成"连不上"
        return {"connect_timeout": 10}
    return {}


def run_migrations_online() -> None:
    # ⚠ 不能再用 engine_from_config + prefix="sqlalchemy."：那样会把 ini 里
    #   不属于本方言的参数一并传给 DBAPI。改为显式构造 URL + 方言化 connect_args。
    connectable = create_engine(
        db_url,
        poolclass=pool.NullPool,
        connect_args=_migration_connect_args(db_url),
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table="alembic_version_music",  # X3: FLOW 과 버전 테이블 분리
            include_object=_include_object,  # X4: 상대 테이블 drop 방지
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
