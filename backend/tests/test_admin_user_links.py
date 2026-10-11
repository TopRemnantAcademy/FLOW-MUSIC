"""跨域账号桥接只读查询端点测试 (GET /api/admin/user-links)。

不依赖真实支付/外部服务；直接复用 flow-link 建立映射，再用 admin 端点回查。
核心是 P0 的「跨账号可见性」落地 + 零 PII 外泄（email 仅存在性标记）。
"""

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.config import settings as _settings
from app.db import SessionLocal
from app.billing.models import Subscription


ADMIN = {"X-Admin-Token": "test-admin"}


def _register(client: TestClient, email: str = "ulink@test.com") -> dict:
    r = client.post("/api/auth/register", json={"email": email, "password": "pw123456"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _link(client: TestClient, h: dict, subscriber_id: str = "sub_ul_1") -> int:
    _settings.flow_internal_token = "test-flow-internal"
    r = client.post("/api/auth/flow-link", json={"subscriber_id": subscriber_id}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()["music_user_id"]


def test_user_links_requires_admin(client: TestClient):
    # 未配置 admin_token → 503（功能未启用）
    _settings.admin_token = ""
    r = client.get("/api/admin/user-links?subscriber_id=x")
    assert r.status_code == 503

    # 已配置但未传令牌 → 401
    _settings.admin_token = "test-admin"
    assert client.get("/api/admin/user-links?subscriber_id=x").status_code == 401
    # 错误令牌 → 401
    assert (
        client.get(
            "/api/admin/user-links?subscriber_id=x", headers={"X-Admin-Token": "bad"}
        ).status_code
        == 401
    )


def test_user_links_lookup_by_subscriber(client: TestClient):
    _settings.admin_token = "test-admin"
    h = _register(client)
    music_user_id = _link(client, h, subscriber_id="sub_ul_lookup")

    r = client.get("/api/admin/user-links?subscriber_id=sub_ul_lookup", headers=ADMIN)
    assert r.status_code == 200, r.text
    data = r.json()
    assert isinstance(data, list) and len(data) == 1
    row = data[0]
    assert row["flow_subscriber_id"] == "sub_ul_lookup"
    assert row["music_user_id"] == music_user_id
    assert row["music_email_present"] is True  # 存在性标记，非原文
    assert row["music_tier_key"] is None  # 未订阅 → free 按 None 计（resolve_limits 会 fallback）
    assert row["subscription"] is None
    assert row["linked_at"] is not None


def test_user_links_includes_subscription(client: TestClient):
    _settings.admin_token = "test-admin"
    h = _register(client, email="ulink_sub@test.com")
    music_user_id = _link(client, h, subscriber_id="sub_ul_sub")

    with SessionLocal() as s:
        s.add(
            Subscription(
                user_id=music_user_id,
                tier_key="standard",
                provider="stripe",
                status="active",
                current_period_end=datetime(2026, 12, 31, tzinfo=timezone.utc),
            )
        )
        s.commit()

    r = client.get("/api/admin/user-links?subscriber_id=sub_ul_sub", headers=ADMIN)
    assert r.status_code == 200, r.text
    sub = r.json()[0]["subscription"]
    assert sub is not None
    assert sub["tier_key"] == "standard"
    assert sub["provider"] == "stripe"
    assert sub["status"] == "active"
    assert sub["current_period_end"].startswith("2026-12-31")


def test_user_links_reverse_lookup(client: TestClient):
    _settings.admin_token = "test-admin"
    h = _register(client, email="ulink_rev@test.com")
    music_user_id = _link(client, h, subscriber_id="sub_ul_rev")

    r = client.get(f"/api/admin/user-links?music_user_id={music_user_id}", headers=ADMIN)
    assert r.status_code == 200, r.text
    data = r.json()
    assert len(data) == 1
    assert data[0]["music_user_id"] == music_user_id
    assert data[0]["flow_subscriber_id"] == "sub_ul_rev"
