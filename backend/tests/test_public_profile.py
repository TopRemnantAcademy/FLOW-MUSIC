"""GAP-015（公开部分）：公开资料页只读端点。

钉住的契约（每条都对应一个会真出事的面）：

1. **响应用白名单，不是「User 减几个字段」**：邮箱 / 哈希 / 验证与重置 token /
   MFA 密钥 / 封禁与锁定列，一个都不能进响应体（逐键断言，不是抽查）。
2. **他人 profile 只见公开已过审作品**：未公开、未完成、已驳回、回收站里的歌，
   在 `public_song_count` 与 `/songs` 里都不得出现 —— 数字与列表必须同口径，
   否则「共 5 首却列出 3 首」本身就是一条泄露。
3. **本人也不例外**：资料页是"公开面"，本人若要私有作品走 /api/songs。
   一个端点两套可见性规则，迟早有人在改一处时忘掉另一处。
4. **不存在 = 404**：越权探测（猜 id、给已删用户）拿不到任何区分性信息。
5. **读端点无副作用**：匿名高频 GET 不得改写任何状态（爬虫面）。
6. **Follow / Comment 未实现就要硬失败**：没有表就没有端点，返回 404 而不是
   假装成功或返回 `follower_count: 0` 这种"看起来实现了"的假数字。
"""

import pytest
from sqlalchemy import func

from app.db import SessionLocal
from app.models import Song, User
from app.routers.auth import create_token

# 响应体里**绝不允许**出现的键。用「键名」而非「值」断言：值可以脱敏成 ***，
# 但键名一旦存在，就说明构造路径上有人把整行 User 丢了进去（下一次改动就会漏值）。
FORBIDDEN_KEYS = {
    "email",
    "user_email",
    "hashed_password",
    "password",
    "verify_token",
    "reset_token",
    "mfa_secret",
    "locked_until",
    "failed_logins",
    "is_banned",
    "ban_reason",
    "banned_at",
    "oauth_sub",
    "tier_key",
}


def _register(client, email: str = "profile@test.com") -> dict:
    r = client.post("/api/auth/register", json={"email": email, "password": "pw123456"})
    assert r.status_code in (200, 201), r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _uid(email: str) -> int:
    db = SessionLocal()
    try:
        u = db.query(User).filter(User.email == email).first()
        assert u is not None
        return u.id
    finally:
        db.close()


def _song(user_id: int, **kw) -> int:
    """建歌并返回 id（建完即关会话：测试模式是"建数据 → 关会话 → 打接口"）。"""
    db = SessionLocal()
    try:
        s = Song(
            user_id=user_id,
            title=kw.pop("title", "曲"),
            style="国风",
            status=kw.pop("status", "completed"),
            audio_url="https://cdn.test/a.mp3",
            cover_url="https://cdn.test/c.jpg",
            moderation_status=kw.pop("moderation_status", "approved"),
            is_public=kw.pop("is_public", True),
            **kw,
        )
        db.add(s)
        db.commit()
        return s.id
    finally:
        db.close()


def _walk_keys(payload) -> set[str]:
    out: set[str] = set()
    if isinstance(payload, dict):
        for k, v in payload.items():
            out.add(k)
            out |= _walk_keys(v)
    elif isinstance(payload, list):
        for item in payload:
            out |= _walk_keys(item)
    return out


# ─────────────────────────── 1. 隐私白名单 ───────────────────────────


