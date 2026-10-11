"""spawn 链路接线回归测试（extend_type 三处链路 + 机审生效值）。

2026-10-11 审计（GAP-143 / GAP-149）挖出的两个真缺陷，本文件为其行为锁定：

缺陷 1 — `extend_type` 从落地起就存不下来：
  `GenerateRequest.extend_type` 有定义，但 `SpawnReq` 没有、`Song` 也没有该列，
  而后台任务 `_process_song` 是**从 DB 的 Song 重建**请求的 → 该值恒为 None
  → 供应商侧一律按 tail 处理。「从头部衔接」在 API/UI 上都不存在，传了被静默丢弃。
  修复：Song.extend_type 列 + SpawnReq/GenerateReq 字段 + spawn→持久化→重建三处接通
       + 非法值 400 fail-closed（不静默改写为 tail）。

缺陷 2 — 机审能被「父歌回退」绕过：
  `_moderate_spawn(body)` 只审请求体，而 `_spawn_from_parent` 在 body 留空时会
  回退到父歌的 lyric/prompt/style。cover/remix 允许对**他人公开歌**发起，父歌未必审过
  → 「拿公开歌当载体把未审文本送进引擎」的审核绕过路径。
  修复：`_moderate_spawn_text(*texts)` 逐项校验**回退后的生效值**（不拼接）。

设计：mock provider 注入（`set_orchestrator`），**零网络**；后台任务同步执行，
断言落在「DB 行」与「provider 收到的 payload」两个可观测面上。
"""

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.db import SessionLocal
from app.models import Song, User
from app.orchestrator.base import GenerateRequest, SongResult
from app.orchestrator.client import set_orchestrator, get_orchestrator
from app.orchestrator.mock import MockProvider


# ============================================================
# 夹具 / 工具
# ============================================================
class _CapturingProvider(MockProvider):
    """MockProvider + 捕获 provider 最终收到的 GenerateRequest。

    这是「链路是否真的接通」的关键观测面：只看 DB 行只能证明存了，
    只有 provider 收到的 payload 才能证明**重建后**也把值带到了供应商侧。
    """

    def __init__(self):
        super().__init__()
        self.requests: list[GenerateRequest] = []

    async def create(self, req: GenerateRequest) -> SongResult:
        self.requests.append(req)
        return await super().create(req)


@pytest.fixture()
def provider():
    p = _CapturingProvider()
    set_orchestrator(p)
    yield p
    set_orchestrator(MockProvider())


