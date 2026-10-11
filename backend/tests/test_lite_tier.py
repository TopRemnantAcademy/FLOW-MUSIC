"""저가 티어 lite + mureka-7.6 실질 원가절감 테스트 (2026-09-04).

핵심 규칙:
- lite 는 **값싼 모델(mureka-7.6) 바인딩** 으로 원가를 실제로 낮춘다.
  가격만 내리면 원가가 그대로여서 마진이 음수 → "절감" 이 장부에만 남는다.
- ⚠ "코드에 수정이 있다" ≠ "작동한다". 테스트 6번은 **실제 전송 payload** 를 캡처해
  model 이 정말 mureka-7.6 으로 나가는지 확인한다(no-op 회귀 방지).
- lite 는 유료 티어다 → TIER_LIMITS 에 없으면 is_subscribed=False 가 되어
  결제한 사용자가 완곡 재생·다운로드를 못 한다(조용한 기능정지).

주의: conftest 의 `_clean` 이 매 테스트 후 **전 테이블을 비운다** → 각 테스트가
필요한 시드/설정을 직접 만들어야 한다(라우팅 캐시 TTL 도 있어 invalidate 필수).
"""

import asyncio

import pytest

from app.db import SessionLocal
from app.entitlements import is_subscribed, resolve_limits
from app.models import EngineConfig, PricingTier, RoutingRule
from app.orchestrator.selection import (
    MODEL_COST_CNY,
    invalidate_config_cache,
    resolve_cost_cny,
    resolve_tier_model_version,
    seed_default_config,
)


def _seed_all():
    """엔진/라우팅/티어 기본 시드 + 라우팅 캐시 무효화."""
    db = SessionLocal()
    try:
        db.query(EngineConfig).delete()
        db.query(RoutingRule).delete()
        db.query(PricingTier).delete()
        db.commit()
        seed_default_config(db)
    finally:
        db.close()
    invalidate_config_cache()


def _register(client, email: str) -> dict:
    r = client.post("/api/auth/register", json={"email": email, "password": "pw123456"})
    assert r.status_code in (200, 201), r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


# ---------- 1. lite 는 "구독 상태" 다(게이트가 막으면 안 됨) ----------
def test_lite_is_subscribed():
    assert is_subscribed("lite") is True, (
        "lite 는 유료 티어 — 미구독으로 취급되면 완곡 재생이 막힌다"
    )
    limits = resolve_limits(None, "lite")
    assert limits["tier_key"] == "lite"
    assert limits["daily_quota"] == 8
    assert limits["max_quality"] == "standard"
    assert limits["features"] == ["generate"]


# ---------- 2. 티어 → 모델 바인딩 ----------
def test_tier_model_binding():
    _seed_all()
    db = SessionLocal()
    try:
        row = db.query(PricingTier).filter(PricingTier.tier_key == "lite").first()
        assert row is not None, "seed 가 lite 를 주입하지 않았다(시드 멱등 함정 재발)"
        assert row.model_version == "mureka-7.6"
        assert resolve_tier_model_version(db, "lite") == "mureka-7.6"
        # 모델 미바인딩 티어/없는 티어 → 빈 문자열(엔진 기본 모델)
        assert resolve_tier_model_version(db, "standard") == ""
        assert resolve_tier_model_version(db, "no-such-tier") == ""
        assert resolve_tier_model_version(db, None) == ""
    finally:
        db.close()


# ---------- 3. 모델별 원가가 실제로 다르다(실측 단가 표) ----------
def test_model_cost_is_model_specific():
    _seed_all()
    db = SessionLocal()
    try:
        base = resolve_cost_cny(db, "general", n=1)
        cheap = resolve_cost_cny(db, "general", n=1, model_version="mureka-7.6")
        pricey = resolve_cost_cny(db, "general", n=1, model_version="mureka-9.5")
        assert base > 0, f"시드 후 기본 원가가 0 이면_engine_설정이 안 잡힌 것: {base}"
        assert cheap < base, f"저가 모델 원가가 기본보다 싸야 한다: {cheap} vs {base}"
        assert pricey > base, f"고가 모델 원가가 기본보다 비싸야 한다: {pricey} vs {base}"
        assert ("mureka", "mureka-7.6") in MODEL_COST_CNY
        # n 배 선형
        assert resolve_cost_cny(db, "general", n=2, model_version="mureka-7.6") == pytest.approx(
            cheap * 2, abs=1e-6
        )
    finally:
        db.close()


