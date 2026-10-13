"""LRC（.lrc）编解码 + 「改词后重对齐」纯逻辑。

单一事实源：`Song.lyrics_sync`（= mureka.py 对 `choices[].lyrics_sections`
做 `json.dumps` 的原文；结构见 orchestrator/base.py:SongResult.lyrics_sync 注释）。

本模块解决三件事：
  ① 导出：`lyrics_sync` → 标准 `.lrc`（第三方播放器 foobar2000 / 墨墨 / 椒盐 等可解析）；
  ② 导入：`.lrc` → 时间轴（行级）+ 纯文本歌词；
  ③ 重对齐：用户**改词**后，把新歌词按旧时间轴重采样（行数/内容变了也不失配）。

为什么导出用「行级」而不是逐字（增强型 LRC <mm:ss.xx> 中括号内嵌时标）：
  · 逐字时间轴是我们内部的数据优势，但**第三方播放器的支持面极窄**（foobar2000 默认
    只认行首 `[mm:ss]`，增强型逐字需装插件且各家方言不同）。任务要求「必须能在第三方
    播放器正确解析」→ 导出**只输出标准行级时标**，逐字信息留在库内、由自家播放器消费。
  · 一行多时标（`[00:12.00][01:30.00]同一句`）是重复段的行业惯用压缩写法，我们**不生成**
    （无重复段判定依据，且部分解析器只取首个时标），但解析侧**要能读**（兼容他人文件）。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

# ── 文件级 LRC 元信息标签：[ar:艺术家] [ti:标题] [al:专辑] [by:制作] [offset:±ms] ──
_META_KEYS = ("ar", "ti", "al", "by", "offset", "length", "re", "ve")

# 段落类型（Mureka section_type 的闭词表 + 我们生成时写回的值）。
# 不认识的类型不报错、按 "verse" 归档（前向兼容：供应商将来新增类型不该炸导出）。
SECTION_TYPES = ("intro", "verse", "pre-chorus", "chorus", "bridge", "outro", "instrumental")


# ═══════════════════════════════════════════════════════════════════════════════
# ① 时间格式：LRC 用 [mm:ss.xx]（百分秒，两位）；内部统一用**毫秒整数**
# ═══════════════════════════════════════════════════════════════════════════════
_TIME_TAG_RE = re.compile(r"\[(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?\]")


def format_time(ms: int) -> str:
    """毫秒 → `mm:ss.xx`。负值钳到 0；>99 分钟继续进位（`mm` 支持三位，LRC 常见做法）。"""
    if ms is None:
        ms = 0
    ms = max(0, int(round(ms)))
    total_cs = ms // 10  # 百分秒
    mm, rem = divmod(total_cs, 6000)
    ss, cs = divmod(rem, 100)
    return f"{mm:02d}:{ss:02d}.{cs:02d}"


def parse_time(mm: str, ss: str, frac: str | None) -> int:
    """`mm:ss.xx` 片段 → 毫秒。

    frac 位数按位权解释：`.5`=500ms、`.05`=50ms、`.005`=5ms（不把 "5" 当 50ms —— 这是
    各家 LRC 解析器最常见的偏差源，也会让往返（round-trip）不再相等）。
    """
    minutes = int(mm)
    seconds = int(ss)
    if frac:
        # 补齐/截断到毫秒精度：最多三位
        f = (frac + "000")[:3]
        millis = int(f)
    else:
        millis = 0
    return (minutes * 60 + seconds) * 1000 + millis


@dataclass
class LrcLine:
    """一行歌词 + 其行级起止（毫秒）。end=None 表示「未知」（解析纯 LRC 时的常态）。"""

    start: int
    text: str
    end: int | None = None
    # 段落类型（verse/chorus/...）；空 = 未标注。仅用于编辑器分组展示，LRC 文件里
    # 以 `[Verse 1]` 形态的裸标签行承载（见 build_lrc 的 section_labels）。
    section: str = ""

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"start": self.start, "text": self.text}
        if self.end is not None:
            out["end"] = self.end
        if self.section:
            out["section"] = self.section
        return out


@dataclass
class LrcDoc:
    """解析结果。lines 按 start 升序（并列时保持文件内出现顺序）。"""

    lines: list[LrcLine] = field(default_factory=list)
    meta: dict[str, str] = field(default_factory=dict)
    offset_ms: int = 0  # [offset:+/-ms]：正值=时间轴整体前移（LRC 规范：加在时间戳上）
    warnings: list[str] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════════════
# ② 解析：.lrc 文本 → LrcDoc
# ═══════════════════════════════════════════════════════════════════════════════
# 行首裸段落标签：[Verse] / [Verse 1] / [Chorus] / [Pre-Chorus] / [间奏] —— 无时标。
# ⚠ 与元信息标签 [ar:xx] 的区分：元信息含冒号，本正则**不含**冒号。
# ⚠ 标签体允许空格但不允许冒号（冒号 = 元信息 [ti:…]，由上面 _META_KEYS 分支拦截），
# 且必须含至少一个字母/汉字 —— 否则 `[123]`、`[00:12.00]` 这类纯数字内容会被误判成
# 段落标签，把真正的时标行吃掉（这是解析器最隐蔽的一类错）。
_SECTION_LABEL_RE = re.compile(r"^\s*\[\s*([A-Za-z\u4e00-\u9fff][^\]:]{0,23})\s*\]\s*$")

_SECTION_ALIASES = {
    "verse": "verse",
    "verses": "verse",
    "chorus": "chorus",
    "bridge": "bridge",
    "intro": "intro",
    "outro": "outro",
    "hook": "chorus",
    "prechorus": "pre-chorus",
    "pre-chorus": "pre-chorus",
    "pre chorus": "pre-chorus",
    "instrumental": "instrumental",
    "solo": "instrumental",
    "interlude": "instrumental",
}


def _norm_section(raw: str) -> str:
    """`Verse 1` / `pre-chorus` / `主歌` → 归一化段落类型（认不出返回 "verse"）。

    认不出时归 "verse" 而非报错：供应商将来新增段落类型不该炸掉导出/编辑。
    """
    s = (raw or "").strip().lower()
    s = re.sub(r"\s*\d+\s*$", "", s)  # 去掉尾部编号："verse 1" → "verse"
    s = s.strip(" -–—")
    if s in _SECTION_ALIASES:
        return _SECTION_ALIASES[s]
    if "chorus" in s:
        return "chorus"
    if "verse" in s:
        return "verse"
    if "bridge" in s:
        return "bridge"
    if "intro" in s:
        return "intro"
    if "outro" in s:
        return "outro"
    return "verse"


def parse_lrc(text: str, *, min_ms: int = 0) -> LrcDoc:
    """解析 `.lrc`。容错优先（导入的是**别人的**文件，宁降级不报错）：

    · 一行多时标（`[00:12.00][01:30.00]同一句`）→ 展开成多行；
    · 时标嵌在行中/行尾也能提取（有些播放器导出的格式不规范）；
    · `[ar:..]` 等元信息、`[Verse 1]` 段落标签、空行、`#` 注释都能识别；
    · 无时标的纯文本行 → 记录 warning，**不丢弃**（按「未定时行」追加到末尾，
      让用户至少看到内容并可在编辑器里手工定时）。
    """
    doc = LrcDoc()
    offset = 0
    pending_section = ""
    untimed: list[str] = []

    for raw_line in (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw_line.strip()
        if not line:
            continue

        # ── 元信息标签（整行都是 [key:value]，可能多个）──
        meta_hits = re.findall(r"\[([A-Za-z]+):([^\]]*)\]", line)
        stripped_meta = re.sub(r"\[[A-Za-z]+:[^\]]*\]", "", line).strip()
        if meta_hits and not stripped_meta:
            for k, v in meta_hits:
                k = k.lower()
                if k == "offset":
                    try:
                        offset = int(float(v.strip()))
                    except ValueError:
                        doc.warnings.append(f"offset 非法：{v!r}")
                        continue
                doc.meta[k] = v.strip()
            continue

        # ── 纯段落标签：[Verse 1] / [Chorus] ──
        m_sec = _SECTION_LABEL_RE.match(line)
        if m_sec:
            name = m_sec.group(1)
            # 元信息键会被上面的分支吃掉；这里全是「非 key:value」的内容
            pending_section = _norm_section(name)
            continue

        # ── 时标行 ──
        times = list(_TIME_TAG_RE.finditer(line))
        text_after = _TIME_TAG_RE.sub("", line).strip()
        if times:
            for m in times:
                ms = parse_time(m.group(1), m.group(2), m.group(3))
                doc.lines.append(
                    LrcLine(start=max(min_ms, ms), text=text_after, section=pending_section)
                )
            continue

        # ── 无时标纯文本：不丢，延后处理 ──
        untimed.append(line)

    doc.offset_ms = offset
    if offset:
        # [offset:+500] 语义：**时间轴提前** 500ms（播放器把 500 加到时间戳上）
        for ln in doc.lines:
            ln.start = max(min_ms, ln.start - offset)
    if untimed:
        doc.warnings.append(
            f"{len(untimed)} 行无时间轴，已作为未定时行保留（可在编辑器里补时标）"
        )

    # 稳定排序（并列时间保持出现顺序，Python sort 稳定），随后**把未定时行挪到末尾**。
    # 顺序必须显式保证：未定时行的 start 与 min_ms（默认 0）相同，若直接排序，
    # sort 的稳定性会让它们保留「文件内出现顺序」，从而插在真实首行**之前/之间** ——
    # 于是导出的 LRC 时间轴不再单调，播放器高亮会在开头闪一下再回到第一句。
    doc.lines.sort(key=lambda ln: ln.start)
    if untimed:
        doc.lines.extend(LrcLine(start=min_ms, text=t, section=pending_section) for t in untimed)
    return doc


# ═══════════════════════════════════════════════════════════════════════════════
# ③ lyrics_sync（Mureka lyrics_sections）↔ 行级时间轴
# ═══════════════════════════════════════════════════════════════════════════════
def _num(v: Any) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.strip())
        except ValueError:
            return None
    return None


def _first(obj: dict, keys: Sequence[str]) -> Any:
    for k in keys:
        if k in obj and obj[k] is not None:
            return obj[k]
    return None


def _pick_text(obj: dict) -> str:
    v = _first(obj, ("text", "word", "content", "lyric", "line"))
    return "" if v is None else str(v)


def _sections_of(data: Any) -> list[dict]:
    """把 lyrics_sync 的任意合理形态归一成「段列表」。

    认 Mureka 真形态（段→lines），也认前端 syncedLyrics.ts 已容错的简化形态
    （顶层就是行数组）—— 后端解析器与前端 `flattenSync` 保持同等宽容度，
    否则会出现「前端能显示、导出 LRC 却为空」的诡异不一致。
    """
    obj = data
    if isinstance(obj, str):
        try:
            obj = json.loads(obj)
        except (ValueError, TypeError):
            return []
    if isinstance(obj, dict):
        for k in ("lyrics_sections", "sections", "lines", "words", "rows"):
            if isinstance(obj.get(k), list):
                obj = obj[k]
                break
        else:
            return []
    if not isinstance(obj, list) or not obj:
        return []
    head = obj[0]
    if not isinstance(head, dict):
        return []
    if isinstance(head.get("lines"), list) or isinstance(head.get("rows"), list):
        return [o for o in obj if isinstance(o, dict)]
    return [{"lines": [o for o in obj if isinstance(o, dict)]}]


def sections_to_lines(sync: Any) -> list[LrcLine]:
    """`lyrics_sync` 原始值（JSON 字符串 / 对象 / 数组）→ 行级 LrcLine（升序）。

    取每行的 `start`/`end`（ms）；行内 `words[]` **仅在行缺时标时** 用于兜底
    （取首词 start / 末词 end），因为逐字信息在行级 LRC 里没有承载位置。
    """
    out: list[LrcLine] = []
    for sec in _sections_of(sync):
        sec_type_raw = _first(sec, ("section_type", "tag", "type"))
        sec_type = _norm_section(str(sec_type_raw)) if sec_type_raw else ""
        rows = sec.get("lines") if isinstance(sec.get("lines"), list) else sec.get("rows")
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            text = _pick_text(row)
            start = _num(_first(row, ("start", "startTime", "begin", "t0", "time")))
            end = _num(_first(row, ("end", "endTime", "finish", "t1")))
            words = row.get("words") if isinstance(row.get("words"), list) else row.get("word")
            if (start is None or end is None) and isinstance(words, list) and words:
                first_w = next((w for w in words if isinstance(w, dict)), None)
                last_w = next(
                    (w for w in reversed(words) if isinstance(w, dict)), None
                )
                if first_w is not None and start is None:
                    start = _num(_first(first_w, ("start", "startTime", "begin", "t0")))
                if last_w is not None and end is None:
                    end = _num(_first(last_w, ("end", "endTime", "finish", "t1")))
            # 「无可用时间轴」的判定必须与 `has_timeline` / 导出降级口径**一致**：
            # start 与 end 都缺 = 没有时点（text 只说明有词，不代表有轴）。若在这里
            # 靠 text 兜一条 start=0 出来，`is_sync_empty` 会判成「有轴」→ 导出时
            # 输出一整屏 [00:00.00]（播放器把所有行挤在开头闪），而重对齐路径又会
            # 因「多行 start 相同」被正确拒掉 —— 同一份数据两种结论，必然出 bug。
            if start is None and end is None:
                continue
            out.append(
                LrcLine(
                    start=int(max(0, start if start is not None else end)),
                    end=int(end) if end is not None else None,
                    text=text,
                    section=sec_type,
                )
            )
    out.sort(key=lambda ln: ln.start)
    return out


def is_sync_empty(sync: Any) -> bool:
    """`lyrics_sync` 是否「没有可用时间轴」（用于降级判断，避免导出空文件）。"""
    return not sections_to_lines(sync)


# ═══════════════════════════════════════════════════════════════════════════════
# ③b 重对齐：改词后按行重采样
# ═══════════════════════════════════════════════════════════════════════════════
def is_section_label(line: str) -> bool:
    """`[Verse]` / `[Chorus 2]` / `[间奏]` 这类**无冒号**的结构标记行。

    ⚠ 与元信息 `[ti:…]` / `[ar:…]` 的唯一区分就是**有没有冒号**。仅按
    `_SECTION_LABEL_RE`（本身已排除冒号）判断即可，无需再额外排除 —— 这个函数存在的
    意义是把这条判断收敛成一处，避免「解析侧认、提取侧不认」的双口径漂移。
    """
    return bool(_SECTION_LABEL_RE.match(line or ""))


def extract_plain_lines(text: str, *, drop_section_labels: bool = True) -> list[str]:
    """纯文本歌词 → 行列表：去空行、去 `[Verse]` 结构标记、strip 每行。

    与 `services/prompt_assist.optimize_lyrics` 的「结构标签」是同一套写法（`[Verse]`），
    这里只做读取侧归一，不写回格式。
    """
    out: list[str] = []
    for raw in (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw.strip()
        if not line:
            continue
        if drop_section_labels and is_section_label(line):
            continue
        out.append(line)
    return out


def lyric_from_lines(lines: Iterable[str]) -> str:
    """行列表 → 落库用的纯文本歌词（保留空行分隔？不 —— 空行是排版噪音，统一单行排列）。"""
    return "\n".join(x.strip() for x in lines if x and x.strip())


def realign_sync(
    old_sync: Any,
    new_lines: Sequence[str],
    *,
    duration: float | None = None,
) -> list[dict[str, Any]]:
    """改词后重对齐：把 `new_lines` 套回 `old_sync` 的时间轴。

    返回行级时间轴（`[{"start":ms,"end":ms,"text":..,"section":..}]`），
    正是 `sections_to_lines` 能吃回去的形态，也便于前端直接渲染。

    策略（行数变了也不失配）：
      · n_new == n_old → **一对一**，时间轴原样保留（改字不改时 —— 最常见、最该保真的情形）；
      · n_new < n_old  → 按比例**下采样**旧行起点（删行/合并行）；
      · n_new > n_old  → 在最大间隙里**插入**新行时点（加行）；
      · 末尾（最后一行 end 之后）→ 用 `duration` 兜底；仍未知则给最后一词 2s（行业惯用最小值）。

    ⚠ 不引入任何新列/迁移：重对齐结果直接序列化回 `Song.lyrics_sync`（列已存在）。
    """
    old = sections_to_lines(old_sync)
    new_lines = [x.strip() for x in new_lines if x is not None and x.strip()]
    if not new_lines:
        return []
    # 「没有旧时间轴」与「旧时间轴不可用（多行时点相同）」是同一件事：都没法真对齐。
    # 后者必须显式识别 —— 否则重采样会产出一串 start 并列/为 0 的行，看着像成功、
    # 实际把播放器高亮废掉（静默数据损坏，比报错更糟）。
    starts_seq = [ln.start for ln in old]
    usable = bool(old) and len(set(starts_seq)) > 1
    if not usable:
        # **绝不伪造均匀时轴**（那是编数据）；如实标记 aligned=False 由调用方决策。
        return [{"start": 0, "end": None, "text": t, "aligned": False} for t in new_lines]

    n_new, n_old = len(new_lines), len(old)
    starts: list[int] = []

    if n_new == n_old:
        starts = [ln.start for ln in old]
    elif n_new < n_old:
        # 下采样：把新第 i 行映射到旧行等分位置（保持整体时长与节奏分布）
        for i in range(n_new):
            idx = int(round(i * (n_old - 1) / max(1, n_new - 1))) if n_new > 1 else 0
            starts.append(old[idx].start)
    else:
        # 上采样：把时间轴按新行数**整体重新均分**到旧时间轴上。
        #
        # 为什么不是「保留旧行时点 + 只在最大间隙插入」：加行往往意味着用户在同一句里
        # 拆出了新句（或补写了被漏掉的段落），旧行的时点就不再是「新行的时点」。硬保留
        # 旧时点会让新行与旧行撞在同一毫秒上（高亮来回跳），反而更失真。整体重采样 =
        # 「按原节奏重新分配」，所有行仍落在原曲时轴上、顺序与相对位置不破。
        span_start = old[0].start
        tail_ms0 = int(duration * 1000) if duration else None
        if tail_ms0 is None:
            tail_ms0 = old[-1].end or (old[-1].start + 2000 * max(1, n_old))
        span_end = max(span_start + n_new * 250, tail_ms0)  # 每行至少 250ms，避免挤成一点
        starts = [
            int(span_start + (span_end - span_start) * i / n_new) for i in range(n_new)
        ]

    # 逐行成对，end 取「下一行 start」，末行用 duration / 旧末行 end / +2s
    lines: list[dict[str, Any]] = []
    tail_ms = int(duration * 1000) if duration else None
    if tail_ms is None and old[-1].end:
        tail_ms = old[-1].end
    for i, text in enumerate(new_lines):
        start = int(max(0, starts[i]))
        if i + 1 < n_new:
            end = int(max(start + 1, starts[i + 1]))
        else:
            end = int(max(start + 250, tail_ms if tail_ms else start + 2000))
        lines.append(
            {
                "start": start,
                "end": end,
                "text": text,
                "section": old[min(i, n_old - 1)].section or "verse",
                "aligned": True,
            }
        )
    return lines


def normalize_timeline(
    lines: Sequence[LrcLine | dict], *, duration_s: float | None = None
) -> list[dict[str, Any]]:
    """把任意形态的行序列规整为「升序 + 无重叠 + end>start」的落库结构。

    编辑器提交的行可能被用户手改成倒序/重叠/end<start（UI 允许自由编辑），
    入库前必须收敛，否则播放器高亮会因重叠区间来回跳。

    `duration_s` 已知时用作末行 end 的兜底 —— 否则末行只能猜 2s，导出 LRC 后
    播放器会在曲末还有 10 秒时就把高亮停在最后一句上（看似卡住）。
    """
    rows: list[LrcLine] = []
    for x in lines:
        if isinstance(x, dict):
            start = x.get("start")
            end = x.get("end")
            rows.append(
                LrcLine(
                    start=int(max(0, _num(start) or 0)),
                    end=int(_num(end)) if end is not None else None,
                    text=str(x.get("text") or ""),
                    section=str(x.get("section") or ""),
                )
            )
        else:
            rows.append(LrcLine(start=int(max(0, x.start)), end=x.end, text=x.text, section=x.section))
    rows.sort(key=lambda ln: ln.start)
    tail_ms = int(duration_s * 1000) if duration_s else None
    out: list[dict[str, Any]] = []
    for i, ln in enumerate(rows):
        start = ln.start
        nxt = rows[i + 1].start if i + 1 < len(rows) else None
        end = ln.end
        if end is None or end <= start:
            if nxt is not None and nxt - 1 > start:
                end = nxt  # 缺 end 时吃到下一行起点（LRC 的常规语义）
            elif tail_ms and tail_ms > start:
                end = tail_ms  # 末行 → 曲末
            else:
                end = start + 2000  # 行业惯用最小值
        if nxt is not None and end > nxt:
            end = nxt  # 不允许跨界：改词后一行占用下一行的时间 = 高亮错位
        out.append(
            {
                "start": start,
                "end": end,
                "text": ln.text,
                "section": ln.section or "verse",
            }
        )
    return out


# ═══════════════════════════════════════════════════════════════════════════════
# ④ 导出：行级时间轴 → 标准 `.lrc`
# ═══════════════════════════════════════════════════════════════════════════════
def build_lrc(
    lines: Sequence[Any],
    *,
    title: str = "",
    artist: str = "",
    album: str = "",
    by: str = "",
    offset_ms: int = 0,
    duration_s: float | None = None,
    include_meta: bool = True,
) -> str:
    """行级时间轴 → `.lrc` 文本。

    · 只写标准 `[mm:ss.xx]` 行首时标 + 元信息标签 —— **不写增强型逐字标记**，
      保证 foobar2000 / Winamp / VLC / 手机自带播放器等一律可解析；
    · 空文本行（间奏占位）会输出为「只有时标、无内容」，这是 LRC 表达间奏的标准做法；
    · `offset_ms` 写入 `[offset:]`（正值 = 时间轴提前，符合 LRC 规范）；
    · 段落标签**默认不写**成裸 `[Verse]` 行 —— 部分解析器会把裸括号行当脏数据丢弃、
      也有解析器直接显示出来。段落分组是编辑器 UI 的事，文件里只保留时间与文本。
    """
    normalized = normalize_timeline(list(lines))
    out: list[str] = []
    if include_meta:
        if title:
            out.append(f"[ti:{_sanitize_meta(title)}]")
        if artist:
            out.append(f"[ar:{_sanitize_meta(artist)}]")
        if album:
            out.append(f"[al:{_sanitize_meta(album)}]")
        if by:
            out.append(f"[by:{_sanitize_meta(by)}]")
        if duration_s:
            out.append(f"[length:{format_time(int(duration_s * 1000))}]")
        if offset_ms:
            out.append(f"[offset:{int(offset_ms):+d}]")
    for ln in normalized:
        out.append(f"[{format_time(ln['start'])}]{ln['text']}")
    return "\n".join(out) + ("\n" if out else "")


def _sanitize_meta(v: str) -> str:
    """元信息不能含 `]`（会提前闭合标签）与换行（会破坏「一行一标签」结构）。

    这是**注入面**：歌曲标题由用户输入，`[ti:a]b]` 这类内容会污染整个文件头。
    """
    cleaned = re.sub(r"[\]\[\r\n]", " ", str(v or ""))
    return re.sub(r"\s{2,}", " ", cleaned).strip()[:120]
