"""提示词 / 歌词 本地优化器（确定性规则，无外部 LLM 依赖）。

为什么是本地规则而不是 LLM（2026-09-10 核对官方文档后的判断）：
- Mureka 官方 API **只有** ``POST /v1/lyrics/generate`` 一个 AI 辅助端点
  （``prompt`` → ``{title, lyrics}``），**没有**“风格/提示词优化”端点。
- ``POST /v1/song/generate`` 的 ``prompt`` 是**自由文本**（≤1024 字符），官方示例
  ``"r&b, slow, passionate, male vocal"`` = 曲风 + 速度 + 情绪 + 人声，**逗号分隔**。
  ⇒ 结论：风格**不是越详细越好，而是越结构化越好**——
    逗号分隔的短描述符 远胜 一整段自然语言。
- ``POST /v1/song/generate`` 的 ``lyrics`` ≤5000 字符。

故「一键优化」在此落地为两件确定性事件：
  1) **对齐 align**：结构标签规范化、空行收敛、缩进统一。
  2) **去冗余 prune**：删空行、删填充词、去重、截断超限文本。
确定性 = 可测试 / 可复现 / 零成本 / **不产生幻觉**（不会把用户歌词改写成别的意思）。
"""

from __future__ import annotations

import re

# ── Mureka 官方约束（platform.mureka.ai/docs/api/operations/post-v1-song-generate.html）──
MAX_PROMPT_CHARS = 1024
MAX_LYRIC_CHARS = 5000

# easy-generate 的结构化 styles 枚举（仅该端点有；本产品走 generate，故仅用于**归一化提示**，
# 不作为校验——generate 的 prompt 是自由文本，用户可写 国风/古风 等枚举外词）
STYLE_ENUM = (
    "pop",
    "rock",
    "jazz",
    "r&b",
    "edm",
    "ambient",
    "folk",
    "latin",
    "k-pop",
    "j-pop",
    "house",
    "gospel",
    "lo-fi",
)

# 中文/常见写法 → 官方枚举或英文规范写法（用于把用户自然语言收敛成短描述符）
_STYLE_SYNONYMS: dict[str, str] = {
    "流行": "pop",
    "流行乐": "pop",
    "通俗": "pop",
    "摇滚": "rock",
    "摇滚乐": "rock",
    "爵士": "jazz",
    "爵士乐": "jazz",
    "节奏布鲁斯": "r&b",
    "节奏蓝调": "r&b",
    "rnb": "r&b",
    "rb": "r&b",
    "布鲁斯": "r&b",
    "电子": "edm",
    "电子乐": "edm",
    "电音": "edm",
    "舞曲": "edm",
    "氛围": "ambient",
    "环境音乐": "ambient",
    "氛围音乐": "ambient",
    "民谣": "folk",
    "乡村": "folk",
    "拉丁": "latin",
    "韩流": "k-pop",
    "韩国流行": "k-pop",
    "kpop": "k-pop",
    "日系": "j-pop",
    "日本流行": "j-pop",
    "jpop": "j-pop",
    "浩室": "house",
    "浩室音乐": "house",
    "福音": "gospel",
    "赞美": "gospel",
    "敬拜": "gospel",
    "诗歌": "gospel",
    "低保真": "lo-fi",
    "洛菲": "lo-fi",
    "lofi": "lo-fi",
    # 人声/速度/情绪类规范写法
    "男声": "male vocal",
    "男生": "male vocal",
    "男嗓": "male vocal",
    "女声": "female vocal",
    "女生": "female vocal",
    "女嗓": "female vocal",
    "慢": "slow",
    "慢速": "slow",
    "抒情": "slow",
    "快": "upbeat",
    "快速": "upbeat",
    "欢快": "upbeat",
    "钢琴": "piano",
    "吉他": "guitar",
    "弦乐": "strings",
    "鼓": "drums",
    "合成器": "synth",
    "电子琴": "synth",
    "伤感": "melancholic",
    "悲伤": "melancholic",
    "治愈": "healing",
    "温柔": "gentle",
    "轻柔": "gentle",
    "柔和": "gentle",
    "温暖": "warm",
    "激情": "passionate",
    "深情": "passionate",
}

