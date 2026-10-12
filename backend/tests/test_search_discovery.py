"""GAP-014 搜索/发现：标签建模 / 分类浏览 / 相似推荐 / 时间窗趋势。

钉住的契约（每条都对应一个会真出事的面）：

1. **标签是结构化且归并的**：`伤感` 与 `sad` 落成同一个规范标签
   （否则浏览面被切成两半），未知值不落库（闭词表不破）。
2. **标签在"生成完成"时才建**：完成时 title/lyric 会被上游覆盖，
   提前建的标签会与最终内容不符（本仓库已发生过一次"预览过早生成"的同类问题）。
3. **浏览面只见公开曲**：未完成 / 未公开 / 已驳回 / 回收站里的歌，
   在任何标签浏览与相似推荐里都不得出现（复用 _public_filters 的意义就在这）。
4. **多标签是 AND**：加标签应越筛越窄，OR 会让筛选越加越宽（与直觉相反）。
5. **相似推荐限公开 + 给理由**：源曲自己不可见时 404（否则是私有歌探测接口）；
   每条结果都要能说明"为什么相似"。
6. **趋势榜是增量而非累计**：窗口外的赞不算，老歌的存量播放不能霸榜。
7. **排序口径与广场一致**：hot/new/top 复用同一公式，不出现"同页两个次序"。
"""

import datetime

import pytest

from app.db import SessionLocal
from app.models import Song, SongFavorite, SongLike, SongRating, SongTag, Tag, User


def _register(client, email: str = "search@test.com") -> dict:
    r = client.post("/api/auth/register", json={"email": email, "password": "pw123456"})
    assert r.status_code in (200, 201), r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _uid(db, email: str) -> int:
    u = db.query(User).filter(User.email == email).first()
    assert u is not None
    return u.id


class _Row:
    """测试用的**脱离会话快照**：只留 id/title 等只读字段。

    为什么不直接返回 ORM 实例：本文件的模式是「在 db 里建数据 → close() →
    用 client 打接口断言」。Session 关闭后访问实例属性会触发 refresh →
    DetachedInstanceError（本文件第一版就栽在这里）。快照让"关闭后仍可用"
    变成类型事实，而不是靠每次记得 `.id` 提前取出来。
    """

    __slots__ = ("id", "title")

    def __init__(self, song):
        self.id = song.id
        self.title = song.title


