"""D-A 잔여 경로 실증 — 한국 PII 금고 물리분리 라우팅 (샌드박스, 무환각).

목표: `KOREA_DATABASE_URL` 이 설정(≠ `DATABASE_URL`) 되면
  (1) `KOREA_SEPARATE == True` 이고 별도 한국 엔진이 바인딩되며,
  (2) `build_profile_token` 이 한국 금고(`SubscriberSensitive`) 에서만 민감속성을 로드하고,
  (3) `profile_from_token` 은 HK(메인) DB 왕복 0 으로 claim 을 복원한다.

검증 방식: 메인·한국 모두 **임시 sqlite 엔진** 으로 모듈 전역을 monkeypatch 격리 →
실제 dev DB 미변경, HK `Subscriber` 민감컬럼 물리삭제도 수행하지 않음(컬럼제거+백필은
D-A 후 ops 마이그레이션). 본 테스트는 "env 게이트가 실제로 데이터 흐름을 분리하는가" 만 증명(로컬 프록시 검증).
모듈 reload 를 쓰지 않아 다른 테스트와의 전역 상태 오염을 방지한다.
"""

import tempfile
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def test_korea_separate_data_flow_build_and_verify_via_korea_db(monkeypatch):
    import backend.app.config as cfgmod
    import backend.app.db as dbmod
    import backend.app.models.orm as ormmod

    tmp = Path(tempfile.mkdtemp(prefix="flow_korea_sep_"))
    main_db = tmp / "main.db"
    korea_db = tmp / "korea.db"
    main_engine = create_engine(
        f"sqlite:///{main_db}", future=True, connect_args={"check_same_thread": False}
    )
    korea_engine = create_engine(
        f"sqlite:///{korea_db}", future=True, connect_args={"check_same_thread": False}
    )
    main_sm = sessionmaker(
        bind=main_engine, autoflush=False, autocommit=False, expire_on_commit=False
    )
    korea_sm = sessionmaker(
        bind=korea_engine, autoflush=False, autocommit=False, expire_on_commit=False
    )

    # 모듈 전역 격리(다른 테스트 오염 차단 — reload 미사용)
    monkeypatch.setattr(dbmod, "engine", main_engine)
    monkeypatch.setattr(dbmod, "SessionLocal", main_sm)
    monkeypatch.setattr(dbmod, "korea_engine", korea_engine)
    monkeypatch.setattr(dbmod, "KoreaSessionLocal", korea_sm)
    monkeypatch.setattr(dbmod, "KOREA_SEPARATE", True)
    monkeypatch.setattr(cfgmod.settings, "database_url", f"sqlite:///{main_db}")
    monkeypatch.setattr(cfgmod.settings, "korea_database_url", f"sqlite:///{korea_db}")

    # 양측 스키마 생성(샌드박스 프록시)
    ormmod.Base.metadata.create_all(bind=main_engine)
    ormmod.Base.metadata.create_all(bind=korea_engine)

    sid = "korea-sep-001"
    # HK(메인) 에 민감속성 보유 Subscriber 시드
    with dbmod.get_session() as s:
        s.add(
            ormmod.Subscriber(
                subscriber_id=sid,
                salvation_status="saved",
                gender="m",
                age_group="30s",
                religion="christian",
            )
        )
        s.commit()

    # D-A 백필(운영은 마이그레이션이 수행): 한국 금고에 SubscriberSensitive 시드
    with dbmod.get_korea_session() as s:
        s.add(
            ormmod.SubscriberSensitive(
                subscriber_id=sid,
                salvation_status="saved",
                gender="m",
                age_group="30s",
                religion="christian",
                emotional_state="calm",
                preferred_tone="gentle",
            )
        )
        s.commit()

    # 라우팅 경계 검증: 금고 행은 한국 세션에, Subscriber 는 메인 세션에
    with dbmod.get_korea_session() as s:
        assert s.get(ormmod.SubscriberSensitive, sid) is not None
    with dbmod.get_hk_session() as s:
        assert s.get(ormmod.Subscriber, sid) is not None

    # build_profile_token: korea url 설정 → 한국 금고에서만 로드
    from backend.app.services import subscriber_service as svc

    tok = svc.build_profile_token(sid, ttl_sec=900)
    claims = svc.profile_from_token(tok)

    # 토큰 복원: 민감속성 서명됨 + HK DB 왕복 없이 복원
    assert claims["subscriber_id"] == sid
    assert claims["salvation_status"] == "saved"
    assert claims["gender"] == "m"
    assert claims["religion"] == "christian"
    assert claims["emotional_state"] == "calm"
    assert claims["preferred_tone"] == "gentle"

    # 토큰 속성 경계(D-C) 준수: 운영/준식별자 누락
    assert "tokens_daily" not in claims
    assert "subscription_tier" not in claims
    assert "journey_stage" not in claims
    assert "darakbang_chapter" not in claims
