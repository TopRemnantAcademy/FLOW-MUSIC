"""PostgreSQL 驱动就绪（Ref: #72 / #74）—— 方言条件化与迁移可用性。

拆成两类断言，避免「假绿」：

1. **纯离线**（无需 PG 服务、无需装 psycopg）：方言识别 + connect_args 分流。
   这类断言在任何机器上都真跑，是防止「sqlite 专属参数误传给 PG」回归的硬闸门。
2. **真 PG**（需 TEST_PG_URL 指向一个可连的 PostgreSQL）：`alembic upgrade head`
   建出与 ORM 元数据一致的 schema，且 `postgresql+psycopg://` 能真正连上。
   本机（含 CI 无 PG 的 runner）没有 PG 时 **skip 并写明原因** ——
   绝不静默"通过"。
"""

import importlib
import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy.engine import make_url

BACKEND_ROOT = Path(__file__).resolve().parent.parent  # backend/

# PG 测试连接串。PG 侧由部署机（朴素侧）实测；无则整组 skip。
TEST_PG_URL = os.environ.get("TEST_PG_URL", "").strip()


def _db_facts_via_subprocess(database_url: str) -> dict:
    """在**子进程**里以给定 DATABASE_URL 导入 app.db，回传方言相关事实。

    为什么不在本进程 importlib.reload(app.db)：
      `class User(Base)` 里的 Base 是 app.db 的模块级对象。一旦本进程 reload
      app.db，app.models 里的 User 仍绑在**旧 Base** 上；而 metadata 是"谁先注册
      谁占坑"——旧 Base 的 metadata 仍持有 users/songs 等 Table，后续任何针对
      新 Base 的 `Base.metadata.tables` 都会是空的（表现为"元数据为空"/
      NoSuchTableError 这类假故障）。子进程隔离从根上避免这种跨用例污染。
    """
    code = f"""
import os, sys, json
os.environ["DATABASE_URL"] = {database_url!r}
sys.path.insert(0, {str(BACKEND_ROOT)!r})
import app.db as db
import app.models, app.billing.models  # noqa: F401 注册模型
print(json.dumps({{
    "dialect": db._DIALECT,
    "is_sqlite": db._is_sqlite,
    "connect_args": db.connect_args,
    "engine_dialect": db.engine.dialect.name,
    "engine_driver": db.engine.dialect.driver,
    "pool_pre_ping": bool(db.engine.pool._pre_ping),
    "metadata_tables": len(db.Base.metadata.tables),
}}))
"""
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    import json

    return json.loads(out.stdout.strip().splitlines()[-1])


# ───────────────────────── 1. 纯离线：方言 & connect_args ─────────────────────────


def test_sqlite_connect_args_keep_check_same_thread():
    """sqlite（默认）：必须保留 check_same_thread（FastAPI sync 依赖跨线程复用连接）。"""
    facts = _db_facts_via_subprocess("sqlite:///./pg_readiness_tmp.db")
    assert facts["is_sqlite"] is True
    assert facts["connect_args"] == {"check_same_thread": False}
    assert facts["engine_dialect"] == "sqlite"
    # sqlite 分支不开 pre_ping
    assert facts["pool_pre_ping"] is False
    assert facts["metadata_tables"] > 0


def test_postgres_connect_args_never_carry_sqlite_only_kwargs():
    """★ 核心回归闸门 ★

    postgresql+psycopg：connect_args 里**不得**出现 check_same_thread。
    psycopg 的 connect() 不接受该 kwarg → 传了就是 TypeError（换库即崩）。
    纯离线：不建连接（端口 65535 只是占位），故无需真 PG 也能跑。
    """
    facts = _db_facts_via_subprocess(
        "postgresql+psycopg://u:p@127.0.0.1:65535/nonexistent_db"
    )
    assert facts["is_sqlite"] is False
    assert "check_same_thread" not in facts["connect_args"]
    # engine 构造成功（不建连接）且方言解析为 postgresql
    assert facts["engine_dialect"] == "postgresql"
    # psycopg3 的 DBAPI 名，确认走的是 +psycopg 而非 psycopg2
    assert facts["engine_driver"] == "psycopg"
    # pool_pre_ping 仅 PG 侧开启
    assert facts["pool_pre_ping"] is True
    assert facts["metadata_tables"] > 0


