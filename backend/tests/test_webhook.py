"""GAP-022 Webhook：SSRF 拦截 / HMAC 签名 / 指数退避重试 / 失败落库 / 投递日志。

钉住的契约：

1. **SSRF 注册闸门**：169.254.169.254（云元数据）/ 127.0.0.1 / ::1 / 10.x / 192.168.x /
   172.16.x / localhost / `file://` / `gopher://` 一律 400；公网 URL 放行。
2. **域名解析也要逐个校验**：多 A 记录里混一个内网地址不能"只看第一个就放行"。
3. **HMAC 签名可验证**：`X-Flow-Signature` = `t=<ts>,v1=<hex>`，验签通过；改一个字节即失败；
   过期时间戳失败；错密钥失败。
4. **指数退避重试**：5xx/网络错误重试到 5 次，退避 1/2/4/8 秒；**4xx 不重试**。
5. **失败落库**：失败也留 WebhookDelivery 行（attempts / status_code / error），
   这是"哪些事件没送到"的唯一可观测入口。
6. **SSRF 在投递时复验**：注册时是公网的域名，投递时解析到内网 → 拒绝（DNS-rebinding）。
7. **生成完成真的触发投递**（事件名 song.completed，幂等落库）。
"""

import hashlib
import hmac
import json

import pytest

from app.db import SessionLocal
from app.developer.delivery import (
    MAX_ATTEMPTS,
    backoff_seconds,
    deliver,
    sign_payload,
    verify_signature,
)
from app.developer.webhook_security import check_webhook_url
from app.models import Song, User, WebhookDelivery, WebhookEndpoint


def _register(client, email: str) -> dict:
    r = client.post("/api/auth/register", json={"email": email, "password": "pw123456"})
    assert r.status_code in (200, 201), r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _add_webhook(client, auth: dict, url: str, events=()) -> dict:
    r = client.post(
        "/api/developer/webhooks", json={"url": url, "events": list(events)}, headers=auth
    )
    assert r.status_code == 200, r.text
    return r.json()


# ═══════════════════════════════════════════════════════════════════════════
# 1. SSRF 注册闸门（URL 字面量，无需 DNS）
# ═══════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize(
    "url,reason",
    [
        # 云元数据 —— SSRF 的经典目标（能读出实例凭证）
        ("http://169.254.169.254/latest/meta-data/", "云元数据"),
        ("http://metadata.google.internal/computeMetadata/v1/", "GCP 元数据主机名"),
        # 回环
        ("http://127.0.0.1:8000/hook", "loopback v4"),
        ("http://127.1.2.3/hook", "loopback 段内其他地址"),
        ("http://[::1]/hook", "loopback v6"),
        # 私网
        ("http://10.0.0.5/hook", "10/8"),
        ("http://192.168.1.10/hook", "192.168/16"),
        ("http://172.16.0.1/hook", "172.16/12"),
        # 链路本地 / 保留
        ("http://169.254.1.1/hook", "link-local"),
        ("http://0.0.0.0/hook", "unspecified"),
        # 主机名
        ("http://localhost/hook", "localhost"),
        # scheme
        ("file:///etc/passwd", "file://"),
        ("gopher://127.0.0.1:70/hook", "gopher://"),
        ("ftp://example.com/hook", "ftp://"),
        # 畸形
        ("", "空"),
        ("http:///nohost", "无主机"),
    ],
)
def test_ssrf_registration_blocked(url, reason):
    with pytest.raises(ValueError):
        check_webhook_url(url)
    _ = reason  # 参数名只是文档，便于失败时一眼看出这条在测什么


def test_ssrf_ipv4_mapped_ipv6_loopback_blocked():
    """::ffff:127.0.0.1 —— SSRF 过滤最经典的绕过形式。"""
    with pytest.raises(ValueError):
        check_webhook_url("http://[::ffff:127.0.0.1]/hook")


def test_ssrf_public_url_allowed(monkeypatch):
    # 公网域名需要 DNS：注入确定性解析结果，测试不依赖外网。
    monkeypatch.setattr(
        "app.developer.webhook_security.socket.getaddrinfo",
        lambda host, port: [(2, 1, 6, "", ("93.184.216.34", port))],
    )
    assert check_webhook_url("https://hooks.example.com/cb") == "https://hooks.example.com/cb"


