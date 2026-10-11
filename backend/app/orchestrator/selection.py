"""引擎路由与计费解析：从 DB 后台配置读取（多引擎架构路由层，支持后台实时控制）。

设计要点：
- 管理后台（/api/admin/engines、/api/admin/routing-rules、/api/admin/pricing-tiers）增删改
  engine_configs / routing_rules / pricing_tiers，本模块运行时读取并带 TTL 缓存，
  不重启、不改代码即生效（后台控制）。
- 引擎名空间与 MusicProvider 名空间一致：
  suno / mock / musicgen / ace_step_private / tencent_mps / volcano / mureka。
- 任何读取在无配置时都安全回退到 settings.music_provider，不会导致生成中断。
"""

from datetime import datetime, timedelta
import threading

from sqlalchemy.orm import Session

from app.config import settings
from app.models import EngineConfig, RoutingRule, PricingTier

_lock = threading.Lock()
_cache: dict = {"ts": None, "routing": {}, "default_engine": None}
_CACHE_TTL = 30  # 秒；后台改配置后最长 30s 生效（也可调用 invalidate_config_cache 立即生效）

# ---- 默认种子（首次启动注入；若已被后台改过则保留后台值，不覆盖） ----
# 成本数字为「官方样例/公开价估算」占位，真实单价以你接入时各家最新报价为准（拒绝幻觉：不臆造报价）。
DEFAULT_ENGINES = [
    {
        "provider": "suno",
        "display_name": "Suno (open.suno.cn)",
        "enabled": True,
        "is_default": False,
        "api_base": settings.suno_api_base,
        "api_key": settings.suno_api_key,
        "cost_per_song_cny": 0,
        "priority": 100,
        "notes": "商业 API（国际版网关）；作 failover 备份（宗教词非确定性误拦、中文较弱）",
    },
    {
        "provider": "mock",
        "display_name": "Mock 本地开发",
        "enabled": True,
        "is_default": False,
        "api_base": "",
        "api_key": "",
        "cost_per_song_cny": 0,
        "priority": 0,
        "notes": "CI/本地，不调用外部",
    },
    {
        "provider": "musicgen",
        "display_name": "MusicGen 本地开源",
        "enabled": False,
        "is_default": False,
        "api_base": "",
        "api_key": "",
        "cost_per_song_cny": 0,
        "priority": 50,
        "notes": "CC-BY-NC 不可商用；需 torch+audiocraft+GPU，未启用",
    },
    {
        "provider": "ace_step_private",
        "display_name": "ACE-Step 私有引擎(合规通道)",
        "enabled": False,
        "is_default": False,
        "api_base": "",
        "api_key": "",
        "cost_per_song_cny": 0.003,
        "priority": 200,
        # ⚠ 许可以源码实测为准（2026-09-25）：CNB chguanw/ace-step-1.5 根 LICENSE 实读为
        # **MIT License, © 2026 ACEStudio**，非此处早前所记的 Apache-2.0，故已订正。
        # 注意「代码 MIT ≠ 模型许可」：权重按各自仓库（Modelscope/HF）条款，部署前须自查。
        "notes": "MIT 可商用（实测 LICENSE）；本地 GPU 零审核误杀；启用后 christian_worship 走此（需 GPU/部署）；provider 见 orchestrator/ace_step.py",
    },
    {
        "provider": "tencent_mps",
        "display_name": "腾讯云 MPS",
        "enabled": False,
        "is_default": False,
        "api_base": "",
        "api_key": "",
        "cost_per_song_cny": 0.22,
        "priority": 150,
        "notes": "云 API；需 AK；0.22 元/首为官方样例估算",
    },
    {
        "provider": "volcano",
        "display_name": "火山引擎",
        "enabled": False,
        "is_default": False,
        "api_base": "",
        "api_key": "",
        "cost_per_song_cny": 1.5,
        "priority": 150,
        "notes": "云 API；需 AK；1.5 元/首（火山方舟资源包实测价，2026-09-03 校准，此前 0.36 低估 4.2×）",
    },
    {
        "provider": "mureka",
        "display_name": "Mureka (昆仑万维 Skywork AI)",
        "enabled": True,
        "is_default": True,
        "api_base": settings.mureka_api_base,
        "api_key": settings.mureka_api_key,
        "cost_per_song_cny": 0.33,
        "priority": 180,
        "notes": "商业 API；主引擎（中文+英文主线，non-English 盲测反超 Suno，gospel 原生风格，官方 API 稳定）；V9 $0.045/首≈¥0.33；Suno 作 failover",
    },
]
DEFAULT_ROUTING = [
    {"category": "general", "provider": "mureka", "priority": 200, "enabled": True},
    {"category": "christian_worship", "provider": "mureka", "priority": 200, "enabled": True},
    {"category": "instrumental", "provider": "mureka", "priority": 200, "enabled": True},
]
# ---- 모델별 실측 단가 (원가절감의 근거) ----
# 출처: platform.mureka.ai/pricing (2026-09-04 실측, Song Generation / Lyrics to song)
#   · V9 / V8 / O2 = $0.045/song
#   · V7.6         = $0.03/song   ← 33% 저렴 → 저가 티어(lite) 바인딩용
#   · 9.5          = $0.15/song
# 환율: $1 ≈ ¥7.3333 (기존 "V9 $0.045 ≈ ¥0.33" 주석과 동일 기준)
# ⚠ 여기 값은 **추정이 아닌 실측**이다. Mureka 가 단가를 바꾸면 반드시 이 표부터 갱신할 것
#   (이 표가 틀리면 "원가절감" 이 장부상으로만 절감되고 실제로는 적자가 난다).
MODEL_COST_CNY: dict[tuple[str, str], float] = {
    ("mureka", "mureka-7.6"): 0.22,  # $0.03
    ("mureka", "mureka-o1"): 0.33,  # $0.045
    ("mureka", "mureka-o2"): 0.33,  # $0.045
    ("mureka", "mureka-8"): 0.33,  # $0.045
    ("mureka", "mureka-9"): 0.33,  # $0.045
    ("mureka", "mureka-9.5"): 1.10,  # $0.15
}

