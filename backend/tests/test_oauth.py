"""GAP-003 OAuth 授权码登录（Google / Kakao / Naver）—— 22 例回归。

设计原则：**全 mock、不出网**。
`app.oauth.tokens.exchange_code` / `fetch_userinfo` 是本项目唯一 OAuth 出网点
（见该模块 docstring），测试直接 monkeypatch 这两个函数即可覆盖全部路径 ——
不依赖外网、不依赖 provider 沙箱、CI 稳定。

覆盖矩阵（每条都对应一个真实可被写错的决策，而非「为凑数而测」）：
  A. state 票据（签名/寿命/provider 绑定/不可当会话令牌）     6 例
  B. 凭据与 provider 可用性（含 Kakao 空 Secret）             4 例
  C. 邮箱合并策略（仅 email_verified=true）                  6 例
  D. 失败路径与 reason 白名单/令牌不外泄                      4 例
  E. 路由契约与 providers 状态端点                            2 例
"""

import jwt
import pytest
from fastapi.testclient import TestClient

from app import oauth
from app.config import settings
from app.db import SessionLocal
from app.models import User
from app.oauth import providers as providers_mod
from app.oauth import service as service_mod
from app.oauth import tokens as tokens_mod
from app.routers.auth import create_token

_PWD = "Str0ng!Passw0rd"


# ── 公共装置 ──────────────────────────────────────────────────────────────────


@pytest.fixture
def oauth_cfg(monkeypatch):
    """打开三家凭据并把回跳指向本地前端。

    ⚠ 每家 provider 的 enabled 判定都**实时读 settings**（providers.py 用工厂函数
    而非 import 期快照），所以 monkeypatch settings 即生效 —— 这也是那处设计的用途。
    """
    monkeypatch.setattr(settings, "jwt_secret", "test-secret-for-pytest-only")
    monkeypatch.setattr(settings, "google_client_id", "g-client")
    monkeypatch.setattr(settings, "oauth_google_client_secret", "g-secret")
    monkeypatch.setattr(settings, "oauth_kakao_client_id", "k-client")
    monkeypatch.setattr(settings, "oauth_kakao_client_secret", "")  # Kakao 允许空 Secret
    monkeypatch.setattr(settings, "oauth_naver_client_id", "n-client")
    monkeypatch.setattr(settings, "oauth_naver_client_secret", "n-secret")
    monkeypatch.setattr(settings, "oauth_redirect_base_url", "http://api.test")
    monkeypatch.setattr(settings, "oauth_frontend_redirect_url", "http://web.test/account")
    yield


def _mock_identity(monkeypatch, sub, email="", email_verified=False):
    """把出网段替换为固定身份，并记录被调用的 provider/参数。"""
    calls = {}

    def fake_exchange(provider, code, redirect_uri):
        calls["exchange"] = (provider.name, code, redirect_uri)
        return {"access_token": "AT"}

    def fake_userinfo(provider, access_token):
        calls["userinfo"] = (provider.name, access_token)
        return {"sub": sub, "email": email, "email_verified": email_verified}

    monkeypatch.setattr(tokens_mod, "exchange_code", fake_exchange)
    monkeypatch.setattr(tokens_mod, "fetch_userinfo", fake_userinfo)
    return calls


def _users(db=None):
    sl = db or SessionLocal()
    try:
        return sl.query(User).all()
    finally:
        if db is None:
            sl.close()


# ── A. state 票据（6 例）──────────────────────────────────────────────────────


def test_state_roundtrip_returns_nonce(oauth_cfg):
    """签发→校验 走通，且 nonce 与 sub 一致。"""
    st = oauth.issue_state("google")
    nonce = oauth.verify_state(st, "google")
    payload = jwt.decode(st, settings.jwt_secret, algorithms=["HS256"],
                         audience=settings.jwt_audience, issuer=settings.jwt_issuer)
    assert nonce == payload["sub"]


def test_state_scope_format_is_provider_and_nonce_bound(oauth_cfg):
    """scope 必须是 `oauth:state:{provider}:{nonce}` —— 三段都参与绑定。"""
    st = oauth.issue_state("kakao")
    payload = jwt.decode(st, settings.jwt_secret, algorithms=["HS256"],
                         audience=settings.jwt_audience, issuer=settings.jwt_issuer)
    assert payload["scope"] == f"oauth:state:kakao:{payload['sub']}"


