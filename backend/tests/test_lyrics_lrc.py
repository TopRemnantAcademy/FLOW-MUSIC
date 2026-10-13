"""GAP-012 歌词编辑器 / LRC 导出导入 / 改词后重对齐 —— 真实样例断言。

分两层：
  A. 纯函数层（app/lyrics/lrc.py）：时间格式、解析容错、导出可解析性、重对齐策略、
     **往返（build → parse）等价**、边界（空行 / 长时轴 / 多段 / 无时标 / 一行多时标 / offset）；
  B. HTTP 层：/lyrics/doc、/lyrics.lrc、/lyrics/import、PUT /lyrics、/lyrics/realign，
     含权限（非 owner 404、匿名只读公开曲）与「保存后前端高亮仍在容差内」。

数据源统一走 **mock provider 的镜像结构**（orchestrator/mock.py:68 aligned_lyrics 与
mureka.py:498-499 同契约的 lyrics_sections），不碰真实网络。
"""

import json

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.lyrics import lrc as lrcx
from app.lyrics.lrc import (
    build_lrc,
    extract_plain_lines,
    normalize_timeline,
    parse_lrc,
    realign_sync,
    sections_to_lines,
)
from app.models import Song, User

# ── 真实样例：与 tests/test_stage2_mock_e2e.py:145 的假 Mureka 响应同形状 ────────
# 真机 Mureka choices[].lyrics_sections 实测字段（mureka.py:498 注释）：
#   段 {section_type, start, end, lines[]} → 行 {start, end, text, words[]} → 词 {start, end, text}
REAL_SYNC = [
    {
        "section_type": "verse",
        "start": 0,
        "end": 8000,
        "lines": [
            {
                "start": 0,
                "end": 4000,
                "text": "夜色落在窗台",
                "words": [
                    {"start": 0, "end": 1000, "text": "夜色"},
                    {"start": 1000, "end": 2000, "text": "落在"},
                    {"start": 2000, "end": 4000, "text": "窗台"},
                ],
            },
            {"start": 4000, "end": 8000, "text": "风把灯吹歪"},
        ],
    },
    {
        "section_type": "chorus",
        "start": 8000,
        "end": 16000,
        "lines": [
            {"start": 8000, "end": 12000, "text": "我听见你在唱"},
            {"start": 12000, "end": 16000, "text": "唱到天也亮"},
        ],
    },
]


# ══════════════════════════════════════════════════════════════════════════════
# A · 纯函数层
# ══════════════════════════════════════════════════════════════════════════════
def test_format_time_boundaries():
    assert lrcx.format_time(0) == "00:00.00"
    assert lrcx.format_time(4000) == "00:04.00"
    assert lrcx.format_time(61_230) == "01:01.23"
    # 长时轴：>99 分钟继续进位（mm 三位），不能回绕成 03:xx
    assert lrcx.format_time(3_600_000) == "60:00.00"
    assert lrcx.format_time(372_345) == "06:12.34"
    # 负值（脏数据）钳到 0，绝不产出 "[−1:..]"
    assert lrcx.format_time(-500) == "00:00.00"


def test_parse_time_fraction_weight():
    """小数位按**位权**解释：.5=500ms / .05=50ms / .005=5ms。

    这是各家 LRC 解析器最常见的偏差源（把 "5" 当 50ms），也是往返等价的前提。
    """
    assert lrcx.parse_time("00", "04", "00") == 4000
    assert lrcx.parse_time("00", "04", "5") == 4500
    assert lrcx.parse_time("00", "04", "05") == 4050
    assert lrcx.parse_time("00", "04", "005") == 4005
    assert lrcx.parse_time("01", "30", None) == 90_000


def test_sections_to_lines_from_real_mureka_shape():
    lines = sections_to_lines(REAL_SYNC)
    assert [x.start for x in lines] == [0, 4000, 8000, 12000]
    assert [x.text for x in lines] == ["夜色落在窗台", "风把灯吹歪", "我听见你在唱", "唱到天也亮"]
    # 段级 section_type 摊到行上 —— 编辑器据此分组
    assert [x.section for x in lines] == ["verse", "verse", "chorus", "chorus"]
    assert lines[0].end == 4000


