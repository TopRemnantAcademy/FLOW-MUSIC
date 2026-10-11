import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

import app.db as dbmod
from app.db import Base, SessionLocal
from app.config import settings

# 注意：不要用 `from app.main import app`，因为 `import app.db` 会把顶层
# 名字 `app` 绑成「app 包模块」本身，导致 fixture 里 app 变成 module 而非
# FastAPI 实例。显式取到模块对象再取 .app 属性，避开名字遮蔽。
import app.main as _app_main

fastapi_app = _app_main.app

# 单一测试库（文件型 SQLite，便于跨连接可见）
DB_PATH = "/tmp/music_test_session.db"
engine = create_engine(
    f"sqlite:///{DB_PATH}",
    connect_args={"check_same_thread": False},
    future=True,
)

# 关键修复：重绑「同一个」sessionmaker 对象的 bind，而不是替换对象本身。
# songs.py / auth.py 等都用 `from app.db import SessionLocal`，持有的是该对象引用，
# 只有 configure(bind=...) 改同一对象才能让所有持有者都指向测试库。
SessionLocal.configure(bind=engine)
dbmod.engine = engine  # 让 lifespan / 其它用 dbmod.engine 的地方也指向测试库

import app.models  # 确保模型注册到 Base.metadata
import app.wallet_track  # P1 基础件：镜像 FLOW wallet_reservation 表（独立元数据）
from app.wallet_track import wallet_metadata

Base.metadata.drop_all(bind=engine)
Base.metadata.create_all(bind=engine)
# wallet_reservation 由 FLOW migration 拥有；测试库无 FLOW，此处单独建表
wallet_metadata.create_all(bind=engine)

from app.orchestrator.client import set_orchestrator
from app.orchestrator.mock import MockProvider

set_orchestrator(MockProvider())  # 注入 mock 编排层，全程不依赖真实 Suno

# 测试配置覆盖
settings.enable_rate_limit = False
settings.enable_moderation = True
settings.song_poll_interval = 0
settings.song_poll_timeout = 2
settings.jwt_secret = "test-secret-for-pytest-only"
# 安全加固后（2026-09-13 审计）mock 自助升档默认关闭，仅测试显式打开。
# 生产部署保持 False → 无法绕过支付直接升档。
settings.enable_mock_subscribe = True
# ⏸ 音色克隆产品暂停（生产默认 False → 端点 501），但**实现必须继续被测**：
# 测试环境显式开启，确保封存代码不被静默腐化（重新上线时可直接使用）。
settings.enable_vocal_clone = True
# 启动期 Mureka 出口自检：**测试环境必须关闭**。
# 否则 `with TestClient(app)` 会触发 lifespan → 对 api.mureka.ai 发起真实网络探测
# （backend/.env 里有真实 key），导致测试依赖外网且每个会话多等数秒。
settings.mureka_preflight_mode = "off"


@pytest.fixture(scope="session")
def client():
    with TestClient(fastapi_app) as c:
        yield c


@pytest.fixture(autouse=True)
def _clean(client):
    yield
    # [SEC] 2026-09-13：cookie 认证会随共享 session client 跨测试持久化（header 认证不会）。
    # 不清则登录态在测试间泄漏（如「未认证 /me 应 401」误判为 200）。每次测试后清 cookie。
    client.cookies.clear()
    sl = SessionLocal()
    try:
        for tbl in reversed(Base.metadata.sorted_tables):
            sl.execute(tbl.delete())
        # wallet_reservation 属于 FLOW 共享表，不在 Base.metadata 内，需单独清理，
        # 否则跨测试残留 held 预约会污染「按次扣费」E2E 断言。
        for tbl in reversed(wallet_metadata.sorted_tables):
            sl.execute(tbl.delete())
        sl.commit()
    finally:
        sl.close()