def test_state_signed_with_wrong_secret_rejected(oauth_cfg):
    """换一把密钥签的 state 必须被拒（防伪造 state = 防 CSRF 的根）。"""
    forged = jwt.encode(
        {"sub": "x", "scope": "oauth:state:google:x", "exp": 9999999999,
         "iss": settings.jwt_issuer, "aud": settings.jwt_audience},
        "another-secret", algorithm="HS256",
    )
    with pytest.raises(oauth.state.StateError):
        oauth.verify_state(forged, "google")


def test_state_expired_rejected(oauth_cfg):
    """过期 state 被拒（短寿命票据的意义所在）。"""
    st = oauth.issue_state("google", ttl_seconds=-1)
    with pytest.raises(oauth.state.StateError):
        oauth.verify_state(st, "google")


def test_state_cannot_cross_providers(oauth_cfg):
    """Google 签的 state 不能在 Kakao 回调上通过（跨 provider 混用 = 可被利用的洞）。"""
    st = oauth.issue_state("google")
    with pytest.raises(oauth.state.StateError):
        oauth.verify_state(st, "kakao")


def test_state_ticket_is_not_a_session_token(oauth_cfg):
    """state 票据**不能**当登录令牌用。

    `get_current_user` 明文拒绝带 scope 声明的令牌（auth.py），本用例把这条
    交叉约束钉死：否则 state 一旦泄露即可直接当会话 —— 那 state 就成了后门。
    """
    st = oauth.issue_state("google")
    with TestClient(__import__("app.main", fromlist=["app"]).app) as c:
        assert c.get("/api/auth/me", headers={"Authorization": f"Bearer {st}"}).status_code == 401


# ── B. 凭据与 provider 可用性（4 例）─────────────────────────────────────────


def test_all_three_providers_enabled_when_configured(oauth_cfg):
    assert {n: p.enabled for n, p in providers_mod.all_providers().items()} == {
        "google": True, "kakao": True, "naver": True,
    }


def test_provider_disabled_without_client_id(oauth_cfg, monkeypatch):
    monkeypatch.setattr(settings, "oauth_naver_client_id", "")
    assert providers_mod.get_provider("naver").enabled is False


def test_google_requires_nonempty_secret(oauth_cfg, monkeypatch):
    """Google 是机密客户端：Secret 为空即视为未配置（不能像 Kakao 那样放行）。"""
    monkeypatch.setattr(settings, "oauth_google_client_secret", "")
    assert providers_mod.get_provider("google").enabled is False


def test_kakao_enabled_with_empty_client_secret(oauth_cfg, monkeypatch):
    """Kakao 允许空 Client Secret（官方可关闭该增强）—— 不得因空 Secret 判为未启用。"""
    monkeypatch.setattr(settings, "oauth_kakao_client_secret", "")
    p = providers_mod.get_provider("kakao")
    assert p.enabled is True
    assert p.allow_empty_secret is True


# ── C. 邮箱合并策略（6 例）───────────────────────────────────────────────────


def test_verified_email_merges_into_existing_account(client, oauth_cfg, monkeypatch):
    """email_verified=true + 同邮箱既有账号 → 合并（登录到该账号）并补绑 identity。"""
    client.post("/api/auth/register", json={"email": "merge@test.com", "password": _PWD})
    db = SessionLocal()
    uid = db.query(User).filter(User.email == "merge@test.com").one().id
    db.close()

    _mock_identity(monkeypatch, "g-sub-1", "merge@test.com", email_verified=True)
    r = client.get("/api/auth/oauth/google/callback", follow_redirects=False,
                   params={"code": "C", "state": oauth.issue_state("google")})
    assert r.status_code == 302
    assert "oauth=1" in r.headers["location"]

    db = SessionLocal()
    try:
        assert db.query(User).count() == 1, "必须合并而非新建第二个账号"
        u = db.get(User, uid)
        assert (u.oauth_provider, u.oauth_sub) == ("google", "g-sub-1")
    finally:
        db.close()


