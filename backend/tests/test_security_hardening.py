"""2026-09-13 安全审计 — 高危项加固的回归测试。

每条测试都对应一个已确认的真实缺陷（file:line 见注释），且**在加固前必然失败**，
用于锁定修复不被回退。
"""

from app.config import settings as _settings
from fastapi.testclient import TestClient


def _register(client: TestClient, email: str) -> dict:
    r = client.post("/api/auth/register", json={"email": email, "password": "pw123456"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _link(client: TestClient, h: dict, subscriber_id: str) -> None:
    _settings.flow_internal_token = "test-flow-internal"
    r = client.post("/api/auth/flow-link", json={"subscriber_id": subscriber_id}, headers=h)
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------- CWE-306
def test_assist_endpoints_require_auth(client: TestClient):
    """/assist/style 与 /assist/lyrics/format 原为匿名端点（songs.py:1700/1738），
    可被脚本高频调用烧掉按量计费的上游 LLM token。"""
    assert (
        client.post("/api/songs/assist/style", json={"text": "x", "mode": "style"}).status_code
        == 401
    )
    assert client.post("/api/songs/assist/lyrics/format", json={"text": "x"}).status_code == 401


def test_assist_endpoints_work_when_authed(client: TestClient):
    h = _register(client, "sec_assist@test.com")
    r = client.post("/api/songs/assist/style", json={"text": "忧伤", "mode": "style"}, headers=h)
    assert r.status_code == 200, r.text
    r2 = client.post("/api/songs/assist/lyrics/format", json={"text": "第一行"}, headers=h)
    assert r2.status_code == 200, r2.text


# ---------------------------------------------------------------- CWE-639
def test_flow_link_cannot_hijack_other_users_subscriber(client: TestClient):
    """原 auth.py:268-272 允许任意登录用户把他人 subscriber_id 改写到自己账号
    （身份劫持 + 扣费嫁祸）。"""
    hA = _register(client, "sec_hijack_a@test.com")
    hB = _register(client, "sec_hijack_b@test.com")
    _link(client, hA, "sub_shared_1")
    r = client.post("/api/auth/flow-link", json={"subscriber_id": "sub_shared_1"}, headers=hB)
    assert r.status_code == 409, r.text


def test_flow_link_cannot_rebind_own_account_to_another_subscriber(client: TestClient):
    h = _register(client, "sec_rebind@test.com")
    _link(client, h, "sub_rebind_1")
    # 改绑到另一个 FLOW 身份 → 拒绝
    r = client.post("/api/auth/flow-link", json={"subscriber_id": "sub_rebind_2"}, headers=h)
    assert r.status_code == 409, r.text
    # 幂等：重复绑同一个 → 200
    r2 = client.post("/api/auth/flow-link", json={"subscriber_id": "sub_rebind_1"}, headers=h)
    assert r2.status_code == 200, r2.text


# ---------------------------------------------------------------- CWE-840
def test_mock_subscribe_denied_when_flag_off(client: TestClient):
    """原默认 billing_mode=offline 即允许自助升到任意档位（绕过支付）。
    现需显式打开 enable_mock_subscribe。"""
    h = _register(client, "sec_sub@test.com")
    saved = _settings.enable_mock_subscribe
    _settings.enable_mock_subscribe = False
    try:
        r = client.post("/api/auth/subscribe", json={"tier_key": "standard"}, headers=h)
        assert r.status_code == 403, r.text
    finally:
        _settings.enable_mock_subscribe = saved


# ---------------------------------------------------------------- CWE-693
def test_security_headers_present(client: TestClient):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.headers.get("X-Content-Type-Options") == "nosniff"
    assert r.headers.get("X-Frame-Options") == "DENY"
    assert r.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"
    assert "Content-Security-Policy" in r.headers
    assert "Permissions-Policy" in r.headers


# ---------------------------------------------------------------- CWE-200
def test_docs_disabled_by_default(client: TestClient):
    """/docs、/openapi.json 默认关闭，避免暴露全部接口结构。"""
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404
    assert client.get("/redoc").status_code == 404


# ---------------------------------------------------------------- CWE-942
def test_plaza_cors_only_reflects_allowlisted_origin(client: TestClient):
    """/api/plaza 不再原样反射任意 Origin（main.py:74/82）。"""
    r = client.get("/api/plaza", headers={"Origin": "https://evil.example.com"})
    # 非白名单 → 回落 "*"（匿名只读 + credentials=false，安全），绝不回显 evil 源
    assert r.headers.get("Access-Control-Allow-Origin") == "*"
    assert r.headers.get("Access-Control-Allow-Credentials") == "false"

    allowed = _settings.cors_origin_list[0]
    r2 = client.get("/api/plaza", headers={"Origin": allowed})
    assert r2.headers.get("Access-Control-Allow-Origin") == allowed


# ---------------------------------------------------------------- CWE-918
def test_upload_rejects_internal_audio_url(client: TestClient):
    """H6: POST /api/songs/upload 的 audio_url 会原样交给 Mureka 去抓取
    （orchestrator/mureka.py:431），本服务在此充当第三方 SSRF 的混淆代理。
    加固前该值完全不校验，可让 Mureka 去拉 169.254.169.254 / 内网地址。"""
    h = _register(client, "sec_upload@test.com")
    for bad in (
        "http://169.254.169.254/latest/meta-data/",
        "http://127.0.0.1:8080/internal.wav",
        "http://10.0.0.5/internal.wav",
    ):
        r = client.post("/api/songs/upload", json={"audio_url": bad}, headers=h)
        assert r.status_code == 400, f"{bad} 应被拒绝，实际 {r.status_code}: {r.text}"


def test_upload_rejects_non_http_scheme(client: TestClient):
    """file:// / gopher:// 等 scheme 必须拦下（守卫只放行 http/https）。"""
    h = _register(client, "sec_upload2@test.com")
    r = client.post("/api/songs/upload", json={"audio_url": "file:///etc/passwd"}, headers=h)
    assert r.status_code == 400, r.text
