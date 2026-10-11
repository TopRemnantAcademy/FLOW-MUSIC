"""库组织（GAP-011）：播放列表 / 文件夹 / 批量操作 / 回收站。

一个 router 承载四件事，因为它们共享同一条边界：**库内实体的归属与可见性**。
拆成四个 router 会让「这首歌属于我吗 / 它在回收站里吗」这类判断散落四处，
最终必然出现某处漏判（上轮 explore 与 plaza 就各写过一份 _public_filters）。

三条硬约束（都来自已发生的事故面，不是风格偏好）：

1. **软删的歌不得回流**：回收站里的歌一旦被列表/广场/收藏/计数查出来，
   用户会以为删了没生效。所有列表查询统一走 `_live()` 过滤。
2. **删歌不退歌单/收藏**：删歌只置 deleted_at；恢复时歌单成员、收藏记录原样还在，
   因为那些行从未被动过。彻底删除（purge）才清理关联行。
3. **跨用户不可见**：歌单/文件夹/回收站全部按 user_id 收口；
   公开歌单只暴露元信息，不含他人私有曲目。
"""

import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import (
    Folder,
    FolderItem,
    Playlist,
    PlaylistSong,
    Song,
    SongFavorite,
    SongLike,
    SongRating,
    TrashKeepRule,
    User,
)
from app.routers.auth import get_current_user, get_optional_user
from app.routers.songs import _author_names, _interaction_stats, _to_out
from app.schemas import SongOut

router = APIRouter(prefix="/api/library", tags=["library"])

# 回收站默认保留天数（TrashKeepRule 无行时的回退值）
DEFAULT_TRASH_KEEP_DAYS = 30

# 批量端点一次最多处理多少首：批量 ≠ 无界。无上限的 IN (...) 在 PG 上是
# 参数爆炸（SQLITE_MAX_VARIABLE_NUMBER / PG 的 65535 绑定上限）→ 先 400 而不是 500。
BATCH_MAX = 200


# ═══════════════════════════════════════════════════════════════════════════════
# 回收站：软删 / 恢复 / 彻底删除
# ═══════════════════════════════════════════════════════════════════════════════


def _keep_days(db: Session) -> int:
    """回收站保留天数（读配置表，无行时回退默认）。"""
    row = db.query(TrashKeepRule).order_by(TrashKeepRule.id).first()
    if row and row.keep_days and row.keep_days > 0:
        return int(row.keep_days)
    return DEFAULT_TRASH_KEEP_DAYS


def _now_utc() -> datetime.datetime:
    """当前 UTC 时间（带时区）。

    ⚠ 不能写 datetime.utcnow()：它返回 naive datetime，与 sqlite 上存回来的
    带时区值比较会抛 "can't compare offset-naive and offset-aware datetimes"。
    统一用 aware-UTC，比较双方都是 aware。
    """
    return datetime.datetime.now(datetime.timezone.utc)


