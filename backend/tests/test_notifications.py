"""GAP-021 通知通道：订阅存储 / 未配置降级 / 推送签名。

本文件钉住的每一条，都对应一个"上线后才会发现"的具体失败：

1. **未配置 ≠ 发送成功。** 无 SMTP / 无 VAPID 时端点必须回 202 + `sent=False` +
   原因，而不是 200。一旦这里回归成 200，用户会永远等一封不会到的信，
   而工单里没有任何线索（服务端认为它成功了）。
2. **VAPID 签名真的是 ES256 且可被验证。** 只断言"返回了字符串"等于没测 ——
   推送网关是拿公钥真验签的。这里用 cryptography 反验一次，顺带核
   `aud` 是 origin（不是完整端点路径）、`exp` ≤ 24h 上限（这两个是网关硬约束）。
3. **订阅是用户级的。** 别人的 endpoint 既不能被退订，也不会出现在我的自检里。
4. **订阅不落业务表。** GAP-021 明确禁改 models.py / 禁新增迁移；
   若有人日后"顺手"把它塞进 User，本文件的断言会红。
5. **载荷里没有可执行内容。** 推送载荷要过第三方网关，塞 HTML/脚本就是把 XSS 面
   交给中间人。
"""

import base64
import json
import os
import time

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils

from app.config import settings
from app.services import notification_store as store
from app.services import notifications as notify

# 固定的测试端点（**不触网**：所有用例都让通道停在"未配置"或"未安装发送器"，
# 因此不会真的向 FCM/Mozilla 发请求，也不会真的连 SMTP）。
ENDPOINT = "https://fcm.googleapis.com/fcm/send/abc123-def456"
KEYS = {"p256dh": "B" * 87, "auth": "a" * 22}


def _register(client, email: str = "notify@test.com") -> dict:
    r = client.post("/api/auth/register", json={"email": email, "password": "pw123456"})
    assert r.status_code in (200, 201), r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture
def clean_env(tmp_path, monkeypatch):
    """每个用例独立的订阅文件 + 显式置空所有通道凭证。

    不依赖"开发者本机恰好没配 SMTP"：显式 monkeypatch 成空值，
    否则一个本地 .env 就能让"降级"用例变成"真发信"用例（且静默通过）。
    """
    monkeypatch.setattr(settings, "notification_store_path", str(tmp_path / "subs.jsonl"))
    monkeypatch.setattr(settings, "smtp_host", "")
    monkeypatch.setattr(settings, "enable_email_delivery", False)
    monkeypatch.setattr(settings, "vapid_private_key", "")
    monkeypatch.setattr(settings, "vapid_public_key", "")
    monkeypatch.setattr(settings, "vapid_subject", "mailto:ops@test.local")
    notify.clear_pending_push_queue()
    yield
    notify.clear_pending_push_queue()


@pytest.fixture
def vapid_keys(clean_env, monkeypatch):
    """生成一对临时 VAPID 密钥（P-256）注入配置，返回私钥对象供反验签名。"""
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    monkeypatch.setattr(settings, "vapid_private_key", pem)
    return key


# ───────────────────────── 1. 模板层（纯函数） ─────────────────────────


def test_render_email_templates_cover_three_kinds(clean_env):
    sub, body = notify.render_email(notify.NOTIFY_GENERATION_DONE, {"song_id": 7, "title": "晨光"})
    assert "生成完成" in sub
    assert "《晨光》" in body and "/songs/7" in body

    sub, body = notify.render_email(notify.NOTIFY_SHARE, {"title": "晚祷", "link": "https://x/y", "sender": "甲"})
    assert "分享" in sub
    assert "甲" in body and "《晚祷》" in body and "https://x/y" in body

    sub, body = notify.render_email(notify.NOTIFY_SYSTEM, {"subject": "维护", "message": "今晚升级"})
    assert sub == "维护" and "今晚升级" in body


