"""每日生成配额（MVP）。

按「当日该用户创建的 Song 数」统计免费额度；超限返回 402 引导订阅。
长远：P5 支付/计费会引入独立 usage ledger（区分资源类型、支持套餐），
此处零新模型、零迁移，是 MVP 阶段的最小可用实现。
"""

from datetime import datetime, timedelta, timezone
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Song
from app.ratelimit import rate_limit_generate
from app.entitlements import resolve_limits

# [D38] 일일 쿼터 "하루" 경계를 DB 함수(func.date/now) 가 아닌 애플리케이션에서 계산.
# KST 비즈니스 데이 로 통일 — FLOW 도 동일 기준(quota_service.py:34) 이므로 공존 후 양측 일치.
# naive UTC 반환: SQLite(stored naive UTC) 와 Postgres(timestamptz=UTC) 양쪽에서
# naive UTC 대조가 성립하도록 tzinfo 제거. DB 세션 TZ=UTC 전제(docker-compose TZ=UTC).
_KST = timezone(timedelta(hours=9))


def _kst_day_start_naive_utc() -> datetime:
    now_kst = datetime.now(_KST)
    day_start_kst = now_kst.replace(hour=0, minute=0, second=0, microsecond=0)
    return day_start_kst.astimezone(timezone.utc).replace(tzinfo=None)


def daily_generated_count(db: Session, user_id: int) -> int:
    """该用户今日(KST 业务日)已创建的歌曲数（generate/extend/cover/remix 均计入）。"""
    day_start = _kst_day_start_naive_utc()
    return db.query(Song).filter(Song.user_id == user_id, Song.created_at >= day_start).count()


def assert_quota(user, db: Session) -> None:
    if daily_generated_count(db, user.id) >= settings.per_user_daily_quota:
        raise HTTPException(
            status_code=402,
            detail="今日免费生成额度已用完，请升级订阅以继续",
        )


def enforce_generate_limits(user, db: Session) -> None:
    """生成类端点的统一前置校验：突发限流(429) + 티어별 일일쿼터(402).

    쿼터는 flat `per_user_daily_quota` 가 아니라 사용자 티어(`user.tier_key`) 에서
    결정된다(GAP-004). free=기본값, standard/sacred/enterprise=상위 한도.
    """
    rate_limit_generate(str(user.id))
    limits = resolve_limits(db, user.tier_key)
    if daily_generated_count(db, user.id) >= limits["daily_quota"]:
        raise HTTPException(
            status_code=402,
            detail="今日生成额度已用完，请升级订阅以继续",
        )
