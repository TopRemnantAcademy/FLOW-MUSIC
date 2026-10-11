"""广场（Plaza）：所有用户已发布作品的公共展示 + 排行榜 + 无文本互动。

设计约束（2026-09-10 产品决策）：
- **改名**：原「发现」→「广场」。
- **互动只做无文本**：点赞 / 收藏 / 星标评分。**不做留言与评论**
  （降低 UGC 审核成本与中国大陆内容合规风险）。
- 排行榜三种：
    · hot  —— 热度 = 赞×3 + 播放（近期作品优先）
    · new  —— 最新发布
    · top  —— 星标评分最高（评分人数 ≥1 才入榜，避免 1 人 5 星霸榜）
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

# [D37] 검증 전 fail-closed — 공개 기본값으로 위조된 토큰을 거부한다.
from app.routers.auth import _assert_jwt_secret_configured
from app.config import settings
from jose import jwt
from app.db import get_db
from app.models import Song, SongFavorite, SongLike, SongRating, User
from app.routers.auth import get_current_user
from app.routers.songs import _interaction_stats, _to_out
from app.schemas import SongOut

router = APIRouter(prefix="/api/plaza", tags=["plaza"])

# 热度权重：一个赞 ≈ 3 次播放
HOT_LIKE_WEIGHT = 3

# 广场**允许匿名浏览**（公开内容，无需登录即可看到大家的作品）；
# 带 token 时解析出用户，用于回显 liked_by_me / favorited_by_me / my_rating。
oauth2_optional = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)


def get_optional_user(
    db: Session = Depends(get_db),
    token: Optional[str] = Depends(oauth2_optional),
) -> Optional[User]:
    if not token:
        return None
    try:
        _assert_jwt_secret_configured()
        payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
        uid = int(payload.get("sub"))
        # 最小权限：scope 한정 토큰은 전권한 엔드포인트에서 무효
        if "scope" in payload:
            return None
    except Exception:
        return None  # 无效/过期 token → 视作匿名（不阻断公开浏览）
    return db.get(User, uid)


class RateReq(BaseModel):
    score: int


def _public_filters():
    """广场可见条件：已公开 + 生成完成 + 未驳回。"""
    filters = [
        Song.is_public.is_(True),
        Song.status == "completed",
        Song.moderation_status != "rejected",
        # 软删（回收站）的作品一律不可见：软删会同时下架，但这里是**第二道闸**——
        # 历史数据/并发写入下仍可能出现「deleted_at 非空却 is_public 为真」的行，
        # 广场作为对外展示面按最严格条件收口。
        Song.deleted_at.is_(None),
    ]
    # 机审开启时，显式 approved 才可见（与 explore 一致，防未审内容上架）
    if settings.enable_moderation:
        filters.append(Song.moderation_status == "approved")
    return filters


def _serialize(song: Song, db: Session, user: Optional[User]) -> SongOut:
    stats = _interaction_stats(db, [song.id], user.id if user else None)
    st = stats.get(song.id) or {}
    if song.user_id:
        from app.routers.songs import _author_names

        st["author_name"] = _author_names(db, [song.user_id]).get(song.user_id)
    return _to_out(song, user, st)


def _visible_song(song_id: int, db: Session) -> Song:
    """互动目标必须**当前可见于广场**（防止给未公开/未完成的作品刷赞）。"""
    song = db.get(Song, song_id)
    if not song:
        raise HTTPException(status_code=404, detail="作品不存在")
    if not song.is_public or song.status != "completed" or song.deleted_at is not None:
        raise HTTPException(status_code=404, detail="作品不存在或未发布")
    if settings.enable_moderation and song.moderation_status != "approved":
        raise HTTPException(status_code=404, detail="作品不存在或未发布")
    return song


@router.get("", response_model=list[SongOut])
def list_plaza(
    sort: str = Query("hot", pattern="^(hot|new|top)$"),
    limit: int = Query(20, ge=1, le=50),
    offset: int = Query(0, ge=0),
    q: Optional[str] = None,
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_optional_user),
):
    """广场列表：所有已发布作品（含他人），支持排行与关键字搜索。

    **匿名可读**（公开内容）；带 token 时额外回显当前用户的赞/收藏/评分状态。
    """
    like_sq = (
        db.query(SongLike.song_id, func.count(SongLike.id).label("n"))
        .group_by(SongLike.song_id)
        .subquery()
    )
    rate_sq = (
        db.query(
            SongRating.song_id,
            func.avg(SongRating.score).label("avg"),
            func.count(SongRating.id).label("n"),
        )
        .group_by(SongRating.song_id)
        .subquery()
    )

    query = (
        db.query(Song)
        .outerjoin(like_sq, like_sq.c.song_id == Song.id)
        .outerjoin(rate_sq, rate_sq.c.song_id == Song.id)
        .filter(*_public_filters())
    )

    if q and q.strip():
        kw = f"%{q.strip().lower()}%"
        query = query.filter(
            or_(
                func.lower(Song.title).like(kw),
                func.lower(Song.lyric).like(kw),
                func.lower(Song.prompt).like(kw),
            )
        )

    if sort == "new":
        query = query.order_by(Song.created_at.desc(), Song.id.desc())
    elif sort == "top":
        # 评分人数 ≥1 才入榜；同分比评分人数，再比播放
        query = query.order_by(
            func.coalesce(rate_sq.c.avg, 0).desc(),
            func.coalesce(rate_sq.c.n, 0).desc(),
            Song.play_count.desc(),
        )
    else:  # hot
        hot = func.coalesce(like_sq.c.n, 0) * HOT_LIKE_WEIGHT + Song.play_count
        query = query.order_by(hot.desc(), Song.created_at.desc())

    songs = query.offset(offset).limit(limit).all()
    if not songs:
        return []

    stats = _interaction_stats(db, [s.id for s in songs], user.id if user else None)
    names = _author_names_cached(db, [s.user_id for s in songs])
    out: list[SongOut] = []
    for s in songs:
        st = dict(stats.get(s.id) or {})
        st["author_name"] = names.get(s.user_id)
        out.append(_to_out(s, user, st))
    return out


def _author_names_cached(db: Session, user_ids: list[int]) -> dict[int, str]:
    from app.routers.songs import _author_names

    return _author_names(db, user_ids)


@router.post("/songs/{song_id}/like", response_model=SongOut)
def toggle_like(
    song_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """点赞 / 取消点赞（幂等切换）。广场不做留言，赞是无文本认同。"""
    song = _visible_song(song_id, db)
    existing = (
        db.query(SongLike).filter(SongLike.song_id == song_id, SongLike.user_id == user.id).first()
    )
    if existing:
        db.delete(existing)
    else:
        db.add(SongLike(song_id=song_id, user_id=user.id))
    db.commit()
    db.refresh(song)
    return _serialize(song, db, user)


@router.post("/songs/{song_id}/favorite", response_model=SongOut)
def toggle_favorite(
    song_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """收藏 / 取消收藏（幂等切换）。语义区别于赞：赞=认同，收藏=想再听。"""
    song = _visible_song(song_id, db)
    existing = (
        db.query(SongFavorite)
        .filter(SongFavorite.song_id == song_id, SongFavorite.user_id == user.id)
        .first()
    )
    if existing:
        db.delete(existing)
    else:
        db.add(SongFavorite(song_id=song_id, user_id=user.id))
    db.commit()
    db.refresh(song)
    return _serialize(song, db, user)


@router.post("/songs/{song_id}/rate", response_model=SongOut)
def rate_song(
    song_id: int,
    body: RateReq,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """星标评分 1..5（覆盖式，一人一首一个分数）。"""
    if not (1 <= int(body.score) <= 5):
        raise HTTPException(status_code=400, detail="评分需为 1–5 星")
    song = _visible_song(song_id, db)
    existing = (
        db.query(SongRating)
        .filter(SongRating.song_id == song_id, SongRating.user_id == user.id)
        .first()
    )
    if existing:
        existing.score = int(body.score)
    else:
        db.add(SongRating(song_id=song_id, user_id=user.id, score=int(body.score)))
    db.commit()
    db.refresh(song)
    return _serialize(song, db, user)


@router.get("/favorites", response_model=list[SongOut])
def my_favorites(
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """我的收藏（跨作者）。"""
    rows = (
        db.query(Song)
        .join(SongFavorite, SongFavorite.song_id == Song.id)
        .filter(SongFavorite.user_id == user.id, Song.deleted_at.is_(None))
        .order_by(SongFavorite.created_at.desc())
        .limit(limit)
        .all()
    )
    if not rows:
        return []
    stats = _interaction_stats(db, [s.id for s in rows], user.id)
    names = _author_names_cached(db, [s.user_id for s in rows])
    return [
        _to_out(s, user, {**(stats.get(s.id) or {}), "author_name": names.get(s.user_id)})
        for s in rows
    ]
