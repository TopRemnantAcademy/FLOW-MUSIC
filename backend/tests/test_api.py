"""后端 mock 测试套件。

覆盖核心链路：注册/登录 → 生成（处理完成）→ 续写/翻唱/Remix → 同步到 FLOW APP → 歌单拉取；
以及异常路径：未登录拦截、机审拦截、未完成即同步拦截、越权同步拦截。

所有外部依赖均被 mock，不触达真实 Suno/S3。生成是异步后台任务，
测试中用 `_drive(song_id)` 显式驱动与生产 BackgroundTasks 同一个处理函数
`app.routers.songs._process_song`，从而稳定验证「编排 → 写回 DB」的逻辑。
"""

import asyncio

from fastapi.testclient import TestClient

from app.config import settings as _settings
from app.routers.songs import _process_song


def _drive(song_id: int) -> None:
    """显式驱动后台处理函数（与生产 BackgroundTasks 调用的是同一函数）。"""
    asyncio.run(_process_song(song_id))


def _register(client: TestClient, email: str = "u1@test.com") -> dict:
    r = client.post("/api/auth/register", json={"email": email, "password": "pw123456"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _link_and_exchange(client: TestClient, h: dict, subscriber_id: str = "sub_default") -> dict:
    """用户绑定 + server-to-server 交换 → 返回 scope 限定(sync:flow) 토큰 헤더。

    테스트는 flow_internal_token 을 고정값으로 설정해 exchange 엔드포인트 활성화.
    """
    _settings.flow_internal_token = "test-flow-internal"
    r = client.post("/api/auth/flow-link", json={"subscriber_id": subscriber_id}, headers=h)
    assert r.status_code == 200, r.text
    r = client.post(
        "/api/auth/flow-exchange",
        json={"subscriber_id": subscriber_id},
        headers={"X-Flow-Internal-Token": "test-flow-internal"},
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _wait_completed(client: TestClient, song_id: int, h: dict, tries: int = 30) -> dict:
    for _ in range(tries):
        s = client.get(f"/api/songs/{song_id}", headers=h).json()
        if s["status"] in ("completed", "failed"):
            return s
    return s


def test_health(client):
    assert client.get("/health").json()["status"] == "ok"


def test_register_login(client):
    r = client.post("/api/auth/register", json={"email": "a@test.com", "password": "pw123456"})
    assert r.status_code == 200 and "access_token" in r.json()
    r2 = client.post("/api/auth/login", json={"email": "a@test.com", "password": "pw123456"})
    assert r2.status_code == 200 and r2.json()["access_token"]


def test_register_duplicate(client):
    client.post("/api/auth/register", json={"email": "dup@test.com", "password": "pw123456"})
    r = client.post("/api/auth/register", json={"email": "dup@test.com", "password": "pw123456"})
    assert r.status_code == 409


def test_generate_requires_auth(client):
    r = client.post("/api/songs/generate", json={"prompt": "x", "style": "y"})
    assert r.status_code == 401


def test_generate_and_poll(client):
    h = _register(client)
    # Freemium 게이트(2026-09-04): 미구독자는「앞 60초 프리뷰」만 받으므로 완곡 audio_url
    # 검증은 구독 상태에서만 유효하다. 게이트 동작 자체는 test_preview_gate.py 가 전담.
    r = client.post("/api/auth/subscribe", json={"tier_key": "standard"}, headers=h)
    assert r.status_code == 200, r.text
    r = client.post(
        "/api/songs/generate",
        json={"prompt": "失恋的雨夜", "style": "国风 伤感 钢琴"},
        headers=h,
    )
    assert r.status_code == 200
    song_id = r.json()["song_id"]
    _drive(song_id)
    song = client.get(f"/api/songs/{song_id}", headers=h).json()
    assert song["status"] == "completed"
    assert song["audio_url"] == "https://mock.cdn.example.com/audio.mp3"
    # P0 引擎名/模型版本不暴露给公共端点（仅后台 AdminSongOut 可见）
    assert "model_version" not in song
    assert "engine_name" not in song
    assert song["duration"] == 120
    assert song["task_type"] == "generate"


def test_moderation_blocks_bad_lyric(client):
    h = _register(client)
    r = client.post(
        "/api/songs/generate",
        json={"prompt": "ok", "style": "pop", "lyric": "这是赌博内容"},
        headers=h,
    )
    assert r.status_code == 400


def test_moderation_passes_clean(client):
    h = _register(client)
    r = client.post(
        "/api/songs/generate",
        json={"prompt": "ok", "style": "pop", "lyric": "今天天气真好"},
        headers=h,
    )
    assert r.status_code == 200


def test_extend_cover_remix(client):
    from app.config import settings as _s

    original_quota = _s.per_user_daily_quota
    _s.per_user_daily_quota = 10  # 本测试需创建 1 父 + 3 子共 4 首，临时放宽避免触发配额
    try:
        h = _register(client)
        r = client.post("/api/songs/generate", json={"prompt": "父歌", "style": "rock"}, headers=h)
        pid = r.json()["song_id"]
        _drive(pid)
        parent = client.get(f"/api/songs/{pid}", headers=h).json()
        assert parent["status"] == "completed"

        for task in ("extend", "cover", "remix"):
            r = client.post(f"/api/songs/{pid}/{task}", json={}, headers=h)
            assert r.status_code == 200, task
            child_id = r.json()["song_id"]
            _drive(child_id)
            child = client.get(f"/api/songs/{child_id}", headers=h).json()
            assert child["status"] == "completed", task
            assert child["parent_song_id"] == pid, task
            assert child["task_type"] == task, task
    finally:
        _s.per_user_daily_quota = original_quota


def test_sync_flow_full(client):
    h = _register(client)
    r = client.post("/api/songs/generate", json={"prompt": "sync测试", "style": "pop"}, headers=h)
    sid = r.json()["song_id"]
    _drive(sid)
    s = client.get(f"/api/songs/{sid}", headers=h).json()
    assert s["status"] == "completed"

    # 同步到 FLOW APP —— 必须使用 scope 限定(sync:flow) 토큰(🔴A 闭合)
    hs = _link_and_exchange(client, h)
    r = client.post(f"/api/v1/songs/{sid}/sync-flow", headers=hs)
    assert r.status_code == 200
    assert r.json()["status"] == "synced"

    # APK 拉取歌单
    r = client.get("/api/v1/sync/flow", headers=hs)
    assert r.status_code == 200
    assert any(item["song_id"] == sid for item in r.json())


def test_sync_flow_before_complete(client):
    h = _register(client)
    # 直接插入一条 pending 状态的歌曲（绕过 generate 路由的后台自动完成），
    # 验证「未完成即同步」被拦截（应返回 400）。
    from app.db import SessionLocal
    from app.models import Song, User

    sl = SessionLocal()
    user = sl.query(User).filter_by(email="u1@test.com").first()
    song = Song(user_id=user.id, status="pending", task_type="generate")
    sl.add(song)
    sl.commit()
    sl.refresh(song)
    sid = song.id
    sl.close()
    hs = _link_and_exchange(client, h)
    r = client.post(f"/api/v1/songs/{sid}/sync-flow", headers=hs)
    assert r.status_code == 400


def test_sync_flow_wrong_owner(client):
    h1 = _register(client, "owner@test.com")
    r = client.post("/api/songs/generate", json={"prompt": "x", "style": "pop"}, headers=h1)
    sid = r.json()["song_id"]
    _drive(sid)

    h2 = _register(client, "other@test.com")
    hs2 = _link_and_exchange(client, h2, subscriber_id="sub_other")
    r = client.post(f"/api/v1/songs/{sid}/sync-flow", headers=hs2)
    assert r.status_code == 404


def test_sync_flow_rejected_is_blocked(client):
    """D24: 内容审核被拒(rejected)的歌曲不得同步到 FLOW APP。"""
    h = _register(client)
    from app.db import SessionLocal
    from app.models import Song, User

    sl = SessionLocal()
    user = sl.query(User).filter_by(email="u1@test.com").first()
    song = Song(
        user_id=user.id, status="completed", moderation_status="rejected", task_type="generate"
    )
    sl.add(song)
    sl.commit()
    sl.refresh(song)
    sid = song.id
    sl.close()

    hs = _link_and_exchange(client, h)
    r = client.post(f"/api/v1/songs/{sid}/sync-flow", headers=hs)
    assert r.status_code == 400, r.text


def test_list_songs(client):
    h = _register(client)
    r = client.post("/api/songs/generate", json={"prompt": "list测试", "style": "pop"}, headers=h)
    sid = r.json()["song_id"]
    _drive(sid)
    r = client.get("/api/songs", headers=h)
    assert r.status_code == 200
    assert any(s["id"] == sid for s in r.json())


def test_factory_selects_provider_by_config():
    """Route B：工厂按 settings.music_provider 选择 provider（不依赖注入）。"""
    from app.config import settings as _settings
    import app.orchestrator.client as cl
    from app.orchestrator.mock import MockProvider
    from app.orchestrator.suno import SunoProvider

    original = _settings.music_provider
    try:
        _settings.music_provider = "mock"
        cl.set_orchestrator(None)
        assert isinstance(cl.get_orchestrator(), MockProvider)

        _settings.music_provider = "suno"
        cl.set_orchestrator(None)
        assert isinstance(cl.get_orchestrator(), SunoProvider)
    finally:
        _settings.music_provider = original
        cl.set_orchestrator(MockProvider())  # 还原 conftest 注入，避免影响其他用例


def test_factory_rejects_unknown_provider():
    """未知供应商配置应抛出清晰错误。"""
    import pytest
    from app.config import settings as _settings
    import app.orchestrator.client as cl
    from app.orchestrator.mock import MockProvider

    original = _settings.music_provider
    try:
        _settings.music_provider = "bogus-vendor"
        cl.set_orchestrator(None)
        with pytest.raises(ValueError):
            cl.get_orchestrator()
    finally:
        _settings.music_provider = original
        cl.set_orchestrator(None)
        cl.set_orchestrator(MockProvider())


def test_continue_uses_custom_id(client):
    """续写应基于父歌 custom_id（UUID）发起，而非轮询用的 task_id。"""
    import app.orchestrator.client as cl
    from app.orchestrator.mock import MockProvider

    mp = MockProvider()
    cl.set_orchestrator(mp)
    try:
        h = _register(client)
        r = client.post("/api/songs/generate", json={"prompt": "父歌", "style": "rock"}, headers=h)
        pid = r.json()["song_id"]
        _drive(pid)
        parent = client.get(f"/api/songs/{pid}", headers=h).json()
        assert parent["status"] == "completed"
        assert parent["custom_id"] == "mock-custom-0001"

        r = client.post(f"/api/songs/{pid}/extend", json={}, headers=h)
        child_id = r.json()["song_id"]
        _drive(child_id)
        # 续写请求携带的续写基准必须是父歌 custom_id（UUID），不是 task_id
        assert mp.last_continue_song_id == "mock-custom-0001"
    finally:
        cl.set_orchestrator(MockProvider())


def test_spawn_moderation_blocks_bad_lyric(client):
    """续写/翻唱携带违规歌词应被机审拦截（400）。"""
    h = _register(client)
    r = client.post("/api/songs/generate", json={"prompt": "父歌", "style": "rock"}, headers=h)
    pid = r.json()["song_id"]
    _drive(pid)
    r = client.post(f"/api/songs/{pid}/cover", json={"lyric": "这是赌博内容"}, headers=h)
    assert r.status_code == 400


def test_daily_quota_blocks_after_limit(client):
    """每日免费配额耗尽后，再次生成返回 402（引导订阅）。"""
    from app.config import settings as _s

    original = _s.per_user_daily_quota
    _s.per_user_daily_quota = 3  # 明确阈值，避免依赖默认值
    try:
        h = _register(client)
        for _ in range(3):
            r = client.post(
                "/api/songs/generate",
                json={"prompt": "配额测试", "style": "pop"},
                headers=h,
            )
            assert r.status_code == 200, r.text
        # 第 4 次超出每日配额
        r = client.post(
            "/api/songs/generate",
            json={"prompt": "配额测试", "style": "pop"},
            headers=h,
        )
        assert r.status_code == 402, r.text
    finally:
        _s.per_user_daily_quota = original


def test_rate_limit_generate_throttles(client):
    """突发限流：同一用户每分钟超过阈值返回 429。"""
    from app.config import settings as _s
    from app.ratelimit import _hits

    original = (_s.enable_rate_limit, _s.generate_rate_limit_per_minute)
    _s.enable_rate_limit = True
    _s.generate_rate_limit_per_minute = 2
    try:
        _hits.clear()
        h = _register(client)
        for _ in range(2):
            r = client.post(
                "/api/songs/generate",
                json={"prompt": "限流测试", "style": "pop"},
                headers=h,
            )
            assert r.status_code == 200, r.text
        # 第 3 次触发突发限流
        r = client.post(
            "/api/songs/generate",
            json={"prompt": "限流测试", "style": "pop"},
            headers=h,
        )
        assert r.status_code == 429, r.text
    finally:
        _s.enable_rate_limit, _s.generate_rate_limit_per_minute = original
        _hits.clear()


def test_flow_link_then_exchange_ok(client):
    """flow-link(사용자 동의) → flow-exchange(server-to-server) → scope 한정 토큰 발급."""
    h = _register(client)
    hs = _link_and_exchange(client, h, subscriber_id="sub_a")
    # 스코프 한정 토큰은 sync 전용 endpoint 에만 접근 가능
    r = client.get("/api/v1/sync/flow", headers=hs)
    assert r.status_code == 200
    assert r.json() == []


def test_flow_sync_rejects_full_token(client):
    """sync endpoint 는 일반 전권한 음악 JWT 를 거부(401) — scope 한정만 통과(🔴A)."""
    h = _register(client)
    r = client.get("/api/v1/sync/flow", headers=h)
    assert r.status_code == 401


def test_flow_sync_rejects_no_token(client):
    """sync endpoint 미인증 호출 차단(401)."""
    r = client.get("/api/v1/sync/flow")
    assert r.status_code == 401


def test_flow_exchange_requires_internal_token(client):
    """flow-exchange 는 내부 키 없이 호출 불가(401)."""
    _settings.flow_internal_token = "test-flow-internal"
    r = client.post("/api/auth/flow-exchange", json={"subscriber_id": "x"})
    assert r.status_code == 401
    r = client.post(
        "/api/auth/flow-exchange",
        json={"subscriber_id": "x"},
        headers={"X-Flow-Internal-Token": "wrong"},
    )
    assert r.status_code == 401


def test_flow_exchange_unknown_subscriber(client):
    """매핑 없는 subscriber_id → 404(바인딩 선행 필요)."""
    _settings.flow_internal_token = "test-flow-internal"
    r = client.post(
        "/api/auth/flow-exchange",
        json={"subscriber_id": "no-such-link"},
        headers={"X-Flow-Internal-Token": "test-flow-internal"},
    )
    assert r.status_code == 404


def test_flow_exchange_scoped_token_cannot_access_full_apis(client):
    """scope 한정 토큰은 sync 외 음악 API(예: 생성) 에 접근 불가(401) — 최소 권한."""
    h = _register(client)
    hs = _link_and_exchange(client, h)
    r = client.post("/api/songs/generate", json={"prompt": "x", "style": "y"}, headers=hs)
    assert r.status_code == 401


# ---- Web 自签腿（POST /api/auth/flow-token, 2026-09-10）----


def test_flow_token_requires_auth(client):
    """自签腿도 미인증 호출 차단(401)."""
    r = client.post("/api/auth/flow-token")
    assert r.status_code == 401


def test_flow_token_self_mint_scoped_and_isolated(client):
    """全权限 JWT 自签 → scope 限定(sync:flow) 令牌：sync 端点 통과, 全权限 API 격리 유지."""
    h = _register(client)
    r = client.post("/api/auth/flow-token", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["access_token"] and body["scope"] == "sync:flow"
    hs = {"Authorization": f"Bearer {body['access_token']}"}
    # scope 令牌可访问 sync 端点（空歌单）
    rs = client.get("/api/v1/sync/flow", headers=hs)
    assert rs.status_code == 200 and rs.json() == []
    # 最小权限不破：scope 令牌依旧不能生成/访问全权限 API
    r2 = client.post("/api/songs/generate", json={"prompt": "x", "style": "y"}, headers=hs)
    assert r2.status_code == 401


def test_flow_token_end_to_end_sync(client):
    """自签令牌走完整同步链路：注册→订阅→生成→自签→sync-flow→拉取。"""
    h = _register(client)
    r = client.post("/api/auth/subscribe", json={"tier_key": "standard"}, headers=h)
    assert r.status_code == 200, r.text
    r = client.post("/api/songs/generate", json={"prompt": "flow sync", "style": "pop"}, headers=h)
    assert r.status_code == 200
    sid = r.json()["song_id"]
    _drive(sid)
    rt = client.post("/api/auth/flow-token", headers=h)
    assert rt.status_code == 200, rt.text
    hs = {"Authorization": f"Bearer {rt.json()['access_token']}"}
    rs = client.post(f"/api/v1/songs/{sid}/sync-flow", headers=hs)
    assert rs.status_code == 200, rs.text
    assert rs.json()["status"] == "synced"
    rg = client.get("/api/v1/sync/flow", headers=hs)
    assert rg.status_code == 200
    assert len(rg.json()) == 1 and rg.json()[0]["song_id"] == sid