def test_render_email_unknown_kind_raises(clean_env):
    """拼错模板名必须响亮失败 —— 静默的"通用通知"会让 bug 一路发到用户邮箱。"""
    with pytest.raises(ValueError):
        notify.render_email("unknown_kind", {})


def test_push_payload_is_pure_data_no_html(clean_env):
    """载荷过第三方网关：只允许 title/body/url 这类数据，不能有可执行内容。"""
    payload = notify.render_push_payload(notify.NOTIFY_GENERATION_DONE, {"song_id": 3, "title": "x"})
    assert set(payload) <= {"type", "title", "body", "url", "tag"}
    assert payload["url"] == "/songs/3"
    blob = json.dumps(payload)
    assert "<" not in blob and "script" not in blob


# ───────────────────────── 2. 未配置时优雅降级 ─────────────────────────


def test_send_email_without_smtp_degrades_not_raises(clean_env):
    r = notify.send_notification_email(notify.NOTIFY_SYSTEM, "u@test.com", {"message": "hi"})
    assert r.sent is False
    assert r.reason == "email_not_configured"


def test_subscribe_endpoint_works_and_stays_user_scoped(client, clean_env):
    h = _register(client, "sub1@test.com")
    r = client.post("/api/notifications/subscribe", json={"endpoint": ENDPOINT, "keys": KEYS}, headers=h)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["ok"] is True
    assert data["subscription_count"] == 1
    # VAPID 未配 → 明确告知"还没准备好"，但订阅本身已被登记。
    assert data["push_ready"] is False
    # 响应体绝不回显凭据。
    assert "p256dh" not in r.text and "auth" not in r.text

    # 同一 endpoint 再提交 = 幂等刷新，不产生第二条。
    r2 = client.post("/api/notifications/subscribe", json={"endpoint": ENDPOINT, "keys": KEYS}, headers=h)
    assert r2.json()["subscription_count"] == 1


def test_subscribe_requires_login(client, clean_env):
    r = client.post("/api/notifications/subscribe", json={"endpoint": ENDPOINT, "keys": KEYS})
    assert r.status_code == 401


def test_subscribe_rejects_non_https_endpoint(client, clean_env):
    """RFC 8030 §3.1 要求推送端点走 TLS；浏览器也只给 https，放行 http 即凭据走明文。"""
    h = _register(client, "sub2@test.com")
    r = client.post(
        "/api/notifications/subscribe",
        json={"endpoint": "http://evil.local/push", "keys": KEYS},
        headers=h,
    )
    assert r.status_code == 400


def test_subscribe_rejects_malformed_keys(client, clean_env):
    h = _register(client, "sub3@test.com")
    r = client.post(
        "/api/notifications/subscribe",
        json={"endpoint": ENDPOINT, "keys": {"p256dh": "short", "auth": "x"}},
        headers=h,
    )
    assert r.status_code == 422


def test_subscribe_503_when_store_not_configured(client, clean_env, monkeypatch):
    """存储未配置 → 503 + 变量名，绝不假装存下了。"""
    monkeypatch.setattr(settings, "notification_store_path", "")
    h = _register(client, "sub4@test.com")
    r = client.post("/api/notifications/subscribe", json={"endpoint": ENDPOINT, "keys": KEYS}, headers=h)
    assert r.status_code == 503
    assert "NOTIFICATION_STORE_PATH" in r.text


def test_unsubscribe_only_own_endpoint(client, clean_env):
    a = _register(client, "own-a@test.com")
    b = _register(client, "own-b@test.com")
    client.post("/api/notifications/subscribe", json={"endpoint": ENDPOINT, "keys": KEYS}, headers=a)

    # b 退订 a 的端点 → 404（不泄露"存在但属于别人"）
    r = client.post("/api/notifications/unsubscribe", json={"endpoint": ENDPOINT}, headers=b)
    assert r.status_code == 404
    # a 的订阅还在
    assert len(store.list_for_user(1)) == 1 or store.count() == 1

    r = client.post("/api/notifications/unsubscribe", json={"endpoint": ENDPOINT}, headers=a)
    assert r.status_code == 200
    assert r.json()["subscription_count"] == 0
    assert store.count() == 0


