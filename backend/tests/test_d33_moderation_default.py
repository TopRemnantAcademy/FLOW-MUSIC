"""D33 — 심의 기본 ON + blocklist 실제 로딩 + 자동 승격 게이팅 회귀.

결함 핵심: 기본 OFF 라서 D24 수정이 no-op 이었고, blocklist 경로가 CWD 의존이었다.
수정 후: (1) 기본 enable_moderation=True, (2) blocklist 가 패키지 기준으로 실제 50행 로딩,
(3) 자동 approved 승격이 enable_moderation 설정 경로에서만 일어난다.
"""

import asyncio

from fastapi.testclient import TestClient

from app.config import settings
from app.moderation import check_lyric, describe_state, _load_blocklist
from app.routers.songs import _process_song


def _register(client: TestClient, email: str = "d33@test.com") -> dict:
    r = client.post("/api/auth/register", json={"email": email, "password": "pw123456"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_blocklist_loads_real_terms_package_relative():
    """[D33 2차] _blocklist_path 패키지 기준 해결 → 실제 단어 로딩(CWD 무의존).

    50행 중 주석/공백을 제외한 실제 단어는 33개. 핵심은 **플레이스홀더 2개가 아닌
    실제 파일이 로딩** 되었는지(using_placeholder=False, len>2) 를 잠그는 것이다.
    """
    terms, using_placeholder, _path = _load_blocklist()
    assert using_placeholder is False
    assert len(terms) > 2, "플레이스홀더(2개) 가 로딩되면 안 됨 — 실제 blocklist 여야 함"
    # describe_state 로 운영 가시성 일치
    st = describe_state()
    assert st["enabled"] is True
    assert st["using_placeholder"] is False
    assert st["terms"] == len(terms)


def test_check_lyric_rejects_blocked_term():
    """실제 blocklist 단어가 게이트 끝까지 차단되는지."""
    terms, _, _ = _load_blocklist()
    assert len(terms) > 0
    assert check_lyric(terms[0]) is False
    # 빈/정상 텍스트는 통과
    assert check_lyric("") is True
    assert check_lyric("찬송가 제1장") is True


def test_moderation_default_on_auto_approves(client, monkeypatch):
    """enable_moderation=True(기본) 일 때 완성곡이 자동 approved 로 승격."""
    monkeypatch.setattr(settings, "enable_moderation", True)
    monkeypatch.setattr(settings, "enable_audio_moderation", False)
    h = _register(client)
    r = client.post("/api/songs/generate", json={"prompt": "정상곡", "is_public": True}, headers=h)
    sid = r.json()["song_id"]
    asyncio.run(_process_song(sid))
    s = client.get(f"/api/songs/{sid}", headers=h).json()
    assert s["moderation_status"] == "approved"


def test_moderation_off_keeps_unreviewed(client, monkeypatch):
    """enable_moderation=False 면 자동 승격 없이 미심의(pending) 유지 — 무심의 노출 차단."""
    monkeypatch.setattr(settings, "enable_moderation", False)
    monkeypatch.setattr(settings, "enable_audio_moderation", False)
    h = _register(client)
    r = client.post("/api/songs/generate", json={"prompt": "정상곡", "is_public": True}, headers=h)
    sid = r.json()["song_id"]
    asyncio.run(_process_song(sid))
    s = client.get(f"/api/songs/{sid}", headers=h).json()
    assert s["moderation_status"] == "pending"
