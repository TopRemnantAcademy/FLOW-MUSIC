"""公开资料页（GAP-015 公开部分）：`GET /api/users/{id}` + `/songs`。

**本文件刻意只做「读」**：不建任何表、不加任何列、不引入任何迁移。
原因见 issue #83：本批迁移槽被 #90 占用，`Follow` / `Comment` 两张新表
（以及 GAP-047 的 `display_name`/`avatar_url`/`bio` 列）必须等迁移槽放开。

因此这里做到的与**做不到**的分界是硬的：

- ✅ 公开 profile：昵称占位（邮箱局部名，**不吐邮箱**）、公开作品列表、
  被点赞总数、注册月份（粒度到月，用于「成员自 YYYY-MM」）。
- ❌ 关注 / 粉丝 / 留言：**没有表就没有端点**。不返回 `follower_count: 0`
  这种"看起来实现了"的假数字；数据层草案见 PR 评论（`Design: GAP-015`）。

可见性复用 `plaza._public_filters()`（已公开 + 已完成 + 未驳回 + 未软删），
与他人 profile 的交集语义即「公开已过审作品」，与广场/搜索逐字一致 ——
不另立第二份口径（上一轮已经吃过"两处 _public_filters 各写一遍"的亏）。

隐私：本文件的响应模型只声明安全字段。`email` / `hashed_password` /
`verify_token` / `reset_token` / `mfa_secret` / 锁定与封禁列**物理上不进入响应构造**，
故不可能因某次改动随手漏出。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Song, SongLike, User
from app.routers.plaza import _public_filters, get_optional_user
from app.routers.songs import (
    _author_names,
    _interaction_stats,
    _to_out,
    schedule_cdn_backfill,
)
from app.schemas import SongOut

router = APIRouter(prefix="/api/users", tags=["users"])

#: 展示用昵称前缀保留长度 —— 与 `songs._author_names` 完全一致的脱敏规则。
#: 复用同一实现的理由：广场卡片与资料页若给出不同昵称，用户会以为是两个人。
_NAME_MASK_KEEP = 2


def _display_name(email: str | None) -> str:
    """邮箱局部名 → 展示名占位（**不是**真实昵称）。

    GAP-047 补上 `User.display_name` 后应改为 `display_name or 本函数`，
    在此之前这里就是唯一真源，且**不得**回退成完整邮箱/局部名全量。
    """
    local = (email or "").split("@")[0]
    if not local:
        return "匿名用户"
    return local[:_NAME_MASK_KEEP] + "***" if len(local) > _NAME_MASK_KEEP else local


def _member_since(user: User) -> str | None:
    """注册月份（`YYYY-MM`）。只到月：精确到日的时间戳是可跨站复用的准标识符。"""
    created = user.created_at
    if created is None:
        return None
    return f"{created.year:04d}-{created.month:02d}"


class PublicProfileOut(BaseModel):
    """公开资料页的全部字段面（**白名单**，不是「User 减掉几个字段」）。

    ⚠️ 新增字段前先问：陌生人（含未登录）真的需要它吗？
    邮箱、凭据、封禁/锁定状态一律不属于这里。

    ⚠️ 模型必须放在路由**之前**定义：若用 `from __future__ import annotations`
    （本仓库其他 router 有先例），注解会被字符串化，FastAPI 在注册路由时按模块作用域
    解析 `PublicProfileOut` —— 定义在路由之后就会失败。这里保持在前面，直白且免坑。
    """

    id: int
    display_name: str
    member_since: str | None = None
    public_song_count: int = 0
    likes_received: int = 0
    # 昵称是否只是「邮箱局部名占位」——前端据此提示「资料完善功能开发中」，
    # 而不是把占位名当成用户真名去展示。
    display_name_is_placeholder: bool = True
    is_me: bool = False


@router.get("/{user_id}", response_model=PublicProfileOut)
def public_profile(
    user_id: int,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_optional_user),
):
    """公开资料（**任何登录态均可读，包括匿名**）。

    与 plaza 一致：能公开看到的作品，其作者就应能被点进去看到 ——
    否则「广场能看到歌、点作者 404/401」。

    ⚠️ 只读、不统计浏览、不改任何状态：一个允许匿名打写的端点就是爬虫的玩具。
    """
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="用户不存在")

    # 作品数与被赞数都只按**公开可见**口径统计：把私有/未过审作品算进「共 N 首」
    # 等于通过数字泄露它的存在（用户看到 5 首却只列出 3 首，凭空多出一条信息面）。
    public_ids = [
        row[0]
        for row in db.query(Song.id).filter(Song.user_id == user_id, *_public_filters()).all()
    ]
    likes = 0
    if public_ids:
        likes = int(
            db.query(func.count(SongLike.id))
            .filter(SongLike.song_id.in_(public_ids))
            .scalar()
            or 0
        )
    return PublicProfileOut(
        id=target.id,
        display_name=_display_name(target.email),
        member_since=_member_since(target),
        public_song_count=len(public_ids),
        likes_received=likes,
        display_name_is_placeholder=True,  # GAP-047 迁移落地后由该列驱动
        is_me=bool(user is not None and user.id == target.id),
    )


@router.get("/{user_id}/songs", response_model=list[SongOut])
def public_profile_songs(
    user_id: int,
    limit: int = Query(30, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    user: User | None = Depends(get_optional_user),
):
    """某人的公开作品（他人视角 = 广场同款可见性；自己也只列公开的）。

    为什么**本人**在这里看不到自己的私有作品：本端点是"公开资料页"的数据源，
    本人补齐私有作品应走 `/api/songs`（我的作品）—— 一个端点两套可见性规则，
    迟早有人在改一处时忘了另一处。客户端要合并时它自己合并，服务端不猜。
    """
    if db.get(User, user_id) is None:
        raise HTTPException(status_code=404, detail="用户不存在")

    songs = (
        db.query(Song)
        .filter(Song.user_id == user_id, *_public_filters())
        .order_by(Song.created_at.desc(), Song.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    if not songs:
        return []

    stats = _interaction_stats(db, [s.id for s in songs], user.id if user else None)
    # 作者名复用全局实现：单人分支下 _author_names 仍返回同一脱敏格式，
    # 不在这里内联第二份 mask 逻辑（否则两处规则一旦漂移，页面自相矛盾）。
    names = _author_names(db, [s.user_id for s in songs])
    out: list[SongOut] = []
    for s in songs:
        schedule_cdn_backfill(s)  # GAP-019：资料页也是播放入口，按需回填 CDN
        out.append(_to_out(s, user, {**(stats.get(s.id) or {}), "author_name": names.get(s.user_id)}))
    return out
