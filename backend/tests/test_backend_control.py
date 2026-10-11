"""后台控制测试：引擎/路由/计费由 DB 驱动、可经 admin API 实时改且立即生效。

注意：conftest 在会话级 set_orchestrator(MockProvider()) 并把 enable_moderation=True，
且每个测试后用 _clean 清空全表行，因此本测试内自行建配置行 / 调用 seed_default_config。
"""

import pytest

from app.db import SessionLocal
from app.config import settings
from app.models import EngineConfig, RoutingRule, PricingTier, Song, User
from app.orchestrator.selection import (
    resolve_engine,
    resolve_cost_cny,
    seed_default_config,
    invalidate_config_cache,
    list_engines,
    list_routing,
    list_tiers,
)
from app.orchestrator.client import get_orchestrator, set_orchestrator
from app.orchestrator.mock import MockProvider


ADMIN = {"X-Admin-Token": "test-admin"}


@pytest.fixture(autouse=True)
def _admin_token():
    settings.admin_token = "test-admin"
    yield
    settings.admin_token = ""


def test_resolve_engine_uses_routing_rule():
    db = SessionLocal()
    try:
        invalidate_config_cache()
        db.query(RoutingRule).delete()
        db.query(EngineConfig).delete()
        db.add(
            EngineConfig(
                provider="suno", display_name="S", enabled=True, is_default=True, priority=1
            )
        )
        db.add(
            EngineConfig(provider="ace_step_private", display_name="A", enabled=True, priority=1)
        )
        db.add(
            RoutingRule(
                category="christian_worship",
                provider="ace_step_private",
                priority=200,
                enabled=True,
            )
        )
        db.add(RoutingRule(category="general", provider="suno", priority=100, enabled=True))
        db.commit()
        # 路由规则生效：christian_worship -> ace_step_private（规避通用引擎对宗教词误杀）
        assert resolve_engine(db, "christian_worship") == "ace_step_private"
        # 无专属规则 -> 默认引擎
        assert resolve_engine(db, "instrumental") == "suno"
    finally:
        db.close()


def test_resolve_engine_fallback_when_disabled():
    db = SessionLocal()
    try:
        invalidate_config_cache()
        db.query(RoutingRule).delete()
        db.query(EngineConfig).delete()
        # 路由指向已禁用的引擎 -> 回退默认（仍启用）
        db.add(
            EngineConfig(
                provider="suno", display_name="S", enabled=True, is_default=True, priority=1
            )
        )
        db.add(
            EngineConfig(provider="ace_step_private", display_name="A", enabled=False, priority=1)
        )
        db.add(
            RoutingRule(
                category="christian_worship",
                provider="ace_step_private",
                priority=200,
                enabled=True,
            )
        )
        db.commit()
        assert resolve_engine(db, "christian_worship") == "suno"
    finally:
        db.close()


def test_resolve_cost_cny_returns_engine_cost():
    db = SessionLocal()
    try:
        invalidate_config_cache()
        db.query(EngineConfig).delete()
        db.query(RoutingRule).delete()
        db.add(
            EngineConfig(
                provider="suno",
                display_name="S",
                enabled=True,
                is_default=True,
                cost_per_song_cny=0.5,
            )
        )
        db.add(RoutingRule(category="general", provider="suno", priority=100, enabled=True))
        db.commit()
        # 成本 = 引擎单价 × 生成首数 n（默认 1）
        assert resolve_cost_cny(db, "general") == 0.5
        assert resolve_cost_cny(db, "general", n=2) == 1.0
        # 套餐价不再混入成本解析（归入 resolve_price_cny），成本只来自供应商计费
        db.add(
            PricingTier(tier_key="sacred", tier_name="圣乐", engine_provider="suno", price_cny=2.0)
        )
        db.commit()
        assert resolve_cost_cny(db, "general") == 0.5
    finally:
        db.close()