def _auth_headers(client: TestClient, email: str) -> dict:
    pw = "Password123!"
    client.post("/api/auth/register", json={"email": email, "password": pw})
    r = client.post("/api/auth/login", json={"email": email, "password": pw})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _make_parent(
    email: str,
    *,
    lyric: str = "orig lyric",
    prompt: str = "orig prompt",
    style: str = "orig style",
    is_public: bool = False,
    status: str = "completed",
) -> int:
    """直接建父歌（绕过生成流程，让测试聚焦 spawn 链路本身）。"""
    db = SessionLocal()
    u = db.query(User).filter(User.email == email).first()
    s = Song(
        user_id=u.id,
        title="parent",
        status=status,
        custom_id="mock-custom-parent-1",
        audio_url="parent.mp3",
        lyric=lyric,
        prompt=prompt,
        style=style,
        is_public=is_public,
        task_type="generate",
        content_category="general",
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    sid = s.id
    db.close()
    return sid


def _song(song_id: int) -> Song:
    db = SessionLocal()
    try:
        return db.get(Song, song_id)
    finally:
        db.close()


# ============================================================
# 缺陷 1：extend_type 三处链路
# ============================================================
def test_extend_type_head_persisted_to_db(client, provider):
    """spawn 链路①：请求带 extend_type=head → 落到 Song.extend_type（此前该列不存在）。"""
    h = _auth_headers(client, "et-persist@test.com")
    sid = _make_parent("et-persist@test.com")
    r = client.post(f"/api/songs/{sid}/extend", json={"extend_type": "head"}, headers=h)
    assert r.status_code == 200, r.text
    child = _song(r.json()["song_id"])
    assert child.extend_type == "head"
    assert child.task_type == "extend"
    assert child.parent_song_id == sid


def test_extend_type_reaches_provider_after_db_rebuild(client, provider):
    """spawn 链路③（根因所在）：后台从 DB 重建请求后，extend_type 仍必须带进 payload。

    这是本缺陷的核心断言：旧实现该值在重建时丢失 → provider 永远拿到 None → 一律 tail。
    """
    h = _auth_headers(client, "et-rebuild@test.com")
    sid = _make_parent("et-rebuild@test.com", lyric="lyric for extend")
    r = client.post(
        f"/api/songs/{sid}/extend",
        json={"extend_type": "head", "lyric": "lyric for extend"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    assert provider.requests, "provider 未被调用"
    last = provider.requests[-1]
    assert last.task == "extend"
    assert last.extend_type == "head", "重建后 extend_type 丢失 → 供应商一律 tail"


def test_extend_type_tail_explicitly_persisted(client, provider):
    """显式 tail 同样要存下来（不能只处理 head 分支）。"""
    h = _auth_headers(client, "et-tail@test.com")
    sid = _make_parent("et-tail@test.com")
    r = client.post(f"/api/songs/{sid}/extend", json={"extend_type": "tail"}, headers=h)
    assert r.status_code == 200, r.text
    assert _song(r.json()["song_id"]).extend_type == "tail"


def test_extend_type_defaults_to_null_when_omitted(client, provider):
    """未指定 = NULL（交 provider 按 tail 默认处理），保持既有客户端行为不变。"""
    h = _auth_headers(client, "et-default@test.com")
    sid = _make_parent("et-default@test.com")
    r = client.post(f"/api/songs/{sid}/extend", json={}, headers=h)
    assert r.status_code == 200, r.text
    assert _song(r.json()["song_id"]).extend_type is None


@pytest.mark.parametrize("bad", ["middle", "TAILX", "both", "从头部", "head, tail"])
def test_extend_type_illegal_value_400_fail_closed(client, provider, bad):
    """非法值必须 400 fail-closed —— 不静默改写为 tail（那正是「假功能」形态）。"""
    email = f"et-bad-{abs(hash(bad))}@test.com"
    h = _auth_headers(client, email)
    sid = _make_parent(email)
    r = client.post(f"/api/songs/{sid}/extend", json={"extend_type": bad}, headers=h)
    assert r.status_code == 400, r.text
    assert "tail" in r.json()["detail"] and "head" in r.json()["detail"]


def test_extend_type_case_insensitive_normalized(client, provider):
    """"HEAD" 归一为 "head"（大小写不敏感，但不接受其它拼写）。"""
    h = _auth_headers(client, "et-case@test.com")
    sid = _make_parent("et-case@test.com")
    r = client.post(f"/api/songs/{sid}/extend", json={"extend_type": "HEAD"}, headers=h)
    assert r.status_code == 200, r.text
    assert _song(r.json()["song_id"]).extend_type == "head"


def test_extend_type_blank_string_treated_as_unspecified(client, provider):
    """空串 = 未指定（前端下拉未选/后端默认），不是非法值。"""
    h = _auth_headers(client, "et-blank@test.com")
    sid = _make_parent("et-blank@test.com")
    r = client.post(f"/api/songs/{sid}/extend", json={"extend_type": ""}, headers=h)
    assert r.status_code == 200, r.text
    assert _song(r.json()["song_id"]).extend_type is None


def test_cover_and_remix_ignore_extend_type(client, provider):
    """cover/remix 不是 extend 能力，带了 extend_type 也不应落到子歌（语义隔离）。"""
    h = _auth_headers(client, "et-cov@test.com")
    sid = _make_parent("et-cov@test.com")
    for task in ("cover", "remix"):
        r = client.post(
            f"/api/songs/{sid}/{task}", json={"extend_type": "head"}, headers=h
        )
        assert r.status_code == 200, r.text
        assert _song(r.json()["song_id"]).extend_type is None


def test_cover_illegal_extend_type_not_validated(client, provider):
    """cover/remix 的 extend_type 不参与链路 → 不因它 400（只对 extend 做白名单）。"""
    h = _auth_headers(client, "et-cov-bad@test.com")
    sid = _make_parent("et-cov-bad@test.com")
    r = client.post(f"/api/songs/{sid}/cover", json={"extend_type": "nonsense"}, headers=h)
    assert r.status_code == 200, r.text


def test_generate_req_unknown_field_rejected_by_schema():
    """Schema 层：SpawnReq 必须**认识** extend_type，否则前端传了会被 pydantic 拒成 422。

    这正是缺陷 1 的另一半 —— 字段缺失时 pydantic 不是忽略而是报错。
    """
    from app.schemas import GenerateReq, SpawnReq

    assert "extend_type" in SpawnReq.model_fields
    assert "extend_type" in GenerateReq.model_fields
    assert SpawnReq(extend_type="head").extend_type == "head"
    assert GenerateReq(extend_type="tail").extend_type == "tail"


def test_song_out_exposes_extend_type(client, provider):
    """SongOut 必须下发该字段，否则前端无法回显/调试「这首是怎么续的」。"""
    h = _auth_headers(client, "et-out@test.com")
    sid = _make_parent("et-out@test.com")
    child_id = client.post(
        f"/api/songs/{sid}/extend", json={"extend_type": "head"}, headers=h
    ).json()["song_id"]
    r = client.get(f"/api/songs/{child_id}", headers=h)
    assert r.status_code == 200, r.text
    assert r.json().get("extend_type") == "head"


def test_model_column_exists_for_extend_type():
    """模型层：songs.extend_type 列存在（迁移 c3d4e5f7a8b9 的 ORM 侧对应）。"""
    assert "extend_type" in Song.__table__.columns
    assert Song.__table__.columns["extend_type"].nullable is True


# ============================================================
# 缺陷 2：机审改判「生效值」，锁死父歌回退绕过
# ============================================================
def test_moderation_blocks_unmoderated_parent_via_cover(client, provider):
    """核心绕过场景：父歌是**他人公开歌**且带未审文本，请求体留空 → 必须 400。

    旧实现只审空请求体 → 放行 → 未审文本被回退送进引擎。
    """
    from app.moderation import check_lyric, _load_blocklist

    terms, using_placeholder, _ = _load_blocklist()
    if using_placeholder:
        pytest.skip("blocklist 为占位词库，无法构造真实敏感文本")
    bad = f"{terms[0]}的歌词"

    h_owner = _auth_headers(client, "mod-owner@test.com")
    h_other = _auth_headers(client, "mod-other@test.com")
    sid = _make_parent("mod-owner@test.com", lyric=bad, is_public=True)

    r = client.post(f"/api/songs/{sid}/cover", json={}, headers=h_other)
    assert r.status_code == 400, "父歌回退绕过机审：未审文本被送进引擎"
    assert "审核" in r.json()["detail"]


def test_moderation_blocks_unmoderated_parent_via_remix(client, provider):
    """同一绕过路径的 remix 分支也要锁死（父歌 prompt 未审、lyric 合法）。"""
    from app.moderation import _load_blocklist

    terms, using_placeholder, _ = _load_blocklist()
    if using_placeholder:
        pytest.skip("blocklist 为占位词库，无法构造真实敏感文本")
    bad_prompt = f"{terms[0]}主题"

    h_owner = _auth_headers(client, "mod-r-owner@test.com")
    h_other = _auth_headers(client, "mod-r-other@test.com")
    sid = _make_parent(
        "mod-r-owner@test.com", lyric="clean lyric", prompt=bad_prompt, is_public=True
    )

    r = client.post(f"/api/songs/{sid}/remix", json={}, headers=h_other)
    assert r.status_code == 400, r.text
    assert "审核" in r.json()["detail"]


def test_moderation_checks_each_field_separately_not_concatenated(client, provider):
    """逐项校验（不拼接）：合法 lyric + 违规 prompt 必须被拒。

    拼接实现会因「两段各自合法、拼起来命中」或反过来产生假阴/假阳；
    本用例锁定「每一项都过闸」的语义。
    """
    from app.moderation import _load_blocklist

    terms, using_placeholder, _ = _load_blocklist()
    if using_placeholder:
        pytest.skip("blocklist 为占位词库，无法构造真实敏感文本")

    h = _auth_headers(client, "mod-each@test.com")
    sid = _make_parent("mod-each@test.com")
    r = client.post(
        f"/api/songs/{sid}/cover",
        json={"lyric": "clean lyric text", "prompt": f"{terms[0]}主题"},
        headers=h,
    )
    assert r.status_code == 400, r.text


def test_moderation_allows_clean_request_body(client, provider):
    """合法请求体正常放行（修复不能变成一律拦截）。"""
    h = _auth_headers(client, "mod-clean@test.com")
    sid = _make_parent("mod-clean@test.com", lyric="clean lyric", prompt="clean prompt")
    r = client.post(
        f"/api/songs/{sid}/cover",
        json={"lyric": "clean new lyric", "prompt": "clean new prompt"},
        headers=h,
    )
    assert r.status_code == 200, r.text


def test_moderation_uses_effective_lyric_for_extend_only(client, provider):
    """extend 只审 lyrics（prompt/style 不进 extend payload）→ 父歌 prompt 违规不拦 extend。

    机审要精确：拦下根本不会送进引擎的字段，会把合法操作误判成违规。
    """
    from app.moderation import _load_blocklist

    terms, using_placeholder, _ = _load_blocklist()
    if using_placeholder:
        pytest.skip("blocklist 为占位词库，无法构造真实敏感文本")

    h = _auth_headers(client, "mod-ext@test.com")
    sid = _make_parent(
        "mod-ext@test.com", lyric="clean lyric", prompt=f"{terms[0]}主题"
    )
    r = client.post(
        f"/api/songs/{sid}/extend", json={"lyric": "clean lyric"}, headers=h
    )
    assert r.status_code == 200, r.text


def test_spawn_moderation_disabled_bypasses_check(client, provider, monkeypatch):
    """开关关闭时不拦（保持既有开关语义，机审非硬编码常开）。"""
    h = _auth_headers(client, "mod-off@test.com")
    sid = _make_parent("mod-off@test.com")
    monkeypatch.setattr(settings, "enable_moderation", False)
    r = client.post(f"/api/songs/{sid}/cover", json={}, headers=h)
    assert r.status_code == 200, r.text


def test_spawn_parent_access_control_unchanged(client, provider):
    """回归护栏：改动机审取值路径后，父歌访问控制语义必须不变。

    · extend 需本人（他人歌 → 404，不泄露存在性）
    · cover 允许他人**公开**歌；他人**私有**歌 → 403
    """
    h_a = _auth_headers(client, "acl-a@test.com")
    h_b = _auth_headers(client, "acl-b@test.com")
    private_id = _make_parent("acl-a@test.com", is_public=False)
    public_id = _make_parent("acl-a@test.com", is_public=True)

    assert client.post(f"/api/songs/{private_id}/extend", json={}, headers=h_b).status_code == 404
    assert client.post(f"/api/songs/{private_id}/cover", json={}, headers=h_b).status_code == 403
    assert client.post(f"/api/songs/{public_id}/cover", json={}, headers=h_b).status_code == 200