def test_sections_to_lines_tolerates_json_string_and_flat_shapes():
    """前端 flattenSync 有多宽容，后端解析器就必须同等宽容。

    否则会出现「前端显示得出来、导出 LRC 却空文件」的诡异不一致。
    """
    as_str = json.dumps(REAL_SYNC, ensure_ascii=False)
    assert len(sections_to_lines(as_str)) == 4
    # 平铺行数组（无段落包裹）
    flat = [{"start": 0, "end": 1000, "text": "a"}, {"start": 1000, "end": 2000, "text": "b"}]
    assert [x.text for x in sections_to_lines(flat)] == ["a", "b"]
    # 别名字段（startTime/begin/word/content）
    aliased = [{"lines": [{"startTime": 500, "endTime": 900, "content": "x"}]}]
    got = sections_to_lines(aliased)
    assert got[0].start == 500 and got[0].text == "x"
    # 行缺时标 → 用首词/末词兜底
    from_words = [{"lines": [{"text": "k", "words": [{"start": 200, "end": 400, "text": "k"}]}]}]
    assert sections_to_lines(from_words)[0].start == 200
    # 垃圾输入不炸
    assert sections_to_lines(None) == []
    assert sections_to_lines("not json") == []
    assert sections_to_lines({"unexpected": 1}) == []


def test_build_lrc_is_standard_and_reparsable():
    """导出必须能被**普通** LRC 解析器（foobar2000 口径）正确解析。"""
    lines = sections_to_lines(REAL_SYNC)
    text = build_lrc(lines, title="夜之歌", artist="朴素", duration_s=16)
    body = text.splitlines()
    assert "[ti:夜之歌]" in body
    assert "[ar:朴素]" in body
    assert "[length:00:16.00]" in body
    assert "[00:00.00]夜色落在窗台" in body
    assert "[00:08.00]我听见你在唱" in body
    # 只输出行首标准时标：不得出现增强型逐字标记 <mm:ss.xx>（第三方支持面极窄）
    assert "<00:" not in text and "<0" not in text
    # 段落标签不写进文件（部分解析器会当脏数据丢/直接显示）
    assert "[Verse" not in text and "[Chorus" not in text
    # 往返：再解析回来，时间与文本必须一一等价
    doc = parse_lrc(text)
    assert [(x.start, x.text) for x in doc.lines] == [(x.start, x.text) for x in lines]
    assert doc.meta["ti"] == "夜之歌"


def test_build_lrc_roundtrip_on_generated_grid():
    """密集网格往返（0..300s 每 100ms）：覆盖长时轴 + 百分秒进位。"""
    lines = [lrcx.LrcLine(start=i * 100, text=f"L{i}") for i in range(3000)]
    doc = parse_lrc(build_lrc(lines))
    assert len(doc.lines) == 3000
    assert [x.start for x in doc.lines] == [x.start for x in lines]


def test_parse_lrc_handles_blank_lines_and_multi_tags():
    text = "\n".join(
        [
            "[ti:T]",
            "",
            "[00:00.00]",  # 空行歌词（间奏占位）：合法且常见
            "[00:05.00]第一句",
            "   ",
            "[00:10.00][00:20.00]副歌重复句",  # 一行多时标 → 展开成两行
            "[00:30.00]尾句",
        ]
    )
    doc = parse_lrc(text)
    assert [(x.start, x.text) for x in doc.lines] == [
        (0, ""),
        (5000, "第一句"),
        (10_000, "副歌重复句"),
        (20_000, "副歌重复句"),
        (30_000, "尾句"),
    ]
    assert doc.meta["ti"] == "T"


def test_parse_lrc_section_labels_and_offset():
    text = "[offset:-500]\n[Verse 1]\n[00:01.00]a\n[Chorus]\n[00:03.00]b\n"
    doc = parse_lrc(text)
    # [offset:-500] = 时间轴整体后移 500ms
    assert [(x.start, x.section) for x in doc.lines] == [(1500, "verse"), (3500, "chorus")]