def test_ssrf_public_ip_literal_allowed():
    assert check_webhook_url("https://93.184.216.34/cb") == "https://93.184.216.34/cb"


def test_ssrf_mixed_a_record_rejected(monkeypatch):
    """多 A 记录里混一个内网地址 —— 只看第一个就放行是漏洞。"""
    monkeypatch.setattr(
        "app.developer.webhook_security.socket.getaddrinfo",
        lambda host, port: [
            (2, 1, 6, "", ("93.184.216.34", port)),
            (2, 1, 6, "", ("10.0.0.5", port)),
        ],
    )
    with pytest.raises(ValueError):
        check_webhook_url("https://mixed.example.com/cb")


def test_ssrf_unresolvable_host_rejected(monkeypatch):
    """解析失败必须拒绝（fail-closed），不能"解析不到就当它安全"。"""
    monkeypatch.setattr(
        "app.developer.webhook_security.socket.getaddrinfo",
        lambda host, port: (_ for _ in ()).throw(OSError("NXDOMAIN")),
    )
    with pytest.raises(ValueError):
        check_webhook_url("https://nope.invalid/cb")


def test_webhook_registration_endpoint_blocks_internal(client):
    auth = _register(client, "wh1@test.com")
    for url in ("http://127.0.0.1:9000/hook", "http://169.254.169.254/x", "http://10.1.2.3/x"):
        r = client.post("/api/developer/webhooks", json={"url": url}, headers=auth)
        assert r.status_code == 400, f"{url} → {r.status_code} {r.text}"


def test_webhook_registration_endpoint_allows_public(client, monkeypatch):
    monkeypatch.setattr(
        "app.developer.webhook_security.socket.getaddrinfo",
        lambda host, port: [(2, 1, 6, "", ("93.184.216.34", port))],
    )
    auth = _register(client, "wh2@test.com")
    body = _add_webhook(client, auth, "https://hooks.example.com/cb")
    assert body["url"] == "https://hooks.example.com/cb"
    assert body["active"] is True
    assert body["secret"]  # 创建响应里给一次


def test_webhook_secret_not_echoed_in_list(client, monkeypatch):
    monkeypatch.setattr(
        "app.developer.webhook_security.socket.getaddrinfo",
        lambda host, port: [(2, 1, 6, "", ("93.184.216.34", port))],
    )
    auth = _register(client, "wh3@test.com")
    _add_webhook(client, auth, "https://hooks.example.com/cb")
    listed = client.get("/api/developer/webhooks", headers=auth).json()
    assert len(listed) == 1
    # 列表反复回显签名密钥 = 把它扩散进每张截图/每份日志。
    assert listed[0]["secret"] is None


def test_webhook_endpoint_owner_isolation(client, monkeypatch):
    monkeypatch.setattr(
        "app.developer.webhook_security.socket.getaddrinfo",
        lambda host, port: [(2, 1, 6, "", ("93.184.216.34", port))],
    )
    a = _register(client, "whA@test.com")
    b = _register(client, "whB@test.com")
    ep = _add_webhook(client, a, "https://hooks.example.com/cb")

    assert client.delete(f"/api/developer/webhooks/{ep['id']}", headers=b).status_code == 404
    assert client.get(f"/api/developer/webhooks/{ep['id']}/deliveries", headers=b).status_code == 404
    assert client.get("/api/developer/webhooks", headers=b).json() == []


def test_duplicate_webhook_url_409(client, monkeypatch):
    monkeypatch.setattr(
        "app.developer.webhook_security.socket.getaddrinfo",
        lambda host, port: [(2, 1, 6, "", ("93.184.216.34", port))],
    )
    auth = _register(client, "wh4@test.com")
    _add_webhook(client, auth, "https://hooks.example.com/cb")
    r = client.post(
        "/api/developer/webhooks", json={"url": "https://hooks.example.com/cb"}, headers=auth
    )
    assert r.status_code == 409, r.text


def test_disabled_webhook_no_longer_listed_as_active(client, monkeypatch):
    monkeypatch.setattr(
        "app.developer.webhook_security.socket.getaddrinfo",
        lambda host, port: [(2, 1, 6, "", ("93.184.216.34", port))],
    )
    auth = _register(client, "wh5@test.com")
    ep = _add_webhook(client, auth, "https://hooks.example.com/cb")
    r = client.delete(f"/api/developer/webhooks/{ep['id']}", headers=auth)
    assert r.status_code == 200, r.text
    assert client.get("/api/developer/webhooks", headers=auth).json()[0]["active"] is False


