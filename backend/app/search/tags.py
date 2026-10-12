"""标签抽取（GAP-014）：把自由文本 `Song.style` 归并成结构化 `Tag`。

## 为什么是「闭词表 + 同义归并」而不是自由分词

标签是**浏览入口**，不是检索索引：
- 浏览要求「同一批歌都能被同一个词找到」。自由分词会把 `国风 伤感 钢琴`、
  `古筝 悲情`、`Chinese style` 拆成十几个只在单曲上出现的标签 —— 每个标签下
  只有 1 首歌，浏览界面等于不存在（长尾标签不是发现，是噪音）。
- 闭词表让 `/api/search/tags` 的候选列表是可预期的（前端可以直接铺成 chip）。

代价是闭词表覆盖面：未命中的风格词不会被结构化。这个代价是有意接受的 ——
未命中时**关键字搜索（q）依然能命中 style 原文**，用户不会「搜不到」，
只是那条路径不是标签浏览。宁可少一个标签，不要让标签表变成自由文本的副本。

## 词表来源（拒绝幻觉）

本模块的**已知标签词**全部取自本仓库已有的真实取值，不是凭空编的：
- 内容分类：`orchestrator/selection.py:DEFAULT_ROUTING` 的三个 category
  （general / christian_worship / instrumental）+ `models.Song.content_category` 注释；
- 结构化枚举：`Song.vocal_gender`（male/female）、`Song.make_instrumental`；
- 语言：`docs/MINIMAX_API_COMPLIANCE_2026-09-26.md` 记录的三语产品面（中/日/英）+ 韩（FLOW 主体用户）。

⚠ 刻意**不做** BPM / 调性（key）标签：上游 Mureka `/v1/song/generate` 返回体里
没有这两个字段（`orchestrator/mureka.py` 头部实证），librosa 级音频分析需要
真实音频 + 数十 MB 依赖，在 1g/1cpu 的共置主机上跑不动。臆造一个 `bpm=None`
的列并在 UI 上展示 = 假功能，故不建。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# ─────────────────────────────────────────────────────────────────────────────
# 规范标签表：canonical -> 命中该标签的别名（小写、去空格后比对）
# ⚠ 别名冲突会静默丢失（同一别名指向两个规范标签时后者被覆盖）—— 有单测钉住。
# ─────────────────────────────────────────────────────────────────────────────
GENRE_ALIASES: dict[str, tuple[str, ...]] = {
    # 华语传统 / 民族
    "guofeng": ("国风", "古风", "中国风", "chinese style", "chinoiserie", "중국풍"),
    "folk": ("民谣", "folk", "ballad", "木吉他", "어쿠스틱", "民歌"),
    "cpop": ("cpop", "c-pop", "华语流行", "国语流行", "中文流行"),
    "kpop": ("kpop", "k-pop", "한국流行", "韩国流行"),
    "jpop": ("jpop", "j-pop", "日语流行", "日系"),
    # 西方流行 / 摇滚
    "pop": ("pop", "流行", "팝"),
    "rock": ("rock", "摇滚", "金属", "metal", "失真吉他", "punk", "朋克"),
    "jazz": ("jazz", "爵士", "萨克斯", "saxophone", "bossa nova"),
    "rnb": ("rnb", "r&b", "节奏布鲁斯", "soul"),
    "hiphop": ("hiphop", "hip-hop", "说唱", "rap", "trap", "808"),
    "citypop": ("citypop", "city pop", "city-pop", "复古流行"),
    # 电子 / 氛围
    "edm": ("edm", "electronic", "电子", "电音", "synth", "合成器"),
    "house": ("house", "techno", "trance", "dj", "混音"),
    "lofi": ("lofi", "lo-fi", "低保真"),
    "ambient": ("ambient", "氛围", "环境音"),
    "newage": ("newage", "new age", "新世纪", "轻音乐"),
    # 宗教 / 场合
    "worship": (
        "worship",
        "praise",
        "christian",
        "찬양",
        "예배",
        "赞美",
        "敬拜",
        "基督教",
        "圣诗",
        "gospel",
        "福音",
    ),
    "christmas": ("christmas", "圣诞", "성탄", "크리스마스", "holiday"),
    "kids": ("kids", "儿歌", "童声", "少儿"),
    # 民族乐器（常作为国风的具象标记，单列便于精确浏览）
    "guqin": ("古筝", "古琴", "guzheng", "二胡", "琵琶"),
}

MOOD_ALIASES: dict[str, tuple[str, ...]] = {
    "sad": ("伤感", "悲伤", "悲情", "sad", "悲しい", "슬픈", "失恋", "忧伤", "triste", "melancholy"),
    "happy": ("欢快", "轻快", "happy", "upbeat", "开心", "明快", "愉快", "feliz"),
    "calm": ("舒缓", "安静", "calm", "放松", "relax", "治愈", "温柔", "静心", "peaceful"),
    "epic": ("史诗", "宏大", "epic", "激昂", "壮丽", "燃"),
    "romantic": ("浪漫", "romantic", "甜", "甜蜜", "사랑", "恋爱"),
    "dark": ("暗黑", "dark", "压抑", "阴郁", "悬疑"),
    "energetic": ("活力", "energetic", "热血", "动感", "激烈"),
}

LANGUAGE_ALIASES: dict[str, tuple[str, ...]] = {
    "zh": ("中文", "国语", "华语", "mandarin", "chinese", "汉语"),
    "en": ("英文", "英语", "english"),
    "ja": ("日文", "日语", "japanese", "日本語"),
    "ko": ("韩文", "韩语", "korean", "한국어"),
}

# 结构化标签（不从文本抽取，由 Song 的枚举列直接映射）—— 保证「同一件事只有一个名字」。
VOCAL_MALE = "male"
VOCAL_FEMALE = "female"
INSTRUMENTAL = "instrumental"

CATEGORY_ORDER: tuple[tuple[str, dict[str, tuple[str, ...]]], ...] = (
    ("genre", GENRE_ALIASES),
    ("mood", MOOD_ALIASES),
    ("language", LANGUAGE_ALIASES),
)
VALID_CATEGORIES: tuple[str, ...] = tuple(c for c, _ in CATEGORY_ORDER) + ("vocal", "form")

# 单曲标签数上限：标签浏览靠「重合度」排序，单曲挂太多标签会让所有歌都「相似」。
MAX_TAGS_PER_SONG = 12

# 标签来源（可追溯性）：auto=文本抽取，manual=用户/后台写入，enum=结构化列映射
SOURCE_AUTO = "auto"
SOURCE_MANUAL = "manual"
SOURCE_ENUM = "enum"

_SPLIT = re.compile(r"[\s,，、/|;；·\-_+&]+")


def _norm(token: str) -> str:
    """归一化比对键：小写、去首尾空白。"""
    return token.strip().lower()


# 反向索引：别名 -> (category, canonical)。重复别名会被后写覆盖，故用单测钉死唯一性。
_LOOKUP: dict[str, tuple[str, str]] = {}
for _cat, _table in CATEGORY_ORDER:
    for _canon, _aliases in _table.items():
        for _a in _aliases:
            _LOOKUP[_norm(_a)] = (_cat, _canon)


def known_aliases() -> dict[str, tuple[str, str]]:
    """暴露反向索引副本（测试用；调用方不应依赖其可变性）。"""
    return dict(_LOOKUP)


@dataclass(frozen=True)
class ExtractedTag:
    """一条抽取结果。category/value 是结构化查询键，source 说明它从哪来。"""

    category: str
    value: str
    source: str = SOURCE_AUTO


def _match_alias(token: str) -> tuple[str, str] | None:
    key = _norm(token)
    if not key:
        return None
    hit = _LOOKUP.get(key)
    if hit:
        return hit
    # 复合词兜底：`伤感情歌` / `piano-ballad` 这类没有分隔符的写法，
    # 逐别名做子串命中（取最长别名，避免 `pop` 抢先命中 `citypop`）。
    best: tuple[str, str] | None = None
    best_len = 0
    for alias, target in _LOOKUP.items():
        if len(alias) >= 2 and alias in key and len(alias) > best_len:
            best, best_len = target, len(alias)
    return best


def extract_tags(*texts: str | None) -> list[ExtractedTag]:
    """从若干自由文本里抽取结构化标签（去重、保序、有上限）。

    多个文本来源（style / prompt / 歌词）合并抽取：单看 style 会漏掉
    「写在 prompt 里的情绪词」，而这两处都是用户真实写过的内容。
    """
    out: list[ExtractedTag] = []
    seen: set[tuple[str, str]] = set()

    def _add(cat: str, value: str, source: str) -> None:
        key = (cat, value)
        if key in seen:
            return
        seen.add(key)
        out.append(ExtractedTag(cat, value, source))

    for text in texts:
        if not text:
            continue
        for token in _SPLIT.split(text):
            hit = _match_alias(token)
            if hit:
                _add(hit[0], hit[1], SOURCE_AUTO)
    return out[:MAX_TAGS_PER_SONG]


def enum_tags(song) -> list[ExtractedTag]:
    """结构化列 → 标签（`vocal_gender` / `make_instrumental` / `content_category`）。

    这些信息本来就在库里且有明确枚举，不该再从文本里"猜"一遍 ——
    同一件事（如纯音乐）有两个来源时，标签表就会出现两份真相。
    """
    out: list[ExtractedTag] = []
    if bool(getattr(song, "make_instrumental", False)):
        out.append(ExtractedTag("form", INSTRUMENTAL, SOURCE_ENUM))
    gender = (getattr(song, "vocal_gender", None) or "").strip().lower()
    if gender in (VOCAL_MALE, VOCAL_FEMALE):
        out.append(ExtractedTag("vocal", gender, SOURCE_ENUM))
    category = (getattr(song, "content_category", None) or "").strip().lower()
    if category == "christian_worship":
        # 与 GENRE_ALIASES.worship 同一个规范值：内容分类与风格标签在浏览面上是同一件事。
        out.append(ExtractedTag("genre", "worship", SOURCE_ENUM))
    return out[:MAX_TAGS_PER_SONG]


def tags_for_song(song) -> list[ExtractedTag]:
    """单曲的完整标签集：文本抽取 + 结构化枚举（合并去重、受上限约束）。"""
    merged: list[ExtractedTag] = []
    seen: set[tuple[str, str]] = set()
    for tag in enum_tags(song) + extract_tags(song.style, song.prompt, song.title):
        key = (tag.category, tag.value)
        if key in seen:
            continue
        seen.add(key)
        merged.append(tag)
    return merged[:MAX_TAGS_PER_SONG]


def normalize_tag(category: str | None, value: str | None) -> tuple[str, str] | None:
    """把外部传入的 (category, value) 归一成表内规范值；非法/未知返回 None。

    ⚠ 未知值**不落库**（不新造标签）：手动写标签也只在闭词表内，
    否则闭词表的意义（可预期的浏览入口）立刻消失。
    """
    cat = (category or "").strip().lower()
    val = _norm(value or "")
    if not cat or not val:
        return None
    if cat in ("vocal", "form"):
        allowed = {
            "vocal": (VOCAL_MALE, VOCAL_FEMALE),
            "form": (INSTRUMENTAL,),
        }[cat]
        return (cat, val) if val in allowed else None
    for c, table in CATEGORY_ORDER:
        if c != cat:
            continue
        for canon, aliases in table.items():
            if val == canon or val in {_norm(a) for a in aliases}:
                return (c, canon)
        return None
    return None
