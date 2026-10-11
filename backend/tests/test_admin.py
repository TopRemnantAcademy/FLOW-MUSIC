"""管理后台路由冒烟测试：鉴权、列表/统计、错误处理。"""

from app.config import settings


def test_admin_disabled_when_token_unset(client, monkeypatch):
    # 未配置 admin_token → 503（功能未启用），而非 500
    # [2026-09-10] 이전엔 monkeypatch 없이 전역 settings 를 그대로 봤다 →
    # ".env 에 ADMIN_TOKEN 을 실제로 설정하는 순간" 테스트가 깨지는 **환경 의존 결함**.
    # 테스트가 검증하려는 건 "토큰이 비어 있을 때 503" 이므로 여기서 명시적으로 비운다.
    monkeypatch.setattr(settings, "admin_token", "")
    r = client.get("/api/admin/stats")
    assert r.status_code == 503


def test_admin_requires_token(client, monkeypatch):
    # 已配置但未传 X-Admin-Token → 401
    monkeypatch.setattr(settings, "admin_token", "secret")
    r = client.get("/api/admin/stats", headers={})
    assert r.status_code == 401


def test_admin_stats_and_list(client, monkeypatch):
    monkeypatch.setattr(settings, "admin_token", "secret")
    h = {"X-Admin-Token": "secret"}
    st = client.get("/api/admin/stats", headers=h)
    assert st.status_code == 200
    assert "total" in st.json()
    lst = client.get("/api/admin/songs", headers=h)
    assert lst.status_code == 200
    assert isinstance(lst.json(), list)


def test_admin_filter_by_moderation(client, monkeypatch):
    monkeypatch.setattr(settings, "admin_token", "secret")
    h = {"X-Admin-Token": "secret"}
    r = client.get("/api/admin/songs?moderation_status=pending", headers=h)
    assert r.status_code == 200


def test_admin_approve_missing_song(client, monkeypatch):
    monkeypatch.setattr(settings, "admin_token", "secret")
    h = {"X-Admin-Token": "secret"}
    r = client.post("/api/admin/songs/999999/approve", headers=h)
    assert r.status_code == 404


def test_admin_reject_missing_song(client, monkeypatch):
    monkeypatch.setattr(settings, "admin_token", "secret")
    h = {"X-Admin-Token": "secret"}
    r = client.post("/api/admin/songs/999999/reject", json={"note": "x"}, headers=h)
    assert r.status_code == 404