# ═══════════════════════════════════════════════════════════════════════════
# 2. HMAC 签名 / 验签
# ═══════════════════════════════════════════════════════════════════════════
def test_signature_verifies_and_matches_manual_hmac():
    secret = "s3cr3t"
    body = b'{"hello":"world"}'
    header = sign_payload(secret, body, timestamp=1_700_000_000)

    # 手工按文档算一遍：sha256(secret, "t.body") —— 证明格式与文档一致，
    # 对方语言（Node/Go/Python SDK）照此实现即可。
    manual = hmac.new(
        secret.encode(), b"1700000000." + body, hashlib.sha256
    ).hexdigest()
    assert header == f"t=1700000000,v1={manual}"
    assert verify_signature(secret, body, header, now=1_700_000_000)


def test_signature_rejects_tampered_body():
    header = sign_payload("k", b'{"a":1}', timestamp=1_700_000_000)
    assert not verify_signature("k", b'{"a":2}', header, now=1_700_000_000)


def test_signature_rejects_wrong_secret():
    header = sign_payload("right", b"body", timestamp=1_700_000_000)
    assert not verify_signature("wrong", b"body", header, now=1_700_000_000)


def test_signature_rejects_stale_timestamp():
    """重放防护：把时间戳签进内容，抓包者改不了它。"""
    header = sign_payload("k", b"body", timestamp=1_000_000_000)
    assert not verify_signature("k", b"body", header, now=1_700_000_000, tolerance_seconds=300)


def test_signature_rejects_malformed_header():
    for bad in ("", "garbage", "t=1", "v1=abc", "t=abc,v1=def"):
        assert not verify_signature("k", b"body", bad, now=1_700_000_000)


# ═══════════════════════════════════════════════════════════════════════════
# 3. 重试 / 退避 / 落库
# ═══════════════════════════════════════════════════════════════════════════
def test_backoff_is_exponential():
    assert [backoff_seconds(i) for i in (1, 2, 3, 4)] == [1.0, 2.0, 4.0, 8.0]


class _Resp:
    def __init__(self, status, text=""):
        self.status_code = status
        self.text = text


class _FakeClient:
    """按脚本返回响应的假 httpx.Client（同时捕获发出的请求，供签名断言）。"""

    def __init__(self, script, capture):
        self._script = script
        self._capture = capture

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def post(self, url, content=None, headers=None, extensions=None):
        self._capture.append({"url": url, "body": content, "headers": dict(headers or {})})
        item = self._script[min(len(self._capture) - 1, len(self._script) - 1)]
        if isinstance(item, Exception):
            raise item
        return item


def _endpoint(url="https://93.184.216.34/cb", secret="sek", events="") -> WebhookEndpoint:
    return WebhookEndpoint(id=1, user_id=1, url=url, secret=secret, events=events, active=True)


def _deliver_with(monkeypatch, script, *, max_attempts=MAX_ATTEMPTS, endpoint=None, db=None):
    """注入假客户端与假 sleep，真实投递链路走一遍。"""
    capture: list[dict] = []
    sleeps: list[float] = []
    factory = lambda timeout=None: _FakeClient(script, capture)  # noqa: E731
    result = deliver(
        endpoint or _endpoint(),
        "song.completed",
        {"event": "song.completed", "song_id": 7},
        db=db,
        sleep=sleeps.append,
        client_factory=factory,
        max_attempts=max_attempts,
    )
    return result, capture, sleeps


def _db_endpoint() -> tuple[WebhookEndpoint, int]:
    """真入库的端点（deliveries 有外键，离线构造的行插不进去）。"""
    db = SessionLocal()
    try:
        u = User(email="wh-retry@test.com", hashed_password="x")
        db.add(u)
        db.commit()
        db.refresh(u)
        ep = WebhookEndpoint(user_id=u.id, url="https://93.184.216.34/cb", secret="sek", active=True)
        db.add(ep)
        db.commit()
        db.refresh(ep)
        return ep, u.id
    finally:
        db.close()


