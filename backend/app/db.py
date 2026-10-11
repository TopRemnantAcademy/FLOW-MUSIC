"""数据库引擎与会话（SQLAlchemy 2.x）。"""

from sqlalchemy import create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings

# PG 连接建立超时（秒）。仅 postgresql 方言使用；sqlite 无此参数。
# 取值偏短是刻意的：连不通应当快速 500，而不是让请求线程停在 TCP 握手上。
_PG_CONNECT_TIMEOUT = 10


def _dialect_of(url: str) -> str:
    """数据库方言名（sqlite / postgresql / ...）。

    只用 sqlalchemy.engine.make_url 解析，**不导入驱动**——所以在未安装 psycopg 的
    机器上也能安全判断方言（驱动缺失只在真正建 engine/连接时才报错）。
    """
    try:
        return make_url(url).get_backend_name()
    except Exception:  # 非法 URL：交给 create_engine 抛原始错误，不在此处掩盖
        return ""


_DIALECT = _dialect_of(settings.database_url)
_is_sqlite = _DIALECT == "sqlite"

# ── connect_args 按方言条件化（PG 就绪）──────────────────────────────────────
# check_same_thread 是 **sqlite3 DBAPI 专属**参数。给 psycopg 传它 → TypeError
# （psycopg 的 connect() 不接受该 kwarg），表现为「换 DATABASE_URL 直接崩」。
# 故必须先按方言分流，而不是写死一个 dict 再传给两处 engine。
# postgres 侧显式带上 connect_timeout：PG 宕机/网络黑洞时 fail fast（默认无限期等待），
# 与 pool_pre_ping 一起把「换库后连不上」变成快速失败而不是全站请求挂死。
if _is_sqlite:
    connect_args: dict = {"check_same_thread": False}
elif _DIALECT in ("postgresql", "postgres"):
    connect_args: dict = {"connect_timeout": _PG_CONNECT_TIMEOUT}
else:
    connect_args: dict = {}

# 并发基线（100 人同时在线，2026-09-10 实测定型，前后对比见当日 loadtest 记录）：
#   修复前（默认 pool 5+10、无 WAL、无 busy_timeout）100 并发注册突发 61×500
#   （database is locked）、浏览期 p50≈35s、整体仅 6 rps。
#   第一轮修复（WAL+busy_timeout+pool 20/30）：tokens 100/100✓，rps 6→164（27×），
#     但 favorites 等读端点 40×500 + p99 30s（QueuePool size 20 overflow 30 reached,
#     timeout 30）——任何 io 拖 30s 即被连累等连接超时。
#   第二轮修复：pool 50/50=100（覆盖「100 VU 同时持连接」，含依赖已解析、handler 排队
#     等线程时的连接停放）→ 池耗尽型 500 归零。⚠ 曾同时把 anyio sync 线程帽 40→100，
#     实测回归（rps 164→26、Phase A 67s→104s）——GIL 下线程超订是负收益，已回退默认 40，
#     详见 main.py lifespan 复盘注释。连接池扩容与线程数解耦，池 100 独立生效。
#   · WAL            → 读写不互斥（浏览不被写卡死）
#   · busy_timeout   → 写锁冲突时等待重试，而不是立刻抛 locked → 500
#   · pool 50+50     → 100 并发下线程不再等连接（默认 5+10 会饿死；20+30 仍吃紧）
# Postgres 走 else 分支：pool 参数同样适用，pre_ping 抗连接失效。
_POOL = dict(pool_size=50, max_overflow=50, pool_timeout=30, future=True)


def _create_engine():
    """按方言建 engine。

    ⚠ 两处 connect_args 必须走同一个「按方言算出来」的变量：sqlite 专属参数
    （check_same_thread）绝不能出现在 postgresql 分支，否则换库即 TypeError。
    """
    if _is_sqlite:
        # sqlite: 无 pool_pre_ping（单文件本地连接，ping 无意义且徒增开销）
        return create_engine(settings.database_url, connect_args=connect_args, **_POOL)
    # postgres（含其他方言）：pre_ping 抗连接失效（PG 重启 / 连接被中间设备回收）
    return create_engine(
        settings.database_url,
        pool_pre_ping=True,
        connect_args=connect_args,
        **_POOL,
    )


def _assert_driver_available() -> None:
    """启动期驱动自检：DATABASE_URL 用 PG 而镜像未装 psycopg 时，**立刻响亮失败**。

    背景（2026-10-11 服务器实测）：flow-hk-music 镜像曾只有 SQLAlchemy + alembic，
    无任何 PG 驱动 → 切 DATABASE_URL 会在首个请求（甚至 import 期）崩，
    而报错是 "ModuleNotFoundError: No module named 'psycopg'"，与「配置写错」
    难以区分。宁可启动即失败并直说该怎么修。
    """
    if _DIALECT not in ("postgresql", "postgres"):
        return
    try:
        __import__("psycopg")
    except ImportError as e:  # pragma: no cover - 依赖齐全的正常环境不触发
        raise RuntimeError(
            f"DATABASE_URL 方言为 {_DIALECT}，但镜像内没有 PostgreSQL 驱动 psycopg。"
            " 请安装：pip install 'psycopg[binary]'（见 backend/requirements.txt）。"
        ) from e


_assert_driver_available()
engine = _create_engine()

if _is_sqlite:

    @event.listens_for(engine, "connect")
    def _sqlite_pragma(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")  # 读写不互斥
        cur.execute("PRAGMA synchronous=NORMAL")  # WAL 下的推荐档（性能/耐久平衡）
        cur.execute("PRAGMA busy_timeout=5000")  # 写锁等待 5s，而非立刻 locked
        cur.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    # 开发兜底：幂等建表，便于本地快速启动；生产 schema 演进以 Alembic 为准
    # （见 migrations/，部署用 `alembic upgrade head`）。两者共存无害。
    import app.models  # noqa: F401 确保模型注册
    import app.billing.models  # noqa: F401 支付/订阅模型注册(GAP-004)

    Base.metadata.create_all(bind=engine)
    # 注入默认后台配置（引擎/路由/计费）；已存在则保留后台值（见 app.orchestrator.selection.seed_default_config）
    from app.orchestrator.selection import seed_default_config

    seed_default_config(SessionLocal())