def _as_aware(dt: Optional[datetime.datetime]) -> Optional[datetime.datetime]:
    """把 DB 取回的时间统一成 aware-UTC。

    sqlite 不存时区，读回来是 naive（PG 是 aware）—— 两种方言都要能比较，
    否则「过期判断」在 sqlite 上通过、在 PG 上炸（或反之），属于典型的环境差异故障。
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=datetime.timezone.utc)
    return dt.astimezone(datetime.timezone.utc)


def _live():
    """未软删（不在回收站）的 Song 过滤条件。

    所有面向用户的列表/搜索/计数都必须叠加它。单点定义而不是散落各处的
    `Song.deleted_at.is_(None)`：漏一处就等于回收站里的歌漏回列表。
    """
    return Song.deleted_at.is_(None)


def _expired(song: Song, keep_days: int) -> bool:
    """是否已超过保留期（前端据此标记「即将清理」，purge 据此真删）。"""
    deleted = _as_aware(song.deleted_at)
    if deleted is None:
        return False
    return deleted + datetime.timedelta(days=keep_days) <= _now_utc()


def _trash_out(song: Song, keep_days: int, now: datetime.datetime) -> dict:
    """回收站条目：歌曲信息 + 删除时刻 + 剩余天数（前端显示倒计时）。"""
    deleted = _as_aware(song.deleted_at)
    expire_at = deleted + datetime.timedelta(days=keep_days) if deleted else None
    days_left = None
    if expire_at is not None:
        # 向上取整：还剩 1 小时显示「1 天」而不是「0 天」，避免「0 天」被理解成已过期
        delta = expire_at - now
        days_left = max(0, int((delta.total_seconds() + 86399) // 86400))
    out = _to_out(song, None).model_dump()
    out.update(
        {
            "deleted_at": deleted.isoformat() if deleted else None,
            "expires_at": expire_at.isoformat() if expire_at else None,
            "days_left": days_left,
            "keep_days": keep_days,
        }
    )
    return out


def _owned_live_song(song_id: int, db: Session, user: User) -> Song:
    """取自己**未在回收站**的歌；不存在/非本人/已软删 → 404。"""
    song = db.get(Song, song_id)
    if not song or song.user_id != user.id or song.deleted_at is not None:
        raise HTTPException(status_code=404, detail="歌曲不存在")
    return song


def _purge_song(song: Song, db: Session) -> None:
    """彻底删除：清理互动/归类行 + 解绑血缘 + 标记同步记录，最后删 Song。

    为何必须手工清：这些关联表都没有数据库级 ON DELETE CASCADE 到 songs
    （song_likes/favorites/ratings/playlist_songs 建表时未加），留着就是悬空 song_id。
    """
    from app.models import SyncRecord

    sid = song.id
    db.query(SongLike).filter(SongLike.song_id == sid).delete(synchronize_session=False)
    db.query(SongFavorite).filter(SongFavorite.song_id == sid).delete(synchronize_session=False)
    db.query(SongRating).filter(SongRating.song_id == sid).delete(synchronize_session=False)
    db.query(PlaylistSong).filter(PlaylistSong.song_id == sid).delete(synchronize_session=False)
    db.query(FolderItem).filter(FolderItem.item_type == "song", FolderItem.item_id == sid).delete(
        synchronize_session=False
    )
    # 子歌血缘解绑（与硬删时代一致）：不置 NULL 会留下指向已删父歌的悬挂外键
    db.query(Song).filter(Song.parent_song_id == sid).update(
        {Song.parent_song_id: None}, synchronize_session=False
    )
    # [F5] 删除传播：FLOW APP 拉歌单时排除已删歌（list_flow 只返回 synced）
    rec = db.query(SyncRecord).filter_by(song_id=sid).first()
    if rec:
        rec.status = "revoked"
    db.delete(song)


@router.get("/trash")
def list_trash(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """回收站列表（只含自己的、已软删的歌）。

    顺带做一次**过期清理**：超过保留期的条目在这里真删。
    为什么不靠定时任务：本项目没有常驻 scheduler（任务是进程内 BackgroundTasks），
    写一个假想的 cron 等于交付一个永远不会跑的清理逻辑 —— 那比不做更糟。
    挂在用户一定会打开的回收站列表上，删除时机由真实访问驱动，行为可观测、可测。
    """
    keep = _keep_days(db)
    now = _now_utc()

    expired = [
        s
        for s in db.query(Song)
        .filter(Song.user_id == user.id, Song.deleted_at.is_not(None))
        .all()
        if _expired(s, keep)
    ]
    for s in expired:
        _purge_song(s, db)
    if expired:
        db.commit()

    rows = (
        db.query(Song)
        .filter(Song.user_id == user.id, Song.deleted_at.is_not(None))
        .order_by(Song.deleted_at.desc(), Song.id.desc())
        .limit(limit)
        .offset(offset)
        .all()
    )
    return [_trash_out(s, keep, now) for s in rows]


@router.post("/trash/{song_id}/restore", response_model=SongOut)
def restore_song(
    song_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """从回收站恢复（清 deleted_at）。

    恢复后的公开状态：软删时**不改编** is_public，故恢复即回到删除前的可见性。
    另有一个失败关闭的细节——软删时若歌已公开，我们会把它下架（见 delete_batch），
    恢复到用户手里的是「私有但可再次发布」，不会出现「删的时候是公开的，
    恢复后未经审核又出现在广场」。
    """
    song = db.get(Song, song_id)
    if not song or song.user_id != user.id:
        raise HTTPException(status_code=404, detail="歌曲不存在")
    if song.deleted_at is None:
        raise HTTPException(status_code=400, detail="该歌曲不在回收站中")
    song.deleted_at = None
    db.commit()
    db.refresh(song)
    return _to_out(song, user)


@router.delete("/trash/{song_id}", status_code=200)
def purge_song(
    song_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """彻底删除（不可恢复）。只允许删回收站里的歌，避免变成硬删后门。"""
    song = db.get(Song, song_id)
    if not song or song.user_id != user.id:
        raise HTTPException(status_code=404, detail="歌曲不存在")
    if song.deleted_at is None:
        raise HTTPException(status_code=400, detail="请先删除到回收站，再彻底删除")
    _purge_song(song, db)
    db.commit()
    return {"detail": "purged", "id": song_id}


@router.post("/trash/empty", status_code=200)
def empty_trash(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """清空回收站（把回收站里全部条目彻底删除）。"""
    rows = db.query(Song).filter(Song.user_id == user.id, Song.deleted_at.is_not(None)).all()
    for s in rows:
        _purge_song(s, db)
    db.commit()
    return {"detail": "emptied", "count": len(rows)}


# ═══════════════════════════════════════════════════════════════════════════════
# 批量操作
# ═══════════════════════════════════════════════════════════════════════════════


class BatchIdsReq(BaseModel):
    song_ids: list[int]


class BatchPublicReq(BatchIdsReq):
    is_public: bool


class BatchAddReq(BatchIdsReq):
    playlist_id: int


def _normalize_ids(raw: list[int]) -> list[int]:
    """去重 + 保序 + 上限校验。重复 id 会让「批量加到歌单」的计数虚高。"""
    seen: set[int] = set()
    out: list[int] = []
    for sid in raw:
        if sid not in seen:
            seen.add(sid)
            out.append(int(sid))
    if not out:
        raise HTTPException(status_code=400, detail="请至少选择一首歌曲")
    if len(out) > BATCH_MAX:
        raise HTTPException(status_code=400, detail=f"单次批量操作最多 {BATCH_MAX} 首")
    return out


def _owned_songs(ids: list[int], db: Session, user: User) -> dict[int, Song]:
    """批量取自己的**未软删**歌：{id: Song}（缺席的 = 不存在/非本人/已在回收站）。

    批量端点的语义是「能做的做掉，做不到的如实汇报」——
    整批 400 会逼前端逐个试错，静默跳过则让用户以为全成了。故返回 skipped 明细。
    """
    rows = (
        db.query(Song)
        .filter(Song.id.in_(ids), Song.user_id == user.id, Song.deleted_at.is_(None))
        .all()
    )
    return {s.id: s for s in rows}


@router.post("/batch/delete")
def batch_delete(
    body: BatchIdsReq,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """批量删除到回收站（软删）。

    软删同时下架（is_public=False）：广场是给**活着**的作品用的，
    一份已被作者删掉（哪怕可恢复）的作品继续对外可见，是隐私事故而非体验问题。
    """
    ids = _normalize_ids(body.song_ids)
    songs = _owned_songs(ids, db, user)
    now = _now_utc()
    for s in songs.values():
        s.deleted_at = now
        s.is_public = False
    db.commit()
    return {
        "deleted": sorted(songs.keys()),
        "skipped": [i for i in ids if i not in songs],
    }


@router.post("/batch/public")
def batch_public(
    body: BatchPublicReq,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """批量公开 / 批量取消公开。

    公开侧沿用单曲端点（PATCH /songs/{id}）的全部守门：只有 completed 且
    moderation_status != rejected 才能上架。这里**逐首判定**而不是整批拒绝：
    一批里混了未完曲时，把能发布的发出去、把不能发布的列进 skipped，比全批失败有用。
    """
    ids = _normalize_ids(body.song_ids)
    songs = _owned_songs(ids, db, user)
    updated: list[int] = []
    skipped: list[dict] = []
    for sid in ids:
        song = songs.get(sid)
        if song is None:
            skipped.append({"id": sid, "reason": "不存在或不在你的库中"})
            continue
        if body.is_public:
            if song.status != "completed":
                skipped.append({"id": sid, "reason": f"尚未生成完成（当前：{song.status}）"})
                continue
            if song.moderation_status == "rejected":
                skipped.append({"id": sid, "reason": "未通过内容审核"})
                continue
        song.is_public = bool(body.is_public)
        updated.append(sid)
    db.commit()
    return {"updated": updated, "skipped": skipped}


@router.post("/batch/playlist")
def batch_add_to_playlist(
    body: BatchAddReq,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """批量加入播放列表（追加到末尾，幂等）。

    已在歌单里的歌**不报错也不重复插入**（唯一约束会挡住），而是原样保留其位置 ——
    「再加一次」不应该把歌从原来的第 3 位挪到末尾。
    """
    ids = _normalize_ids(body.song_ids)
    pl = _owned_playlist(body.playlist_id, db, user)
    songs = _owned_songs(ids, db, user)

    existing = {
        sid
        for (sid,) in db.query(PlaylistSong.song_id)
        .filter(PlaylistSong.playlist_id == pl.id, PlaylistSong.song_id.in_(list(songs.keys()) or [0]))
        .all()
    }
    next_pos = (
        db.query(func.coalesce(func.max(PlaylistSong.position), -1))
        .filter(PlaylistSong.playlist_id == pl.id)
        .scalar()
        + 1
    )
    added: list[int] = []
    for sid in ids:
        if sid not in songs or sid in existing:
            continue
        db.add(PlaylistSong(playlist_id=pl.id, song_id=sid, position=next_pos))
        next_pos += 1
        added.append(sid)
    db.commit()
    return {
        "added": added,
        "already_in": sorted(existing),
        "skipped": [i for i in ids if i not in songs],
        "playlist_id": pl.id,
        "count": _playlist_count(db, pl.id),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 播放列表
# ═══════════════════════════════════════════════════════════════════════════════


class PlaylistReq(BaseModel):
    name: str
    description: str = ""
    cover_url: str = ""
    is_public: bool = False


class PlaylistPatchReq(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    cover_url: Optional[str] = None
    is_public: Optional[bool] = None


class ReorderReq(BaseModel):
    song_ids: list[int]


def _owned_playlist(playlist_id: int, db: Session, user: User) -> Playlist:
    pl = db.get(Playlist, playlist_id)
    if not pl or pl.user_id != user.id:
        raise HTTPException(status_code=404, detail="播放列表不存在")
    return pl


def _playlist_count(db: Session, playlist_id: int) -> int:
    return (
        db.query(func.count(PlaylistSong.id))
        .filter(PlaylistSong.playlist_id == playlist_id)
        .scalar()
        or 0
    )


def _playlist_out(pl: Playlist, db: Session, song_count: Optional[int] = None) -> dict:
    """歌单元信息。cover_url 为空时回退**首曲封面** —— 封面让歌单可辨识，
    强制用户上传封面等于给组织功能加一道无谓的门。"""
    cover = pl.cover_url or ""
    if not cover:
        first = (
            db.query(Song.cover_url)
            .join(PlaylistSong, PlaylistSong.song_id == Song.id)
            .filter(PlaylistSong.playlist_id == pl.id, _live(), Song.cover_url != "")
            .order_by(PlaylistSong.position, PlaylistSong.id)
            .first()
        )
        cover = (first[0] if first else "") or ""
    return {
        "id": pl.id,
        "name": pl.name,
        "description": pl.description or "",
        "cover_url": cover or None,
        "is_public": bool(pl.is_public),
        "song_count": _playlist_count(db, pl.id) if song_count is None else song_count,
        "created_at": pl.created_at.isoformat() if pl.created_at else None,
        "updated_at": pl.updated_at.isoformat() if pl.updated_at else None,
    }


@router.get("/playlists")
def list_playlists(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """我的歌单（含成员数）。一次 group by 取计数，避免每个歌单一次 count。"""
    counts = dict(
        db.query(PlaylistSong.playlist_id, func.count(PlaylistSong.id))
        .group_by(PlaylistSong.playlist_id)
        .all()
    )
    rows = (
        db.query(Playlist)
        .filter(Playlist.user_id == user.id)
        .order_by(Playlist.created_at.desc(), Playlist.id.desc())
        .all()
    )
    return [_playlist_out(pl, db, counts.get(pl.id, 0)) for pl in rows]


@router.post("/playlists")
def create_playlist(
    body: PlaylistReq,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """新建歌单。同名允许（用户可能真的想要两个「练习」），故不加唯一约束。"""
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="歌单名称不能为空")
    if len(name) > 255:
        raise HTTPException(status_code=400, detail="歌单名称过长")
    pl = Playlist(
        user_id=user.id,
        name=name,
        description=body.description or "",
        cover_url=body.cover_url or "",
        is_public=bool(body.is_public),
    )
    db.add(pl)
    db.commit()
    db.refresh(pl)
    return _playlist_out(pl, db, 0)


@router.get("/playlists/{playlist_id}")
def get_playlist(
    playlist_id: int,
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_optional_user),
):
    """歌单详情（含有序曲目）。

    公开歌单可被他人浏览（匿名亦可），但**只返回未软删的歌** ——
    作者删进回收站的曲目不应通过他人访问的歌单漏出。
    """
    pl = db.get(Playlist, playlist_id)
    if not pl:
        raise HTTPException(status_code=404, detail="播放列表不存在")
    owner = user is not None and pl.user_id == user.id
    if not owner and not pl.is_public:
        raise HTTPException(status_code=404, detail="播放列表不存在")

    songs = (
        db.query(Song)
        .join(PlaylistSong, PlaylistSong.song_id == Song.id)
        .filter(PlaylistSong.playlist_id == pl.id, _live())
        .order_by(PlaylistSong.position, PlaylistSong.id)
        .all()
    )
    stats = _interaction_stats(db, [s.id for s in songs], user.id if user else None)
    names = _author_names(db, [s.user_id for s in songs])
    out = _playlist_out(pl, db, len(songs))
    out["songs"] = [
        _to_out(s, user, {**(stats.get(s.id) or {}), "author_name": names.get(s.user_id)})
        for s in songs
    ]
    out["is_owner"] = owner
    return out


@router.patch("/playlists/{playlist_id}")
def update_playlist(
    playlist_id: int,
    body: PlaylistPatchReq,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """改名 / 改描述 / 改封面 / 公开切换。未传的字段不动（PATCH 语义）。"""
    pl = _owned_playlist(playlist_id, db, user)
    if body.name is not None:
        name = body.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="歌单名称不能为空")
        if len(name) > 255:
            raise HTTPException(status_code=400, detail="歌单名称过长")
        pl.name = name
    if body.description is not None:
        pl.description = body.description
    if body.cover_url is not None:
        pl.cover_url = body.cover_url
    if body.is_public is not None:
        pl.is_public = bool(body.is_public)
    db.commit()
    db.refresh(pl)
    return _playlist_out(pl, db)


@router.delete("/playlists/{playlist_id}", status_code=200)
def delete_playlist(
    playlist_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """删歌单 = 解散歌单，**不删歌**。成员行随之清理（歌单没了，位置无意义）。"""
    pl = _owned_playlist(playlist_id, db, user)
    db.query(FolderItem).filter(
        FolderItem.item_type == "playlist", FolderItem.item_id == pl.id
    ).delete(synchronize_session=False)
    db.query(PlaylistSong).filter(PlaylistSong.playlist_id == pl.id).delete(
        synchronize_session=False
    )
    db.delete(pl)
    db.commit()
    return {"detail": "deleted", "id": playlist_id}


def _normalize_positions(playlist_id: int, db: Session) -> None:
    """把歌单内的 position 压实为 0..n-1（按当前顺序）。

    只在**顺序真的变了之后**做一次，作为顺序的最终收敛点：
    单独重排时直接把新顺序写成 0..n-1，插歌时用 max+1 追加。
    不压实也能工作（排序只看相对大小），压实是为了让「第几位」可预测、可断言。
    """
    rows = (
        db.query(PlaylistSong)
        .filter(PlaylistSong.playlist_id == playlist_id)
        .order_by(PlaylistSong.position, PlaylistSong.id)
        .all()
    )
    for i, row in enumerate(rows):
        row.position = i


@router.post("/playlists/{playlist_id}/songs")
def add_songs_to_playlist(
    playlist_id: int,
    body: BatchIdsReq,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """向歌单添加曲目（等价于批量接口的便捷入口）。"""
    pl = _owned_playlist(playlist_id, db, user)
    return batch_add_to_playlist(
        BatchAddReq(song_ids=body.song_ids, playlist_id=pl.id), db, user
    )


@router.delete("/playlists/{playlist_id}/songs/{song_id}", status_code=200)
def remove_song_from_playlist(
    playlist_id: int,
    song_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """从歌单移除一首（歌本身不动，只是离开这张表）。"""
    pl = _owned_playlist(playlist_id, db, user)
    row = (
        db.query(PlaylistSong)
        .filter(PlaylistSong.playlist_id == pl.id, PlaylistSong.song_id == song_id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="该曲目不在此播放列表中")
    db.delete(row)
    db.flush()
    _normalize_positions(pl.id, db)
    db.commit()
    return {"detail": "removed", "playlist_id": pl.id, "song_id": song_id}


@router.put("/playlists/{playlist_id}/order")
def reorder_playlist(
    playlist_id: int,
    body: ReorderReq,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """重排歌单顺序（拖拽）。

    入参是**该歌单曲目的完整新顺序**：局部交换会让「以哪份为准」变成竞态
    （两个标签页各拖一次，后写的赢，顺序与两边看到的都不同）。
    完整列表则天然是幂等覆盖 —— 最后一个提交的版本就是最终顺序。
    """
    pl = _owned_playlist(playlist_id, db, user)
    current = {
        row.song_id: row
        for row in db.query(PlaylistSong).filter(PlaylistSong.playlist_id == pl.id).all()
    }
    order = [sid for sid in body.song_ids if sid in current]
    if len(order) != len(current):
        # 漏掉/多给曲目时**拒绝**而不是自行补齐：补齐等于悄悄移动用户没碰过的曲目，
        # 前端拿到 400 可以重新拉一次列表再重试，而不是安静地得到另一份顺序。
        raise HTTPException(
            status_code=400,
            detail=f"排序需包含本播放列表的全部 {len(current)} 首曲目（收到 {len(order)} 首有效）",
        )
    for i, sid in enumerate(order):
        current[sid].position = i
    db.commit()
    return {"detail": "reordered", "playlist_id": pl.id, "order": order}


# ═══════════════════════════════════════════════════════════════════════════════
# 文件夹（分组）
# ═══════════════════════════════════════════════════════════════════════════════

ITEM_TYPES = ("song", "playlist")


class FolderReq(BaseModel):
    name: str
    item_type: str
    item_id: int


class FolderNameReq(BaseModel):
    name: str


def _owned_folder(folder_id: int, db: Session, user: User) -> Folder:
    folder = db.get(Folder, folder_id)
    if not folder or folder.user_id != user.id:
        raise HTTPException(status_code=404, detail="文件夹不存在")
    return folder


def _validate_item(item_type: str, item_id: int, db: Session, user: User) -> None:
    """只能归拢自己的东西。不加这一步就是「任意 id 都能塞进我的文件夹」。"""
    if item_type not in ITEM_TYPES:
        raise HTTPException(status_code=400, detail=f"item_type 仅支持 {'/'.join(ITEM_TYPES)}")
    if item_type == "song":
        song = db.get(Song, item_id)
        if song is None or song.user_id != user.id:
            raise HTTPException(status_code=404, detail="歌曲不存在")
    else:
        _owned_playlist(item_id, db, user)


def _folder_out(folder: Folder, items: list[FolderItem], db: Session) -> dict:
    """文件夹 + 成员（歌名一并带上，前端渲染分组时不必逐项回查）。"""
    song_ids = [i.item_id for i in items if i.item_type == "song"]
    titles = (
        dict(db.query(Song.id, Song.title).filter(Song.id.in_(song_ids)).all()) if song_ids else {}
    )
    out_items = [
        {
            "id": i.id,
            "item_type": i.item_type,
            "item_id": i.item_id,
            "title": titles.get(i.item_id) if i.item_type == "song" else None,
        }
        for i in items
    ]
    return {
        "id": folder.id,
        "name": folder.name,
        "count": len(out_items),
        "items": out_items,
    }


@router.get("/folders")
def list_folders(
    item_type: Optional[str] = Query(None, pattern="^(song|playlist)$"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """我的文件夹分组：`[{name, count, items[]}]`。

    返回结构按文件夹归拢（而不是平铺的成员行），因为 UI 的用法就是渲染一组组；
    平铺会让每个使用点各写一次 group by。
    """
    folders = (
        db.query(Folder)
        .filter(Folder.user_id == user.id)
        .order_by(Folder.created_at.asc(), Folder.id.asc())
        .all()
    )
    if not folders:
        return []
    q = db.query(FolderItem).filter(FolderItem.folder_id.in_([f.id for f in folders]))
    if item_type:
        q = q.filter(FolderItem.item_type == item_type)
    grouped: dict[int, list[FolderItem]] = {}
    for item in q.order_by(FolderItem.id.asc()).all():
        grouped.setdefault(item.folder_id, []).append(item)
    return [_folder_out(f, grouped.get(f.id, []), db) for f in folders]


@router.post("/folders")
def upsert_folder(
    body: FolderReq,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """把一首歌 / 一张歌单放进某个文件夹（同名文件夹自动复用，重复放入幂等）。

    幂等的关键：重复放入同一项不产生第二条成员行（否则 count 会虚高、UI 会列两遍）。
    """
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="文件夹名称不能为空")
    if len(name) > 255:
        raise HTTPException(status_code=400, detail="文件夹名称过长")
    _validate_item(body.item_type, body.item_id, db, user)

    folder = db.query(Folder).filter(Folder.user_id == user.id, Folder.name == name).first()
    if folder is None:
        folder = Folder(user_id=user.id, name=name)
        db.add(folder)
        db.flush()  # 取 id，供下面建成员行

    existing = (
        db.query(FolderItem)
        .filter(
            FolderItem.folder_id == folder.id,
            FolderItem.item_type == body.item_type,
            FolderItem.item_id == body.item_id,
        )
        .first()
    )
    if existing is None:
        db.add(
            FolderItem(folder_id=folder.id, item_type=body.item_type, item_id=body.item_id)
        )
    db.commit()
    items = db.query(FolderItem).filter(FolderItem.folder_id == folder.id).all()
    return _folder_out(folder, items, db)


@router.patch("/folders/{folder_id}")
def rename_folder(
    folder_id: int,
    body: FolderNameReq,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """整组改名（这正是容器与成员分表的收益：只改一行）。"""
    folder = _owned_folder(folder_id, db, user)
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="文件夹名称不能为空")
    if len(name) > 255:
        raise HTTPException(status_code=400, detail="文件夹名称过长")
    dup = (
        db.query(Folder)
        .filter(Folder.user_id == user.id, Folder.name == name, Folder.id != folder.id)
        .first()
    )
    if dup:
        raise HTTPException(status_code=409, detail="已存在同名文件夹")
    folder.name = name
    db.commit()
    items = db.query(FolderItem).filter(FolderItem.folder_id == folder.id).all()
    return _folder_out(folder, items, db)


@router.delete("/folders/{folder_id}", status_code=200)
def delete_folder(
    folder_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """整组删除 = 解散分组，**被归类的歌/歌单本身不动**。"""
    folder = _owned_folder(folder_id, db, user)
    db.query(FolderItem).filter(FolderItem.folder_id == folder.id).delete(
        synchronize_session=False
    )
    db.delete(folder)
    db.commit()
    return {"detail": "deleted", "id": folder_id}


@router.delete("/folder-items/{item_id}", status_code=200)
def remove_folder_item(
    item_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """把一项移出文件夹（删成员行；被归类的歌/歌单本身不动）。"""
    item = db.get(FolderItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="文件夹成员不存在")
    _owned_folder(item.folder_id, db, user)  # 归属校验：不能移别人的成员
    db.delete(item)
    db.commit()
    return {"detail": "removed", "id": item_id}