def test_parse_lrc_keeps_untimed_lines_with_warning():
    """无时标纯文本行**不能丢**（用户至少要看得到内容），但必须显式告警。"""
    doc = parse_lrc("这是一首没有时间轴的歌词\n第二行\n[00:10.00]真正的定时行")
    assert any("无时间轴" in w for w in doc.warnings)
    assert ("这是一首没有时间轴的歌词" in [x.text for x in doc.lines])
    assert (10_000, "真正的定时行") in [(x.start, x.text) for x in doc.lines]


def test_extract_plain_lines_drops_section_labels():
    raw = "[Verse 1]\n第一句\n\n[Chorus]\n第二句\n"
    assert extract_plain_lines(raw) == ["第一句", "第二句"]


def test_realign_one_to_one_preserves_timeline():
    """行数不变 → 时间轴**原样保留**（改字不改时，最该保真的情形）。"""
    new_words = ["夜色落在窗台", "风把灯吹歪", "我听见你在唱", "唱到天也亮"]
    out = realign_sync(REAL_SYNC, new_words, duration=16)
    assert [x["start"] for x in out] == [0, 4000, 8000, 12000]
    assert [x["text"] for x in out] == new_words
    assert [x["section"] for x in out] == ["verse", "verse", "chorus", "chorus"]
    assert all(x["aligned"] for x in out)


def test_realign_shrink_and_grow_stay_inside_song():
    """行数变了也不失配：时点仍单调不减、落在 [0, duration] 内。"""
    shrink = realign_sync(REAL_SYNC, ["合并成一句"], duration=16)
    assert len(shrink) == 1 and shrink[0]["start"] == 0

    grow = realign_sync(REAL_SYNC, ["一", "二", "三", "四", "五", "六", "七"], duration=16)
    starts = [x["start"] for x in grow]
    assert starts == sorted(starts)  # 单调不减 → 播放器高亮不会回跳
    assert starts[0] == 0 and starts[-1] < 16_000
    assert all(0 <= x["start"] <= 16_000 for x in grow)


def test_realign_without_timeline_never_fakes_one():
    """没有旧时间轴时**绝不伪造均匀时轴**（那是编数据），而是明确标记 aligned=False。"""
    out = realign_sync(None, ["a", "b"])
    assert out == [
        {"start": 0, "end": None, "text": "a", "aligned": False},
        {"start": 0, "end": None, "text": "b", "aligned": False},
    ]


def test_normalize_timeline_fixes_overlap_and_bad_end():
    """编辑器允许自由编辑 → 入库前必须收敛（重叠/倒序/end<start 会让高亮来回跳）。"""
    rows = normalize_timeline(
        [
            {"start": 9000, "end": 9500, "text": "后"},
            {"start": 1000, "end": 1200, "text": "前"},
            {"start": 3000, "end": None, "text": "中"},
        ]
    )
    assert [x["start"] for x in rows] == [1000, 3000, 9000]
    # 行序已升序，且任何一行都不越过下一行（不跨界抢占）
    for a, b in zip(rows, rows[1:]):
        assert a["end"] <= b["start"]
        assert a["end"] > a["start"]
    # 中行缺 end → 吃到下一行起点（LRC 常规语义）；末行 end 缺失 → 用已知 duration 兜底
    assert rows == [
        {"start": 1000, "end": 1200, "text": "前", "section": "verse"},
        {"start": 3000, "end": 9000, "text": "中", "section": "verse"},
        {"start": 9000, "end": 9500, "text": "后", "section": "verse"},
    ]
    # duration 未知时末行才退到 2s 最小值（不是 0）
    no_dur = normalize_timeline([{"start": 3000, "text": "孤行"}])
    assert no_dur[-1]["end"] == 5000


