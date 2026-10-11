"""GAP-011 库组织：播放列表 / 文件夹 / 批量操作 / 回收站。

钉住的契约（每一条都对应一个会真出事的面）：
1. 软删不进回收站以外的地方 —— 列表 / 详情 / 公开流 / 广场 / 收藏 / 配额计数
   全都查不到回收站里的歌。这是本项目最容易漏的一类回归（查询面有 7 处）。
2. 恢复是**无损**的：歌单成员、收藏、评价在软删→恢复之间原样存活。
3. 彻底删除才清理关联行，并解绑子歌血缘、标记 sync 记录（不留悬空 id）。
4. 30 天保留策略：未到期的不清、已到期的在访问回收站时真删。
5. 批量端点：能做的做掉、做不到的如实进 skipped（不整批 400、不静默跳过）。
6. 播放列表：排序为完整覆盖、非法顺序 400、跨用户 404、删歌单不删歌。
7. 文件夹：按名称归组、幂等去重、只允许归拢自己的东西。
8. 收藏复用现有 SongFavorite（不另立收藏表）—— 结构断言。
"""

import datetime
import pathlib

import pytest

from app.db import SessionLocal
from app.models import (
    Folder,
    FolderItem,
    Playlist,
    PlaylistSong,
    Song,
    SongFavorite,
    TrashKeepRule,
    User,
)


def _register(client, email: str = "lib@test.com") -> dict:
    r = client.post("/api/auth/register", json={"email": email, "password": "pw123456"})
    assert r.status_code in (200, 201), r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _uid(db, email: str) -> int:
    u = db.query(User).filter(User.email == email).first()
    assert u is not None
    return u.id