def test_dialect_detection_does_not_import_driver():
    """方言识别不得依赖驱动已安装（否则未装 psycopg 的机器连"判断"都做不到）。"""
    from app.db import _dialect_of

    assert _dialect_of("postgresql+psycopg://u@h/db") == "postgresql"
    assert _dialect_of("postgresql://u@h/db") == "postgresql"
    assert _dialect_of("sqlite:////tmp/x.db") == "sqlite"
    assert _dialect_of("not a url") == ""


def _migration_connect_args_via_subprocess(db_url: str) -> dict:
    """在子进程里执行 alembic env 的 connect_args 函数（绕开 context 注入问题）。

    子进程内直接 exec 源码中 `_migration_connect_args` 的定义段并调用，
    顺带证明该函数不依赖模块级 context 初始化。
    """
    code = f"""
import re, sys
src = open({str(BACKEND_ROOT / "migrations" / "env.py")!r}, encoding="utf-8").read()
start = src.index("def _migration_connect_args")
end = src.index("def run_migrations_online")
ns = {{}}
# 只求值这一段的依赖：make_url 即可
from sqlalchemy.engine import make_url
ns["make_url"] = make_url
exec(src[start:end], ns)
print(repr(ns["_migration_connect_args"]({db_url!r})))
"""
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    return eval(out.stdout.strip())  # noqa: S307 - 测试内自产自销的字面量


def test_alembic_migration_connect_args_are_dialect_scoped():
    """alembic env 的 connect_args 同样按方言分流（迁移连接与 ORM 连接口径一致）。"""
    assert _migration_connect_args_via_subprocess("sqlite:////tmp/x.db") == {
        "check_same_thread": False
    }
    pg_args = _migration_connect_args_via_subprocess("postgresql+psycopg://u:p@h/db")
    assert "check_same_thread" not in pg_args
    assert pg_args == {"connect_timeout": 10}
    assert _migration_connect_args_via_subprocess("not a url") == {}


@pytest.mark.parametrize(
    "url",
    ["postgresql+psycopg://u:p@127.0.0.1:65535/none", "postgresql://u:p@127.0.0.1:65535/none"],
)
def test_pg_url_forms_recognised_as_postgres(url):
    """两种写法都要被认成 postgres（`postgresql://` 默认驱动不一定是 psycopg，
    但方言判断与 connect_args 分流必须一致）。"""
    from app.db import _dialect_of

    assert _dialect_of(url) == "postgresql"
    assert make_url(url).get_backend_name() == "postgresql"


# ───────────────────────── 2. 真 PG：迁移 & 连接 ─────────────────────────


pg_required = pytest.mark.skipif(
    not TEST_PG_URL,
    reason=(
        "未设置 TEST_PG_URL —— 本机无 PostgreSQL，跳过真库测试。"
        "部署机实测请在 .env.music-pg 同环境执行："
        "TEST_PG_URL=postgresql+psycopg://flow_music:***@127.0.0.1:5432/flow_music "
        "python -m pytest tests/test_pg_readiness.py -q"
    ),
)


def _pg_admin_engine():
    """维护用 engine：连到与 TEST_PG_URL **同一个数据库**，再用其连接执行
    CREATE/DROP DATABASE（PG 允许从任意库执行 DDL，无需连 postgres 维护库）。

    为什么不连 `postgres` 库：本机实测该库是 SQL_ASCII 编码，而 SQLAlchemy 的
    psycopg 方言在建连接时会 `re.match` 服务端 version() 字符串 —— SQL_ASCII 下
    psycopg 返回 bytes 而非 str，直接 TypeError。用 TEST_PG_URL 自身的库连接
    可以避开这个与业务无关的本地集群配置坑（不掩盖真正的方言兼容问题）。
    """
    from sqlalchemy import create_engine

    return create_engine(
        make_url(TEST_PG_URL), isolation_level="AUTOCOMMIT"
    )