# 「子串型」速度/情绪词：中文常写成「慢一点」「不要太快」，全等匹配命中不了，
# 故单独按**子串**映射。⚠ 否定式保护必须存在 —— 「不要太快」推出 upbeat 는 명백한 오역.
_SUBSTRING_HINTS: list[tuple[str, str]] = [
    ("慢", "slow"),
    ("快", "upbeat"),
]
_NEGATION_RE = re.compile(r"(不\s*(要|太|用|必|至)|别\s*(太|要)?|无需|不要)")

# 中文语气助词：剥离后为空/单字者 = 무의미 잔재（「唱的」→「唱」→ 폐기）
_CJK_PARTICLES = set("的了着过吧呢啊哦呀啦嘛么呐咯呗喽")

# 无信息量的填充词（删掉不损失风格语义）
_FILLER = {
    "的",
    "了",
    "着",
    "吧",
    "呢",
    "啊",
    "哦",
    "呀",
    "一首",
    "一首歌",
    "一个",
    "请",
    "帮我",
    "给我",
    "我想",
    "我要",
    "希望",
    "要",
    "用",
    "想",
    "帮",
    "给",
    "我",
    "你",
    "他",
    "它",
    "是",
    "有",
    "和",
    "与",
    "生成",
    "创作",
    "制作",
    "写",
    "做",
    "来",
    "关于",
    "有关",
    "有关於",
    "非常",
    "特别",
    "很",
    "十分",
    "有点",
    "有点儿",
    "比较",
    # 부사/완곡어: 「大概慢一点」의 「大概」「一点」등이 잔재로 남는 것을 막는다
    "大概",
    "最好",
    "稍微",
    "一点",
    "一点点",
    "有点点",
    "挺",
    "蛮",
    "超",
    "巨",
    "真的",
    "确实",
    "那个",
    "这个",
    "什么样",
    "一样",
    "谢谢",
    "感谢",
    "请帮",
    "音乐",
    "歌曲",
    "歌",
    "曲目",
    "风格",
    "曲风",
    "类型",
    "music",
    "song",
    "songs",
    "style",
    "please",
    "make",
    "create",
    "generate",
    "a",
    "an",
    "the",
    "some",
    "very",
    "really",
}

# 歌词结构标签 → 规范英文标签
_SECTION_ALIASES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^\s*[\[\(（【]\s*(intro|前奏|引子|开场)\s*[\]\)）】]\s*:?\s*$", re.I), "[Intro]"),
    (
        re.compile(
            r"^\s*[\[\(（【]\s*(verse|主歌|段落| verse1|verse\s*1)\s*[\]\)）】]\s*:?\s*$", re.I
        ),
        "[Verse]",
    ),
    (
        re.compile(r"^\s*[\[\(（【]\s*(pre[-\s]?chorus|预副歌|导歌)\s*[\]\)）】]\s*:?\s*$", re.I),
        "[Pre-Chorus]",
    ),
    (re.compile(r"^\s*[\[\(（【]\s*(chorus|副歌|合唱)\s*[\]\)）】]\s*:?\s*$", re.I), "[Chorus]"),
    (re.compile(r"^\s*[\[\(（【]\s*(bridge|桥段|间奏)\s*[\]\)）】]\s*:?\s*$", re.I), "[Bridge]"),
    (re.compile(r"^\s*[\[\(（【]\s*(outro|尾奏|尾声|结束)\s*[\]\)）】]\s*:?\s*$", re.I), "[Outro]"),
    # 无括号的裸写法：「副歌：」「Chorus:」
    (re.compile(r"^\s*(intro|前奏)\s*[:：]\s*$", re.I), "[Intro]"),
    (re.compile(r"^\s*(verse|主歌)\s*[:：]\s*$", re.I), "[Verse]"),
    (re.compile(r"^\s*(chorus|副歌)\s*[:：]\s*$", re.I), "[Chorus]"),
    (re.compile(r"^\s*(bridge|桥段)\s*[:：]\s*$", re.I), "[Bridge]"),
    (re.compile(r"^\s*(outro|尾奏)\s*[:：]\s*$", re.I), "[Outro]"),
]

_SECTION_RE = re.compile(r"^\s*[\[\(（【].*[\]\)）】]\s*:?\s*$")