def test_test_endpoint_returns_202_with_reasons_when_unconfigured(client, clean_env):
    """自检：无凭证时 202 + sent=False + 该配的变量名（**不是 200**）。"""
    h = _register(client, "test-ch@test.com")
    client.post("/api/notifications/subscribe", json={"endpoint": ENDPOINT, "keys": KEYS}, headers=h)
    r = client.post("/api/notifications/test", headers=h)
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["accepted"] is True
    by_channel = {x["channel"]: x for x in body["results"]}
    assert by_channel["email"]["sent"] is False
    assert by_channel["email"]["reason"] == "email_not_configured"
    assert by_channel["push"]["sent"] is False
    # 降级路径必须自我说明：要么说 VAPID 没配，要么说发送器没装。
    assert by_channel["push"]["reason"] in ("vapid_not_configured", "pywebpush_not_installed")
    assert "SMTP_HOST" in body["required_env"]
    assert "VAPID_PRIVATE_KEY" in body["required_env"]


def test_test_endpoint_without_subscription_says_so(client, clean_env):
    h = _register(client, "no-sub@test.com")
    body = client.post("/api/notifications/test", headers=h).json()
    push = next(x for x in body["results"] if x["channel"] == "push")
    assert push["reason"] == "no_subscription"


def test_push_without_vapid_is_enqueued_not_lost(clean_env):
    """降级**不是丢弃**：未配 VAPID 的推送要进待推送队列，配好后可回放。"""
    r = notify.send_web_push(notify.NOTIFY_SYSTEM, ENDPOINT, KEYS, user_id=42, ctx={"message": "x"})
    assert r.sent is False and r.reason == "vapid_not_configured"
    q = notify.pending_push_queue()
    assert len(q) == 1
    assert q[0]["endpoint"] == ENDPOINT
    assert q[0]["payload"]["type"] == notify.NOTIFY_SYSTEM


def test_pending_queue_has_hard_cap(clean_env):
    """无上限的降级队列会把"通知通道故障"升级成"进程 OOM"。丢最旧的。"""
    for i in range(notify.PENDING_PUSH_MAX + 5):
        notify.send_web_push(notify.NOTIFY_SYSTEM, f"https://fcm.googleapis.com/send/{i}", KEYS, 1, {})
    q = notify.pending_push_queue()
    assert len(q) == notify.PENDING_PUSH_MAX
    # 留下的是最近的（最旧的被逐出）
    assert q[-1]["endpoint"].endswith(str(notify.PENDING_PUSH_MAX + 4))


def test_configured_but_no_sender_still_degrades_to_queue(vapid_keys):
    """配好 VAPID 但没装 pywebpush（本仓库默认状态）→ 仍登记待推送，不 500。"""
    r = notify.send_web_push(notify.NOTIFY_GENERATION_DONE, ENDPOINT, KEYS, 7, {"song_id": 1, "title": "t"})
    assert r.sent is False
    assert r.reason == "pywebpush_not_installed"
    assert len(notify.pending_push_queue()) == 1


# ───────────────────────── 3. 推送签名（VAPID / ES256） ─────────────────────────