def _song_orm(db, user_id: int, **kw) -> Song:
    """需要**改属性**（如软删、人工打标）的用例用这个：返回仍绑定会话的实例。"""
    s = Song(
        user_id=user_id,
        title=kw.pop("title", "曲"),
        style=kw.pop("style", "国风 伤感 钢琴"),
        status=kw.pop("status", "completed"),
        audio_url="https://cdn.test/a.mp3",
        cover_url=kw.pop("cover_url", "https://cdn.test/c.jpg"),
        moderation_status=kw.pop("moderation_status", "approved"),
        is_public=kw.pop("is_public", True),
        **kw,
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


def _mk_song(db, user_id: int, **kw) -> _Row:
    s = Song(
        user_id=user_id,
        title=kw.pop("title", "曲"),
        style=kw.pop("style", "国风 伤感 钢琴"),
        status=kw.pop("status", "completed"),
        audio_url="https://cdn.test/a.mp3",
        cover_url="https://cdn.test/c.jpg",
        moderation_status=kw.pop("moderation_status", "approved"),
        is_public=kw.pop("is_public", True),
        **kw,
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    return _Row(s)


def _tagged_orm(db, user_id: int, title: str, style: str, **kw) -> Song:
    """建歌 + 建标签，返回**仍绑定会话**的实例（需要改 attrs 时用）。"""
    from app.search.service import sync_song_tags

    song = _song_orm(db, user_id, title=title, style=style, **kw)
    sync_song_tags(db, song)
    db.commit()
    return song


def _tagged(db, user_id: int, title: str, style: str, **kw) -> _Row:
    """建歌并**显式**建标签（不经生成流程：本测试面是检索，不是生成）。

    返回脱离会话的快照（见 _Row），调用方在 db.close() 之后仍能用 `.id`。
    """
    from app.search.service import sync_song_tags

    song = _song_orm(db, user_id, title=title, style=style, **kw)
    sync_song_tags(db, song)
    db.commit()
    return _Row(song)


# ═══════════════════════════════════════════════════════════════════════════
# 1. 标签抽取与归并
# ═══════════════════════════════════════════════════════════════════════════


def test_aliases_resolve_to_one_canonical_tag():
    """同义词必须归并：伤感/悲伤/悲情/sad 都是同一个 mood:sad。"""
    from app.search.tags import extract_tags

    for text in ("伤感", "悲伤", "悲情", "sad", "슬픈"):
        hit = {(t.category, t.value) for t in extract_tags(text)}
        assert ("mood", "sad") in hit, f"{text} 未归并到 mood:sad"


def test_no_ambiguous_alias_maps_to_two_canonical_tags():
    """别名表不得有歧义：同一别名指向两个规范标签会静默丢一个。"""
    from app.search.tags import known_aliases

    seen: dict[str, tuple[str, str]] = {}
    collisions = []
    for cat, table in _category_tables():
        for canon, aliases in table.items():
            for alias in (canon, *aliases):
                key = alias.strip().lower()
                if key in seen and seen[key] != (cat, canon):
                    collisions.append((key, seen[key], (cat, canon)))
                seen[key] = (cat, canon)
    assert not collisions, f"别名歧义: {collisions}"
    assert known_aliases(), "反向索引为空 —— 断言本身失效"


def _category_tables():
    from app.search.tags import CATEGORY_ORDER

    return CATEGORY_ORDER


def test_cjk_without_separator_still_matches():
    """没有分隔符的复合写法也要命中（`伤感情歌` / `piano-ballad`）。"""
    from app.search.tags import extract_tags

    assert ("mood", "sad") in {(t.category, t.value) for t in extract_tags("伤感情歌")}
    assert ("genre", "jazz") in {(t.category, t.value) for t in extract_tags("smooth-jazz")}


def test_enum_columns_become_tags_without_text_guessing():
    """结构化列（纯音乐/声部）由枚举映射，不靠文本猜。"""

    class _S:
        style = ""
        prompt = ""
        title = "无题"
        make_instrumental = True
        vocal_gender = "female"
        content_category = "christian_worship"

    from app.search.tags import tags_for_song

    got = {(t.category, t.value) for t in tags_for_song(_S())}
    assert ("form", "instrumental") in got
    assert ("vocal", "female") in got
    assert ("genre", "worship") in got


def test_normalize_tag_rejects_unknown_value():
    """未知标签不落库：闭词表破了，浏览入口就不再可预期。"""
    from app.search.tags import normalize_tag

    assert normalize_tag("genre", "guofeng") == ("genre", "guofeng")
    assert normalize_tag("genre", "国风") == ("genre", "guofeng")
    assert normalize_tag("genre", "死金交响黑" ) is None
    assert normalize_tag("bogus", "whatever") is None


def test_tags_are_built_only_for_completed_songs(client):
    """生成中的歌不建标签（完成时文本会被上游覆盖，提前建必然不符）。"""
    from app.search.service import sync_song_tags

    h = _register(client)
    db = SessionLocal()
    uid = _uid(db, "search@test.com")
    pending = _song_orm(db, uid, status="processing", style="国风 伤感")
    assert sync_song_tags(db, pending) == 0
    assert db.query(SongTag).filter(SongTag.song_id == pending.id).count() == 0

    done = _song_orm(db, uid, style="国风 伤感")
    assert sync_song_tags(db, done) >= 2
    assert db.query(SongTag).filter(SongTag.song_id == done.id).count() >= 2
    db.commit()
    db.close()
    assert h  # 保持注册（避免 lint 认为未用）


def test_manual_tags_survive_resync(client):
    """重算标签不得冲掉人工写入（source 列存在的全部理由）。"""
    from app.search.service import sync_song_tags

    h = _register(client)
    db = SessionLocal()
    uid = _uid(db, "search@test.com")
    song = _tagged_orm(db, uid, "手写标签", "国风")
    manual_tag = db.query(Tag).filter(Tag.category == "mood", Tag.value == "epic").first()
    if manual_tag is None:
        manual_tag = Tag(category="mood", value="epic", label="史诗")
        db.add(manual_tag)
        db.flush()
    db.add(SongTag(song_id=song.id, tag_id=manual_tag.id, source="manual"))
    db.commit()

    sync_song_tags(db, song)
    db.commit()
    rows = db.query(SongTag).filter(SongTag.song_id == song.id, SongTag.source == "manual").all()
    assert len(rows) == 1, "重算把人工标签删掉了"
    db.close()


def test_usage_count_tracks_actual_rows(client):
    """usage_count 必须等于真实关联数（冗余计数漂移不会报错，只会让排序说谎）。"""
    h = _register(client)
    db = SessionLocal()
    uid = _uid(db, "search@test.com")
    _tagged(db, uid, "a", "国风 伤感")
    _tagged(db, uid, "b", "国风 欢快")
    db.close()

    r = client.get("/api/search/tags", params={"category": "genre"})
    assert r.status_code == 200, r.text
    by_value = {t["value"]: t for t in r.json()}
    assert by_value["guofeng"]["usage_count"] == 2

    db = SessionLocal()
    real = (
        db.query(SongTag)
        .join(Tag, Tag.id == SongTag.tag_id)
        .filter(Tag.category == "genre", Tag.value == "guofeng")
        .count()
    )
    assert by_value["guofeng"]["usage_count"] == real
    db.close()


# ═══════════════════════════════════════════════════════════════════════════
# 2. 标签浏览与多条件筛选
# ═══════════════════════════════════════════════════════════════════════════


def test_tags_endpoint_lists_candidates_anonymously(client):
    """标签候选匿名可读（词汇表本身不泄露任何用户内容）。"""
    h = _register(client)
    db = SessionLocal()
    _tagged(db, _uid(db, "search@test.com"), "x", "jazz 慵懒")
    db.close()

    r = client.get("/api/search/tags")
    assert r.status_code == 200
    assert any(t["value"] == "jazz" for t in r.json())
    for t in r.json():
        assert t["category"] in ("genre", "mood", "language", "vocal", "form")
        assert t["label"]


def test_filter_by_single_tag(client):
    h = _register(client)
    db = SessionLocal()
    uid = _uid(db, "search@test.com")
    guofeng = _tagged(db, uid, "国风歌", "国风 伤感")
    jazz = _tagged(db, uid, "爵士歌", "jazz 慵懒")
    db.close()

    r = client.get("/api/search/songs", params={"tag": "genre:guofeng"})
    assert r.status_code == 200, r.text
    ids = {x["id"] for x in r.json()}
    assert guofeng.id in ids and jazz.id not in ids


def test_filter_by_genre_and_mood_convenience_params(client):
    """`genre=` / `mood=` 便捷参数与 `tag=` 等价。"""
    h = _register(client)
    db = SessionLocal()
    uid = _uid(db, "search@test.com")
    target = _tagged(db, uid, "国风伤感", "国风 伤感")
    _tagged(db, uid, "国风欢快", "国风 欢快")
    db.close()

    r = client.get("/api/search/songs", params={"genre": "guofeng", "mood": "sad"})
    assert r.status_code == 200, r.text
    assert {x["id"] for x in r.json()} == {target.id}


def test_multiple_tags_are_intersection_not_union(client):
    """多标签 AND：加标签必须越筛越窄（OR 会越加越宽，与直觉相反）。"""
    h = _register(client)
    db = SessionLocal()
    uid = _uid(db, "search@test.com")
    both = _tagged(db, uid, "又国风又伤感", "国风 伤感")
    only_g = _tagged(db, uid, "只国风", "国风")
    db.close()

    one = client.get("/api/search/songs", params={"tag": ["genre:guofeng"]}).json()
    two = client.get(
        "/api/search/songs", params={"tag": ["genre:guofeng", "mood:sad"]}
    ).json()
    assert len(two) <= len(one)
    assert {x["id"] for x in two} == {both.id}
    assert only_g.id in {x["id"] for x in one}


def test_unknown_tag_in_filter_is_400(client):
    r = client.get("/api/search/songs", params={"tag": "genre:not-a-genre"})
    assert r.status_code == 400, r.text


def test_too_many_tags_is_400_not_empty_result(client):
    """超过上限要明确 400，而不是拼出一条注定返回空的 SQL。"""
    r = client.get(
        "/api/search/songs",
        params={
            "tag": [
                "genre:guofeng",
                "genre:jazz",
                "genre:rock",
                "genre:pop",
                "mood:sad",
                "mood:calm",
            ]
        },
    )
    assert r.status_code == 400, r.text


def test_filter_only_returns_public_visible_songs(client):
    """浏览面只见公开曲：未完成 / 未公开 / 已驳回 / 回收站里的歌一律不出现。"""
    h = _register(client)
    db = SessionLocal()
    uid = _uid(db, "search@test.com")
    ok = _tagged(db, uid, "可见", "国风")
    pending = _tagged(db, uid, "未完成", "国风", status="processing")
    private = _tagged(db, uid, "未公开", "国风", is_public=False)
    rejected = _tagged(db, uid, "已驳回", "国风", is_public=True, moderation_status="rejected")
    trashed = _tagged_orm(db, uid, "回收站", "国风")
    trashed.deleted_at = datetime.datetime.now(datetime.timezone.utc)
    db.commit()
    hidden = {pending.id, private.id, rejected.id, trashed.id}
    db.close()

    r = client.get("/api/search/songs", params={"tag": "genre:guofeng", "limit": 50})
    assert r.status_code == 200, r.text
    ids = {x["id"] for x in r.json()}
    assert ok.id in ids
    assert not (ids & hidden), "浏览面泄露了不可见作品"


def test_keyword_query_still_works_with_tags(client):
    """关键字与标签可组合：`q` 命中 style 原文（闭词表未命中时的后备路径）。"""
    h = _register(client)
    db = SessionLocal()
    uid = _uid(db, "search@test.com")
    _tagged(db, uid, "雨夜钢琴", "国风 伤感 钢琴")
    db.close()

    r = client.get("/api/search/songs", params={"tag": "genre:guofeng", "q": "雨夜"})
    assert r.status_code == 200
    assert r.json(), "关键字 + 标签组合把结果筛没了"


def test_sort_hot_matches_plaza_ordering(client):
    """hot 排序与广场一致：同一份热度公式，不出现"同页两个次序"。"""
    h = _register(client)
    db = SessionLocal()
    uid = _uid(db, "search@test.com")
    a = _tagged(db, uid, "冷门", "国风", play_count=1)
    b = _tagged(db, uid, "热门", "国风", play_count=100)
    db.close()

    mine = [x["id"] for x in client.get("/api/search/songs", params={"tag": "genre:guofeng"}).json()]
    plaza = [x["id"] for x in client.get("/api/plaza", params={"sort": "hot", "limit": 50}).json()]
    visible_plaza = [i for i in plaza if i in {a.id, b.id}]
    assert mine.index(b.id) < mine.index(a.id)
    assert visible_plaza.index(b.id) < visible_plaza.index(a.id)


# ═══════════════════════════════════════════════════════════════════════════
# 3. 相似推荐
# ═══════════════════════════════════════════════════════════════════════════


def test_similar_prefers_shared_tags_and_gives_reasons(client):
    """相似推荐：同标签排前，且每条都要给出理由。"""
    h = _register(client)
    db = SessionLocal()
    uid = _uid(db, "search@test.com")
    src = _tagged(db, uid, "源曲", "国风 伤感 钢琴")
    near = _tagged(db, uid, "很像", "国风 伤感 古筝")
    far = _tagged(db, uid, "不像", "jazz 慵懒 萨克斯")
    db.close()

    r = client.get(f"/api/search/songs/{src.id}/similar", params={"limit": 10})
    assert r.status_code == 200, r.text
    rows = r.json()
    ids = [x["id"] for x in rows]
    assert near.id in ids, "同标签的歌没被推荐出来"
    if far.id in ids:
        assert ids.index(near.id) < ids.index(far.id), "同标签的歌应排在不相关曲目前"
    assert all(isinstance(x["reasons"], list) for x in rows)
    assert any("genre:guofeng" in x["reasons"] for x in rows), "推荐理由缺失"


def test_similar_excludes_invisible_songs(client):
    """相似结果必须是已审核公开曲（不能把私密/未审/回收站的歌推荐出去）。"""
    h = _register(client)
    db = SessionLocal()
    uid = _uid(db, "search@test.com")
    src = _tagged(db, uid, "源曲", "jazz 慵懒")
    visible = _tagged(db, uid, "可见相似", "jazz 深夜")
    private = _tagged(db, uid, "私有相似", "jazz 深夜", is_public=False)
    rejected = _tagged(db, uid, "驳回相似", "jazz 深夜", moderation_status="rejected")
    pending = _tagged(db, uid, "未完成相似", "jazz 深夜", status="processing")
    trashed = _tagged_orm(db, uid, "回收相似", "jazz 深夜")
    trashed.deleted_at = datetime.datetime.now(datetime.timezone.utc)
    db.commit()
    hidden = {private.id, rejected.id, pending.id, trashed.id}
    db.close()

    rows = client.get(f"/api/search/songs/{src.id}/similar", params={"limit": 20}).json()
    ids = {x["id"] for x in rows}
    assert visible.id in ids
    assert not (ids & hidden), "相似推荐泄露了不可见作品"


def test_similar_on_invisible_source_is_404(client):
    """源曲自身不可见 → 404：否则这就是个"拿 id 探测私有歌"的接口。"""
    h = _register(client)
    db = SessionLocal()
    uid = _uid(db, "search@test.com")
    private = _tagged(db, uid, "私有", "国风", is_public=False)
    db.close()

    assert client.get(f"/api/search/songs/{private.id}/similar").status_code == 404
    assert client.get("/api/search/songs/999999/similar").status_code == 404


def test_similar_is_deterministic(client):
    """同一输入两次调用必须同序（排序无随机项；否则前端列表会抖动）。"""
    h = _register(client)
    db = SessionLocal()
    uid = _uid(db, "search@test.com")
    src = _tagged(db, uid, "源", "国风 伤感")
    for i in range(5):
        _tagged(db, uid, f"候选{i}", "国风 伤感")
    db.close()

    a = [x["id"] for x in client.get(f"/api/search/songs/{src.id}/similar").json()]
    b = [x["id"] for x in client.get(f"/api/search/songs/{src.id}/similar").json()]
    assert a == b


def test_similar_without_tags_falls_back_to_play_count(client):
    """无标签的冷启动库：兜底给热度池，端点不因"没标签"而永远空。（返回不含源曲）"""
    h = _register(client)
    db = SessionLocal()
    uid = _uid(db, "search@test.com")
    # 两首都是相同风格的文本，但**都不建标签**（模拟闭词表未命中的库）
    src = _mk_song(db, uid, title="无标签源", style="某种冷门风格")
    other = _mk_song(db, uid, title="另一首同类", style="某种冷门风格", play_count=50)
    db.close()

    r = client.get(f"/api/search/songs/{src.id}/similar")
    assert r.status_code == 200, r.text
    ids = [x["id"] for x in r.json()]
    assert src.id not in ids, "相似推荐把自己也推荐了"
    assert other.id in ids


# ═══════════════════════════════════════════════════════════════════════════
# 4. 时间窗趋势
# ═══════════════════════════════════════════════════════════════════════════


def _like_at(db, song_id: int, user_id: int, when: datetime.datetime) -> None:
    """写一条「某时刻的赞」。

    ⚠ song_likes 有 (song_id, user_id) 唯一约束（一人一首一赞，切换式）——
    要造 N 条赞必须来自 N 个**不同用户**；用一个用户循环插会直接 IntegrityError。
    """
    db.add(SongLike(song_id=song_id, user_id=user_id, created_at=when))


def _extra_users(db, n: int) -> list[int]:
    """批量造用户（只为凑够"多人的赞"）。"""
    out = []
    for i in range(n):
        u = User(email=f"trend{i}_{db.query(User).count()}@test.com", hashed_password="x")
        db.add(u)
        db.flush()
        out.append(u.id)
    db.commit()
    return out


def test_trending_uses_window_increment_not_lifetime_total(client):
    """趋势榜是**增量**：窗口外的赞不算，老歌的存量播放不能霸榜。"""
    h = _register(client)
    db = SessionLocal()
    uid = _uid(db, "search@test.com")
    old_star = _tagged(db, uid, "老爆款", "国风", play_count=100000)
    rising = _tagged(db, uid, "新上升", "国风", play_count=3)

    now = datetime.datetime.now(datetime.timezone.utc)
    others = _extra_users(db, 3)
    # 老爆款：40 天前的赞（7d 窗口外）
    _like_at(db, old_star.id, others[0], now - datetime.timedelta(days=40))
    # 新上升：昨天的赞（窗口内，3 个不同用户）
    for u in others:
        _like_at(db, rising.id, u, now - datetime.timedelta(days=1))
    db.commit()
    db.close()

    rows = client.get("/api/search/trending", params={"window": "7d", "limit": 10}).json()
    ids = [x["id"] for x in rows]
    assert rising.id in ids, "窗口内上升的歌没进趋势榜"
    assert old_star.id not in ids, "窗口外的历史赞把老歌顶进了趋势榜（累计当成了增量）"
    assert ids.index(rising.id) == 0


def test_trending_30d_window_includes_older_activity(client):
    """30d 窗口要比 7d 宽：同一批数据下 30d 至少不更少。"""
    h = _register(client)
    db = SessionLocal()
    uid = _uid(db, "search@test.com")
    song = _tagged(db, uid, "20天前火过", "jazz")
    now = datetime.datetime.now(datetime.timezone.utc)
    _like_at(db, song.id, _extra_users(db, 1)[0], now - datetime.timedelta(days=20))
    db.commit()
    db.close()

    assert song.id not in {x["id"] for x in client.get("/api/search/trending", params={"window": "7d"}).json()}
    assert song.id in {x["id"] for x in client.get("/api/search/trending", params={"window": "30d"}).json()}


def test_trending_excludes_invisible_and_bad_window_is_422(client):
    h = _register(client)
    db = SessionLocal()
    uid = _uid(db, "search@test.com")
    private = _tagged(db, uid, "私有", "国风", is_public=False)
    _like_at(db, private.id, _extra_users(db, 1)[0], datetime.datetime.now(datetime.timezone.utc))
    db.commit()
    db.close()

    rows = client.get("/api/search/trending").json()
    assert private.id not in {x["id"] for x in rows}
    assert client.get("/api/search/trending", params={"window": "1y"}).status_code == 422


def test_song_tags_endpoint_returns_structured_tags(client):
    h = _register(client)
    db = SessionLocal()
    uid = _uid(db, "search@test.com")
    song = _tagged(db, uid, "看看标签", "国风 伤感", make_instrumental=True)
    db.close()

    r = client.get(f"/api/search/songs/{song.id}/tags")
    assert r.status_code == 200, r.text
    got = {(t["category"], t["value"]) for t in r.json()}
    assert ("genre", "guofeng") in got
    assert ("mood", "sad") in got
    assert ("form", "instrumental") in got


# ═══════════════════════════════════════════════════════════════════════════
# 5. 端到端：真实生成流程必须产出标签（否则标签表永远只有测试塞的数据）
# ═══════════════════════════════════════════════════════════════════════════


def test_generation_pipeline_tags_completed_song(client):
    """★ 关键闭环 ★：走真实 /api/songs/generate → _process_song → 收尾建标签。

    本文件其余用例都是直接建表（把生成流程 mock 掉了），只钉检索语义。
    这一条专门钉「生产路径真的会建标签」—— 少它的话，接线断掉（比如把
    sync_song_tags 挪出 _finalize_song）时其它用例全绿，线上却一个标签都没有。
    """
    import asyncio

    from app.routers.songs import _process_song

    h = _register(client, "gap014e2e@test.com")
    r = client.post(
        "/api/songs/generate",
        json={"prompt": "失恋的雨夜", "style": "国风 伤感 钢琴", "is_public": True},
        headers=h,
    )
    assert r.status_code == 200, r.text
    sid = r.json()["song_id"]
    asyncio.run(_process_song(sid))

    rows = client.get(f"/api/search/songs/{sid}/tags")
    assert rows.status_code == 200, rows.text
    got = {(x["category"], x["value"]) for x in rows.json()}
    assert ("genre", "guofeng") in got, f"生成完成后没有抽出曲风标签: {got}"
    assert ("mood", "sad") in got, f"生成完成后没有抽出情绪标签: {got}"

    # 并且这些标签能真的用来浏览到这首歌（标签不是"存了但查不到"）
    listed = client.get("/api/search/songs", params={"tag": "genre:guofeng"})
    assert sid in {x["id"] for x in listed.json()}
