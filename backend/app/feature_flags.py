"""特性开关（灰度发布）判定与读写：GAP-023。

设计结论（理由随下）：

1. **判定必须是纯函数**：`is_enabled(flag, user_id, tier_key)` 只看行值，不查库、
   不看时间、不用随机数。灰度的可复现性全靠这一点 —— 同一用户同一开关，
   任何时候判定结果都一致。
2. **失败即关闭（fail-closed）**：开关缺失 / 值为空 / DB 读不到，都返回 False。
   灰度机制出错时应当"少开一个功能"，而不是"把未验证的功能全量放开"。
3. **与套餐能力（entitlements）正交**：`min_tier` 只做粗粒度门槛（免费用户先不给），
   真正的功能权限仍在 `entitlements.resolve_limits`。开关不是权限系统的替代品，
   它决定"这个（已允许的）功能现在对谁可见"。

优先级（从高到低）：
  1. `enabled=False` → **一律关闭**（总闸）。
     这一条必须在名单之前：`enabled` 是"这个功能现在还要不要发"，而名单只回答
     "发给谁"。让 allow 名单压过总闸会出现"开关关了但几个账号还能用" ——
     关不掉的功能等于没有开关。
  2. deny 名单 → 关闭。
     灰度出事故时运维的第一动作是"把几个出问题的账号关掉"；若 allow 或 rollout
     能压过 deny，这个动作就会静默失效。
  3. allow 名单 → 开启（不受 min_tier/rollout 限制：手挑的账号就是要立刻能用）。
  4. 否则 平台级 enabled × min_tier × rollout 三层同时满足才开启。
"""

import hashlib
from typing import Iterable

from sqlalchemy.orm import Session

from app.entitlements import TIER_LIMITS, VALID_TIERS
from app.models import FeatureFlag

# 档位排序用于 min_tier 比较（free 最低）。顺序取自 entitlements.TIER_LIMITS 的声明序，
# 不在这里另写一份 —— 两处各写一套必然漂移。
_TIER_RANK = {tier: i for i, tier in enumerate(TIER_LIMITS.keys())}


def _parse_ids(raw: str | None) -> set[int]:
    """把 "12,34 , 56" 解析成 {12,34,56}。

    容忍空格/空段/非法段（跳过而不是抛异常）：这串数字由人手输进 admin 输入框，
    一个多余的逗号不该让整个开关判定 500。非法段的代价是"该 id 未列入名单"，
    比"功能整体不可用"轻。
    """
    out: set[int] = set()
    for part in (raw or "").replace(" ", "").split(","):
        if not part:
            continue
        try:
            out.add(int(part))
        except ValueError:
            continue
    return out


def _in_rollout(key: str, user_id: int | None, rollout: int) -> bool:
    """确定性灰度：hash(key:user_id) % 100 < rollout。

    ⚠ 不能用 `user_id % 100`：用户 id 是自增的，取模会把"前 N% 的用户"全部命中
      —— 早期用户（往往是种子/内部账号）先被灰度，样本完全不代表生产分布。
      md5 散列打散了这个相关性，同时保持确定性（同一 key+id 恒等）。

    ⚠ 也不能用 random：同一用户每次请求可能落在不同分支，用户会看到功能时有时无。
    """
    if rollout <= 0:
        return False
    if rollout >= 100:
        return True
    if user_id is None:
        # 未登录/无身份 → 只吃 100% 全开这一种（上面已处理），否则一律不命中。
        # 理由：百分比灰度必须落在具体的人身上才能复核"谁被灰度了"。
        return False
    digest = hashlib.md5(f"{key}:{int(user_id)}".encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % 100 < int(rollout)


def _meets_tier(min_tier: str, tier_key: str | None) -> bool:
    """是否达到开关要求的最低档位。空 min_tier = 不限。"""
    need = (min_tier or "").strip().lower()
    if not need:
        return True
    if need not in _TIER_RANK:
        # 配置了未知档位 → 不满足（fail-closed）。宁可暂时不给用，
        # 也不要因为"拼错一个字母"就把功能发给所有人。
        return False
    have = (tier_key or "free").strip().lower()
    if have not in VALID_TIERS:
        have = "free"
    return _TIER_RANK[have] >= _TIER_RANK[need]


def is_enabled(flag: FeatureFlag | None, user_id: int | None = None, tier_key: str | None = None) -> bool:
    """判定某开关对某人是否生效（纯函数，见模块头）。"""
    if flag is None:
        return False
    # ① 总闸：关掉的开关对任何人都不生效（含 allow 名单）。
    if not bool(flag.enabled):
        return False
    uid = int(user_id) if user_id is not None else None
    # ② deny 压过 allow 与 rollout（灰度排障的第一动作必须立刻生效）。
    if uid is not None and uid in _parse_ids(flag.deny_user_ids):
        return False
    # ③ allow 名单：手挑的账号直接开，不再受档位/百分比限制。
    if uid is not None and uid in _parse_ids(flag.allow_user_ids):
        return True
    if not _meets_tier(flag.min_tier or "", tier_key):
        return False
    return _in_rollout(flag.key, uid, int(flag.rollout or 0))


def get_flag(db: Session, key: str) -> FeatureFlag | None:
    return db.query(FeatureFlag).filter(FeatureFlag.key == key).first()


def resolve_for_user(db: Session, user_id: int | None, tier_key: str | None = None) -> dict[str, bool]:
    """当前用户可见的全部开关（key → bool）。前端一次性拉取，避免每个功能一次请求。

    只返回**已生效**的 key（False 的不下发）：前端读开关的写法因此统一是
    `flags[key] === true`，不必区分"不存在"与"关闭" —— 这两种在灰度语义里
    是同一件事（功能不可用）。
    """
    rows: Iterable[FeatureFlag] = db.query(FeatureFlag).all()
    return {r.key: True for r in rows if is_enabled(r, user_id=user_id, tier_key=tier_key)}


def normalize_key(raw: str) -> str:
    """开关 key 规范化为小写蛇形（只允许 [a-z0-9_]）。

    拒绝点号/横线/大写：key 是前端代码里的字面量，写法不统一会出现
    `song.stem` 与 `song_stem` 两个"同一功能"的开关，灰度结果无从解释。
    """
    key = (raw or "").strip().lower()
    if not key:
        raise ValueError("特性开关 key 不能为空")
    if len(key) > 64:
        raise ValueError("特性开关 key 过长（上限 64）")
    allowed = set("abcdefghijklmnopqrstuvwxyz0123456789_")
    if not set(key) <= allowed:
        raise ValueError("特性开关 key 只允许小写字母、数字与下划线")
    return key
