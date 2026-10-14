"""GAP-022 开发者 API：凭证创建 / 吊销 / scope 限定 / 越权。

钉住的契约（每一条都对应一个会真出事的面）：

1. **明文只出现一次**：创建响应里有 `token`，之后 `GET /tokens` 绝不复现
   （否则"泄露即吊销"就退化成"列表页天天在泄露"）。
2. **库里只存摘要**：DB 里找不到明文，`token_hint` 是摘要前 8 位而非明文前缀。
3. **越权查不到**：别人的 token id → 404（不是 403，403 等于确认 id 存在）。
4. **吊销即时生效**：DELETE 之后同一串 token 立刻 401。
5. **scope 是硬门**：`songs:read` 的 token 访问需要别的 scope 的端点 → 403（不是 401）。
6. **未知 scope 拒绝创建**（fail-closed），不静默过滤。
7. **过期即失效**。
8. **封禁账号的 token 同步失效**。
9. **开发者 token 不能当登录 JWT 用**，反之亦然（两条认证路径不串味）。
"""

from datetime import datetime, timedelta, timezone

from app.db import SessionLocal
from app.developer.tokens import VALID_SCOPES, hash_token, parse_scopes
from app.models import DeveloperToken, User


def _register(client, email: str) -> dict:
    r = client.post("/api/auth/register", json={"email": email, "password": "pw123456"})
    assert r.status_code in (200, 201), r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _mk_token(client, auth: dict, scopes=("songs:read",), name="ci") -> dict:
    r = client.post(
        "/api/developer/tokens", json={"name": name, "scopes": list(scopes)}, headers=auth
    )
    assert r.status_code == 200, r.text
    return r.json()


# ── 1 / 2. 明文一次性 + 只存摘要 ────────────────────────────────────────────
def test_create_token_returns_plaintext_once_and_stores_only_digest(client):
    auth = _register(client, "dev1@test.com")
    body = _mk_token(client, auth)

    raw = body["token"]
    assert raw.startswith("flow_")
    assert body["token_hint"] == hash_token(raw)[:8]
    assert body["scopes"] == ["songs:read"]
    assert body["revoked"] is False

    # 列表端点**不得**回显明文，也不得回显 token_hash。
    listed = client.get("/api/developer/tokens", headers=auth).json()
    assert len(listed) == 1
    assert "token" not in listed[0]
    assert "token_hash" not in listed[0]
    assert listed[0]["token_hint"] == body["token_hint"]

    # 库里只有摘要：明文既不在 token_hash 也不在 hint 里。
    db = SessionLocal()
    try:
        row = db.query(DeveloperToken).filter(DeveloperToken.id == body["id"]).first()
        assert row is not None
        assert row.token_hash == hash_token(raw)
        assert raw not in (row.token_hash, row.token_hint)
        assert not raw.startswith(row.token_hint)  # hint 是摘要前缀，不是明文前缀
    finally:
        db.close()


def test_token_hint_is_not_plaintext_prefix(client):
    """hint 绝不能是明文前 8 位 —— 那会把 token 熵泄露给能看列表的任何人。"""
    auth = _register(client, "dev2@test.com")
    body = _mk_token(client, auth)
    assert body["token_hint"] != body["token"][:8]


# ── 3. 越权 ────────────────────────────────────────────────────────────────
def test_cannot_revoke_other_users_token(client):
    a = _register(client, "devA@test.com")
    b = _register(client, "devB@test.com")
    tok = _mk_token(client, a)

    # B 吊销 A 的 token → 404（不泄露存在性）
    r = client.delete(f"/api/developer/tokens/{tok['id']}", headers=b)
    assert r.status_code == 404, r.text

    # A 的 token 仍然可用
    db = SessionLocal()
    try:
        assert db.get(DeveloperToken, tok["id"]).revoked is False
    finally:
        db.close()


def test_token_list_is_scoped_to_owner(client):
    a = _register(client, "devC@test.com")
    b = _register(client, "devD@test.com")
    _mk_token(client, a)
    assert client.get("/api/developer/tokens", headers=b).json() == []


# ── 4. 吊销即时生效 ────────────────────────────────────────────────────────
def test_revoke_takes_effect_immediately(client):
    auth = _register(client, "dev3@test.com")
    tok = _mk_token(client, auth)
    dev = {"Authorization": f"Bearer {tok['token']}"}

    assert client.get("/api/developer/songs", headers=dev).status_code == 200

    r = client.delete(f"/api/developer/tokens/{tok['id']}", headers=auth)
    assert r.status_code == 200, r.text

    assert client.get("/api/developer/songs", headers=dev).status_code == 401
    # 断言是失败原因而非"偶发"：行还在，只是 revoked=True（保留审计痕迹）。
    db = SessionLocal()
    try:
        assert db.get(DeveloperToken, tok["id"]).revoked is True
    finally:
        db.close()