def test_normalize_timeline_tail_uses_duration_when_known():
    """末行 end 已知曲长时必须吃到曲末 —— 否则导出后播放器在曲末 10s 就停在高亮最后一句。"""
    rows = normalize_timeline([{"start": 0, "text": "唯一句"}], duration_s=16)
    assert rows[0]["end"] == 16_000


# ══════════════════════════════════════════════════════════════════════════════
# B · HTTP 层
# ══════════════════════════════════════════════════════════════════════════════
def _auth(client: TestClient, email: str) -> dict:
    pw = "Password123!"
    client.post("/api/auth/register", json={"email": email, "password": pw})
    r = client.post("/api/auth/login", json={"email": email, "password": pw})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _mk_song(email: str, *, sync=None, lyric="夜色落在窗台\n风把灯吹歪", public=False) -> int:
    db = SessionLocal()
    try:
        u = db.query(User).filter(User.email == email).first()
        s = Song(
            user_id=u.id,
            title="夜之歌",
            status="completed",
            audio_url="https://cdn.test/a.mp3",
            lyric=lyric,
            duration=16,
            lyrics_sync=json.dumps(sync, ensure_ascii=False) if sync is not None else None,
            is_public=public,
            task_type="generate",
        )
        db.add(s)
        db.commit()
        db.refresh(s)
        return s.id
    finally:
        db.close()


def _row(song_id: int) -> Song:
    db = SessionLocal()
    try:
        return db.get(Song, song_id)
    finally:
        db.close()


def test_doc_endpoint_exposes_lines_and_paragraphs(client):
    h = _auth(client, "g12-doc@test.com")
    sid = _mk_song("g12-doc@test.com", sync=REAL_SYNC)
    r = client.get(f"/api/songs/{sid}/lyrics/doc", headers=h)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["has_timeline"] is True
    assert d["can_edit"] is True
    assert [x["start"] for x in d["lines"]] == [0, 4000, 8000, 12000]
    assert [x["section"] for x in d["lines"]] == ["verse", "verse", "chorus", "chorus"]
    assert d["word_count"] == 3  # 只有真机那条行带 words
    assert d["duration"] == 16


def test_doc_endpoint_without_timeline_falls_back_to_plain_text(client):
    h = _auth(client, "g12-plain@test.com")
    sid = _mk_song("g12-plain@test.com", sync=None, lyric="第一句\n第二句")
    d = client.get(f"/api/songs/{sid}/lyrics/doc", headers=h).json()
    assert d["has_timeline"] is False
    assert [x["text"] for x in d["lines"]] == ["第一句", "第二句"]
    assert all(x["start"] == 0 for x in d["lines"])


def test_lrc_download_is_parseable_and_has_proper_headers(client):
    h = _auth(client, "g12-lrc@test.com")
    sid = _mk_song("g12-lrc@test.com", sync=REAL_SYNC)
    r = client.get(f"/api/songs/{sid}/lyrics.lrc", headers=h)
    assert r.status_code == 200, r.text
    assert "text/plain" in r.headers["content-type"]
    assert r.headers["x-lyrics-source"] == "lyrics_sync"
    # 非 ASCII 标题必须走 RFC 5987 双写法，且 ASCII 兜底里不得出现裸中文
    cd = r.headers["content-disposition"]
    assert "filename*=" in cd and "UTF-8''" in cd
    assert 'filename="' in cd
    # 正文字节→文本后能被标准解析器吃回去
    doc = parse_lrc(r.content.decode("utf-8"))
    assert [x.start for x in doc.lines] == [0, 4000, 8000, 12000]
    assert doc.meta["ti"] == "夜之歌"


def test_lrc_download_without_timeline_is_plain_not_broken(client):
    """无时间轴 → 给纯文本歌词文件（带说明头），而不是空文件/404。"""
    h = _auth(client, "g12-lrcplain@test.com")
    sid = _mk_song("g12-lrcplain@test.com", sync=None, lyric="只有词\n没有时间轴")
    r = client.get(f"/api/songs/{sid}/lyrics.lrc", headers=h)
    assert r.status_code == 200
    assert r.headers["x-lyrics-source"] == "plain"
    body = r.content.decode("utf-8")
    assert "只有词" in body and "[re:" in body
    assert "[00:00.00]" not in body  # 不塞 0 时标（会让播放器把全部行挤在开头闪）