def _split_descriptors(raw: str) -> list[str]:
    """把用户输入切成候选描述符。

    分隔符：中英文逗号 / 顿号 / 斜杠 / 竖线 / 分号 / 换行。
    空白处理**分语种**（关键）：
      - 含中文 → 空格也是分隔符（中文不用空格分词，「国风 伤感 钢琴」= 3 项）
      - 纯英文 → 空格**不是**分隔符（否则 ``male vocal`` 会被拆成 male + vocal）
    """
    if not raw:
        return []
    text = raw.replace("\r", "\n")
    for sep in (",", "，", "、", "/", "|", "；", ";", "·", "+"):
        text = text.replace(sep, "\n")
    parts: list[str] = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        if re.search(r"[\u4e00-\u9fff]", line):
            toks = re.split(r"\s+", line)
        else:
            toks = re.split(r"\s{2,}", line)
        for t in toks:
            t = t.strip().strip("。.!！?？\"'“”‘’（）()[]【】")
            if t:
                parts.append(t)
    return parts


def _canonical_descriptor(piece: str) -> str:
    """单个描述符归一：去填充词 → 同义词映射到官方写法 → 小写。"""
    key = piece.strip()
    if not key:
        return ""
    low = key.lower()
    if low in _STYLE_SYNONYMS:
        return _STYLE_SYNONYMS[low]
    # 去填充词（仅在该词单独成词时删，避免误伤「的」在词中的情况）
    if low in _FILLER:
        return ""
    # 英文规范小写
    return key if re.search(r"[\u4e00-\u9fff]", key) else low


# 长句关键词抽取用：按长度倒序，保证「节奏布鲁斯」优先于「布鲁斯」命中
_KNOWN_KEYS = sorted(_STYLE_SYNONYMS.keys(), key=len, reverse=True)


def _extract_known(text: str) -> list[str]:
    """从长自然语句中按**最长匹配**抽取已知风格词（防止短词误伤）。"""
    found: list[str] = []
    remaining = text
    for k in _KNOWN_KEYS:
        # 单字中文词极易误伤（如「慢」命中「慢慢」），仅在≥2 字或纯英文术语时采用
        if len(k) < 2 and not re.fullmatch(r"[a-z&+\- ]+", k):
            continue
        if k in remaining:
            found.append(_STYLE_SYNONYMS[k])
            remaining = remaining.replace(k, " ")
    return found


def _strip_particles(tok: str) -> str:
    """剥掉中文语气助词（首尾反复剥离）。

    「唱的」→「唱」、「谢谢啦」→「谢谢」(→ _FILLER 로 폐기)。
    """
    s = tok
    while s and (s[0] in _CJK_PARTICLES or s[-1] in _CJK_PARTICLES):
        s = s.strip("".join(_CJK_PARTICLES))
    return s.strip()


def _keep_residual_cjk(tok: str) -> str | None:
    """判断剥离助词后的中文残留是否**值得保留**（否则返回 None = 폐기）。

    保留条件：长度 ≥2 且不在填充词表。
    단잉 1자（「唱」「爱」）는 스타일 정보가 거의 없고 오히려 노이즈 → 버린다.
    """
    # 否定式 속도 지시（「不要太快」）는 **스타일 서술어가 아니다** — 남기면 노이즈.
    if _NEGATION_RE.search(tok) and any(kw in tok for kw, _ in _SUBSTRING_HINTS):
        return None
    t = _strip_particles(tok)
    if not t:
        return None
    if t.lower() in _FILLER:
        return None
    if len(t) < 2:
        return None
    return t