def test_retry_until_success_and_backoff_sequence():
    ep, _ = _db_endpoint()
    db = SessionLocal()
    try:
        row, capture, sleeps = _deliver_with(
            None, [_Resp(500), _Resp(503), _Resp(200, "ok")], endpoint=ep, db=db
        )
        assert row.attempts == 3
        assert row.delivered_at is not None
        assert row.status_code == 200
        # 只在失败之后退避：第 3 次成功就不该再等。
        assert sleeps == [1.0, 2.0]
        assert len(capture) == 3
    finally:
        db.close()


def test_no_retry_on_4xx():
    """4xx 是对方说"这请求有问题"，重试是打扰不是韧性。"""
    ep, _ = _db_endpoint()
    db = SessionLocal()
    try:
        row, capture, sleeps = _deliver_with(None, [_Resp(400, "bad")], endpoint=ep, db=db)
        assert row.attempts == 1
        assert row.delivered_at is None
        assert row.status_code == 400
        assert sleeps == []
        assert len(capture) == 1
    finally:
        db.close()


def test_retries_network_error_up_to_max():
    ep, _ = _db_endpoint()
    db = SessionLocal()
    try:
        row, capture, sleeps = _deliver_with(
            None, [ConnectionError("boom")], endpoint=ep, db=db, max_attempts=MAX_ATTEMPTS
        )
        assert row.attempts == MAX_ATTEMPTS == 5
        assert row.delivered_at is None
        assert row.status_code is None
        assert "ConnectionError" in row.error
        assert sleeps == [1.0, 2.0, 4.0, 8.0]  # 4 次重试前的等待
        assert len(capture) == 5
    finally:
        db.close()


def test_retries_429():
    ep, _ = _db_endpoint()
    db = SessionLocal()
    try:
        row, _, sleeps = _deliver_with(None, [_Resp(429, "slow down"), _Resp(200)], endpoint=ep, db=db)
        assert row.attempts == 2
        assert row.delivered_at is not None
        assert sleeps == [1.0]
    finally:
        db.close()


def test_failure_is_persisted_with_reason():
    """失败也必须留痕 —— 这是"哪些事件没送到"的唯一可观测入口。"""
    ep, _ = _db_endpoint()
    db = SessionLocal()
    try:
        row, _, _ = _deliver_with(None, [_Resp(500, "kaboom")], endpoint=ep, db=db, max_attempts=2)
        row_id = row.id
    finally:
        db.close()

    db = SessionLocal()
    try:
        persisted = db.get(WebhookDelivery, row_id)
        assert persisted is not None
        assert persisted.delivered_at is None
        assert persisted.attempts == 2
        assert "500" in persisted.error
        # payload 原文落库 → 重试/重放发的是同一份内容。
        assert json.loads(persisted.payload)["song_id"] == 7
    finally:
        db.close()


def test_signed_header_is_present_on_every_attempt():
    ep, _ = _db_endpoint()
    db = SessionLocal()
    try:
        row, capture, _ = _deliver_with(
            None, [_Resp(500), _Resp(200)], endpoint=ep, db=db
        )
        assert row.delivered_at is not None
        for req in capture:
            sig = req["headers"]["X-Flow-Signature"]
            assert verify_signature(ep.secret, req["body"], sig, tolerance_seconds=3600)
            assert req["headers"]["X-Flow-Event"] == "song.completed"
            assert req["headers"]["X-Flow-Delivery"]
    finally:
        db.close()


def test_delivery_rejects_endpoint_that_now_resolves_internal(monkeypatch):
    """注册时公网、投递时解析到内网 → 拒绝（DNS-rebinding / TOCTOU，CWE-918）。

    这是"注册期校验不算数"的证明：投递期必须自己再验一遍。
    """
    ep, _ = _db_endpoint()  # url = https://93.184.216.34（字面量公网 IP）
    db = SessionLocal()
    try:
        # 把端点改成一个域名，并让投递期的解析返回内网地址。
        ep.url = "https://rebind.example.com/cb"
        db.commit()
        monkeypatch.setattr(
            "app.security.ssrf.SSRFGuard._resolved_ips",
            lambda self, host: ["169.254.169.254"],
        )
        capture: list[dict] = []
        row = deliver(
            ep,
            "song.completed",
            {"event": "song.completed"},
            db=db,
            sleep=lambda _s: None,
            client_factory=lambda timeout=None: _FakeClient([_Resp(200)], capture),
        )
        assert row.delivered_at is None
        assert "SSRF" in row.error
        assert capture == []  # 一个字节都没发出去
    finally:
        db.close()