def test_lrc_download_allowed_for_anonymous_public_song_only(client):
    """公开曲的 .lrc 对匿名放开（第三方播放器/脚本不该为了字幕去登录）；
    非公开曲对非 owner 一律 404（连地址都不认）。"""
    owner = _auth(client, "g12-vis@test.com")
    stranger = _auth(client, "g12-vis-stranger@test.com")
    public_id = _mk_song("g12-vis@test.com", sync=REAL_SYNC, public=True)
    private_id = _mk_song("g12-vis@test.com", sync=REAL_SYNC, public=False)
    assert client.get(f"/api/songs/{public_id}/lyrics.lrc").status_code == 200
    assert client.get(f"/api/songs/{public_id}/lyrics.lrc", headers=stranger).status_code == 200
    assert client.get(f"/api/songs/{private_id}/lyrics.lrc", headers=stranger).status_code == 404
    assert client.get(f"/api/songs/{private_id}/lyrics.lrc", headers=owner).status_code == 200


def test_import_lrc_saves_lines_and_words(client):
    h = _auth(client, "g12-imp@test.com")
    sid = _mk_song("g12-imp@test.com", sync=None, lyric="旧词")
    imported = "[ti:新]\n[Verse 1]\n[00:02.00]新第一句\n[00:06.50]新第二句\n[Chorus]\n[00:12.00]新副歌\n"
    r = client.post(f"/api/songs/{sid}/lyrics/import", headers=h, json={"lrc": imported})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["parsed_lines"] == 3 and d["saved"] is True
    assert [x["start"] for x in d["imported_lines"]] == [2000, 6500, 12_000]
    assert [x["section"] for x in d["imported_lines"]] == ["verse", "verse", "chorus"]

    row = _row(sid)
    assert row.lyric == "新第一句\n新第二句\n新副歌"
    # 落库形状 = mureka 的段结构（消费侧只需一套解析）
    stored = json.loads(row.lyrics_sync)
    assert [s["section_type"] for s in stored] == ["verse", "chorus"]
    assert stored[0]["lines"][0]["start"] == 2000
    # 行级时标 → 词级时点已补齐（播放器逐字高亮有东西可跟）
    words = next(l for l in stored[0]["lines"] if l["text"] == "新第一句")["words"]
    assert [w["text"] for w in words] == ["新", "第", "一", "句"]
    assert words[0]["start"] == 2000 and words[-1]["end"] == 6500

    # 导出 → 再导入 的往返必须稳定（同一份时轴）
    exported = client.get(f"/api/songs/{sid}/lyrics.lrc", headers=h).content.decode("utf-8")
    doc = parse_lrc(exported)
    assert [(x.start, x.text) for x in doc.lines] == [
        (2000, "新第一句"),
        (6500, "新第二句"),
        (12_000, "新副歌"),
    ]


def test_import_rejects_garbage(client):
    h = _auth(client, "g12-bad@test.com")
    sid = _mk_song("g12-bad@test.com")
    r = client.post(f"/api/songs/{sid}/lyrics/import", headers=h, json={"lrc": "   \n\n"})
    assert r.status_code == 400


def test_save_edited_lines_persists_user_timings(client):
    h = _auth(client, "g12-save@test.com")
    sid = _mk_song("g12-save@test.com", sync=REAL_SYNC)
    body = {
        "lyric": "",
        "lines": [
            {"start": 500, "end": 3000, "text": "改过的第一句", "section": "verse"},
            {"start": 3000, "end": 7000, "text": "改过的第二句", "section": "chorus"},
        ],
    }
    r = client.put(f"/api/songs/{sid}/lyrics", headers=h, json=body)
    assert r.status_code == 200, r.text
    d = r.json()
    assert [x["start"] for x in d["lines"]] == [500, 3000]
    row = _row(sid)
    assert row.lyric == "改过的第一句\n改过的第二句"
    assert json.loads(row.lyrics_sync)[1]["section_type"] == "chorus"


