"""D38 / D39 — 쿼터 KST 경계 통일 + synced_at UTC 정규화 회귀.

D38: 일일 쿼터 "하루" 가 DB 엔진/세션 TZ 에 좌우되지 않고 KST 비즈니스 데이 로 통일.
D39: synced_at 응답 형식이 SQLite(naive) · Postgres(tz-aware) 에서 달라지지 않고
     항상 UTC 고정 "...Z" 로 나온다.
"""

from datetime import timedelta

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import Song, SyncRecord, User
from app.quota import _kst_day_start_naive_utc, daily_generated_count
from app.routers.auth import create_token


def test_kst_day_boundary_counts_only_today():
    """[D38] KST 자정 기준: 오늘 KST 곡은 세고, 어제 KST 곡은 세지 않는다."""
    uid = 98001
    day_start = _kst_day_start_naive_utc()
    db = SessionLocal()
    try:
        s_today = Song(user_id=uid, title="today", created_at=day_start + timedelta(hours=3))
        s_yest = Song(user_id=uid, title="yest", created_at=day_start - timedelta(hours=1))
        db.add_all([s_today, s_yest])
        db.commit()
        assert daily_generated_count(db, uid) == 1
    finally:
        db.close()


def test_synced_at_normalized_to_utc_z(client: TestClient):
    """[D39] list_flow 응답의 synced_at 이 항상 UTC 고정 "...Z" 형식."""
    db = SessionLocal()
    u = User(email="d39@test.com", hashed_password="x")
    db.add(u)
    db.commit()
    db.refresh(u)
    uid = u.id  # 세션 close 전에 캡처(expire_on_commit 로 인해 close 후 접근 불가)
    # list_flow 는 song_id 로 Song 을 조회하므로 유효한 Song 을 함께 생성해야 한다.
    song = Song(
        user_id=uid,
        title="s",
        status="completed",
        moderation_status="approved",
        audio_url="http://example.com/y.mp3",
    )
    db.add(song)
    db.commit()
    db.refresh(song)
    rec = SyncRecord(user_id=uid, song_id=song.id, status="synced")
    db.add(rec)
    db.commit()
    db.refresh(rec)
    db.close()

    tok = create_token(uid, scope="sync:flow")
    r = client.get("/api/v1/sync/flow", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert len(data) == 1
    sat = data[0]["synced_at"]
    assert sat.endswith("Z"), f"synced_at 형식이 UTC Z 가 아님: {sat}"
    assert "+" not in sat and "-" not in sat[11:], f"tz-offset 가 노출됨: {sat}"