def test_vapid_headers_signature_verifies_es256(vapid_keys):
    """★ 核心：签名必须能被公钥真验过，且 aud/exp 符合网关硬约束。★"""
    headers = notify.vapid_headers(ENDPOINT)
    auth = headers["Authorization"]
    assert auth.startswith("vapid t=")
    token = auth.split("t=", 1)[1].split(",", 1)[0]
    signing_input, sig_b64 = token.rsplit(".", 1)

    # ① JWS 是固定长度 raw r||s（64 字节），不是 DER —— 推送网关只认前者。
    sig = base64.urlsafe_b64decode(sig_b64 + "=" * (-len(sig_b64) % 4))
    assert len(sig) == 64
    r, s = int.from_bytes(sig[:32], "big"), int.from_bytes(sig[32:], "big")
    der = utils.encode_dss_signature(r, s)
    vapid_keys.public_key().verify(der, signing_input.encode("ascii"), ec.ECDSA(hashes.SHA256()))

    # ② 声明合规：aud = 推送服务 origin（非完整路径）；exp ≤ 24h。
    payload_b64 = signing_input.split(".", 1)[1]
    claims = json.loads(base64.urlsafe_b64decode(payload_b64 + "=" * (-len(payload_b64) % 4)))
    assert claims["aud"] == "https://fcm.googleapis.com"
    assert claims["sub"] == settings.vapid_subject
    assert claims["exp"] - int(time.time()) <= 24 * 3600
    assert headers["TTL"] == str(int(settings.web_push_ttl_seconds))


def test_vapid_public_key_derives_from_private(vapid_keys):
    """公钥未配时应从私钥推导（少一个"必须配对否则静默失败"的变量）。"""
    pub = notify.public_key_b64()
    raw = base64.urlsafe_b64decode(pub + "=" * (-len(pub) % 4))
    assert len(raw) == 65 and raw[0] == 4  # uncompressed point
    expected = vapid_keys.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    assert raw == expected
    assert notify.vapid_configured() is True