def test_unverified_email_does_not_merge(client, oauth_cfg, monkeypatch):
    """email_verified=false + 同邮箱 → **不合并**，落合成地址（防账号接管）。"""
    client.post("/api/auth/register", json={"email": "victim@test.com", "password": _PWD})

    _mock_identity(monkeypatch, "k-sub-9", "victim@test.com", email_verified=False)
    r = client.get("/api/auth/oauth/kakao/callback", follow_redirects=False,
                   params={"code": "C", "state": oauth.issue_state("kakao")})
    assert "oauth=1" in r.headers["location"]

    db = SessionLocal()
    try:
        assert db.query(User).count() == 2, "未验证邮箱不得合并进既有账号"
        victim = db.query(User).filter(User.email == "victim@test.com").one()
        assert victim.oauth_provider is None, "既有账号不得被未验证邮箱绑定"
        oauth_user = db.query(User).filter(
            User.email == "k-sub-9@kakao.oauth.local").one()
        assert oauth_user.oauth_sub == "k-sub-9"
    finally:
        db.close()


def test_missing_email_falls_back_to_synthetic(client, oauth_cfg, monkeypatch):
    """provider 未返回邮箱（Kakao 未开通邮箱权限）→ `{sub}@kakao.oauth.local`。"""
    _mock_identity(monkeypatch, "k-sub-42", "", email_verified=False)
    r = client.get("/api/auth/oauth/kakao/callback", follow_redirects=False,
                   params={"code": "C", "state": oauth.issue_state("kakao")})
    assert "oauth=1" in r.headers["location"]

    db = SessionLocal()
    try:
        u = db.query(User).filter(User.email == "k-sub-42@kakao.oauth.local").one()
        assert u.email.endswith(".oauth.local")
    finally:
        db.close()


def test_synthetic_domain_never_collides_with_real_address(client, oauth_cfg, monkeypatch):
    """合成域是 `.oauth.local`（RFC 6761 保留 TLD）—— 不占用任何真实可投递地址。"""
    assert service_mod.SYNTHETIC_EMAIL_DOMAIN == "oauth.local"
    assert service_mod.synthetic_email("naver", "abc") == "abc@naver.oauth.local"


def test_second_login_by_identity_is_idempotent(client, oauth_cfg, monkeypatch):
    """同 (provider,sub) 二次登录 → 复用同一账号，不重复建号。"""
    _mock_identity(monkeypatch, "g-sub-77", "repeat@test.com", email_verified=True)
    for _ in range(2):
        client.get("/api/auth/oauth/google/callback", follow_redirects=False,
                   params={"code": "C", "state": oauth.issue_state("google")})
    db = SessionLocal()
    try:
        assert db.query(User).count() == 1
    finally:
        db.close()


def test_rebinding_linked_identity_is_refused(client, oauth_cfg, monkeypatch):
    """既有账号已绑其它 OAuth 身份 → 拒绝隐式改绑（共享邮箱场景的接管面）。"""
    client.post("/api/auth/register", json={"email": "shared@test.com", "password": _PWD})
    db = SessionLocal()
    try:
        u = db.query(User).filter(User.email == "shared@test.com").one()
        u.oauth_provider, u.oauth_sub = "google", "g-existing"
        db.commit()
    finally:
        db.close()

    _mock_identity(monkeypatch, "k-new", "shared@test.com", email_verified=True)
    r = client.get("/api/auth/oauth/kakao/callback", follow_redirects=False,
                   params={"code": "C", "state": oauth.issue_state("kakao")})
    assert r.status_code == 302
    assert "oauth=0" in r.headers["location"]
    assert "reason=identity_already_linked" in r.headers["location"]

    db = SessionLocal()
    try:
        u = db.query(User).filter(User.email == "shared@test.com").one()
        assert (u.oauth_provider, u.oauth_sub) == ("google", "g-existing"), "绑定不得被改写"
    finally:
        db.close()


# ── D. 失败路径与令牌安全（4 例）─────────────────────────────────────────────


def test_callback_failure_redirects_with_reason(client, oauth_cfg, monkeypatch):
    """换令牌失败 → 302 `?oauth=0&reason=exchange_failed`（不 500、不回 JSON）。"""
    def boom(provider, code, redirect_uri):
        raise tokens_mod.OAuthExchangeError("provider 400")

    monkeypatch.setattr(tokens_mod, "exchange_code", boom)
    r = client.get("/api/auth/oauth/google/callback", follow_redirects=False,
                   params={"code": "C", "state": oauth.issue_state("google")})
    assert r.status_code == 302
    assert "oauth=0" in r.headers["location"] and "reason=exchange_failed" in r.headers["location"]