# ---------- 4. lite 로 생성하면 모델이 반영되고 유료 권한을 갖는다 ----------
def test_lite_generation_binds_cheap_model(client, monkeypatch):
    _seed_all()
    h = _register(client, "lite-gen@test.com")
    r = client.post("/api/auth/subscribe", json={"tier_key": "lite"}, headers=h)
    assert r.status_code == 200, r.text

    # ⚠ DB 의 song.model_version 은 **provider 가 되돌려준 실제 모델** 로 덮어써진다
    #   (mock 은 'chirp-v4.5'回传). 진짜 절감은 "요청에 실린 모델" 이 결정하므로
    #   provider.create 로 들어가는 req.model_version 을 스파이로 캡처해 검증한다.
    from app.orchestrator.mock import MockProvider

    seen: dict = {}
    orig_create = MockProvider.create

    async def spy(self, req):
        seen["model_version"] = req.model_version
        return await orig_create(self, req)

    monkeypatch.setattr(MockProvider, "create", spy)

    body = {"prompt": "lite tier cost check", "lyric": "la la la", "title": "lite"}
    r = client.post("/api/songs/generate", json=body, headers=h)
    assert r.status_code in (200, 201), r.text
    song_id = r.json()["song_id"]

    assert seen.get("model_version") == "mureka-7.6", (
        f"provider 에 전달된 모델이 티어 바인딩과 다르다: {seen.get('model_version')!r}"
    )

    d = client.get(f"/api/songs/{song_id}", headers=h).json()
    # 유료 티어이므로 완곡 재생·다운로드 권한이 있어야 한다
    assert d["can_play_full"] is True, "lite 는 유료 — 완곡 재생이 막히면 안 된다"
    assert d["can_download"] is True

    # 모델/원가는 공개 SongOut 에서 의도적으로 제외(엔진·비용 노출 방지) → DB 로 확인
    db = SessionLocal()
    try:
        from app.models import Song

        s = db.get(Song, song_id)
        # 원가가 실제로 낮아졌는지(¥0.33 → ¥0.22) — 요청 시점에 계산되어 DB 에 남는다
        assert float(s.cost_cny) == pytest.approx(0.22, abs=1e-6), f"원가 미절감: {s.cost_cny}"
        assert float(s.price_cny) == pytest.approx(0.26, abs=1e-6), f"lite 가격: {s.price_cny}"
        assert float(s.margin_cny) > 0, f"저가 티어 마진이 음수면 절감 실패: {s.margin_cny}"
    finally:
        db.close()


# ---------- 5. 사용자가 명시한 모델이 티어 바인딩보다 우선 ----------
def test_explicit_model_overrides_tier_binding(client, monkeypatch):
    _seed_all()
    h = _register(client, "lite-override@test.com")
    r = client.post("/api/auth/subscribe", json={"tier_key": "lite"}, headers=h)
    assert r.status_code == 200, r.text

    from app.orchestrator.mock import MockProvider

    seen: dict = {}
    orig_create = MockProvider.create

    async def spy(self, req):
        seen["model_version"] = req.model_version
        return await orig_create(self, req)

    monkeypatch.setattr(MockProvider, "create", spy)

    body = {"prompt": "explicit model", "lyric": "la", "title": "ov", "model_version": "mureka-9"}
    r = client.post("/api/songs/generate", json=body, headers=h)
    assert r.status_code in (200, 201), r.text
    song_id = r.json()["song_id"]
    assert seen.get("model_version") == "mureka-9", "사용자 명시 모델이 최우선이어야 한다"

    db = SessionLocal()
    try:
        from app.models import Song

        s = db.get(Song, song_id)
        # 명시 모델 단가가 적용돼야 한다(V9 = ¥0.33 ≠ lite 바인딩 ¥0.22)
        assert float(s.cost_cny) == pytest.approx(0.33, abs=1e-6), (
            f"명시 모델 원가 미적용: {s.cost_cny}"
        )
    finally:
        db.close()


# ---------- 6. [핵심] 요청 바디에 모델이 정말 실려 나가는가 ----------
def test_mureka_payload_uses_request_model(monkeypatch):
    """mureka.create() 가 req.model_version 을 무시하면 lite 원가절감은 no-op 이 된다.

    → _post 를 가로채 **실제 전송 payload** 를 캡처해 model 값을 직접 확인한다.
    (pytest-asyncio 미설치 환경이라 asyncio.run 으로 동기 래핑)
    """
    from app.orchestrator.base import GenerateRequest
    from app.orchestrator.mureka import MurekaProvider

    captured: dict = {}

    async def fake_post(self, path: str, json: dict) -> dict:
        captured["path"] = path
        captured["payload"] = json
        return {"id": "mok-1", "model": json.get("model"), "status": "preparing"}

    monkeypatch.setattr(MurekaProvider, "_post", fake_post)

    async def _run(req):
        return await MurekaProvider().create(req)

    res = asyncio.run(
        _run(GenerateRequest(lyric="la la", prompt="style", model_version="mureka-7.6"))
    )
    assert captured["path"] == "/song/generate"
    assert captured["payload"]["model"] == "mureka-7.6", (
        f"요청 모델이 payload 에 반영되지 않았다(no-op 회귀): {captured['payload']!r}"
    )
    assert res.model_version == "mureka-7.6"

    # 모델 미지정 시엔 엔진 기본 모델로 폴백
    captured.clear()
    p = MurekaProvider()
    res2 = asyncio.run(_run(GenerateRequest(lyric="la la", prompt="style")))
    assert captured["payload"]["model"] == p.model
    assert res2.model_version == p.model