def _normalize_piece(piece: str) -> tuple[list[str], bool]:
    """归一单个片段：抽已知风格词 + 保留**未知但合理**的实词。

    长句（≥8 字符且含中文）残余视为填充语丢弃（如「请帮我写一首…的歌」），
    短片段残余保留（如「国风」不在官方枚举里，但对 ``prompt`` 自由文本有效）。

    返回 ``(descriptors, did_extract)``；``did_extract`` 表示**确实命中了已知风格词**，
    仅此时前端才提示“已提取风格词”（避免把“原样保留”说成“已提取”）。
    """
    # 全等匹配捷径：片段本身就是已知词（如「慢」「快」这类单字）→ 直接映射。
    # 放在最长匹配之前，规避下面「单字词防误伤」规则把独立成词的单字漏掉。
    exact = piece.strip().lower()
    if exact in _STYLE_SYNONYMS:
        return [_STYLE_SYNONYMS[exact]], True

    out: list[str] = []
    hit = False
    remaining = piece
    for k in _KNOWN_KEYS:
        if len(k) < 2 and not re.fullmatch(r"[a-z&+\- ]+", k):
            continue
        if k in remaining:
            out.append(_STYLE_SYNONYMS[k])
            remaining = remaining.replace(k, " ")
            hit = True

    # 子串型速度词（「慢一点」「不要太快」）。否定式는 절대 매핑하지 않는다.
    for kw, canon in _SUBSTRING_HINTS:
        if kw in remaining:
            if _NEGATION_RE.search(remaining):
                continue
            out.append(canon)
            remaining = remaining.replace(kw, " ")
            hit = True

    # 长句残余仅在**含中文**时丢弃（中文长句多为填充语）；
    # 纯英文长词（passionate / male vocal）必须保留，否则会误删有效描述符。
    has_cjk = bool(re.search(r"[\u4e00-\u9fff]", piece))
    if len(piece) < 8 or not has_cjk:
        # 英文残余**不能**按单空格切（否则 "male vocal" → male + vocal）
        sep = r"[\s,，、/|；;·+]+" if has_cjk else r"\s{2,}|[,，、/|；;·+]+"
        for tok in re.split(sep, remaining):
            tok = tok.strip().strip("。.!！?？\"'“”‘’（）()[]【】")
            if not tok:
                continue
            if re.search(r"[\u4e00-\u9fff]", tok):
                # 中文残留：剥助词 + 填充词/单자 폐기（「唱的」「谢谢啦」「大概」정리）
                kept = _keep_residual_cjk(tok)
                if kept:
                    out.append(kept)
                continue
            low = tok.lower()
            if low in _FILLER:
                continue
            out.append(low)
    return out, hit


# 歌词情绪关键词 → 风格描述符（确定性兜底：LLM 关闭/失败时，从歌词推断风格）
_LYRIC_MOOD_HINTS: list[tuple[str, str]] = [
    ("喜乐", "upbeat"),
    ("欢喜", "upbeat"),
    ("欢乐", "upbeat"),
    ("庆贺", "upbeat"),
    ("平安", "healing"),
    ("安息", "healing"),
    ("安慰", "healing"),
    ("治愈", "healing"),
    ("恩典", "healing"),
    ("忧伤", "melancholic"),
    ("悲伤", "melancholic"),
    ("眼泪", "melancholic"),
    ("孤独", "melancholic"),
    ("叹息", "melancholic"),
    ("盼望", "passionate"),
    ("期待", "passionate"),
    ("渴望", "passionate"),
    ("感恩", "warm"),
    ("感谢", "warm"),
    ("温柔", "gentle"),
    ("安静", "gentle"),
    ("宁静", "gentle"),
    ("激昂", "passionate"),
    ("呐喊", "passionate"),
    ("得胜", "passionate"),
    ("敬拜", "gospel"),
    ("赞美", "gospel"),
    ("歌颂", "gospel"),
]


def _extract_mood_from_lyrics(lyrics: str) -> list[str]:
    """从歌词抽情绪关键词 → 风格描述符（确定性兜底）。

    每个描述符只取首个命中，避免重复；最多 5 个，防止把 prompt 撑爆。
    """
    found: list[str] = []
    seen: set[str] = set()
    for kw, canon in _LYRIC_MOOD_HINTS:
        if kw in lyrics and canon not in seen:
            found.append(canon)
            seen.add(canon)
        if len(found) >= 5:
            break
    return found