def test_save_text_only_realigns_to_existing_timeline(client):
    """**核心场景**：用户只改了文字（行数不变）→ 时间轴必须原样保住。"""
    h = _auth(client, "g12-realign@test.com")
    sid = _mk_song("g12-realign@test.com", sync=REAL_SYNC)
    new_text = "夜色落在窗台啊\n风把灯吹歪了\n我听见你在唱着\n唱到天也亮了"
    r = client.put(f"/api/songs/{sid}/lyrics", headers=h, json={"lyric": new_text, "realign": True})
    assert r.status_code == 200, r.text
    d = r.json()
    assert [x["text"] for x in d["lines"]] == new_text.split("\n")
    assert [x["start"] for x in d["lines"]] == [0, 4000, 8000, 12000]
    assert d["has_timeline"] is True

    # 播放器高亮不失配：导出时轴与原曲逐行一致（容差 0ms —— 一行一位，不是近似）
    exported = client.get(f"/api/songs/{sid}/lyrics.lrc", headers=h).content.decode("utf-8")
    doc = parse_lrc(exported)
    assert [(x.start, x.text) for x in doc.lines] == [
        (0, "夜色落在窗台啊"),
        (4000, "风把灯吹歪了"),
        (8000, "我听见你在唱着"),
        (12_000, "唱到天也亮了"),
    ]


def test_save_text_only_with_realign_false_drops_timeline(client):
    """明确要求「丢弃时间轴」时要真的丢 —— 否则用户无法从有轴退回纯文本。

    ⚠ 与 realign=True 的区别就是这一条：路 B 保住旧时轴，路 C 把它归零。
    两条路都必须可走，否则「改字」和「重新定时」在 UI 上无法表达。
    """
    h = _auth(client, "g12-drop@test.com")
    sid = _mk_song("g12-drop@test.com", sync=REAL_SYNC)
    r = client.put(f"/api/songs/{sid}/lyrics", headers=h, json={"lyric": "只剩一句", "realign": False})
    assert r.status_code == 200, r.text
    assert r.json()["has_timeline"] is False
    assert r.json()["lyric"] == "只剩一句"
    assert _row(sid).lyrics_sync is None


def test_save_empty_is_rejected(client):
    h = _auth(client, "g12-empty@test.com")
    sid = _mk_song("g12-empty@test.com", sync=REAL_SYNC)
    assert client.put(f"/api/songs/{sid}/lyrics", headers=h, json={"lyric": "  "}).status_code == 400


def test_save_text_only_without_timeline_does_not_fake_timings(client):
    """无旧轴时**绝不伪造时轴**：歌词照常落库，lyrics_sync 留空（导出走纯文本）."""
    h = _auth(client, "g12-notl@test.com")
    sid = _mk_song("g12-notl@test.com", sync=None, lyric="旧词")
    r = client.put(f"/api/songs/{sid}/lyrics", headers=h, json={"lyric": "新词一\n新词二"})
    assert r.status_code == 200, r.text
    assert r.json()["has_timeline"] is False
    assert _row(sid).lyric == "新词一\n新词二"
    assert _row(sid).lyrics_sync is None


def test_realign_endpoint_requires_timeline(client):
    h = _auth(client, "g12-rl@test.com")
    sid = _mk_song("g12-rl@test.com", sync=None, lyric="旧词")
    assert client.post(f"/api/songs/{sid}/lyrics/realign", headers=h).status_code == 409

    # 有轴：按**当前库内歌词行数**重排（_mk_song 的歌词是两行 → 重采样成两行）
    sid2 = _mk_song("g12-rl@test.com", sync=REAL_SYNC)
    r = client.post(f"/api/songs/{sid2}/lyrics/realign", headers=h)
    assert r.status_code == 200, r.text
    starts = [x["start"] for x in r.json()["lines"]]
    assert r.json()["has_timeline"] is True
    assert len(starts) == 2 and starts == sorted(starts) and starts[0] == 0
    assert starts[-1] < 16_000