def _make_pg_tmp_db(tmp_db: str):
    """建一个 UTF8 临时库；返回 admin engine（调用方负责 finally 里 drop）。"""
    admin = _pg_admin_engine()
    with admin.connect() as conn:
        conn.exec_driver_sql(f'DROP DATABASE IF EXISTS "{tmp_db}" WITH (FORCE)')
        # ⚠ 显式 ENCODING UTF8 + TEMPLATE template0：否则新库继承 template1 的
        #   编码（本机实测 SQL_ASCII），psycopg 方言的 version() 解析会 TypeError。
        conn.exec_driver_sql(
            f'CREATE DATABASE "{tmp_db}" ENCODING UTF8 TEMPLATE template0'
        )
    return admin


def _drop_pg_tmp_db(admin, tmp_db: str):
    try:
        with admin.connect() as conn:
            conn.exec_driver_sql(f'DROP DATABASE IF EXISTS "{tmp_db}" WITH (FORCE)')
    finally:
        admin.dispose()


@pg_required
def test_alembic_upgrade_head_on_postgres(tmp_path):
    """★ PG 侧核心用例 ★：`alembic upgrade head` 能在 postgresql 方言下建全 schema，
    且建出的表/列与 ORM Base.metadata 完全一致（无方言专属类型导致的漂移）。

    隔离手段：把 ALEMBIC_DB_URL 指向「新建的临时数据库」（而非 search_path），
    因为 PG 的 `pg_` 是保留 schema 前缀、且把 search_path 塞进 URL query 会被
    configparser 的 % 插值规则咬到。整库隔离更干净：跑完 DROP DATABASE。
    """
    from alembic import command, config as alembic_config
    from sqlalchemy import create_engine, inspect

    base_url = make_url(TEST_PG_URL)
    tmp_db = "music_pg_readiness_mig"
    admin = _make_pg_tmp_db(tmp_db)

    tmp_url = base_url.set(database=tmp_db)
    tmp_url_s = tmp_url.render_as_string(hide_password=False)
    cfg = alembic_config.Config(str(BACKEND_ROOT / "alembic.ini"))
    monkeypatch_env = {"ALEMBIC_DB_URL": tmp_url_s}
    old_env = os.environ.copy()
    os.environ.update(monkeypatch_env)
    # alembic 的 Config 是 configparser 子类：URL 里含 % 会炸，这里已确认纯净
    cfg.set_main_option("sqlalchemy.url", tmp_url_s)
    try:
        command.upgrade(cfg, "head")

        engine = create_engine(tmp_url)
        try:
            insp = inspect(engine)
            actual = set(insp.get_table_names()) - {
                "alembic_version",
                "alembic_version_music",
            }
            # ⚠ 期望表清单必须用「此刻真正注册了模型的 metadata」：
            #   app.db 在别的用例里可能被 reload，届时 `from app.db import Base`
            #   拿到的是另一个 Base 实例（tables 为空）→ 假失败。故此处重新
            #   import 模型并直接取 Base.metadata。
            import app.billing.models  # noqa: F401
            import app.models  # noqa: F401
            from app.db import Base

            expected = set(Base.metadata.tables.keys())
            assert expected, "元数据为空 —— 模型未注册，测试本身失效（不许假绿）"
            assert actual == expected, (
                f"PG 迁移建出的表 {sorted(actual)} 与元数据 {sorted(expected)} 不一致"
            )
            for table in expected:
                actual_cols = {c["name"] for c in insp.get_columns(table)}
                expected_cols = set(Base.metadata.tables[table].columns.keys())
                assert actual_cols == expected_cols, (
                    f"PG 表 {table} 列不一致: {sorted(actual_cols)} vs {sorted(expected_cols)}"
                )
        finally:
            engine.dispose()
    finally:
        os.environ.clear()
        os.environ.update(old_env)
        _drop_pg_tmp_db(admin, tmp_db)


