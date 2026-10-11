"""管理后台 RBAC + 审计日志(GAP-006) 测试。"""

from app.db import SessionLocal
from app.models import AuditLog


def _h(token):
    return {"X-Admin-Token": token}


def test_viewer_cannot_mutate(client, monkeypatch):
    monkeypatch.setattr("app.config.settings.admin_token", "super")
    monkeypatch.setattr("app.config.settings.admin_viewer_token", "viewer")
    # viewer 读允许
    r = client.get("/api/admin/engines", headers=_h("viewer"))
    assert r.status_code == 200
    # viewer 变更被 403
    r = client.post(
        "/api/admin/engines", json={"provider": "vx", "enabled": True}, headers=_h("viewer")
    )
    assert r.status_code == 403
    # 错误令牌 401
    r = client.post(
        "/api/admin/engines", json={"provider": "vx", "enabled": True}, headers=_h("wrong")
    )
    assert r.status_code == 401


def test_super_can_mutate_and_audit_logged(client, monkeypatch):
    monkeypatch.setattr("app.config.settings.admin_token", "super")
    monkeypatch.setattr("app.config.settings.admin_viewer_token", "viewer")
    h = _h("super")
    r = client.post(
        "/api/admin/engines",
        json={"provider": "sx", "enabled": True, "api_key": "secret-xyz"},
        headers=h,
    )
    assert r.status_code == 201, r.text
    assert r.json()["api_key_set"] is True  # 仅脱敏标记，不返回明文

    # 审计日志已记录该次变更
    db = SessionLocal()
    try:
        row = db.query(AuditLog).order_by(AuditLog.id.desc()).first()
        assert row is not None
        assert row.actor_role == "super"
        assert row.method == "POST"
        assert row.path == "/api/admin/engines"
        assert row.actor_token_prefix == "super"[:6]
    finally:
        db.close()

    # 审计接口可见该记录
    r = client.get("/api/admin/audit", headers=h)
    assert r.status_code == 200
    assert any(a["method"] == "POST" and a["path"] == "/api/admin/engines" for a in r.json())
