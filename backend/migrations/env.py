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
"""

from logging.config import fileConfig
import os

from sqlalchemy import engine_from_config
from sqlalchemy import pool

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


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
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
