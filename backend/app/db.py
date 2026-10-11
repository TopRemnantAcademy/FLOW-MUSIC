"""数据库引擎与会话（SQLAlchemy 2.x）。"""

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings

_is_sqlite = settings.database_url.startswith("sqlite")
connect_args = {"check_same_thread": False} if _is_sqlite else {}

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
if _is_sqlite:
    engine = create_engine(settings.database_url, connect_args=connect_args, **_POOL)

    @event.listens_for(engine, "connect")
    def _sqlite_pragma(dbapi_conn, _record):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")  # 读写不互斥
        cur.execute("PRAGMA synchronous=NORMAL")  # WAL 下的推荐档（性能/耐久平衡）
        cur.execute("PRAGMA busy_timeout=5000")  # 写锁等待 5s，而非立刻 locked
        cur.close()
else:
    engine = create_engine(
        settings.database_url, pool_pre_ping=True, connect_args=connect_args, **_POOL
    )
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
