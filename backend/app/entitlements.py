"""GAP-004(로직층): 티어 → 권한 매핑 + 생성 게이팅.

실제 PG 결제는 미연동(추후 구현). 결제 완료 시 `user.tier_key` 가 설정되도록
`/api/auth/subscribe`(mock) 엔드포인트를 둔다. 실 PG 연동 시 이 엔드포인트는
웹훅/결제완료 검증 핸들러로 교체한다.

설계 원칙:
- 티어별 기본 권한(TIER_LIMITS) 은 코드에 명시(설정 누락에도 동작).
- DB `PricingTier`(models.py:123) 행이 있으면 price_cny 를 실제값으로 덮어씀.
- 알 수 없는 티어는 free 로 폴백(fail-safe).
"""

from sqlalchemy.orm import Session

from app.config import settings
from app.models import PricingTier

# 티어별 기본 권한. daily_quota=일일 생성 한도, max_quality=최대 음질,
# features=허용 생성 기능(task_type), price_cny=명목 월 가격(실결제 미연동).
TIER_LIMITS: dict[str, dict] = {
    "free": {
        "daily_quota": settings.per_user_daily_quota,
        "max_quality": "standard",
        "features": ["generate"],
        "price_cny": 0.0,
    },
    # 저가 티어(2026-09-04): mureka-7.6 바인딩으로 원가 절감.
    # ⚠ TIER_LIMITS 에 없으면 is_subscribed('lite') 가 False → 결제한 유료 사용자가
    #   "미구독" 으로 취급되어 완곡 재생·다운로드가 막힌다(조용한 기능정지).
    #   신규 유료 티어 추가 시 **여기 + DEFAULT_TIERS 양쪽 모두** 추가할 것.
    "lite": {
        "daily_quota": 8,
        "max_quality": "standard",
        "features": ["generate"],
        "price_cny": 19.0,
    },
    "standard": {
        "daily_quota": 20,
        "max_quality": "high",
        "features": ["generate", "extend", "cover"],
        "price_cny": 39.0,
    },
    "sacred": {
        "daily_quota": 50,
        "max_quality": "studio",
        "features": ["generate", "extend", "cover", "remix"],
        "price_cny": 99.0,
    },
    "enterprise": {
        "daily_quota": 999,
        "max_quality": "studio",
        "features": ["generate", "extend", "cover", "remix", "stem"],
        "price_cny": 299.0,
    },
}

VALID_TIERS = list(TIER_LIMITS.keys())


def resolve_limits(db: Session | None, tier_key: str | None) -> dict:
    """티어 → 권한 dict. DB PricingTier 행이 있으면 price_cny 를 실제값으로 덮어씀.

    존재하지 않는/None 티어는 free 로 폴백(안전).
    """
    tier_key = tier_key or "free"
    if tier_key not in TIER_LIMITS:
        tier_key = "free"
    limits = dict(TIER_LIMITS[tier_key])
    # free 쿼터는 전역 기본값을 런타임에 반영(import 시점 캡처 방지 — 테스트/런타임 설정 변경 반영).
    if tier_key == "free":
        limits["daily_quota"] = settings.per_user_daily_quota
    if tier_key != "free" and db is not None:
        row = (
            db.query(PricingTier)
            .filter(PricingTier.tier_key == tier_key, PricingTier.enabled == True)
            .first()
        )
        if row is not None and row.price_cny is not None:
            limits["price_cny"] = float(row.price_cny)
    limits["tier_key"] = tier_key
    return limits


def has_feature(limits: dict, feature: str) -> bool:
    """티어 권한에 주어진 기능(task_type) 이 포함되는지."""
    return feature in limits.get("features", [])


# ---------- 프리미엄(Freemium) 시청·다운로드 게이팅 ----------
# 비즈니스 규칙(사용자 확정 2026-09-04):
#   · 생성은 **항상 완곡**(Mureka 는 duration 파라미터가 없음 — orchestrator/mureka.py 주석).
#   · 게이트는 **배포 단계**에만 존재: 미구독자는 앞 N초 프리뷰만 재생, 구독자는 완곡 재생 + 완곡 다운로드.
FREE_TIER = "free"


def is_subscribed(tier_key: str | None) -> bool:
    """구독 여부. 유효 티어 중 free 가 아니면 구독으로 본다.

    - 알 수 없는 티어는 구독 아님(fail-safe). `resolve_limits` 가 unknown→free 로
      폴백하는 것과 동일한 판정이라 두 함수가 어긋나지 않는다.
    - ⚠ 신규 유료 티어(예: lite) 를 추가할 때는 **반드시 TIER_LIMITS 에도 추가**할 것.
      누락 시 이 함수는 미구독으로 판정해 완곡이 열리지 않는다(조용한 기능 정지).
    """
    t = (tier_key or "").strip().lower()
    return t in VALID_TIERS and t != FREE_TIER


def preview_seconds() -> int:
    """미구독자 프리뷰 길이(초). 런타임 설정 변경 반영을 위해 호출 시점에 읽는다."""
    return int(settings.preview_seconds or 60)


def can_play_full(user) -> bool:
    """완곡 재생 권한 = 구독 여부(소유자도 예외 없음 — 프리미엄 업셀 구조)."""
    return is_subscribed(getattr(user, "tier_key", None))


def can_download(user) -> bool:
    """완곡 다운로드 권한 = 구독 여부."""
    return is_subscribed(getattr(user, "tier_key", None))
