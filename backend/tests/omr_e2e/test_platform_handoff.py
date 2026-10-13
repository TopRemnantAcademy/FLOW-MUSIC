"""平台闭环：成品 → Song 注册 → explore 匿名命中（issue #122 的最后一段）。

严守 issue #122 铁律 3/4：
  · 复用 `tests/conftest.py` 的 session TestClient + `MockProvider`（不另起炉灶）；
  · **零网络**：mock 的「上游音频 URL」故意用内网环回地址，CDN 回源与试听片段
    都会被安全守卫 / 配置判空挡下 —— 也就顺带证明了「平台侧不依赖网络」；
  · 不碰 `models.py`、不加表、不加迁移：只写既有的 `songs` 字段。

⚠ 边界（写在这里，免得把"explore 命中"当成"HTTP 全链路通了"）：
  真正被验证的是「成品 → Song 行 → 审核态 → 公开流 → 订阅门控」；
  `POST /api/songs/generate` 那条**要拉音频的** HTTP 提交路径不在本组覆盖范围内
  （沙箱给不出可拉取的音频 URL，而伪造一个可拉取的服务等于偷偷起网络）。
"""

import os

from . import fixtures as fx
from .mocks import MockVocalEngine, sha256_file
from .orchestrator import run_score_to_music
from .platform import MOCK_AUDIO_URL, MOCK_CDN_URL, MOCK_CUSTOM_ID, MOCK_MODEL_VERSION, ClosedLoop


def _produce(tmp_path):
    """先跑一遍 OMR 流水线拿到 master（这是"上游产出的成品"）。"""
    result = run_score_to_music(
        fx.synth_score_png(),
        fx.SAMPLE_LYRIC,
        {"song_id": fx.SONG_ID},
        work_dir=str(tmp_path),
    )
    assert result.ok is True, result.halt_reason
    return result


def test_platform_handoff(tmp_path, client):
    """成品经 TestClient 注册为 Song，并能被匿名用户在 /api/songs/explore 取到。"""
    loop = ClosedLoop(client)
    loop.register("omr-handoff@test.com")
    loop.subscribe()  # 订阅档 → 整曲链接可下发（未订阅只会拿到试听片段）

    pipeline = _produce(tmp_path)
    evidence = loop.register_master(
        pipeline,
        title=fx.TITLE,
        lyric=fx.SAMPLE_LYRIC,
        is_public=True,
    )

    # ---- 注册结果本身 ----
    song_id = evidence["song_id"]
    assert isinstance(song_id, int) and song_id > 0
    assert evidence["status"] == "completed"
    assert evidence["moderation_status"] == "approved"
    assert evidence["is_public"] is True
    assert evidence["external_id"] == "mock-task-0001"
    assert evidence["custom_id"] == MOCK_CUSTOM_ID
    assert evidence["duration"] > 0

    # 成品音频与流水线产物**是同一份东西**（否则闭环就是假的）
    assert evidence["master_sha256"] == sha256_file(pipeline.artifacts["master_wav"])
    assert abs(evidence["master_seconds"] - fx.BASELINE_SECONDS) < 0.01

    # ---- 详情接口（本人）----
    detail = client.get(f"/api/songs/{song_id}", headers=loop.headers)
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["status"] == "completed"
    assert body["is_owner"] is True
    assert body["duration"] == evidence["duration"]
    # mock 的上游 URL 是环回地址 → 不会泄露成"可播放的假链接"，但字段本身在
    assert body["audio_url"] is None or "mock" in body["audio_url"] or MOCK_AUDIO_URL in body["audio_url"]

    # ---- explore：匿名（不带 Authorization）必须能取到 ----
    hit = loop.find_in_explore(song_id)
    assert hit is not None, "注册的 Song 没进公开流"
    assert hit["title"] == fx.TITLE
    assert hit["moderation_status"] == "approved"
    assert hit["is_public"] is True
    assert hit["id"] == song_id

    # 按标题搜索也命中（GAP 的 q 参数）
    assert any(r["id"] == song_id for r in loop.explore(q="찬양"))