@pg_required
def test_psycopg_driver_can_connect_and_create_all(tmp_path):
    """驱动实连：`postgresql+psycopg://` 能建连接 + create_all 建 schema
    （即 SCHEMA_CREATE_ALL=1 的启动路径）。同样用临时库隔离。"""
    from sqlalchemy import create_engine, inspect, text

    import app.billing.models  # noqa: F401 注册计费模型到 Base.metadata
    import app.models  # noqa: F401 注册业务模型
    from app.db import Base

    base_url = make_url(TEST_PG_URL)
    tmp_db = "music_pg_readiness_createall"
    admin = _make_pg_tmp_db(tmp_db)

    tmp_url = base_url.set(database=tmp_db)
    # connect_args 与 app/db.py PG 分支一致：不得含 check_same_thread
    engine = create_engine(
        tmp_url, pool_pre_ping=True, connect_args={"connect_timeout": 10}
    )
    try:
        with engine.connect() as conn:
            assert conn.execute(text("SELECT 1")).scalar() == 1
        Base.metadata.create_all(bind=engine)
        tables = set(inspect(engine).get_table_names())
        assert set(Base.metadata.tables.keys()) <= tables
        # 自增主键在 PG 侧必须是 identity/serial，否则 insert 不返回 id → 应用层崩
        id_col = next(c for c in inspect(engine).get_columns("songs") if c["name"] == "id")
        assert id_col["autoincrement"] is True
    finally:
        engine.dispose()
        _drop_pg_tmp_db(admin, tmp_db)


@pg_required
def test_pg_create_all_then_insert_roundtrip():
    """端到端：PG 上建表后真正插入/查询一行，确认「切库后应用可用」而非只是能建表。"""
    from sqlalchemy import create_engine, text

    base_url = make_url(TEST_PG_URL)
    tmp_db = "music_pg_readiness_rw"
    admin = _make_pg_tmp_db(tmp_db)

    tmp_url = base_url.set(database=tmp_db)
    from app.models import User  # noqa: F401 触发 Base 注册
    from app.db import Base

    engine = create_engine(tmp_url, pool_pre_ping=True, connect_args={"connect_timeout": 10})
    try:
        Base.metadata.create_all(bind=engine)
        from sqlalchemy.orm import Session

        with Session(engine) as sess:
            u = User(email="pg-readiness@example.com", hashed_password="x")
            sess.add(u)
            sess.commit()
            assert u.id is not None, "PG 自增主键未回填 → 插入后拿不到 id"
            got = sess.execute(
                text("SELECT email FROM users WHERE id = :i"), {"i": u.id}
            ).scalar()
            assert got == "pg-readiness@example.com"
    finally:
        engine.dispose()
        _drop_pg_tmp_db(admin, tmp_db)