# ⚠ price_usd / price_krw 为按固定汇率(FX, config.fx_rate_*)由 price_cny 估算的占位标价(MVP 量级, 非实时行情).
#   真实上线前应替换为各渠道实际定价; 缺失时结账仍会按 FX 自动换算, 故这里仅为展示/显式优先价.
DEFAULT_TIERS = [
    {
        "tier_key": "free",
        "tier_name": "自由版",
        "engine_provider": None,
        "price_cny": 0,
        "price_usd": 0,
        "price_krw": 0,
        "credits_per_song": 1,
        "model_version": "",
        "description": "mock/限量，免费",
        "enabled": True,
    },
    # 저가 티어: mureka-7.6 바인딩으로 **실제 원가 절감**(¥0.33→¥0.22, -33%)
    {
        "tier_key": "lite",
        "tier_name": "轻量版",
        "engine_provider": None,
        "price_cny": 0.26,
        "price_usd": 0.04,
        "price_krw": 49,
        "credits_per_song": 1,
        "model_version": "mureka-7.6",
        "description": "低档位：绑定 mureka-7.6（$0.03/首，较 V9 省 33%），音质/额度低于标准版",
        "enabled": True,
    },
    {
        "tier_key": "standard",
        "tier_name": "标准版",
        "engine_provider": None,
        "price_cny": 0.36,
        "price_usd": 0.05,
        "price_krw": 68,
        "credits_per_song": 1,
        "model_version": "",
        "description": "便宜云引擎按首计费（占位价，以接入报价为准）",
        "enabled": True,
    },
    {
        "tier_key": "sacred",
        "tier_name": "专业版",
        "engine_provider": "mureka",
        "price_cny": 2.0,
        "price_usd": 0.28,
        "price_krw": 380,
        "credits_per_song": 1,
        "model_version": "",
        "description": "合规通道差异化高价（持卡用户）；实际由 christian_worship→mureka 路由",
        "enabled": True,
    },
    {
        "tier_key": "enterprise",
        "tier_name": "企业API",
        "engine_provider": None,
        "price_cny": 0,
        "price_usd": 0,
        "price_krw": 0,
        "credits_per_song": 1,
        "model_version": "",
        "description": "引擎名显式可选，按量议价",
        "enabled": True,
    },
]


def seed_default_config(db: Session) -> None:
    """幂等注入默认配置：仅当对应表为空/该行不存在时插入，保留后台已改值。"""
    for e in DEFAULT_ENGINES:
        if not db.get(EngineConfig, e["provider"]):
            db.add(EngineConfig(**e))
    if db.query(RoutingRule).count() == 0:
        for r in DEFAULT_ROUTING:
            db.add(RoutingRule(**r))
    # ⚠ tier 는 "행 존재성" 기준으로 **tier_key 별** 보충한다.
    #   count()==0 검사로 전체 시드를 막으면, 이미 운영 중인 DB 에 신규 티어(lite) 가
    #   영구히 주입되지 않는다(시드 멱등 함정 — 신규 티어는 "추가" 지 "초기화" 가 아니다).
    existing_tiers = {t.tier_key for t in db.query(PricingTier.tier_key).all()}
    for t in DEFAULT_TIERS:
        if t["tier_key"] not in existing_tiers:
            db.add(PricingTier(**t))
    db.commit()
    invalidate_config_cache()