def test_editing_is_owner_only(client):
    owner = _auth(client, "g12-owner@test.com")
    other = _auth(client, "g12-other@test.com")
    sid = _mk_song("g12-owner@test.com", sync=REAL_SYNC)

    # 非 owner：读 doc / 写 / 导入 / 重对齐一律 404（**不泄露「这首歌存在」**）
    assert client.get(f"/api/songs/{sid}/lyrics/doc", headers=other).status_code == 404
    assert client.put(f"/api/songs/{sid}/lyrics", headers=other, json={"lyric": "x"}).status_code == 404
    assert client.post(f"/api/songs/{sid}/lyrics/import", headers=other, json={"lrc": "[00:01.00]x"}).status_code == 404
    assert client.post(f"/api/songs/{sid}/lyrics/realign", headers=other).status_code == 404
    # 写端点必须认证：非法 token → 401（由 get_current_user 依赖在取歌前拦下，
    # 与「歌不存在」的 404 区分开 —— 前者让用户去检查登录态，后者才去检查作品列表）
    bad = {"Authorization": "Bearer not-a-real-token"}
    assert client.put(f"/api/songs/{sid}/lyrics", headers=bad, json={"lyric": "x"}).status_code == 401
    # owner 能读能写
    assert client.get(f"/api/songs/{sid}/lyrics/doc", headers=owner).status_code == 200
    assert client.put(f"/api/songs/{sid}/lyrics", headers=owner, json={"lyric": "新词"}).status_code == 200


def test_import_respects_line_and_length_limits(client):
    h = _auth(client, "g12-lim@test.com")
    sid = _mk_song("g12-lim@test.com")
    huge = "\n".join(f"[{i // 60:02d}:{i % 60:02d}.00]line{i}" for i in range(3200))
    assert client.post(f"/api/songs/{sid}/lyrics/import", headers=h, json={"lrc": huge}).status_code == 400
    long_text = "\n".join("很长的一句歌词内容" for _ in range(600))
    assert client.post(f"/api/songs/{sid}/lyrics/import", headers=h, json={"lrc": long_text}).status_code == 400


def test_lrc_meta_injection_is_sanitized(client):
    """标题里的 `]`/换行会提前闭合标签污染整个文件头 → 必须清洗（注入面）。"""
    h = _auth(client, "g12-inj@test.com")
    sid = _mk_song("g12-inj@test.com", sync=REAL_SYNC)
    row = _row(sid)
    row.title = "恶意]标题\n[00:99.99]假行"
    db = SessionLocal()
    try:
        db.get(Song, sid).title = row.title
        db.commit()
    finally:
        db.close()
    body = client.get(f"/api/songs/{sid}/lyrics.lrc", headers=h).content.decode("utf-8")
    assert "[ti:恶意 标题 00:99.99 假行]" in body
    assert "\n[00:99.99]" not in body  # 假行没被伪造成一条独立歌词行
    assert len(parse_lrc(body).lines) == 4  # 仍是原来的 4 行


def test_mock_provider_shape_is_accepted_by_exporter(client):
    """铁律：契约以 mock 的镜像实现为准 —— mock.py:68 的 `[00:00] {lyrics}` 形态也要能解析。

    （Suno 风格 aligned_lyrics 返回的是一整行 LRC 文本，不是 lyrics_sections；
     这里验证我们的容错解析器读得进，避免「换了引擎导出就空」的隐藏耦合。）
    """
    doc = parse_lrc("[00:00] 恩典够我用")
    assert len(doc.lines) == 1 and doc.lines[0].start == 0
    as_sections = [{"section_type": "verse", "lines": [{"start": 0, "text": "恩典够我用"}]}]
    text = build_lrc(sections_to_lines(as_sections), title="T")
    assert "[00:00.00]恩典够我用" in text