@pg_required
def test_psycopg_installed_in_image_requirements():
    """requirements 必须锁定 PG 驱动，否则镜像重建后又回到"切 DATABASE_URL 就崩"。"""
    req = (BACKEND_ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert "psycopg[binary]" in req
    import psycopg  # noqa: F401  当前环境确实装了

    assert subprocess.run(
        [sys.executable, "-c", "import psycopg; print(psycopg.__version__)"],
        capture_output=True,
        check=True,
    ).stdout.strip()


# ───────────────────── 3. 迁移漂移：跨方言必须一致 ─────────────────────


def test_alembic_check_no_drift_on_sqlite(tmp_path):
    """`alembic check` 在 **sqlite** 上必须无漂移（CI 现有口径，锁死不回退）。

    历史缺陷：`pricing_tiers.tier_key` 同时声明 unique=True + index=True →
    PG 上落两条唯一索引，而建表迁移只写 unique=True → PG 报漂移。
    sqlite 把 UNIQUE INDEX 折叠进 autoindex（inspect 不回读）→ 漂移在 sqlite 上隐身，
    CI 又只跑 sqlite，于是长期无人发现（2026-10-11 PG 实测暴露）。
    """
    from alembic import command, config as alembic_config

    db_url = f"sqlite:///{tmp_path / 'drift_check.db'}"
    cfg = alembic_config.Config(str(BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    old = os.environ.get("ALEMBIC_DB_URL")
    os.environ["ALEMBIC_DB_URL"] = db_url
    try:
        command.upgrade(cfg, "head")
        command.check(cfg)  # 有漂移即抛 AutogenerateDiffsDetected
    finally:
        if old is None:
            os.environ.pop("ALEMBIC_DB_URL", None)
        else:
            os.environ["ALEMBIC_DB_URL"] = old


def test_no_dual_unique_declaration_on_tier_key():
    """回归闸门：`tier_key` 不得同时 unique=True + index=True。

    该组合在不同方言上落不同数量的唯一索引（PG 两条 / sqlite 一条），
    必然产生跨方言 schema 漂移。断言"只保留 unique=True"。
    """
    import app.models as models

    col = models.PricingTier.__table__.c.tier_key
    assert col.unique is True, "tier_key 的唯一性由 unique=True 承担"
    assert not col.index, (
        "tier_key 不应再设 index=True —— 会与 unique=True 在 PG 上生成第二条唯一索引，"
        "导致 alembic check 跨方言漂移（见迁移 b9c1d2e3f4a5）"
    )


def test_payment_order_idempotency_key_is_unique_in_schema(tmp_path):
    """★ 真实缺陷回归闸门 ★ 幂等键必须**在 schema 层面**唯一。

    `payment_orders.idempotency_key` 模型声明 unique=True，而建表迁移
    c9d0e1f2a3b4 只建了普通索引 → 实测同一个 key 能插入两次，
    而 checkout 端点本来就接收该参数（billing.py:97/114）→ 调用方以为重试安全，
    实际每次重试都新建一张订单。迁移 c3d4e5f6a7b8 已把索引纠正为 UNIQUE。
    """
    import sqlite3

    from alembic import command, config as alembic_config

    from app.db import Base
    import app.billing.models  # noqa: F401

    db_url = f"sqlite:///{tmp_path / 'idem.db'}"
    cfg = alembic_config.Config(str(BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", db_url)
    old = os.environ.get("ALEMBIC_DB_URL")
    os.environ["ALEMBIC_DB_URL"] = db_url
    try:
        command.upgrade(cfg, "head")
    finally:
        if old is None:
            os.environ.pop("ALEMBIC_DB_URL", None)
        else:
            os.environ["ALEMBIC_DB_URL"] = old
    _ = Base

    conn = sqlite3.connect(tmp_path / "idem.db")
    conn.execute(
        "INSERT INTO users (email, hashed_password) VALUES ('idem@t.com','x')"
    )
    uid = conn.execute("SELECT id FROM users WHERE email='idem@t.com'").fetchone()[0]
    row = (
        "INSERT INTO payment_orders (user_id, provider, tier_key, amount_cny, "
        "currency, amount, status, idempotency_key) VALUES (?,?,?,?,?,?,?,?)"
    )
    args = (uid, "manual", "lite", 1, "CNY", 1, "pending", "SAME-KEY")
    conn.execute(row, args)
    conn.commit()
    # 第二次同 key 必须失败（唯一性由 schema 强制，而非靠应用层记得去重）
    import sqlite3 as _s

    with pytest.raises(_s.IntegrityError):
        conn.execute(row, args)
        conn.commit()
    conn.close()


def test_payment_order_model_declares_unique():
    """模型声明侧也要有 unique=True —— 与迁移的 UNIQUE INDEX 保持一致。"""
    import app.billing.models as billing_models

    col = billing_models.PaymentOrder.__table__.c.idempotency_key
    assert col.unique is True


def test_subscription_user_id_not_dual_declared():
    """`subscriptions.user_id` 同样不得 unique+index 双写（PG 会落两条唯一索引）。"""
    import app.billing.models as billing_models

    col = billing_models.Subscription.__table__.c.user_id
    assert col.unique is True
    assert col.nullable is False, "订阅必须挂用户，与迁移 c9d0e1f2a3b4 的 nullable=False 一致"
    assert not col.index, "index=True 会与 unique 在 PG 上重复落一条唯一索引"