def test_vapid_private_key_accepts_single_line_der(clean_env, monkeypatch):
    """两种私钥形态都要能吃：PEM（openssl 产出）与单行 base64url DER（py_vapid 产出）。"""
    key = ec.generate_private_key(ec.SECP256R1())
    der = key.private_bytes(
        serialization.Encoding.DER,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    one_line = base64.urlsafe_b64encode(der).rstrip(b"=").decode()
    monkeypatch.setattr(settings, "vapid_private_key", one_line)
    assert notify.public_key_b64().startswith("B")  # P-256 未压缩点的 base64url 以 'B' 起
    assert notify.vapid_configured() is True


def test_vapid_invalid_key_degrades_instead_of_raising(vapid_keys, monkeypatch):
    """配错一个环境变量不该让调用方 500：解析失败 → 未配置语义。"""
    monkeypatch.setattr(settings, "vapid_private_key", "not-a-real-key")
    assert notify.public_key_b64() == "" or notify._load_private_key() is None
    assert notify.vapid_configured() is False


def test_vapid_subject_must_be_mailto_or_https(vapid_keys, monkeypatch):
    """RFC 8292 §2.1：sub 只能是 mailto: 或 https://。配错 → 未就绪，别发出去被 4xx。"""
    monkeypatch.setattr(settings, "vapid_subject", "ops@example.com")
    assert notify.vapid_configured() is False
    monkeypatch.setattr(settings, "vapid_subject", "mailto:ops@example.com")
    assert notify.vapid_configured() is True


def test_vapid_headers_reject_bad_endpoint(vapid_keys):
    with pytest.raises(ValueError):
        notify.vapid_headers("not-a-url")


def test_vapid_public_key_endpoint_reports_unconfigured(client, clean_env):
    h = _register(client, "vapid@test.com")
    body = client.get("/api/notifications/vapid-public-key", headers=h).json()
    assert body["configured"] is False and body["public_key"] == ""


def test_vapid_public_key_endpoint_reports_configured(client, vapid_keys):
    h = _register(client, "vapid2@test.com")
    body = client.get("/api/notifications/vapid-public-key", headers=h).json()
    assert body["configured"] is True and len(body["public_key"]) > 80


# ───────────────────────── 4. 订阅存储（零迁移） ─────────────────────────


def test_store_does_not_touch_orm_metadata():
    """GAP-021 硬约束：禁改 models.py / 禁新增迁移。

    若日后有人把订阅"顺手"塞进 User 或新表，本断言会红 ——
    那一步必须等 #90 的迁移槽，而不是混在通知 PR 里。
    """
    from app.db import Base
    from app.models import User

    assert "push_subscriptions" not in Base.metadata.tables
    assert not hasattr(User, "push_endpoint")
    assert not hasattr(User, "web_push_subscription")


def test_store_roundtrip_and_isolation(clean_env):
    store.save(1, ENDPOINT, KEYS, user_agent="UA")
    store.save(2, "https://updates.push.services.mozilla.com/wpush/v2/xyz", KEYS)
    assert store.count() == 2
    assert len(store.list_for_user(1)) == 1
    assert len(store.list_for_user(2)) == 1
    assert store.delete(ENDPOINT) is True
    assert store.delete(ENDPOINT) is False  # 已删 → False，不抛


def test_store_idempotent_refresh_keeps_first_created_at(clean_env):
    first = store.save(1, ENDPOINT, KEYS)
    again = store.save(1, ENDPOINT, {"p256dh": "C" * 87, "auth": "b" * 22})
    assert again.created_at == first.created_at
    assert again.keys["p256dh"].startswith("C")
    assert store.count() == 1


def test_store_evicts_oldest_beyond_per_user_cap(clean_env, monkeypatch):
    monkeypatch.setattr(settings, "web_push_max_subscriptions_per_user", 2)
    for i in range(4):
        store.save(1, f"https://fcm.googleapis.com/send/{i}", KEYS)
        time.sleep(0.01)  # 让 created_at 单调，逐出顺序可断言
    subs = store.list_for_user(1)
    assert len(subs) == 2
    assert {s.endpoint.rsplit("/", 1)[1] for s in subs} == {"2", "3"}


def test_store_survives_corrupt_line(clean_env):
    """一行损坏不能让整个文件报废（否则等于一次崩溃丢掉全部订阅）。"""
    store.save(1, ENDPOINT, KEYS)
    with open(store.store_path(), "a", encoding="utf-8") as f:
        f.write("{ not json at all\n")
    store.save(2, "https://fcm.googleapis.com/send/other", KEYS)
    assert store.count() == 2


def test_store_file_is_not_world_readable(clean_env):
    """文件含推送凭据（p256dh/auth）→ 权限必须收紧。"""
    store.save(1, ENDPOINT, KEYS)
    mode = os.stat(store.store_path()).st_mode & 0o777
    assert mode == 0o600, oct(mode)


def test_store_writes_no_temp_leftovers(clean_env):
    store.save(1, ENDPOINT, KEYS)
    d = os.path.dirname(store.store_path())
    assert [f for f in os.listdir(d) if ".tmp." in f] == []


def test_store_disabled_returns_empty(clean_env, monkeypatch):
    monkeypatch.setattr(settings, "notification_store_path", "")
    assert store.list_for_user(1) == []
    assert store.count() == 0
    assert store.save(1, ENDPOINT, KEYS) is None


# ───────────────────────── 5. 模板化发送（业务接线用） ─────────────────────────


def test_notify_helpers_never_raise_without_channels(client, clean_env, monkeypatch):
    """三个模板化发送函数在"全通道未配置"下必须安静降级 ——

    它们会被生成完成回调调用，那里抛异常 = 作品状态被通知问题拖住。
    """
    from app.db import SessionLocal
    from app.models import User
    from app.routers import notifications as router_mod

    h = _register(client, "helper@test.com")
    assert h  # 触发注册
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == "helper@test.com").first()
        for results in (
            router_mod.notify_generation_done(db, user, 1, "晨光"),
            router_mod.notify_share(db, user, "晚祷", "https://x/y", sender="甲"),
            router_mod.notify_system(db, user, "维护", "今晚升级"),
        ):
            assert results and all(x.sent is False for x in results)
    finally:
        db.close()