def test_invalid_state_redirects_and_never_calls_provider(client, oauth_cfg, monkeypatch):
    """state 无效 → 302 state_invalid，且**不触发**任何出网（少一次凭据外发）。"""
    called = {"n": 0}

    def counting_exchange(provider, code, redirect_uri):
        called["n"] += 1
        return {"access_token": "AT"}

    monkeypatch.setattr(tokens_mod, "exchange_code", counting_exchange)
    r = client.get("/api/auth/oauth/google/callback", follow_redirects=False,
                   params={"code": "C", "state": "not-a-jwt"})
    assert "reason=state_invalid" in r.headers["location"]
    assert called["n"] == 0, "state 不过就不该把授权码发给 provider"


def test_token_never_leaks_into_redirect_url(client, oauth_cfg, monkeypatch):
    """成功回跳的 Location 里**不得**含任何令牌 —— 令牌只走 HttpOnly cookie。

    URL 会进浏览器历史 / Referer / 代理日志，一旦带上令牌等于公开泄露会话。
    """
    _mock_identity(monkeypatch, "g-sub-leak", "leak@test.com", email_verified=True)
    r = client.get("/api/auth/oauth/google/callback", follow_redirects=False,
                   params={"code": "C", "state": oauth.issue_state("google")})
    loc = r.headers["location"]
    assert loc == "http://web.test/account?oauth=1", loc
    token = create_token(1)  # 形状参考
    assert token not in loc and "access_token" not in loc and "token=" not in loc
    # 令牌在 cookie 上（HttpOnly）—— 与邮箱登录同一条腿。
    assert "flow_auth=" in r.headers.get("set-cookie", "")
    assert "HttpOnly" in r.headers.get("set-cookie", "")


def test_unknown_provider_reason_is_whitelisted(client, oauth_cfg):
    """未知 provider 名 → 302 unknown_provider；白名单外的 reason 一律归一为 failed。

    防注入：攻击者若能控制 reason 值，就能把任意文本带进前端 URL 渲染。
    """
    r = client.get("/api/auth/oauth/facebook/login", follow_redirects=False)
    assert r.status_code == 302
    assert "reason=unknown_provider" in r.headers["location"]
    # 白名单过滤：伪造的 reason 不得原样透传
    assert service_mod.oauth_callback_redirect(False, "<script>alert(1)</script>").endswith(
        "reason=failed"
    )


# ── E. 路由契约（2 例）───────────────────────────────────────────────────────


def test_login_redirects_to_authorize_url_with_state_and_redirect_uri(client, oauth_cfg):
    """发起登录 → 302 到 provider 授权页，且带 state 与逐字一致的 redirect_uri。"""
    r = client.get("/api/auth/oauth/naver/login", follow_redirects=False)
    assert r.status_code == 302
    loc = r.headers["location"]
    assert loc.startswith("https://nid.naver.com/oauth2.0/authorize?")
    assert "client_id=n-client" in loc
    assert "state=" in loc
    # redirect_uri 必须 URL 编码后逐字一致（未编码的 `://` 会被 provider 解析错）
    assert "redirect_uri=http%3A%2F%2Fapi.test%2Fapi%2Fauth%2Foauth%2Fnaver%2Fcallback" in loc
    # Naver 不在授权 URL 传 scope（权限在后台登记）
    assert "scope=" not in loc

    r2 = client.get("/api/auth/oauth/google/login", follow_redirects=False)
    assert "scope=openid+email+profile" in r2.headers["location"]
    assert "accounts.google.com" in r2.headers["location"]


def test_providers_status_endpoint_reports_enabled_flags(client, oauth_cfg, monkeypatch):
    """前端据 /oauth/providers 决定按钮显隐；未配置凭据的必须为 false。"""
    r = client.get("/api/auth/oauth/providers")
    assert r.status_code == 200
    assert {k: v["enabled"] for k, v in r.json()["providers"].items()} == {
        "google": True, "kakao": True, "naver": True,
    }
    monkeypatch.setattr(settings, "oauth_kakao_client_id", "")
    after = client.get("/api/auth/oauth/providers").json()["providers"]
    assert after["kakao"]["enabled"] is False
    assert after["google"]["enabled"] is True