def test_private_song_stays_out_of_explore(tmp_path, client):
    """未公开的成品不进公开流（平台的可见性规则在闭环里依然生效）。"""
    loop = ClosedLoop(client)
    loop.register("omr-private@test.com")
    loop.subscribe()
    pipeline = _produce(tmp_path)
    evidence = loop.register_master(
        pipeline, title="私有成品", lyric=fx.SAMPLE_LYRIC, is_public=False
    )
    assert loop.find_in_explore(evidence["song_id"]) is None


def test_unapproved_song_stays_out_of_explore(tmp_path, client):
    """审核态不是 approved 就不进公开流（内容审核不是装饰）。"""
    from app.db import SessionLocal
    from app.models import Song

    loop = ClosedLoop(client)
    loop.register("omr-pending@test.com")
    loop.subscribe()
    pipeline = _produce(tmp_path)
    evidence = loop.register_master(pipeline, title="待审核成品", lyric=fx.SAMPLE_LYRIC)
    db = SessionLocal()
    try:
        db.get(Song, evidence["song_id"]).moderation_status = "pending"
        db.commit()
    finally:
        db.close()
    assert loop.find_in_explore(evidence["song_id"]) is None


def test_subscription_gate_still_active_after_handoff(tmp_path, client):
    """交付后门控不失效：未订阅用户拿不到整曲链接（fail-closed）。"""
    loop = ClosedLoop(client)
    loop.register("omr-gate@test.com")
    loop.subscribe()
    pipeline = _produce(tmp_path)
    evidence = loop.register_master(pipeline, title="门控成品", lyric=fx.SAMPLE_LYRIC)

    # 换一个未订阅的账号来看同一首公开歌
    other = ClosedLoop(client)
    other.register("omr-free-viewer@test.com")
    r = other.client.get(f"/api/songs/{evidence['song_id']}", headers=other.headers)
    assert r.status_code == 200, r.text
    assert r.json()["is_owner"] is False
    # 未订阅 → 只能拿试听片段（mock 里没有片段 → None），绝不回落成整曲
    assert r.json()["audio_url"] is None


def test_zero_network_handoff(tmp_path, client):
    """把「上游 URL 不可达」当常态：注册、审核态、公开流、播放计数都不该被它拖垮。"""
    loop = ClosedLoop(client)
    loop.register("omr-nonet@test.com")
    loop.subscribe()
    pipeline = _produce(tmp_path)
    evidence = loop.register_master(
        pipeline, title="离线成品", lyric=fx.SAMPLE_LYRIC, audio_url=MOCK_AUDIO_URL
    )
    # 播放计数：与音频能不能播无关（计数是平台自己的账）
    r = client.post(f"/api/songs/{evidence['song_id']}/play", headers=loop.headers)
    assert r.status_code == 200, r.text
    assert r.json()["play_count"] >= 1
    assert loop.find_in_explore(evidence["song_id"]) is not None

    # 「我的作品」列表里也有它
    mine = client.get("/api/songs", headers=loop.headers)
    assert mine.status_code == 200
    assert evidence["song_id"] in {s["id"] for s in mine.json()}


def test_cdn_outlet_prefers_cdn_when_present(client):
    """CDN 出口（GAP-019）在闭环里同样生效：有 CDN 链接就不下发易失的上游链接。"""
    loop = ClosedLoop(client)
    loop.register("omr-cdn@test.com")
    loop.subscribe()

    from app.db import SessionLocal
    from app.models import Song, User

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == "omr-cdn@test.com").first()
        song = Song(
            user_id=user.id,
            title="CDN 成品",
            status="completed",
            audio_url=MOCK_AUDIO_URL,
            audio_cdn_url=MOCK_CDN_URL,
            moderation_status="approved",
            is_public=True,
            model_version=MOCK_MODEL_VERSION,
        )
        db.add(song)
        db.commit()
        song_id = song.id
    finally:
        db.close()

    body = client.get(f"/api/songs/{song_id}", headers=loop.headers).json()
    assert body["audio_url"] == MOCK_CDN_URL, "应优先下发 CDN 链接"
    # 上游链接（含内网地址）不该出现在下发给客户端的声音地址里
    assert MOCK_AUDIO_URL not in (body["audio_url"] or "")