def optimize_style(
    raw: str, *, force_gospel: bool = False, lyrics: str = ""
) -> tuple[str, list[str]]:
    """把自由文本风格收敛为 Mureka 官方推荐的逗号分隔短描述符。

    返回 ``(optimized, notes)``；``notes`` 说明本次做了什么（前端可展示“已优化：去重 2 项”）。

    规则顺序（确定性）：
      1. 切分 → 2. 同义词归一 → 3. 去填充词 → 4. 去重（保留首次出现顺序）
      5. 官方枚举优先排序（曲风在前，描述符在后）→ 6. 截断到 1024 字符
    """
    parts = _split_descriptors(raw or "")
    notes: list[str] = []

    canonical: list[str] = []
    dropped = 0
    extracted = 0
    for p in parts:
        got, hit = _normalize_piece(p)
        if got:
            canonical.extend(got)
            if hit and len(p) >= 8:
                extracted += 1
        else:
            dropped += 1
    if extracted:
        notes.append(f"已从 {extracted} 个描述句提取风格词")
    if dropped:
        notes.append(f"删除无效描述 {dropped} 项")

    # 歌词兜底：风格文本无有效描述符且给出歌词时，从歌词情绪推断（LLM 关闭/失败时的安全网）
    if not canonical and lyrics:
        derived = _extract_mood_from_lyrics(lyrics)
        if derived:
            canonical = derived
            notes.append("已从歌词推断风格（AI 暂未启用）")

    # 去重（大小写不敏感，保留首次出现）
    seen: set[str] = set()
    deduped: list[str] = []
    for c in canonical:
        k = c.lower()
        if k not in seen:
            seen.add(k)
            deduped.append(c)
    if len(deduped) < len(canonical):
        notes.append(f"去重 {len(canonical) - len(deduped)} 项")

    # 官方枚举优先（曲风）在前，其余描述符在后
    genres = [d for d in deduped if d.lower() in STYLE_ENUM]
    others = [d for d in deduped if d.lower() not in STYLE_ENUM]
    ordered = genres + others
    if genres and others:
        notes.append("已按「曲风 → 情绪/速度/乐器/人声」排序")

    if force_gospel and "gospel" not in [g.lower() for g in genres]:
        ordered.insert(0, "gospel")
        notes.append("已前置 gospel（敬拜曲风命中）")

    result = ", ".join(ordered)
    if len(result) > MAX_PROMPT_CHARS:
        # 从尾部丢弃，保证不破词且 ≤1024
        kept: list[str] = []
        total = 0
        for d in ordered:
            add = len(d) + (2 if kept else 0)
            if total + add > MAX_PROMPT_CHARS - 3:
                break
            kept.append(d)
            total += add
        if kept:
            result = ", ".join(kept) + "..."
        else:
            # 单个描述符就超限 → 直接截该词本身，避免产出仅 "..." 的空结果
            result = ordered[0][: MAX_PROMPT_CHARS - 3] + "..."
        notes.append(f"已截断至 {MAX_PROMPT_CHARS} 字符上限")

    return result, notes


def _canonical_section(line: str) -> str | None:
    """若该行是结构标签，返回规范写法；否则 None。"""
    for pattern, repl in _SECTION_ALIASES:
        if pattern.match(line):
            return repl
    if _SECTION_RE.match(line):
        # 已是 [xxx] 形式但别名表未覆盖 → 归一化大小写与空白
        inner = re.sub(r"^\s*[\[\(（【]\s*|\s*[\]\)）】]\s*:?\s*$", "", line)
        inner = inner.strip()
        return f"[{inner[:1].upper()}{inner[1:]}]" if inner else None
    return None


def optimize_lyrics(raw: str) -> tuple[str, list[str]]:
    """歌词「一键优化」：对齐结构 + 去冗余（确定性，绝不改写语义）。

    规则：
      1. 去每行行尾空白；去掉全空白行
      2. 结构标签规范化（副歌/Chorus:/（副歌）→ ``[Chorus]``）
      3. 结构标签前强制空一行（对齐），标签后不留空行
      4. 连续空行收敛为 1 行
      5. 删除首尾空行
      6. 超 5000 字符截断（Mureka lyrics 上限）
    """
    if not raw:
        return "", []

    notes: list[str] = []
    lines = raw.replace("\r\n", "\n").replace("\r", "\n").split("\n")

    cleaned: list[str] = []
    for ln in lines:
        ln = ln.rstrip()
        if not ln.strip():
            continue
        tag = _canonical_section(ln)
        cleaned.append(tag if tag else ln)

    # 结构标签前空一行（对齐）
    aligned: list[str] = []
    for ln in cleaned:
        is_tag = bool(_SECTION_RE.match(ln))
        if is_tag and aligned and aligned[-1] != "":
            aligned.append("")
        aligned.append(ln)
        if is_tag:
            aligned.append("")  # 标签后留空，稍后统一收敛

    # 收敛空行 + 去首尾
    out: list[str] = []
    blank = False
    for ln in aligned:
        if ln == "":
            if blank or not out:
                continue
            blank = True
            out.append("")
        else:
            blank = False
            out.append(ln)
    while out and out[-1] == "":
        out.pop()

    result = "\n".join(out)
    if result != raw:
        if len(out) != len([l for l in lines if l.strip()]):
            notes.append("已删除空行")
        notes.append("已对齐段落结构")
    if len(result) > MAX_LYRIC_CHARS:
        result = result[:MAX_LYRIC_CHARS]
        notes.append(f"已截断至 {MAX_LYRIC_CHARS} 字符上限（Mureka 限制）")

    return result, notes
