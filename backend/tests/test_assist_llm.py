"""✨ 辅助按钮 AI 化（app.assist_llm）回归测试。

覆盖：
- LLM 已配置且可用 → /assist/style(mode=style|prompt)、/assist/lyrics 走 AI 输出
- LLM 调用失败 → 自动降级（本地规则 / 引擎官方端点），按钮不 5xx 悬空
- LLM 未配置（默认）→ 维持原本地行为
- /assist/lyrics/format 刻意不走 AI（防改写用户原词）
- split_lyrics_output 解析（标题行 / 书名号 / markdown 围栏容错）
"""

from app import assist_llm
from app.assist_llm import LLMError


def _hdr(client, email: str) -> dict:
    r = client.post("/api/auth/register", json={"email": email, "password": "pw123456"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _enable(monkeypatch, chat_impl=None):
    """打开 LLM 开关；chat_impl(system, user) -> str 可选。"""
    monkeypatch.setattr(assist_llm, "llm_enabled", lambda: True)
    if chat_impl is not None:

        async def chat(system, user, *, max_tokens=800, temperature=0.7):
            return chat_impl(system, user)

        monkeypatch.setattr(assist_llm, "chat", chat)


def _broken_async(system, user, *, max_tokens=800, temperature=0.7):
    raise LLMError("boom")


# ── /assist/style ────────────────────────────────────────────────────────────


def test_style_uses_llm(client, monkeypatch):
    seen = {}

    def impl(system, user):
        seen["system"] = system
        seen["user"] = user
        return "福音，慢速，温暖，钢琴，女声"

    _enable(monkeypatch, impl)
    h = _hdr(client, "llm-style1@test.com")
    r = client.post(
        "/api/songs/assist/style",
        json={"text": "来一首慢一点的钢琴 worship"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["optimized"] == "福音，慢速，温暖，钢琴，女声"
    assert any("AI" in n for n in data["notes"])
    assert "风格" in seen["system"]
    assert seen["user"].startswith("来一首")


def test_style_mode_prompt_uses_prompt_system(client, monkeypatch):
    seen = {}

    def impl(system, user):
        seen["system"] = system
        return "清晨的第一缕光里，带着感恩开始一天的敬拜。"

    _enable(monkeypatch, impl)
    h = _hdr(client, "llm-style2@test.com")
    r = client.post(
        "/api/songs/assist/style",
        json={"text": "清晨感恩", "mode": "prompt"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    assert r.json()["optimized"].startswith("清晨的第一缕光")
    assert "主题" in seen["system"]


def test_style_mode_prompt_falls_back_to_original_when_llm_down(client, monkeypatch):
    async def broken(system, user, *, max_tokens=800, temperature=0.7):
        raise LLMError("boom")

    monkeypatch.setattr(assist_llm, "llm_enabled", lambda: True)
    monkeypatch.setattr(assist_llm, "chat", broken)
    h = _hdr(client, "llm-style3@test.com")
    r = client.post(
        "/api/songs/assist/style",
        json={"text": "清晨感恩", "mode": "prompt"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["optimized"] == "清晨感恩"  # 原样返回，不压碎成标签
    assert any("AI" in n for n in data["notes"])


def test_style_falls_back_to_local_rules_when_llm_down(client, monkeypatch):
    async def broken(system, user, *, max_tokens=800, temperature=0.7):
        raise LLMError("boom")

    monkeypatch.setattr(assist_llm, "llm_enabled", lambda: True)
    monkeypatch.setattr(assist_llm, "chat", broken)
    h = _hdr(client, "llm-style4@test.com")
    r = client.post(
        "/api/songs/assist/style",
        json={"text": "gospel, slow, piano, female vocal"},
        headers=h,
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["optimized"]  # 本地确定性规则产出非空
    assert not any("AI" in n for n in data["notes"])


def test_style_local_when_llm_not_configured(client):
    # 默认 settings.assist_llm_api_key 为空 → 走原本地规则（回归保障）
    h = _hdr(client, "llm-style5@test.com")
    r = client.post(
        "/api/songs/assist/style",
        json={"text": "gospel, slow, piano"},
        headers=h,
    )
    assert r.status_code == 200
    assert r.json()["optimized"]


# ── /assist/lyrics ───────────────────────────────────────────────────────────


def test_lyrics_uses_llm_and_parses_title(client, monkeypatch):
    def impl(system, user):
        assert "[Verse]" in system  # 结构标记指令必须在 system 提示中
        assert user == "清晨的恩典"
        return "标题：晨光\n\n[Verse]\n晨光洒在窗台\n心随旋律醒来\n\n[Chorus]\n歌声飞扬"

    _enable(monkeypatch, impl)
    h = _hdr(client, "llm-lyrics1@test.com")
    r = client.post("/api/songs/assist/lyrics", json={"prompt": "清晨的恩典"}, headers=h)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["title"] == "晨光"
    assert data["lyrics"].startswith("[Verse]")
    assert data["engine"] == "llm"


def test_lyrics_falls_back_to_engine_when_llm_down(client, monkeypatch):
    monkeypatch.setattr(assist_llm, "llm_enabled", lambda: True)
    monkeypatch.setattr(assist_llm, "chat", _broken_async)

    class FakeProvider:
        async def generate_lyrics(self, prompt):
            return {"title": "备用", "lyrics": "[Verse]\n备用歌词"}

    import app.routers.songs as songs_mod

    monkeypatch.setattr(songs_mod, "get_orchestrator", lambda name: FakeProvider())
    h = _hdr(client, "llm-lyrics2@test.com")
    r = client.post("/api/songs/assist/lyrics", json={"prompt": "清晨"}, headers=h)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["lyrics"] == "[Verse]\n备用歌词"
    assert data["engine"] == "mureka"


def test_lyrics_400_when_llm_down_and_engine_unsupported(client, monkeypatch):
    monkeypatch.setattr(assist_llm, "llm_enabled", lambda: True)
    monkeypatch.setattr(assist_llm, "chat", _broken_async)

    class NoLyrics:
        pass

    import app.routers.songs as songs_mod

    monkeypatch.setattr(songs_mod, "get_orchestrator", lambda name: NoLyrics())
    h = _hdr(client, "llm-lyrics3@test.com")
    r = client.post("/api/songs/assist/lyrics", json={"prompt": "清晨"}, headers=h)
    assert r.status_code == 400  # 原有「引擎不支持」中性文案链路保持


def test_lyrics_requires_prompt(client):
    h = _hdr(client, "llm-lyrics4@test.com")
    r = client.post("/api/songs/assist/lyrics", json={"prompt": "  "}, headers=h)
    assert r.status_code == 400


# ── /assist/lyrics/format（刻意不走 AI） ─────────────────────────────────────


def test_format_never_calls_llm(client, monkeypatch):
    monkeypatch.setattr(assist_llm, "llm_enabled", lambda: True)

    def boom(*args, **kwargs):
        raise AssertionError("format 必须保持本地规则，不得改写用户原词")

    monkeypatch.setattr(assist_llm, "chat", boom)
    h = _hdr(client, "llm-fmt1@test.com")
    r = client.post(
        "/api/songs/assist/lyrics/format",
        json={"text": "[verse]\n你好\n\n\n"},
        headers=h,
    )
    assert r.status_code == 200


# ── split_lyrics_output 解析 ─────────────────────────────────────────────────


def test_split_lyrics_output_variants():
    t, l = assist_llm.split_lyrics_output("标题：微光\n[Verse]\n一句")
    assert t == "微光" and l == "[Verse]\n一句"
    t, l = assist_llm.split_lyrics_output("标题:《星与海》\n[Chorus]\n哈")
    assert t == "星与海"
    t, l = assist_llm.split_lyrics_output("[Verse]\n无标题输出")
    assert t == "" and l.startswith("[Verse]")
    t, l = assist_llm.split_lyrics_output("标题：围栏\n```\n[Verse]\n内文\n```")
    assert l == "[Verse]\n内文"
