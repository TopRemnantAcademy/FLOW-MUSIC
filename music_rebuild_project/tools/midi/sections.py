"""段落结构模型（#51），以及调号/拍号/小节/和弦的编曲前置信息（#48/#50）。

段落是 E 段编曲的**驱动轴**：鼓的主歌/副歌 pattern、贝斯的根音走位、
和弦乐器的密度，全部由「当前在第几小节、属于哪个段落」决定。

本模块只负责「结构」，不负责「内容」：
  - `Section` / `SectionPlan`：段落起止小节（0-based，闭开区间）
  - `SongContext`：调号 + 拍号 + 小节数 + BPM + 段落计划
  - `parse_sections_md`：读 `06_arrangement/<song_id>/sections.md`（#51）
  - `parse_chords_txt`：读 `06_arrangement/<song_id>/chords.txt`（#50）
  - `read_musicxml_context`：用 `xml.etree` 只读 MusicXML 的调号/拍号/BPM

不是本模块的事：解析整份 MusicXML（超范围）、和弦识别的正确性判定（人工，#49）。
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

#: 规范段落名（#51）。顺序即常见曲式顺序。
SECTION_ORDER = ("Intro", "Verse", "Pre-chorus", "Chorus", "Bridge", "Outro")

#: 常见别名 → 规范名。
SECTION_ALIASES = {
    "intro": "Intro",
    "verse": "Verse",
    "v": "Verse",
    "pre-chorus": "Pre-chorus",
    "prechorus": "Pre-chorus",
    "pre chorus": "Pre-chorus",
    "pre": "Pre-chorus",
    "chorus": "Chorus",
    "ch": "Chorus",
    "bridge": "Bridge",
    "br": "Bridge",
    "outro": "Outro",
    "coda": "Outro",
}

#: 音名 → 半音序号（C=0）。
_PITCH_CLASS = {
    "C": 0, "C#": 1, "DB": 1, "D": 2, "D#": 3, "EB": 3, "E": 4, "FB": 4,
    "F": 5, "E#": 5, "F#": 6, "GB": 6, "G": 7, "G#": 8, "AB": 8, "A": 9,
    "A#": 10, "BB": 10, "B": 11, "CB": 11,
}

#: 调号自然大调音阶的级数 → 相对根音的半音数。
MAJOR_SCALE = (0, 2, 4, 5, 7, 9, 11)
#: 自然小调音阶。
MINOR_SCALE = (0, 2, 3, 5, 7, 8, 10)


class SectionError(ValueError):
    """段落结构不合法（重叠、越界、名字未知等）。"""


def normalize_section_name(raw: str) -> str:
    """把各种写法收敛到 `SECTION_ORDER` 里的规范名。"""
    key = raw.strip().lower().replace("_", "-")
    if key in SECTION_ALIASES:
        return SECTION_ALIASES[key]
    for name in SECTION_ORDER:
        if name.lower() == key:
            return name
    raise SectionError(f"未知段落名: {raw!r}（允许: {', '.join(SECTION_ORDER)}）")


@dataclass(frozen=True)
class Section:
    """一个段落：名字 + 起止小节。

    `start_bar` / `end_bar` 均为 **0-based 小节号**，区间为 **左闭右开**，
    因此 `bars = end_bar - start_bar`，且相邻段落首尾相接不重叠。
    """

    name: str
    start_bar: int
    end_bar: int
    label: str = ""  # 可选实例标记，如 Verse 出现两次时用 "Verse 1" / "Verse 2"

    def __post_init__(self) -> None:
        if self.start_bar < 0 or self.end_bar <= self.start_bar:
            raise SectionError(
                f"{self.name}: 小节区间非法 [{self.start_bar}, {self.end_bar})"
            )

    @property
    def bars(self) -> int:
        return self.end_bar - self.start_bar

    def contains(self, bar: int) -> bool:
        return self.start_bar <= bar < self.end_bar

    @property
    def display(self) -> str:
        return self.label or self.name


@dataclass(frozen=True)
class KeySignature:
    """调号：根音 + 大小调。`root` 用音名（如 "F"、"Bb"、"G#"）。"""

    root: str
    mode: str = "major"

    def __post_init__(self) -> None:
        if self.root.upper() not in _PITCH_CLASS:
            raise SectionError(f"未知根音: {self.root!r}")
        if self.mode not in ("major", "minor"):
            raise SectionError(f"mode 只能是 major/minor: {self.mode!r}")

    @property
    def tonic(self) -> int:
        """主音在半音轴上的位置（C=0..B=11）。"""
        return _PITCH_CLASS[self.root.upper()]

    @property
    def scale(self) -> tuple[int, ...]:
        return MAJOR_SCALE if self.mode == "major" else MINOR_SCALE

    def degree(self, index: int) -> int:
        """取第 `index` 级音（0-based，可越八度），返回半音位置。"""
        steps, scale = divmod(index, len(self.scale))
        return self.tonic + 12 * steps + self.scale[scale]

    def triad(self, degree_index: int) -> tuple[int, int, int]:
        """在调内取自然三和弦（顺阶级数，0-based）。"""
        return tuple(self.degree(degree_index + k) for k in (0, 2, 4))  # type: ignore[return-value]

    def pitch(self, degree_index: int, octave: int = 4) -> int:
        """级数 → MIDI 音高。`octave` 用科学音高记号（C4 = 60）。"""
        return (octave + 1) * 12 + self.degree(degree_index)


@dataclass
class SongContext:
    """编曲上下文：调号 + 拍号 + 总小节数 + BPM + 段落计划。

    `bars` *必须* 等于段落计划覆盖的小节总数，否则 `validate()` 抛错 ——
    这是 G4-5「长度可精确计算」的第一道保险。
    """

    key: KeySignature
    beats_per_bar: int = 4
    beat_unit: int = 4
    bars: int = 0
    bpm: int = 120
    plan: "SectionPlan | None" = None
    chords: dict[int, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.beats_per_bar <= 0:
            raise SectionError(f"beats_per_bar 必须为正: {self.beats_per_bar}")
        if self.bpm <= 0:
            raise SectionError(f"bpm 必须为正: {self.bpm}")
        if self.plan is None:
            self.plan = SectionPlan.single(self.bars or 1)
        if not self.bars:
            self.bars = self.plan.total_bars

    def validate(self) -> "SongContext":
        assert self.plan is not None
        self.plan.validate(total_bars=self.bars)
        for bar in self.chords:
            if not 0 <= bar < self.bars:
                raise SectionError(f"和弦小节号越界: {bar} 不在 [0, {self.bars})")
        return self

    def section_at(self, bar: int) -> Section:
        """某小节归属的段落。"""
        assert self.plan is not None
        return self.plan.section_at(bar)

    def seconds(self) -> float:
        """全曲预期时长（秒）= 小节 × 每小节拍数 × 60 / BPM。"""
        return self.bars * self.beats_per_bar * 60.0 / self.bpm

    def is_minor(self) -> bool:
        return self.key.mode == "minor"


@dataclass
class SectionPlan:
    """按顺序排列的段落序列，必须**无损覆盖** 0..total_bars。"""

    sections: list[Section] = field(default_factory=list)

    @classmethod
    def single(cls, bars: int, name: str = "Verse") -> "SectionPlan":
        return cls([Section(name=name, start_bar=0, end_bar=bars)])

    @classmethod
    def from_bars(cls, spec: list[tuple[str, int]], names: list[str] | None = None) -> "SectionPlan":
        """按「(段落名, 小节数)」序列构造。`names` 可选显式实例名。"""
        sections: list[Section] = []
        cursor = 0
        for index, (raw_name, length) in enumerate(spec):
            if length <= 0:
                raise SectionError(f"{raw_name}: 小节数必须为正: {length}")
            name = normalize_section_name(raw_name)
            label = names[index] if names else ""
            sections.append(Section(name, cursor, cursor + length, label))
            cursor += length
        return cls(sections)

    @property
    def total_bars(self) -> int:
        return self.sections[-1].end_bar if self.sections else 0

    def validate(self, total_bars: int | None = None) -> "SectionPlan":
        if not self.sections:
            raise SectionError("段落计划为空")
        cursor = 0
        for section in self.sections:
            if section.start_bar != cursor:
                raise SectionError(
                    f"{section.display}: 应从第 {cursor} 小节开始（不重叠、不留空）"
                )
            cursor = section.end_bar
        if total_bars is not None and cursor != total_bars:
            raise SectionError(
                f"段落覆盖 {cursor} 小节，与总小节数 {total_bars} 不一致"
            )
        return self

    def section_at(self, bar: int) -> Section:
        for section in self.sections:
            if section.contains(bar):
                return section
        raise SectionError(f"小节 {bar} 不属于任何段落（越界或计划不完整）")

    def with_chord_awareness(self) -> list[Section]:
        """只读便捷访问器；返回段落列表副本。"""
        return list(self.sections)

    def describe(self) -> str:
        """人读的段落结构说明（评论/文档用）。"""
        lines = []
        for section in self.sections:
            span = f"[{section.start_bar}, {section.end_bar})"
            lines.append(f"{section.display:<12} 小节 {span:<12} 共 {section.bars} 小节")
        return "\n".join(lines)


# --------------------------------------------------------------------------
# 文件读取（均容错、不抛 IOError 之外的异常；调用方决定缺失时怎么办）
# --------------------------------------------------------------------------

_SECTION_LINE = re.compile(
    r"^\s*[-*|]?\s*(?P<name>[A-Za-z][A-Za-z\- ]*?)\s*[:：|]\s*"
    r"(?P<start>\d+)\s*(?:[-~–—]|至|到|,|，)\s*(?P<end>\d+)\s*$"
)
_BAR_CHORD = re.compile(r"^\s*(?P<bar>\d+)\s*[:：|]?\s*(?P<chord>[A-Ga-g][#b]?\S*)\s*$")


def parse_sections_md(text: str) -> SectionPlan:
    """读 `sections.md`（#51）。

    接受形如 `Verse: 1-8`、`| Chorus | 9 | 16 |` 的行；小节号按 **1-based**
    书写（音乐人习惯），内部转成 0-based 左闭右开区间。
    兼容中文冒号与「至 / 到」。
    """
    rows: list[tuple[int, int, str]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        table = _parse_table_row(stripped)
        if table:
            rows.append(table)
            continue
        match = _SECTION_LINE.match(stripped)
        if match:
            rows.append(
                (
                    int(match.group("start")),
                    int(match.group("end")),
                    normalize_section_name(match.group("name")),
                )
            )
    if not rows:
        raise SectionError("sections.md 未解析出任何段落行")
    rows.sort(key=lambda row: row[0])
    sections: list[Section] = []
    for start, end, name in rows:
        if end < start:
            raise SectionError(f"{name}: 结束小节 {end} 早于起始小节 {start}")
        sections.append(Section(name=name, start_bar=start - 1, end_bar=end))
    plan = SectionPlan(sections)
    plan.validate()
    return plan


def _parse_table_row(line: str) -> tuple[int, int, str] | None:
    """`| Chorus | 9 | 16 |` 这类 markdown 表格行。"""
    if not line.startswith("|") or set(line) <= set("|-: "):
        return None
    cells = [cell.strip() for cell in line.strip("|").split("|")]
    if len(cells) < 3:
        return None
    name, start, end = cells[0], cells[1], cells[2]
    if not name or not start.isdigit() or not end.isdigit():
        return None
    return int(start), int(end), normalize_section_name(name)


def parse_chords_txt(text: str) -> dict[int, str]:
    """读 `chords.txt`（#50）：逐小节 → 和弦符号。1-based 行号转 0-based 小节的键。"""
    chords: dict[int, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _BAR_CHORD.match(stripped)
        if not match:
            continue
        bar = int(match.group("bar")) - 1
        if bar in chords:
            raise SectionError(f"chords.txt 第 {bar + 1} 小节重复定义")
        chords[bar] = match.group("chord")
    return chords


def read_musicxml_context(path: str | Path) -> dict[str, object]:
    """从冻结基准谱 `ground_truth.musicxml` 只读抽取调号/拍号/BPM（#48）。

    **只**读 `<key>` / `<time>` / `<sound tempo>` / 小节计数；不解析音符，
    不做 MusicXML 完整性校验。找不到的字段不返回，由调用方兜底。
    """
    root = ET.parse(str(path)).getroot()
    info: dict[str, object] = {}
    key_el = root.find(".//key")
    if key_el is not None:
        fifths_el = key_el.find("fifths")
        mode_el = key_el.find("mode")
        if fifths_el is not None and fifths_el.text is not None:
            info["fifths"] = int(fifths_el.text)
            info["key"] = key_from_fifths(int(fifths_el.text))
        if mode_el is not None and mode_el.text:
            info["mode"] = mode_el.text.strip().lower()
    time_el = root.find(".//time")
    if time_el is not None:
        beats_el, unit_el = time_el.find("beats"), time_el.find("beat-type")
        if beats_el is not None and beats_el.text:
            info["beats_per_bar"] = int(beats_el.text)
        if unit_el is not None and unit_el.text:
            info["beat_unit"] = int(unit_el.text)
    sound_el = root.find(".//sound[@tempo]")
    if sound_el is not None:
        info["bpm"] = round(float(sound_el.get("tempo", "0")))
    info["bars"] = len(root.findall(".//measure"))
    return info


#: fifths（升降号数）→ 大调音名。
_FIFTHS_MAJOR = {
    -7: "Cb", -6: "Gb", -5: "Db", -4: "Ab", -3: "Eb", -2: "Bb", -1: "F",
    0: "C", 1: "G", 2: "D", 3: "A", 4: "E", 5: "B", 6: "F#", 7: "C#",
}
#: fifths → 关系小调。
_FIFTHS_MINOR = {
    -7: "Ab", -6: "Eb", -5: "Bb", -4: "F", -3: "C", -2: "G", -1: "D",
    0: "A", 1: "E", 2: "B", 3: "F#", 4: "C#", 5: "G#", 6: "D#", 7: "A#",
}


def key_from_fifths(fifths: int, mode: str = "major") -> KeySignature:
    """MusicXML 的 `<fifths>` + `<mode>` → `KeySignature`。"""
    if fifths not in _FIFTHS_MAJOR:
        raise SectionError(f"fifths 超出 ±7: {fifths}")
    if mode == "minor":
        return KeySignature(_FIFTHS_MINOR[fifths], "minor")
    return KeySignature(_FIFTHS_MAJOR[fifths], "major")