# ---- 缓存 ----
def _reload(db: Session) -> None:
    routing: dict[str, str] = {}
    for r in (
        db.query(RoutingRule)
        .filter(RoutingRule.enabled == True)  # noqa: E712
        .order_by(RoutingRule.priority.desc())
        .all()
    ):
        routing.setdefault(r.category, r.provider)  # 每分类取最高优先级 enabled 规则
    engines_enabled = {
        e.provider: bool(e.enabled)
        for e in db.query(EngineConfig.provider, EngineConfig.enabled).all()
    }
    default_engine = None
    de = (
        db.query(EngineConfig)
        .filter(EngineConfig.is_default == True, EngineConfig.enabled == True)  # noqa: E712
        .first()
    )
    if de:
        default_engine = de.provider
    else:
        fe = (
            db.query(EngineConfig)
            .filter(EngineConfig.enabled == True)  # noqa: E712
            .order_by(EngineConfig.priority.desc())
            .first()
        )
        default_engine = fe.provider if fe else None
    _cache["routing"] = routing
    _cache["engines_enabled"] = engines_enabled
    _cache["default_engine"] = default_engine
    _cache["ts"] = datetime.utcnow()


def _ensure_cache(db: Session) -> dict:
    with _lock:
        if _cache["ts"] is None or (datetime.utcnow() - _cache["ts"]).total_seconds() > _CACHE_TTL:
            _reload(db)
    return _cache


def invalidate_config_cache() -> None:
    """后台改配置后调用，令下次读取立即重建缓存。"""
    with _lock:
        _cache["ts"] = None


# ---- 路由与计费解析 ----
def resolve_engine(db: Session, category: str | None) -> str:
    """返回应选用引擎名（与 provider 名空间一致）。无配置时回退 settings.music_provider。

    若路由命中的引擎被后台禁用，则回退默认启用引擎（避免把流量导向已关停/未接入引擎）。
    """
    cache = _ensure_cache(db)
    enabled = cache.get("engines_enabled", {})
    key = (category or "general").lower()
    target = cache["routing"].get(key)
    if target and enabled.get(target):
        return target
    if cache.get("default_engine") and enabled.get(cache["default_engine"]):
        return cache["default_engine"]
    return settings.music_provider or "suno"


def resolve_engine_chain(db: Session, category: str | None) -> list[str]:
    """返回有序引擎链（Failover, P2）：首要路由引擎在前，其余已启用引擎按优先级降序在后（去重）。

    生成主链路 client.generate 依次尝试链中引擎：一引擎拦截/失败即换下一引擎。
    顺序：① 路由命中且启用的引擎（最高优先级 enabled 规则）② default 启用引擎
    ③ 其余已启用引擎按 priority 降序。全部未启用则回退 settings.music_provider。
    """
    cache = _ensure_cache(db)
    enabled = cache.get("engines_enabled", {})
    key = (category or "general").lower()
    ordered: list[str] = []

    # ① 路由首要引擎
    primary = cache["routing"].get(key)
    if primary and enabled.get(primary):
        ordered.append(primary)
    # ② default 启用引擎
    default = cache.get("default_engine")
    if default and enabled.get(default) and default not in ordered:
        ordered.append(default)
    # ③ 其余已启用引擎（按 priority 降序，去重）
    for row in (
        db.query(EngineConfig.provider)
        .filter(EngineConfig.enabled == True)  # noqa: E712
        .order_by(EngineConfig.priority.desc())
        .all()
    ):
        p = row.provider
        if p not in ordered:
            ordered.append(p)

    if not ordered:
        fallback = settings.music_provider or "suno"
        ordered.append(fallback)
    return ordered