# ═══════════════════════════════════════════════════════════════════════════
# 4. 生成完成 → 真的触发投递（端到端接线）
# ═══════════════════════════════════════════════════════════════════════════
def test_song_completion_creates_delivery_and_sends(monkeypatch):
    """走一次真实的收尾链路：song.completed 必须真的建立投递并带上正确签名。"""
    from app.routers import songs as songs_router

    db = SessionLocal()
    try:
        u = User(email="wh-e2e@test.com", hashed_password="x")
        db.add(u)
        db.commit()
        db.refresh(u)
        uid = u.id
        ep = WebhookEndpoint(
            user_id=uid, url="https://93.184.216.34/cb", secret="e2e-secret", active=True
        )
        db.add(ep)
        db.commit()
        song = Song(user_id=uid, title="e2e", status="completed", moderation_status="approved")
        db.add(song)
        db.commit()
        db.refresh(song)
        song_id = song.id
    finally:
        db.close()

    capture: list[dict] = []

    class _Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, content=None, headers=None, extensions=None):
            capture.append({"url": url, "body": content, "headers": dict(headers or {})})
            return _Resp(200, "ok")

    monkeypatch.setattr("app.developer.delivery.httpx.Client", _Client)
    # 同步路径（无事件循环时 _run_webhook_delivery_async 就地执行），故此调用是确定的。
    db = SessionLocal()
    try:
        songs_router._enqueue_webhook_deliveries(db.get(Song, song_id), db)
    finally:
        db.close()

    assert len(capture) == 1, capture
    req = capture[0]
    assert req["headers"]["X-Flow-Event"] == "song.completed"
    assert verify_signature("e2e-secret", req["body"], req["headers"]["X-Flow-Signature"], tolerance_seconds=3600)
    assert json.loads(req["body"])["song_id"] == song_id

    db = SessionLocal()
    try:
        rows = db.query(WebhookDelivery).all()
        assert len(rows) == 1
        assert rows[0].delivered_at is not None
    finally:
        db.close()


def test_event_filter_skips_unsubscribed_event(monkeypatch):
    """端点订阅了别的事件 → 不该收到 song.completed。"""
    from app.routers import songs as songs_router

    db = SessionLocal()
    try:
        u = User(email="wh-filter@test.com", hashed_password="x")
        db.add(u)
        db.commit()
        db.refresh(u)
        uid = u.id
        db.add(
            WebhookEndpoint(
                user_id=uid,
                url="https://93.184.216.34/cb",
                secret="s",
                events="song.failed",
                active=True,
            )
        )
        db.commit()
        song = Song(user_id=uid, title="f", status="completed")
        db.add(song)
        db.commit()
        db.refresh(song)
        song_id = song.id
    finally:
        db.close()

    capture: list[dict] = []
    monkeypatch.setattr(
        "app.developer.delivery.httpx.Client",
        lambda *a, **k: _FakeClient([_Resp(200)], capture),
    )
    db = SessionLocal()
    try:
        songs_router._enqueue_webhook_deliveries(db.get(Song, song_id), db)
    finally:
        db.close()
    assert capture == []


def test_deliveries_endpoint_lists_history(client, monkeypatch):
    monkeypatch.setattr(
        "app.developer.webhook_security.socket.getaddrinfo",
        lambda host, port: [(2, 1, 6, "", ("93.184.216.34", port))],
    )
    auth = _register(client, "wh6@test.com")
    ep = _add_webhook(client, auth, "https://hooks.example.com/cb")

    monkeypatch.setattr(
        "app.developer.delivery.httpx.Client",
        lambda *a, **k: _FakeClient([_Resp(500, "nope")], []),
    )
    db = SessionLocal()
    try:
        endpoint = db.get(WebhookEndpoint, ep["id"])
        deliver(endpoint, "song.completed", {"event": "song.completed"}, db=db, max_attempts=2)
    finally:
        db.close()

    listed = client.get(
        f"/api/developer/webhooks/{ep['id']}/deliveries", headers=auth
    ).json()
    assert len(listed) == 1
    assert listed[0]["delivered"] is False
    assert listed[0]["attempts"] == 2
    assert listed[0]["status_code"] == 500
    assert "500" in listed[0]["error"]
