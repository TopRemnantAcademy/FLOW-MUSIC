"""APK 第一步门禁：Cookie 契约 + 同站拓扑 回归测试。

为什么需要这个测试
------------------
APK 方案（PLAN_MUSIC_APK_2026-09-13.md 约束 A）成立的前提是：

  1. 后端用 HttpOnly + SameSite=lax Cookie 维持登录态，且 /api/auth/me 只认 Cookie；
  2. WebView 的应用域与 API 域「同站」，否则 Lax 语义下 Cookie 不会被携带。

这两条一旦被后续改动破坏（例如把 cookie_samesite 改成 "none"、
把 secure 无条件置真、或把 APK 域名换到另一个主域），
表现都是「登录接口 200，但 App 里所有请求 401」——**本地 curl/httpx 完全测不出来**，
只有真机 WebView 会暴露。因此把契约钉死在测试里。

注意：同站/跨站的最终执行者是浏览器，本测试断言的是「可注册域是否相同」
（RFC 6265bis §5.2），即浏览器判定的输入；浏览器行为本身需在带 Chrome 的
机器上用 `backend/scripts/probe_apk_samesite.py --serve` 收尾验证。
"""

import re
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from probe_apk_samesite import is_same_site, registrable_domain  # noqa: E402

import app.main as _app_main  # noqa: E402
import app.models  # noqa: E402,F401  确保模型注册

fastapi_app = _app_main.app

_UID = {"n": 0}


def _email() -> str:
    _UID["n"] += 1
    return f"apk-samesite-{_UID['n']}@example.com"


def _register(client: TestClient):
    return client.post("/api/auth/register", json={"email": _email(), "password": "Probe12345!"})


def _set_cookie_of(resp) -> str:
    return " ; ".join(resp.headers.get_list("set-cookie")).lower()


# ---------------------------------------------------------------------------
# 契约 1：Cookie 属性
# ---------------------------------------------------------------------------


def test_register_issues_httponly_samesite_lax_cookie():
    """HTTPS 下必须签发 HttpOnly + SameSite=lax + Secure 的身份 Cookie。"""
    with TestClient(fastapi_app, base_url="https://api.music.test") as c:
        resp = _register(c)
    assert resp.status_code == 200
    ck = _set_cookie_of(resp)
    assert "flow_auth=" in ck
    assert "httponly" in ck, "HttpOnly 缺失 → XSS 可窃取令牌"
    assert "samesite=lax" in ck, "SameSite 被改动会影响 APK/CSRF 语义"
    assert "path=/" in ck
    assert "secure" in ck, "HTTPS 下必须 Secure"


def test_http_local_does_not_set_secure():
    """HTTP 下不得设 Secure，否则 http://localhost 的 WebView 收不到 Cookie。"""
    with TestClient(fastapi_app, base_url="http://api.music.test") as c:
        resp = _register(c)
    assert resp.status_code == 200
    ck = _set_cookie_of(resp)
    assert "flow_auth=" in ck
    assert "secure" not in ck, "HTTP 下带 Secure 会被 WebView 直接丢弃 → 本地/调试环境无法登录"


# ---------------------------------------------------------------------------
# 契约 2：/me 只认 Cookie
# ---------------------------------------------------------------------------


def test_me_requires_cookie():
    """无 Cookie → 401；带 Cookie → 200。

    这条是「APK 登录态失效」故障面的最小复现单元：只要登录态挂在 Cookie 上，
    任何导致 Cookie 不被携带的因素，最终都在这里表现为 401。
    """
    with TestClient(fastapi_app, base_url="http://api.music.test") as c:
        reg = _register(c)
        c.cookies.clear()
        assert c.get("/api/auth/me").status_code == 401

        token = None
        for raw in reg.headers.get_list("set-cookie"):
            m = re.search(r"flow_auth=([^;]+)", raw)
            if m:
                token = m.group(1)
        assert token, "注册未签发 flow_auth Cookie"

        c.cookies.clear()
        c.cookies.set("flow_auth", token, domain="api.music.test")
        assert c.get("/api/auth/me").status_code == 200


# ---------------------------------------------------------------------------
# 契约 3：同站拓扑（浏览器 SameSite 判定的输入）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "app_origin,api_url,expected",
    [
        # 现状直连：WebView 源 localhost vs 公网 API → 跨站 → 登录态失效
        ("http://localhost", "https://api.flow.example.com/x", False),
        # 推荐方案：兄弟子域同父域 → 同站 → Cookie 正常携带
        ("https://music.flow.example.com", "https://api.flow.example.com/x", True),
        # 端口/协议不同不影响同站判定
        ("http://music.flow.example.com:4174", "https://api.flow.example.com/x", True),
        # 换个主域就失效——用于防止将来误换 CDN/API 域名
        ("https://music.flow.example.com", "https://api.other-example.com/x", False),
        # 多标签公有后缀：github.io 下两者是不同站点
        ("https://flow.github.io", "https://api.github.io/x", False),
    ],
)
def test_same_site_topology(app_origin, api_url, expected):
    assert is_same_site(app_origin, api_url) is expected


def test_registrable_domain_basics():
    assert registrable_domain("api.music.test") == "music.test"
    assert registrable_domain("MUSIC.TEST") == "music.test"
    assert registrable_domain("a.b.music.test") == "music.test"
    assert registrable_domain("flow.github.io") == "flow.github.io"
    assert registrable_domain("api.github.io") == "api.github.io"
    assert registrable_domain("127.0.0.1") == "127.0.0.1"