def allowed_model_versions(engine_name: str) -> set[str]:
    """해당 엔진에서 **원가가 실측된** model_version 집합(소문자).

    2026-09-13 감사: model_version 은 클라이언트가 요청 본문으로 그대로 지정할 수 있었고
    (routers/songs.py:274/805) 어떤 화이트리스트도 없었다. 원가는 이 값으로 결정되는데
    (`resolve_cost_cny`) 가격은 티어로만 결정되므로, free 계정이 가장 비싼 모델을 골라
    일일 쿼터 내에서 원가를最大化 할 수 있었다(단위 economics 역전, CWE-840).

    빈 집합이면 "이 엔진은 모델별 원가 표가 없다" 는 뜻 → 호출부는 검증을 건너뛴다.
    """
    engine_name = (engine_name or "").strip().lower()
    return {mv for (eng, mv) in MODEL_COST_CNY if eng == engine_name}


def resolve_cost_cny(
    db: Session, category: str | None, n: int = 1, model_version: str | None = None
) -> float:
    """返回本次生成成本（元）= 引擎(模型)单价 × 实际生成首数 n。

    语义修正（2026-09-03）：成本只来自供应商计费，与套餐价无关；
    旧实现会在传入 tier_key 时返回套餐「价格」冒充成本，导致毛利被错算。
    n 默认 1；Mureka 按首计费（MUREKA_N 可 1~3），n>1 时成本线性放大。

    model_version（2026-09-04）：모델별 단가가 다르다(V9 $0.045 vs V7.6 $0.03).
      모델 지정이 있고 `MODEL_COST_CNY` 에 실측값이 있으면 그 값을 쓴다.
      ⚠ 이게 없으면 저가 티어가 "가격만 내리고 원가는 그대로" 가 되어
        **절감이 장부에 반영되지 않고 마진이 음수로 뒤집힌다**(lite 도입 이유 소멸).
      실측값이 없는 모델 → 엔진 기본 단가로 폴백(보수적).
    """
    engine_name = resolve_engine(db, category)
    if model_version:
        per_song = MODEL_COST_CNY.get((engine_name, str(model_version).strip().lower()))
        if per_song is not None:
            return round(per_song * max(1, int(n or 1)), 4)
    ec = db.get(EngineConfig, engine_name)
    unit = float(ec.cost_per_song_cny or 0) if ec is not None else 0.0
    return round(unit * max(1, int(n or 1)), 4)


def resolve_tier_model_version(db: Session, tier_key: str | None) -> str:
    """档位绑定的生成模型（如 lite→mureka-7.6）。무설정/미해당 → 빈 문자열(엔진 기본 모델).

    사용자가 요청에 model_version 을 명시한 경우가 **우선**한다(호출부에서 처리).
    """
    if not tier_key:
        return ""
    row = (
        db.query(PricingTier.model_version)
        .filter(PricingTier.tier_key == tier_key, PricingTier.enabled == True)  # noqa: E712
        .first()
    )
    return (row[0] or "").strip() if row else ""


def resolve_price_cny(db: Session, tier_key: str | None, category: str | None) -> float:
    """返回本次生成对用户计费（元，名义收入）。

    - 免费档(free / 未订阅) → 不向用户收款(price=0)，成本记为获客费用(loss-leader)，
      毛利 = 0 - cost = 负（贴合 Req6 “免费=试用上限、不收款” 原意）。
    - 付费套餐(price_cny>0) → 取套餐标准价。
    - 付费套餐但未配置价格 → 保守回退到引擎估算成本(break-even)。
    """
    # 免费档(free / None) → loss-leader: 不向用户收款
    if tier_key is None or str(tier_key).strip().lower() == "free":
        return 0.0
    tier = (
        db.query(PricingTier)
        .filter(PricingTier.tier_key == tier_key, PricingTier.enabled == True)  # noqa: E712
        .first()
    )
    if tier is not None and tier.price_cny is not None and float(tier.price_cny) > 0:
        return float(tier.price_cny)
    # 付费套餐인데 가격 미설정 → 보수적 break-even
    return resolve_cost_cny(db, category, n=1)


def compute_margin_cny(price_cny: float, cost_cny: float) -> float:
    """毛利（元）= 计费 - 成本。可为负（亏损）。"""
    return round(float(price_cny or 0) - float(cost_cny or 0), 4)


# ---- 后台读写辅助（供 admin 路由调用） ----
def list_engines(db: Session):
    return db.query(EngineConfig).order_by(EngineConfig.priority.desc()).all()


def get_engine(db: Session, provider: str):
    return db.get(EngineConfig, provider)


def list_routing(db: Session):
    return db.query(RoutingRule).order_by(RoutingRule.priority.desc()).all()


def list_tiers(db: Session):
    return db.query(PricingTier).order_by(PricingTier.tier_key).all()


def get_tier(db: Session, tier_key: str):
    return db.query(PricingTier).filter(PricingTier.tier_key == tier_key).first()