def test_test_endpoint_rate_limited(client, clean_env, monkeypatch):
    """自检会真发信/发推 → 必须有限流（否则可被用来刷爆发信域名信誉）。"""
    monkeypatch.setattr(settings, "enable_rate_limit", True)
    monkeypatch.setattr(settings, "notifications_test_rate_limit_per_minute", 2)
    import app.ratelimit as rl

    rl._hits.clear()
    h = _register(client, "rl@test.com")
    codes = [client.post("/api/notifications/test", headers=h).status_code for _ in range(4)]
    assert 429 in codes, codes


# ───────────────── 6. 配好凭证时「真的发了」（不只是降级） ─────────────────


def test_configured_smtp_actually_sends(clean_env, monkeypatch):
    """★ 反向断言：上一节的 202 降级不能成为「永远不发送」的遮羞布。★

    用假 SMTP 类顶掉 smtplib 的入口（不触网），验证配好 SMTP_HOST 后
    `sent=True` 且**邮件真的被交出去**（主题/收件人/正文都对）。
    只测降级 = 把"通道坏了"和"通道没配"混为一谈，上线后分不清。
    """
    import smtplib

    sent: list = []

    class FakeSMTP:
        def __init__(self, host, port, timeout=None):
            sent.append({"host": host, "port": port})

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self):
            sent.append({"starttls": True})

        def login(self, u, p):
            sent.append({"login": u, "password_len": len(p)})

        def send_message(self, msg):
            sent.append({"to": msg["To"], "subject": msg["Subject"], "body": msg.get_content()})

    monkeypatch.setattr(settings, "smtp_host", "smtp.test.invalid")
    monkeypatch.setattr(settings, "smtp_username", "bot@test.invalid")
    monkeypatch.setattr(settings, "smtp_password", "s3cret-password")
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)

    r = notify.send_notification_email(
        notify.NOTIFY_GENERATION_DONE, "listener@test.com", {"song_id": 12, "title": "晨祷"}
    )
    assert r.sent is True and r.reason == "email_sent"
    final = sent[-1]
    assert final["to"] == "listener@test.com"
    assert "生成完成" in final["subject"]
    assert "《晨祷》" in final["body"] and "/songs/12" in final["body"]
    # 凭证只用于 login，绝不进日志/响应（这里顺带确认它确实被用上了）
    assert any(x.get("login") == "bot@test.invalid" for x in sent)


def test_email_body_never_leaks_password(clean_env, monkeypatch, caplog):
    """SMTP 密码绝不能进日志 —— 通知邮件走了同一条 send_email seam，同样受约束。"""
    import logging
    import smtplib

    class BoomSMTP:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self):
            pass

        def send_message(self, msg):
            raise smtplib.SMTPException("boom")

    monkeypatch.setattr(settings, "smtp_host", "smtp.test.invalid")
    monkeypatch.setattr(settings, "smtp_password", "TOP-SECRET-PW")
    monkeypatch.setattr(smtplib, "SMTP", BoomSMTP)
    with caplog.at_level(logging.DEBUG):
        r = notify.send_notification_email(notify.NOTIFY_SYSTEM, "x@test.com", {"message": "m"})
    assert r.sent is False and r.reason == "email_send_failed"
    assert "TOP-SECRET-PW" not in caplog.text


def test_push_endpoint_is_credential_and_not_logged_in_full(clean_env, monkeypatch, caplog):
    """endpoint 本身就是凭据（拿到即可向该设备推消息）→ 日志只留主机名。"""
    import logging

    monkeypatch.setattr(settings, "vapid_private_key", "")
    with caplog.at_level(logging.DEBUG):
        notify.send_web_push(notify.NOTIFY_SYSTEM, ENDPOINT, KEYS, 1, {"message": "m"})
    assert "/fcm/send/abc123-def456" not in caplog.text
