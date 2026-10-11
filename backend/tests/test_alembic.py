"""验证 Alembic 迁移能基于干净库建出与 ORM 元数据完全一致的 schema。

长远价值：锁定「模型 ↔ 迁移」不漂移。若未来有人改 models.py 却忘了写
对应的迁移，本测试（或 CI 中的 `alembic check`）会失败，阻止 schema
演进事故。测试使用独立临时库（ALEMBIC_DB_URL 隔离），不触碰开发库 app.db，
也不依赖 conftest 的测试库。
"""

import os
import pytest
from pathlib import Path

from alembic import command, config as alembic_config
from sqlalchemy import create_engine, inspect

from app.db import Base

BACKEND_ROOT = Path(__file__).resolve().parent.parent  # backend/
ALEMBIC_INI = BACKEND_ROOT / "alembic.ini"


@pytest.fixture
def alembic_tmp_db(tmp_path, monkeypatch):
    """把 Alembic 指向一个临时 SQLite 库，避免污染开发库 / 测试库。"""
    db_url = f"sqlite:///{tmp_path / 'migrated.db'}"
    monkeypatch.setenv("ALEMBIC_DB_URL", db_url)
    yield db_url


def test_alembic_upgrade_builds_schema_matching_metadata(alembic_tmp_db):
    cfg = alembic_config.Config(str(ALEMBIC_INI))
    command.upgrade(cfg, "head")

    engine = create_engine(alembic_tmp_db, connect_args={"check_same_thread": False})
    try:
        inspector = inspect(engine)

        # 排除 Alembic 自身的管理表，只比对业务表。
        # 本仓库通过 migrations/env.py 的 version_table="alembic_version_music"
        # (X3: FLOW 与音乐版本表分离) 自定义了版本表名，而非标准 alembic_version，
        # 故需同时排除两者，避免版本表污染业务表比对。
        actual_tables = set(inspector.get_table_names()) - {
            "alembic_version",
            "alembic_version_music",
        }
        expected_tables = set(Base.metadata.tables.keys())
        assert actual_tables == expected_tables, (
            f"迁移建出的表 {sorted(actual_tables)} 与元数据 {sorted(expected_tables)} 不一致"
        )

        # 逐表核对列名（跨库类型差异大，仅核对列的存在性与数量）
        for table_name in expected_tables:
            actual_cols = {c["name"] for c in inspector.get_columns(table_name)}
            expected_cols = set(Base.metadata.tables[table_name].columns.keys())
            assert actual_cols == expected_cols, (
                f"表 {table_name} 列不一致: "
                f"实际 {sorted(actual_cols)} vs 期望 {sorted(expected_cols)}"
            )
    finally:
        engine.dispose()