# ── 5. scope 硬门（403 而非 401）────────────────────────────────────────────
def test_scope_gate_is_403_not_401(client):
    auth = _register(client, "dev4@test.com")
    tok = _mk_token(client, auth, scopes=("webhooks:manage",))
    r = client.get("/api/developer/songs", headers={"Authorization": f"Bearer {tok['token']}"})
    # 403 = 认证通过但权限不够；401 会让 SDK 去刷新凭证（刷新也救不回来）。
    assert r.status_code == 403, r.text
    assert "songs:read" in r.json()["detail"]


# ── 6. 未知 scope fail-closed ──────────────────────────────────────────────
def test_unknown_scope_rejected(client):
    auth = _register(client, "dev5@test.com")
    r = client.post(
        "/api/developer/tokens",
        json={"scopes": ["songs:read", "admin:everything"]},
        headers=auth,
    )
    assert r.status_code == 400, r.text
    assert "admin:everything" in r.json()["detail"]
    # 拒绝就整条不落库（不留半个 token）。
    assert client.get("/api/developer/tokens", headers=auth).json() == []


def test_empty_scope_rejected(client):
    auth = _register(client, "dev6@test.com")
    r = client.post("/api/developer/tokens", json={"scopes": []}, headers=auth)
    assert r.status_code == 400, r.text


def test_scope_parse_normalizes():
    assert parse_scopes("a, b ,, a ,") == ["a", "b"]
    assert parse_scopes(None) == []
    assert parse_scopes("") == []


# ── 7. 过期 ────────────────────────────────────────────────────────────────
def test_expired_token_rejected(client):
    auth = _register(client, "dev7@test.com")
    tok = _mk_token(client, auth)
    db = SessionLocal()
    try:
        row = db.get(DeveloperToken, tok["id"])
        row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
    finally:
        db.close()
    r = client.get("/api/developer/songs", headers={"Authorization": f"Bearer {tok['token']}"})
    assert r.status_code == 401, r.text


def test_future_expiry_still_valid(client):
    auth = _register(client, "dev8@test.com")
    tok = _mk_token(client, auth)
    db = SessionLocal()
    try:
        row = db.get(DeveloperToken, tok["id"])
        row.expires_at = datetime.now(timezone.utc) + timedelta(days=1)
        db.commit()
    finally:
        db.close()
    assert (
        client.get(
            "/api/developer/songs", headers={"Authorization": f"Bearer {tok['token']}"}
        ).status_code
        == 200
    )


# ── 8. 封禁账号的 token 同步失效 ───────────────────────────────────────────
def test_banned_user_token_is_revoked_effectively(client):
    auth = _register(client, "dev9@test.com")
    tok = _mk_token(client, auth)
    dev = {"Authorization": f"Bearer {tok['token']}"}
    assert client.get("/api/developer/songs", headers=dev).status_code == 200

    db = SessionLocal()
    try:
        u = db.query(User).filter(User.email == "dev9@test.com").first()
        u.is_banned = True
        db.commit()
    finally:
        db.close()

    # 封了人但凭证还能调 API = 封禁形同虚设。
    assert client.get("/api/developer/songs", headers=dev).status_code == 401


# ── 9. 两条认证路径不串味 ──────────────────────────────────────────────────
def test_login_jwt_is_not_a_developer_token(client):
    auth = _register(client, "dev10@test.com")
    jwt_raw = auth["Authorization"].split(" ", 1)[1]
    r = client.get("/api/developer/songs", headers={"Authorization": f"Bearer {jwt_raw}"})
    # 登录 JWT 不是 flow_ 前缀的不透明串 → 401。反之开发者 token 也进不了登录端点。
    assert r.status_code == 401, r.text


def test_developer_token_cannot_access_authed_endpoints(client):
    auth = _register(client, "dev11@test.com")
    tok = _mk_token(client, auth)
    r = client.get("/api/auth/me", headers={"Authorization": f"Bearer {tok['token']}"})
    assert r.status_code == 401, r.text


def test_missing_credentials_401(client):
    assert client.get("/api/developer/songs").status_code == 401


def test_garbage_token_401(client):
    r = client.get("/api/developer/songs", headers={"Authorization": "Bearer flow_nonsense"})
    assert r.status_code == 401, r.text


def test_developer_songs_returns_own_songs_only(client):
    auth = _register(client, "dev12@test.com")
    other = _register(client, "dev13@test.com")
    db = SessionLocal()
    try:
        me = db.query(User).filter(User.email == "dev12@test.com").first()
        them = db.query(User).filter(User.email == "dev13@test.com").first()
        from app.models import Song

        db.add(Song(user_id=me.id, title="mine", status="completed"))
        db.add(Song(user_id=them.id, title="theirs", status="completed"))
        db.commit()
    finally:
        db.close()

    tok = _mk_token(client, auth)
    rows = client.get(
        "/api/developer/songs", headers={"Authorization": f"Bearer {tok['token']}"}
    ).json()
    assert [r["title"] for r in rows] == ["mine"]
    # 复用既有序列化 → 权益字段仍在（未订阅者 can_play_full=False）。
    assert rows[0]["can_play_full"] is False
    _ = other


def test_valid_scopes_constant_matches_docs():
    """scope 白名单是公开契约（文档/SDK 都引用它）——加成员应是有意为之。"""
    assert VALID_SCOPES == {"songs:read", "songs:write", "webhooks:manage"}
