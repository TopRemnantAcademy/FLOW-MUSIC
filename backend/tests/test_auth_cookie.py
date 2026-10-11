"""[SEC] 2026-09-13：HttpOnly cookie 认证回归。

防 localStorage 存 JWT / 管理员密钥（XSS 可窃取 = 全账号/后台接管）：
- 登录/注册把 JWT 写入 HttpOnly+SameSite cookie；JS 不可读。
- 后续请求不带 Authorization 头、仅凭 cookie 即被识别（浏览器 credentials: include 等价）。
- 登出由后端 delete_cookie 清除。
- 管理员密钥经 /api/auth/admin-login 写入 flow_admin HttpOnly cookie，require_admin 同时接受头与 cookie。
"""

import pytest
from fastapi.testclient import TestClient

import app.main as _app_main
from app.config import settings

fastapi_app = _app_main.app

_PWD = "Str0ng!Passw0rd"


def _register(c: TestClient, email: str):
    return c.post("/api/auth/register", json={"email": email, "password": _PWD})


def test_login_sets_httponly_cookie_and_no_secure_on_http():
    with TestClient(fastapi_app) as c:
        r = _register(c, "cookie1@test.com")
        assert r.status_code == 200
        sc = r.headers.get("set-cookie", "")
        assert "flow_auth=" in sc, "登录必须种下 flow_auth cookie"
        assert "HttpOnly" in sc, "cookie 必须是 HttpOnly（阻断 JS 读取）"
        # 明文 HTTP 测试环境不应打 Secure，否则浏览器会拒写 → 本地 dev 不可用
        assert "Secure" not in sc
        # 不带 Authorization 头，仅凭 cookie 即可被识别
        me = c.get("/api/auth/me")
        assert me.status_code == 200
        assert me.json()["email"] == "cookie1@test.com"


def test_protected_endpoint_401_without_cookie_or_header():
    with TestClient(fastapi_app) as c:
        assert c.get("/api/auth/me").status_code == 401


def test_logout_clears_cookie():
    with TestClient(fastapi_app) as c:
        _register(c, "cookie2@test.com")
        assert c.get("/api/auth/me").status_code == 200
        assert c.post("/api/auth/logout").status_code == 200
        # cookie 被清除后再次访问 → 401
        assert c.get("/api/auth/me").status_code == 401


def test_admin_login_sets_cookie_and_grants_access(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "admin_token", "test-admin-secret")
    with TestClient(fastapi_app) as c:
        # 无 cookie/头 → 401（admin_token 已配置，故是 401 而非 503）
        assert c.get("/api/admin/stats").status_code == 401
        r = c.post("/api/auth/admin-login", json={"key": "test-admin-secret"})
        assert r.status_code == 200
        sc = r.headers.get("set-cookie", "")
        assert "flow_admin=" in sc and "HttpOnly" in sc
        # 仅凭 flow_admin cookie（无 X-Admin-Token 头）即可访问管理端点
        assert c.get("/api/admin/stats").status_code == 200
        # 登出清除 cookie
        assert c.post("/api/auth/admin-logout").status_code == 200
        assert c.get("/api/admin/stats").status_code == 401


def test_admin_login_rejects_wrong_key(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "admin_token", "test-admin-secret")
    with TestClient(fastapi_app) as c:
        r = c.post("/api/auth/admin-login", json={"key": "wrong"})
        assert r.status_code == 401
        assert "flow_admin=" not in r.headers.get("set-cookie", "")