def _mk_song(db, user_id: int, **kw) -> Song:
    """直接建歌：本测试面是库组织，不需要走生成流程（少一层 mock 依赖）。"""
    s = Song(
        user_id=user_id,
        title=kw.pop("title", "曲"),
        status=kw.pop("status", "completed"),
        audio_url="https://cdn.test/a.mp3",
        cover_url=kw.pop("cover_url", "https://cdn.test/c.jpg"),
        moderation_status=kw.pop("moderation_status", "approved"),
        **kw,
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


def _folder_with(db, user_id: int, name: str, song_id: int) -> Folder:
    """直接建「文件夹 + 一首歌的归类」，供不经 API 的用例复用。"""
    folder = db.query(Folder).filter(Folder.user_id == user_id, Folder.name == name).first()
    if folder is None:
        folder = Folder(user_id=user_id, name=name)
        db.add(folder)
        db.flush()
    db.add(FolderItem(folder_id=folder.id, item_type="song", item_id=song_id))
    db.commit()
    return folder


# ═══════════════════════════════════════════════════════════════════════════
# 1. 回收站：软删 / 恢复 / 彻底删除
# ═══════════════════════════════════════════════════════════════════════════


def test_delete_moves_to_trash_not_hard_delete(client):
    """DELETE /songs/{id} 现在是软删：行还在，deleted_at 非空，且同时下架。"""
    h = _register(client)
    db = SessionLocal()
    uid = _uid(db, "lib@test.com")
    s = _mk_song(db, uid, is_public=True)
    sid = s.id
    db.close()

    r = client.delete(f"/api/songs/{sid}", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["detail"] == "trashed"

    db = SessionLocal()
    row = db.get(Song, sid)
    assert row is not None, "软删不应真的删行"
    assert row.deleted_at is not None
    assert row.is_public is False, "进回收站必须同时下架"
    db.close()


def test_soft_deleted_song_hidden_everywhere(client):
    """软删后：我的列表 / 详情 / 公开流 / 广场 / 收藏 全都查不到。"""
    h = _register(client)
    db = SessionLocal()
    uid = _uid(db, "lib@test.com")
    s = _mk_song(db, uid, is_public=True, title="要被删的")
    sid = s.id
    db.close()

    assert client.get("/api/songs", headers=h).json(), "软删前应在列表里"
    assert client.get(f"/api/songs/{sid}", headers=h).status_code == 200
    assert sid in {x["id"] for x in client.get("/api/songs/explore").json()}
    assert sid in {x["id"] for x in client.get("/api/plaza").json()}

    assert client.delete(f"/api/songs/{sid}", headers=h).status_code == 200

    assert sid not in {x["id"] for x in client.get("/api/songs", headers=h).json()}
    assert client.get(f"/api/songs/{sid}", headers=h).status_code == 404, "详情不该还能打开"
    assert sid not in {x["id"] for x in client.get("/api/songs/explore").json()}
    assert sid not in {x["id"] for x in client.get("/api/plaza").json()}
    # 公开流也不该能下载/播放/试听（404 = 不存在）
    assert client.post(f"/api/songs/{sid}/play", headers=h).status_code == 404
    assert client.get(f"/api/songs/{sid}/download", headers=h).status_code == 404


def test_trash_list_and_restore_roundtrip_keeps_memberships(client):
    """恢复是无损的：歌单成员、收藏在软删→恢复之间原样存活。"""
    h = _register(client)
    db = SessionLocal()
    uid = _uid(db, "lib@test.com")
    s = _mk_song(db, uid, title="有归属的歌")
    pl = Playlist(user_id=uid, name="测试歌单")
    db.add(pl)
    db.commit()
    db.add(PlaylistSong(playlist_id=pl.id, song_id=s.id, position=0))
    db.add(SongFavorite(song_id=s.id, user_id=uid))
    db.commit()
    sid, plid = s.id, pl.id
    db.close()

    assert client.delete(f"/api/songs/{sid}", headers=h).status_code == 200

    trash = client.get("/api/library/trash", headers=h).json()
    assert [x["id"] for x in trash] == [sid]
    assert trash[0]["keep_days"] == 30
    assert trash[0]["days_left"] == 30
    assert trash[0]["deleted_at"] and trash[0]["expires_at"]
    # 恢复前：歌单里也不该再出现这首（歌单查询面同样排除回收站）
    pl_before = client.get(f"/api/library/playlists/{plid}", headers=h).json()
    assert [x["id"] for x in pl_before["songs"]] == []

    assert client.post(f"/api/library/trash/{sid}/restore", headers=h).status_code == 200
    assert client.get("/api/library/trash", headers=h).json() == []

    db = SessionLocal()
    assert db.get(Song, sid).deleted_at is None
    assert (
        db.query(PlaylistSong).filter_by(playlist_id=plid, song_id=sid).count() == 1
    ), "恢复后歌单成员必须还在"
    assert (
        db.query(SongFavorite).filter_by(song_id=sid, user_id=uid).count() == 1
    ), "恢复后收藏必须还在"
    db.close()

    pl_after = client.get(f"/api/library/playlists/{plid}", headers=h).json()
    assert [x["id"] for x in pl_after["songs"]] == [sid]


def test_restore_requires_song_in_trash(client):
    h = _register(client)
    db = SessionLocal()
    sid = _mk_song(db, _uid(db, "lib@test.com")).id
    db.close()
    r = client.post(f"/api/library/trash/{sid}/restore", headers=h)
    assert r.status_code == 400, "不在回收站里的歌不该能被「恢复」"


def test_purge_removes_row_and_dependents(client):
    """彻底删除：行消失，互动/歌单成员/文件夹归类一起清掉，子歌血缘解绑。"""
    h = _register(client)
    db = SessionLocal()
    uid = _uid(db, "lib@test.com")
    s = _mk_song(db, uid)
    child = _mk_song(db, uid, title="子歌", parent_song_id=s.id)
    pl = Playlist(user_id=uid, name="P")
    db.add(pl)
    db.commit()
    db.add(PlaylistSong(playlist_id=pl.id, song_id=s.id, position=0))
    db.add(SongFavorite(song_id=s.id, user_id=uid))
    _folder_with(db, uid, "F", s.id)
    db.commit()
    sid, child_id = s.id, child.id
    db.close()

    # 未进回收站时不允许彻底删除（否则就是硬删后门）
    assert client.delete(f"/api/library/trash/{sid}", headers=h).status_code == 400

    client.delete(f"/api/songs/{sid}", headers=h)
    assert client.delete(f"/api/library/trash/{sid}", headers=h).status_code == 200

    db = SessionLocal()
    assert db.get(Song, sid) is None
    assert db.query(PlaylistSong).filter_by(song_id=sid).count() == 0
    assert db.query(SongFavorite).filter_by(song_id=sid).count() == 0
    assert db.query(FolderItem).filter_by(item_type="song", item_id=sid).count() == 0
    assert db.get(Song, child_id).parent_song_id is None, "子歌血缘必须解绑"
    db.close()


def test_repeat_delete_does_not_extend_retention(client):
    """重复 DELETE 幂等：不刷新 deleted_at，否则反复调用能把保留期无限续命。"""
    h = _register(client)
    db = SessionLocal()
    sid = _mk_song(db, _uid(db, "lib@test.com")).id
    db.close()

    first = client.delete(f"/api/songs/{sid}", headers=h).json()["deleted_at"]
    second = client.delete(f"/api/songs/{sid}", headers=h).json()["deleted_at"]
    assert first == second


def test_retention_purges_expired_on_trash_access(client):
    """保留 30 天：访问回收站时清掉已过期的条目，未过期的留着。"""
    h = _register(client)
    db = SessionLocal()
    uid = _uid(db, "lib@test.com")
    old = _mk_song(db, uid, title="过期")
    fresh = _mk_song(db, uid, title="没过期")
    now = datetime.datetime.now(datetime.timezone.utc)
    old.deleted_at = now - datetime.timedelta(days=31)
    fresh.deleted_at = now - datetime.timedelta(days=3)
    db.commit()
    old_id, fresh_id = old.id, fresh.id
    db.close()

    trash = client.get("/api/library/trash", headers=h).json()
    assert [x["id"] for x in trash] == [fresh_id]
    assert trash[0]["days_left"] == 27

    db = SessionLocal()
    assert db.get(Song, old_id) is None, "超过保留期的条目应被真删"
    assert db.get(Song, fresh_id) is not None
    db.close()


def test_retention_days_come_from_config_table(client):
    """保留天数读配置表（不是写死的常量）：改 7 天后 8 天前的条目即过期。"""
    h = _register(client)
    db = SessionLocal()
    uid = _uid(db, "lib@test.com")
    s = _mk_song(db, uid)
    s.deleted_at = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=8)
    db.add(TrashKeepRule(keep_days=7))
    db.commit()
    sid = s.id
    db.close()

    assert client.get("/api/library/trash", headers=h).json() == []
    db = SessionLocal()
    assert db.get(Song, sid) is None
    db.close()


def test_empty_trash(client):
    h = _register(client)
    db = SessionLocal()
    uid = _uid(db, "lib@test.com")
    a_id, b_id = _mk_song(db, uid).id, _mk_song(db, uid).id
    db.close()
    for sid in (a_id, b_id):
        client.delete(f"/api/songs/{sid}", headers=h)

    r = client.post("/api/library/trash/empty", headers=h)
    assert r.status_code == 200 and r.json()["count"] == 2
    assert client.get("/api/library/trash", headers=h).json() == []
    db = SessionLocal()
    assert db.get(Song, a_id) is None and db.get(Song, b_id) is None
    db.close()


def test_trash_is_per_user(client):
    """别人的回收站看不到、也不能恢复/彻底删。"""
    h1 = _register(client, "u1@test.com")
    h2 = _register(client, "u2@test.com")
    db = SessionLocal()
    sid = _mk_song(db, _uid(db, "u1@test.com")).id
    db.close()

    client.delete(f"/api/songs/{sid}", headers=h1)
    assert client.get("/api/library/trash", headers=h2).json() == []
    assert client.post(f"/api/library/trash/{sid}/restore", headers=h2).status_code == 404
    assert client.delete(f"/api/library/trash/{sid}", headers=h2).status_code == 404


# ═══════════════════════════════════════════════════════════════════════════
# 2. 批量操作
# ═══════════════════════════════════════════════════════════════════════════


def test_batch_delete_and_counts(client):
    h = _register(client)
    db = SessionLocal()
    uid = _uid(db, "lib@test.com")
    ids = [_mk_song(db, uid).id for _ in range(3)]
    db.close()

    r = client.post("/api/library/batch/delete", json={"song_ids": ids}, headers=h)
    assert r.status_code == 200, r.text
    assert sorted(r.json()["deleted"]) == sorted(ids)
    assert client.get("/api/songs", headers=h).json() == []
    assert {x["id"] for x in client.get("/api/library/trash", headers=h).json()} == set(ids)


def test_batch_delete_skips_foreign_and_trashed(client):
    """能做的做掉、做不到的如实汇报（不整批 400、不静默跳过）。"""
    h1 = _register(client, "mine@test.com")
    h2 = _register(client, "other@test.com")
    db = SessionLocal()
    mine = _mk_song(db, _uid(db, "mine@test.com")).id
    theirs = _mk_song(db, _uid(db, "other@test.com")).id
    db.close()
    client.delete(f"/api/songs/{mine}", headers=h1)  # 已经在回收站

    r = client.post(
        "/api/library/batch/delete", json={"song_ids": [mine, theirs, 999999]}, headers=h1
    )
    assert r.status_code == 200
    assert r.json()["deleted"] == []
    assert sorted(r.json()["skipped"]) == sorted([mine, theirs, 999999])

    # 对方那首必须原封不动
    assert client.get("/api/songs", headers=h2).json()[0]["id"] == theirs


def test_batch_public_enforces_publish_gates(client):
    """批量公开沿用单曲的发布门：未完曲/被驳回 -> skipped，其余照发。"""
    h = _register(client)
    db = SessionLocal()
    uid = _uid(db, "lib@test.com")
    ok = _mk_song(db, uid, title="可以发")
    pending = _mk_song(db, uid, title="没生成完", status="processing")
    rejected = _mk_song(db, uid, title="被驳回", moderation_status="rejected")
    ok_id, pending_id, rejected_id = ok.id, pending.id, rejected.id
    db.close()

    r = client.post(
        "/api/library/batch/public",
        json={"song_ids": [ok_id, pending_id, rejected_id], "is_public": True},
        headers=h,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["updated"] == [ok_id]
    reasons = {x["id"]: x["reason"] for x in body["skipped"]}
    assert "尚未生成完成" in reasons[pending_id]
    assert "审核" in reasons[rejected_id]

    db = SessionLocal()
    assert db.get(Song, ok_id).is_public is True
    assert db.get(Song, pending_id).is_public is False
    db.close()

    # 批量下架不受发布门限制
    r2 = client.post(
        "/api/library/batch/public",
        json={"song_ids": [ok_id, pending_id], "is_public": False},
        headers=h,
    )
    assert sorted(r2.json()["updated"]) == sorted([ok_id, pending_id])


def test_batch_public_rejects_oversize_and_empty(client):
    h = _register(client)
    assert client.post("/api/library/batch/delete", json={"song_ids": []}, headers=h).status_code == 400
    big = list(range(1, 202))
    assert (
        client.post("/api/library/batch/delete", json={"song_ids": big}, headers=h).status_code
        == 400
    )


def test_batch_add_to_playlist_is_idempotent_and_appends(client):
    h = _register(client)
    db = SessionLocal()
    uid = _uid(db, "lib@test.com")
    a, b, c = (_mk_song(db, uid).id for _ in range(3))
    db.close()

    pl = client.post("/api/library/playlists", json={"name": "批量"}, headers=h).json()
    r = client.post(
        "/api/library/batch/playlist",
        json={"song_ids": [a, b], "playlist_id": pl["id"]},
        headers=h,
    )
    assert r.json()["added"] == [a, b] and r.json()["count"] == 2

    # 再加一次 a + 一首新歌：a 不重复、新歌追加到末尾
    r2 = client.post(
        "/api/library/batch/playlist",
        json={"song_ids": [a, c], "playlist_id": pl["id"]},
        headers=h,
    )
    assert r2.json()["added"] == [c]
    assert r2.json()["already_in"] == [a]
    assert r2.json()["count"] == 3

    detail = client.get(f"/api/library/playlists/{pl['id']}", headers=h).json()
    assert [s["id"] for s in detail["songs"]] == [a, b, c], "已在歌单里的歌不能被挪到末尾"


def test_batch_add_cannot_use_foreign_playlist(client):
    h1 = _register(client, "p1@test.com")
    _register(client, "p2@test.com")
    db = SessionLocal()
    uid2 = _uid(db, "p2@test.com")
    theirs_pl = Playlist(user_id=uid2, name="别人的歌单")
    db.add(theirs_pl)
    db.commit()
    plid = theirs_pl.id
    mine = _mk_song(db, _uid(db, "p1@test.com")).id
    db.close()

    r = client.post(
        "/api/library/batch/playlist", json={"song_ids": [mine], "playlist_id": plid}, headers=h1
    )
    assert r.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════
# 3. 播放列表
# ═══════════════════════════════════════════════════════════════════════════


def test_playlist_crud_and_cover_fallback(client):
    h = _register(client)
    db = SessionLocal()
    uid = _uid(db, "lib@test.com")
    s = _mk_song(db, uid, cover_url="https://cdn.test/first.jpg")
    db.close()

    pl = client.post(
        "/api/library/playlists",
        json={"name": "我的最爱", "description": "d", "is_public": False},
        headers=h,
    ).json()
    assert pl["song_count"] == 0 and pl["cover_url"] is None

    client.post("/api/library/batch/playlist", json={"song_ids": [s.id], "playlist_id": pl["id"]}, headers=h)
    got = client.get(f"/api/library/playlists/{pl['id']}", headers=h).json()
    assert got["cover_url"] == "https://cdn.test/first.jpg", "无自定义封面时回退首曲封面"
    assert got["is_owner"] is True
    assert [x["id"] for x in got["songs"]] == [s.id]

    patched = client.patch(
        f"/api/library/playlists/{pl['id']}",
        json={"name": "改名", "cover_url": "https://cdn.test/custom.jpg", "is_public": True},
        headers=h,
    ).json()
    assert patched["name"] == "改名"
    assert patched["cover_url"] == "https://cdn.test/custom.jpg"

    assert client.delete(f"/api/library/playlists/{pl['id']}", headers=h).status_code == 200
    assert client.get(f"/api/library/playlists/{pl['id']}", headers=h).status_code == 404
    # 删歌单不删歌
    assert [x["id"] for x in client.get("/api/songs", headers=h).json()] == [s.id]


def test_playlist_name_validation(client):
    h = _register(client)
    assert client.post("/api/library/playlists", json={"name": "   "}, headers=h).status_code == 400
    assert client.post("/api/library/playlists", json={"name": "x" * 256}, headers=h).status_code == 400


def test_playlist_visibility(client):
    h = _register(client, "owner@test.com")
    h2 = _register(client, "visitor@test.com")
    db = SessionLocal()
    uid = _uid(db, "owner@test.com")
    s = _mk_song(db, uid)
    db.close()

    private = client.post("/api/library/playlists", json={"name": "私密"}, headers=h).json()
    client.post("/api/library/batch/playlist", json={"song_ids": [s.id], "playlist_id": private["id"]}, headers=h)
    assert client.get(f"/api/library/playlists/{private['id']}", headers=h2).status_code == 404
    # 匿名也一样（公开浏览只放行公开歌单）
    assert client.get(f"/api/library/playlists/{private['id']}").status_code == 404

    client.patch(f"/api/library/playlists/{private['id']}", json={"is_public": True}, headers=h)
    anon = client.get(f"/api/library/playlists/{private['id']}")
    assert anon.status_code == 200
    assert anon.json()["is_owner"] is False
    assert [x["id"] for x in anon.json()["songs"]] == [s.id]


def test_playlist_reorder_full_list_only(client):
    h = _register(client)
    db = SessionLocal()
    uid = _uid(db, "lib@test.com")
    ids = [_mk_song(db, uid).id for _ in range(3)]
    db.close()
    pl = client.post("/api/library/playlists", json={"name": "排序"}, headers=h).json()
    client.post("/api/library/batch/playlist", json={"song_ids": ids, "playlist_id": pl["id"]}, headers=h)

    new_order = [ids[2], ids[0], ids[1]]
    r = client.put(
        f"/api/library/playlists/{pl['id']}/order", json={"song_ids": new_order}, headers=h
    )
    assert r.status_code == 200, r.text
    detail = client.get(f"/api/library/playlists/{pl['id']}", headers=h).json()
    assert [x["id"] for x in detail["songs"]] == new_order

    # 不完整顺序 -> 400（自行补齐等于悄悄挪动用户没碰过的曲目）
    r2 = client.put(
        f"/api/library/playlists/{pl['id']}/order", json={"song_ids": [ids[0]]}, headers=h
    )
    assert r2.status_code == 400


def test_playlist_remove_song_and_foreign_access(client):
    h1 = _register(client, "a1@test.com")
    h2 = _register(client, "a2@test.com")
    db = SessionLocal()
    uid = _uid(db, "a1@test.com")
    s = _mk_song(db, uid)
    db.close()
    pl = client.post("/api/library/playlists", json={"name": "P"}, headers=h1).json()
    client.post("/api/library/batch/playlist", json={"song_ids": [s.id], "playlist_id": pl["id"]}, headers=h1)

    assert (
        client.delete(f"/api/library/playlists/{pl['id']}/songs/{s.id}", headers=h2).status_code
        == 404
    )
    assert (
        client.delete(f"/api/library/playlists/{pl['id']}/songs/{s.id}", headers=h1).status_code
        == 200
    )
    assert client.get(f"/api/library/playlists/{pl['id']}", headers=h1).json()["song_count"] == 0
    # 移除歌单成员不影响歌曲本身
    assert [x["id"] for x in client.get("/api/songs", headers=h1).json()] == [s.id]


def test_playlist_hides_trashed_songs_from_public_viewer(client):
    """公开歌单也不能把作者删进回收站的曲目漏给访客。"""
    h = _register(client, "own2@test.com")
    _register(client, "guest2@test.com")
    db = SessionLocal()
    uid = _uid(db, "own2@test.com")
    keep_id, gone_id = _mk_song(db, uid).id, _mk_song(db, uid).id
    db.close()
    pl = client.post(
        "/api/library/playlists", json={"name": "公开单", "is_public": True}, headers=h
    ).json()
    client.post(
        "/api/library/batch/playlist",
        json={"song_ids": [keep_id, gone_id], "playlist_id": pl["id"]},
        headers=h,
    )
    client.delete(f"/api/songs/{gone_id}", headers=h)

    anon = client.get(f"/api/library/playlists/{pl['id']}").json()
    assert [x["id"] for x in anon["songs"]] == [keep_id]


def test_playlist_members_survive_soft_delete(client):
    """软删不清理歌单成员行 —— 这正是恢复能无损的原因。"""
    h = _register(client)
    db = SessionLocal()
    uid = _uid(db, "lib@test.com")
    s = _mk_song(db, uid)
    pl = Playlist(user_id=uid, name="保成员")
    db.add(pl)
    db.commit()
    db.add(PlaylistSong(playlist_id=pl.id, song_id=s.id, position=0))
    db.commit()
    plid, sid = pl.id, s.id
    db.close()

    client.delete(f"/api/songs/{sid}", headers=h)
    db = SessionLocal()
    assert db.query(PlaylistSong).filter_by(playlist_id=plid, song_id=sid).count() == 1
    db.close()


# ═══════════════════════════════════════════════════════════════════════════
# 4. 文件夹
# ═══════════════════════════════════════════════════════════════════════════


def test_folder_grouping_and_idempotency(client):
    h = _register(client)
    db = SessionLocal()
    uid = _uid(db, "lib@test.com")
    s1 = _mk_song(db, uid, title="一号").id
    s2 = _mk_song(db, uid, title="二号").id
    db.close()

    for sid in (s1, s2):
        assert (
            client.post(
                "/api/library/folders",
                json={"name": "电子", "item_type": "song", "item_id": sid},
                headers=h,
            ).status_code
            == 200
        )
    # 重复添加同一项：幂等，不产生第二行
    again = client.post(
        "/api/library/folders", json={"name": "电子", "item_type": "song", "item_id": s1}, headers=h
    ).json()

    groups = client.get("/api/library/folders", headers=h).json()
    assert len(groups) == 1
    assert groups[0]["name"] == "电子" and groups[0]["count"] == 2, "重复放入不得产生第二条成员"
    assert {i["title"] for i in groups[0]["items"]} == {"一号", "二号"}
    assert again["count"] == 2 and again["id"] == groups[0]["id"]

    # 整组改名只改一行；整组删除后空文件夹真的消失了（容器为空仍存在）
    renamed = client.patch(
        f"/api/library/folders/{groups[0]['id']}", json={"name": "电音"}, headers=h
    ).json()
    assert renamed["name"] == "电音" and renamed["count"] == 2

    assert client.delete(f"/api/library/folders/{groups[0]['id']}", headers=h).status_code == 200
    assert client.get("/api/library/folders", headers=h).json() == []
    # 解散分组不删歌
    assert {x["id"] for x in client.get("/api/songs", headers=h).json()} == {s1, s2}


def test_folder_can_hold_playlists_and_rejects_foreign(client):
    h1 = _register(client, "f1@test.com")
    h2 = _register(client, "f2@test.com")
    db = SessionLocal()
    mine = _mk_song(db, _uid(db, "f1@test.com")).id
    theirs = _mk_song(db, _uid(db, "f2@test.com")).id
    db.close()
    pl = client.post("/api/library/playlists", json={"name": "P"}, headers=h1).json()

    made = client.post(
        "/api/library/folders",
        json={"name": "组", "item_type": "playlist", "item_id": pl["id"]},
        headers=h1,
    )
    assert made.status_code == 200, made.text
    assert [g["name"] for g in client.get("/api/library/folders?item_type=playlist", headers=h1).json()] == ["组"]

    # 移出一项（成员行），文件夹容器保留
    item_id = client.get("/api/library/folders", headers=h1).json()[0]["items"][0]["id"]
    assert client.delete(f"/api/library/folder-items/{item_id}", headers=h1).status_code == 200
    after = client.get("/api/library/folders", headers=h1).json()
    assert after[0]["name"] == "组" and after[0]["count"] == 0, "空文件夹应当还在"
    assert client.delete(f"/api/library/folder-items/{item_id}", headers=h2).status_code == 404

    # 别人的歌不能塞进我的文件夹
    assert (
        client.post(
            "/api/library/folders",
            json={"name": "组", "item_type": "song", "item_id": theirs},
            headers=h1,
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/api/library/folders",
            json={"name": "组", "item_type": "song", "item_id": mine},
            headers=h2,
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/api/library/folders",
            json={"name": "x", "item_type": "album", "item_id": 1},
            headers=h1,
        ).status_code
        == 400
    )


def test_deleting_playlist_clears_its_folder_entries(client):
    h = _register(client)
    pl = client.post("/api/library/playlists", json={"name": "会被删"}, headers=h).json()
    client.post(
        "/api/library/folders",
        json={"name": "组", "item_type": "playlist", "item_id": pl["id"]},
        headers=h,
    )
    client.delete(f"/api/library/playlists/{pl['id']}", headers=h)

    db = SessionLocal()
    assert db.query(FolderItem).filter_by(item_type="playlist", item_id=pl["id"]).count() == 0
    db.close()


# ═══════════════════════════════════════════════════════════════════════════
# 5. 结构与边界
# ═══════════════════════════════════════════════════════════════════════════


def test_favorites_reuse_song_favorite_table():
    """收藏复用 SongFavorite：不新增第二个收藏表（模型层断言）。"""
    from app.db import Base

    fav_tables = [t for t in Base.metadata.tables if "favorite" in t or "collect" in t]
    assert fav_tables == ["song_favorites"], f"出现重复收藏表：{fav_tables}"


def test_library_tables_have_no_trash_side_table():
    """回收站只用 songs.deleted_at，不建 trash 副表。"""
    from app.db import Base

    assert not [t for t in Base.metadata.tables if "trash" in t and t != "trash_keep_rules"]
    assert "deleted_at" in {c.name for c in Song.__table__.columns}


def test_quota_count_excludes_trashed(client):
    """配额计数不把回收站里的歌算进每日用量（否则删了也占额度）。"""
    from app.quota import daily_generated_count

    h = _register(client, "q@test.com")
    db = SessionLocal()
    uid = _uid(db, "q@test.com")
    s = _mk_song(db, uid)
    assert daily_generated_count(db, uid) == 1
    db.close()

    client.delete(f"/api/songs/{s.id}", headers=h)
    db = SessionLocal()
    assert daily_generated_count(db, uid) == 0
    db.close()


def test_my_billing_excludes_trashed(client):
    h = _register(client, "b@test.com")
    db = SessionLocal()
    uid = _uid(db, "b@test.com")
    s = _mk_song(db, uid)
    db.close()
    assert client.get("/api/songs/me/billing", headers=h).json()["total_songs"] == 1
    client.delete(f"/api/songs/{s.id}", headers=h)
    assert client.get("/api/songs/me/billing", headers=h).json()["total_songs"] == 0
    assert client.get("/api/songs/me/billing", headers=h).json()["recent"] == []


def test_admin_list_hides_trashed_by_default(client, monkeypatch):
    """后台默认不列回收站的歌，include_trashed=true 才列（运营需要「到底在不在」的答案）。"""
    monkeypatch.setattr("app.config.settings.admin_token", "super")
    h = {"X-Admin-Token": "super"}
    h_user = _register(client, "adm@test.com")
    db = SessionLocal()
    sid = _mk_song(db, _uid(db, "adm@test.com")).id
    db.close()
    client.delete(f"/api/songs/{sid}", headers=h_user)

    listed = client.get("/api/admin/songs", headers=h)
    assert listed.status_code == 200, listed.text
    assert [x["id"] for x in listed.json()] == []
    all_rows = client.get("/api/admin/songs?include_trashed=true", headers=h)
    assert sid in [x["id"] for x in all_rows.json()]


def test_frontend_api_has_library_surface():
    """前端契约层必须暴露库组织端点（避免后端做完前端接不上）。"""
    api_ts = (pathlib.Path(__file__).resolve().parents[2] / "frontend" / "lib" / "api.ts").read_text(
        encoding="utf-8"
    )
    for path in (
        "/api/library/playlists",
        "/api/library/trash",
        "/api/library/batch/delete",
        "/api/library/batch/public",
        "/api/library/batch/playlist",
        "/api/library/folders",
    ):
        assert path in api_ts, f"前端 api.ts 缺少 {path}"