def test_profile_response_contains_no_credential_or_email_keys(client):
    h = _register(client, "privacy@test.com")
    uid = _uid("privacy@test.com")
    _song(uid, title="公开曲")

    r = client.get(f"/api/users/{uid}", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert not (FORBIDDEN_KEYS & set(body)), f"泄露字段：{FORBIDDEN_KEYS & set(body)}"
    # 连值里也不能出现完整邮箱（脱敏规则一旦被改成回退全量邮箱，这里挂）
    assert "privacy@test.com" not in r.text
    assert body["display_name"] == "pr***"

    r2 = client.get(f"/api/users/{uid}/songs", headers=h)
    assert r2.status_code == 200, r2.text
    leaked = FORBIDDEN_KEYS & _walk_keys(r2.json())
    assert not leaked, f"作品列表泄露字段：{leaked}"
    assert "privacy@test.com" not in r2.text


def test_profile_visible_to_anonymous_without_credentials(client):
    """公开资料是公开面：匿名可读（与广场一致），但一样不含敏感键。"""
    _register(client, "anon@test.com")
    uid = _uid("anon@test.com")
    r = client.get(f"/api/users/{uid}")
    assert r.status_code == 200, r.text
    assert not (FORBIDDEN_KEYS & set(r.json()))


def test_banned_and_locked_columns_never_exposed(client):
    """封禁/锁定状态不对外：它属于运营内部事实（且会诱导"绕过封禁"的试探）。"""
    _register(client, "banned@test.com")
    uid = _uid("banned@test.com")
    db = SessionLocal()
    try:
        u = db.get(User, uid)
        u.is_banned = True
        u.ban_reason = "测试封禁"
        u.failed_logins = 3
        db.commit()
    finally:
        db.close()
    r = client.get(f"/api/users/{uid}")
    assert r.status_code == 200, r.text
    assert not (FORBIDDEN_KEYS & set(r.json()))
    assert "测试封禁" not in r.text


# ─────────────────── 2/3. 可见性：私有与未过审不入他人 profile ───────────────────


def test_only_public_approved_songs_appear_in_other_profile(client):
    h = _register(client, "viewer@test.com")
    owner = _uid("viewer@test.com")
    _register(client, "author@test.com")
    author = _uid("author@test.com")

    pub = _song(author, title="已过审公开")
    _song(author, title="未公开", is_public=False)
    _song(author, title="未完成", status="processing")
    _song(author, title="已驳回", moderation_status="rejected")
    _song(author, title="回收站", deleted_at=__import__("datetime").datetime.now(
        __import__("datetime").timezone.utc
    ))

    prof = client.get(f"/api/users/{author}", headers=h).json()
    assert prof["public_song_count"] == 1

    r = client.get(f"/api/users/{author}/songs", headers=h)
    assert r.status_code == 200, r.text
    ids = [s["id"] for s in r.json()]
    assert ids == [pub]

    # owner 自己看自己的资料页，同样是"公开面"：私有作品不得出现
    r2 = client.post("/api/auth/login", json={"email": "author@test.com", "password": "pw123456"})
    h_author = {"Authorization": f"Bearer {r2.json()['access_token']}"}
    own = client.get(f"/api/users/{author}/songs", headers=h_author).json()
    assert [s["id"] for s in own] == [pub]
    assert client.get(f"/api/users/{author}", headers=h_author).json()["is_me"] is True


def test_owner_private_songs_not_counted_in_public_song_count(client):
    """计数与列表同口径：5 首私有 + 1 首公开 ⇒ 只报 1，不报 6。"""
    _register(client, "counter@test.com")
    uid = _uid("counter@test.com")
    for i in range(5):
        _song(uid, title=f"私有{i}", is_public=False)
    _song(uid, title="公开")
    prof = client.get(f"/api/users/{uid}").json()
    assert prof["public_song_count"] == 1


# ─────────────────────────── 4. 越权 / 不存在 ───────────────────────────


def test_logged_in_token_is_not_silently_downgraded_to_anonymous(client):
    """带合法 token 必须被认出来（匿名降级 = 静默失效，最易漏的回归）。

    历史缺陷（本 PR 实测并被本条钉住）：`plaza.get_optional_user` 用
    `jwt.decode(token, secret, algorithms=["HS256"])` 解码，但 `create_token`
    从 2026-09-13 起在载荷里写入 `aud`；python-jose 默认 `verify_aud=True` 且
    audience=None ⇒ 必然抛 `JWTClaimsError`，而裸 `except Exception: return None`
    把它吞成"匿名"。症状是**所有带 token 的请求都被当成未登录**：
    广场 liked_by_me 恒 False、本页 is_me 恒 False。

    这条断言必须同时覆盖两个消费方（users 与 plaza），否则修好一处另一处又漂。
    """
    client.post("/api/auth/register", json={"email": "token1@test.com", "password": "pw123456"})
    uid = _uid("token1@test.com")
    tok = create_token(uid)
    h = {"Authorization": f"Bearer {tok}"}

    assert client.get(f"/api/users/{uid}", headers=h).json()["is_me"] is True

    # plaza 的同款依赖：登录态回显（点赞后 liked_by_me 必须为 True）
    sid = _song(uid, title="点赞曲")
    assert client.post(f"/api/plaza/songs/{sid}/like", headers=h).status_code == 200
    mine = [s for s in client.get("/api/plaza", headers=h).json() if s["id"] == sid]
    assert mine and mine[0]["liked_by_me"] is True


def test_missing_user_returns_404_for_both_endpoints(client):
    r1 = client.get("/api/users/999999")
    r2 = client.get("/api/users/999999/songs")
    assert r1.status_code == 404 and r2.status_code == 404


def test_nonexistent_user_indistinguishable_from_no_public_songs(client):
    """404 与「有账号但无公开作品」必须是两种可区分但都不泄露内容的结果。

    不存在的 id ≠ 空列表：空列表会让爬虫把「这个人存在」写进字典。
    """
    _register(client, "empty@test.com")
    uid = _uid("empty@test.com")
    r = client.get(f"/api/users/{uid}/songs")
    assert r.status_code == 200
    assert r.json() == []


# ─────────────────────────── 5. 读端点无副作用 ───────────────────────────


def test_repeated_anonymous_reads_do_not_mutate_rows(client):
    _register(client, "noop@test.com")
    uid = _uid("noop@test.com")
    _song(uid, title="公开")

    db = SessionLocal()
    try:
        before = db.query(func.count(Song.id)).scalar(), db.query(func.count(Song.play_count)).scalar()
    finally:
        db.close()
    for _ in range(5):
        assert client.get(f"/api/users/{uid}").status_code == 200
        assert client.get(f"/api/users/{uid}/songs").status_code == 200
    db = SessionLocal()
    try:
        after = db.query(func.count(Song.id)).scalar(), db.query(func.count(Song.play_count)).scalar()
        # 播放计数也不得被列表端点动过（列表不是播放）
        assert db.query(Song).filter(Song.user_id == uid).one().play_count == 0
    finally:
        db.close()
    assert before == after


# ─────────────────── 6. Follow / Comment：未实现就要硬失败 ───────────────────


@pytest.mark.parametrize(
    "path",
    [
        "/api/users/1/follow",
        "/api/users/1/followers",
        "/api/users/1/following",
        "/api/users/1/comments",
    ],
)
def test_social_endpoints_absent_until_migration_slot_frees(client, path):
    """禁止"假实现"：没有 Follow/Comment 表，就不该存在任何会返回 200 的社交端点。

    这条断言的作用是**防止未来的人顺手加一个 memory-only 的假端点**：
    它会在本仓库的标准测试里立刻变红，迫使实现者先拿到迁移槽。
    """
    assert client.get(path).status_code == 404
    assert client.post(path).status_code == 404


def test_no_new_tables_or_migration_for_this_gap():
    """本 PR 的硬约束回归：不得新增表 / 不得新增迁移文件（迁移槽被 #90 占用）。

    以 metadata 反查：Follow / Comment / Profile 这三张未来要加的表名，
    现在**必须不存在**。有人偷偷加表会先在这里挂，而不是等到部署时撞迁移。
    """
    from app.db import Base

    names = set(Base.metadata.tables)
    for forbidden in ("follows", "comments", "profiles", "user_profiles"):
        assert forbidden not in names, f"{forbidden} 不应在本 PR 出现（迁移槽被 #90 占用）"