def test_admin_engines_crud(client):
    # 未带 token -> 鉴权失败
    assert client.get("/api/admin/engines").status_code in (401, 503)
    assert client.get("/api/admin/engines", headers={"X-Admin-Token": "wrong"}).status_code == 401

    r = client.post(
        "/api/admin/engines",
        json={
            "provider": "custom_x",
            "display_name": "X",
            "enabled": True,
            "cost_per_song_cny": 1.5,
        },
        headers=ADMIN,
    )
    assert r.status_code == 201, r.text
    assert r.json()["provider"] == "custom_x"
    assert r.json()["api_key_set"] is False

    # 重复创建 -> 409
    r = client.post(
        "/api/admin/engines", json={"provider": "custom_x", "enabled": True}, headers=ADMIN
    )
    assert r.status_code == 409

    r = client.get("/api/admin/engines", headers=ADMIN)
    assert r.status_code == 200 and any(e["provider"] == "custom_x" for e in r.json())

    r = client.put(
        "/api/admin/engines/custom_x",
        json={"enabled": False, "cost_per_song_cny": 2.0},
        headers=ADMIN,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is False and body["cost_per_song_cny"] == 2.0

    r = client.delete("/api/admin/engines/custom_x", headers=ADMIN)
    assert r.status_code == 200
    # 删除后不存在
    r = client.get("/api/admin/engines/custom_x", headers=ADMIN)
    assert r.status_code == 404


def test_admin_routing_crud(client):
    r = client.post(
        "/api/admin/routing-rules",
        json={
            "category": "christian_worship",
            "provider": "ace_step_private",
            "priority": 200,
            "enabled": True,
        },
        headers=ADMIN,
    )
    assert r.status_code == 201, r.text
    rid = r.json()["id"]

    r = client.put(
        f"/api/admin/routing-rules/{rid}", json={"priority": 300, "enabled": False}, headers=ADMIN
    )
    assert r.status_code == 200 and r.json()["enabled"] is False and r.json()["priority"] == 300

    r = client.get("/api/admin/routing-rules", headers=ADMIN)
    assert r.status_code == 200 and any(x["id"] == rid for x in r.json())

    r = client.delete(f"/api/admin/routing-rules/{rid}", headers=ADMIN)
    assert r.status_code == 200


def test_admin_pricing_crud(client):
    r = client.post(
        "/api/admin/pricing-tiers",
        json={"tier_key": "sacred", "tier_name": "圣乐", "price_cny": 2.0, "enabled": True},
        headers=ADMIN,
    )
    assert r.status_code == 201, r.text
    assert r.json()["tier_key"] == "sacred"

    r = client.put("/api/admin/pricing-tiers/sacred", json={"price_cny": 3.0}, headers=ADMIN)
    assert r.status_code == 200 and r.json()["price_cny"] == 3.0

    r = client.get("/api/admin/pricing-tiers", headers=ADMIN)
    assert r.status_code == 200 and any(t["tier_key"] == "sacred" for t in r.json())

    r = client.delete("/api/admin/pricing-tiers/sacred", headers=ADMIN)
    assert r.status_code == 200


def test_dashboard_cost_and_seed(client):
    db = SessionLocal()
    try:
        invalidate_config_cache()
        db.query(EngineConfig).delete()
        db.query(Song).delete()
        db.query(User).delete()
        db.add(EngineConfig(provider="suno", display_name="S", enabled=True, is_default=True))
        db.commit()

        # seed 端点恢复默认（7 引擎 + 3 路由 + 5 套餐；lite 为 2026-09-04 新增低档位）
        r = client.post("/api/admin/config/seed", headers=ADMIN)
        assert r.status_code == 200, r.text
        d = r.json()
        assert len(d["engines"]) == 7
        assert len(d["routing_rules"]) == 3
        assert len(d["pricing_tiers"]) == 5

        # 成本报表
        u = User(email="dash@x.com", hashed_password="x")
        db.add(u)
        db.commit()
        db.refresh(u)
        db.add(
            Song(
                user_id=u.id,
                title="t",
                engine_name="suno",
                cost_cny=1.5,
                status="completed",
                moderation_status="approved",
            )
        )
        db.commit()
    finally:
        db.close()

    r = client.get("/api/admin/config/dashboard", headers=ADMIN)
    assert r.status_code == 200
    assert r.json()["cost_by_engine"].get("suno") == 1.5
    assert "suno" in [e["provider"] for e in r.json()["engines"]]


def test_generate_with_unimplemented_engine_raises_clear_error():
    # 清除测试注入的 mock 覆盖，才能走到「未接入引擎」的报错分支
    set_orchestrator(None)
    try:
        with pytest.raises(ValueError) as exc:
            get_orchestrator("ace_step_private")
        assert "ace_step_private" in str(exc.value)
    finally:
        set_orchestrator(MockProvider())  # 还原 conftest 注入，避免影响其它测试


def test_resolve_engine_reads_default_seed():
    db = SessionLocal()
    try:
        invalidate_config_cache()
        db.query(EngineConfig).delete()
        db.query(RoutingRule).delete()
        db.query(PricingTier).delete()
        db.commit()
        seed_default_config(db)
        # 默认：general -> mureka（主引擎，已默认启用）
        assert resolve_engine(db, "general") == "mureka"
        # 默认种子里 mureka 已启用且为默认引擎，christian_worship 直接路由到 mureka（合规通道）
        assert resolve_engine(db, "christian_worship") == "mureka"
        assert len(list_engines(db)) == 7
        assert len(list_routing(db)) == 3
        assert len(list_tiers(db)) == 5  # free + lite(저가) + standard + sacred + enterprise
    finally:
        db.close()
