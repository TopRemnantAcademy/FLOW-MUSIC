"""chords.txt 记录与规范化（#50）。

把「逐小节和弦标记」变成**规范化、可校验、可复用**的文本资产，供
`tools/midi`（#52/#53/#54）消费。

## 输入形态

- 自由文本（每行一条，或一行多和弦用空白分隔）
- JSON：字符串 / 字符串列表 / `{小节号: 和弦}` 映射 / 对象列表
  （`{"bar": 3, "chords": ["Dm7", "G7"]}`）

标记支持四种书写形态：

```text
1 Cmaj7                 小节号 + 和弦
2 Am7                   小节号 + 和弦（另一种性质）
3 Dm7 G7                同一小节内多个和弦，空格分隔
Cmaj7                   裸和弦——按出现顺序（第 1、2、3… 小节）编号
2: Am7                  小节号 + 冒号分隔符
```

小节号按 **1-based** 书写（音乐人习惯）。跨行/跨输入的**乱序小节被排序**，
重复小节**显式报错**（静默去重会掩盖人工标注事故）。

## 输出契约（`chords.txt`）

```text
# chords.txt · 由 tools/arrangement/chords.py 规范化
1 Cmaj7
2 Am7
3 Dm7 G7
```

- 每行 `<小节号> <和弦>[ <和弦>...]`，小节号 1-based 升序、连续（1..N）。
- 无空行、无尾随空白，UTF-8 + LF 结尾（`\\n` 收尾，无多余空行）。
- 一律**规范化的和弦名**（见 `normalize_chord`），因此「写法变体」被收敛。

## 校验

每条标记做**根音 / 性质 / 低音**三段解析，非法输入抛 `ChordSyntaxError`，
带 `line`（行号，1-based 行优先；行内序号为 `index`）与原文。例如：

```text
chords.txt:2: 非法根音 'H7'（合法音名: A-G + 可选 #/b）
```

## 与既有能力的边界（刻意的不重复）

`normalize_chord` / `Chord` 是本包**自洽**的规范层。之所以不复用
`tools/midi/bass.py` 的 `ChordSymbol`，是因为二者的**契约不同**：

| | 本包 `parse_chord` | `midi.bass.ChordSymbol.parse` |
|---|---|---|
| 非法输入 | 抛带行号错误 | 抛 `BassError`（无位置信息） |
| 别名 | 显式归一（`M7`→`maj7`、`-`→`min`） | 保留原样 |
| 用途 | 文本规范化 / 评审 | 生成时的半音计算 |

两者对**合法且规范书写**的和弦解析结果一致，有 `test_parity_with_midi_bass`
交叉断言钉住。`tools/midi/sections.py::parse_chords_txt` 是「宽松读取器」
（畸形行静默跳过，给生成侧兜底），本包是「严格校验器」——前者用于跑，
后者用于卡。用 `load_chord_map()` 可从 `chords.txt` 一键转成 MIDI 侧要的映射。

## 退出码（CLI）

| 码 | 含义 |
|---|---|
| 0 | 校验通过（`validate`）或成功写出（`normalize`） |
| 1 | 校验失败：存在非法标记 / 小节号不连续或重复 |
| 2 | 用法或读取错误：文件不存在、JSON 解析失败、`--out` 无法写入 |
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

#: 头行内容（重跑规范化时识别并覆盖，保证幂等）。
HEADER = "# chords.txt · 由 tools/arrangement/chords.py 规范化"

#: 和弦性质枚举：原文写法 → 规范化写法。
QUALITY_ALIASES: dict[str, str] = {
    "": "maj",
    "maj": "maj",
    "major": "maj",
    "M": "maj",
    "ma": "maj",
    "min": "min",
    "minor": "min",
    "m": "min",
    "-": "min",
    "min7": "min7",
    "m7": "min7",
    "-7": "min7",
    "7": "dom7",
    "dom7": "dom7",
    "maj7": "maj7",
    "ma7": "maj7",
    "M7": "maj7",
    "Δ7": "maj7",
    "Δ": "maj7",
    "sus2": "sus2",
    "sus4": "sus4",
    "sus": "sus4",
    "dim": "dim",
    "°": "dim",
    "o": "dim",
    "aug": "aug",
    "+": "aug",
}

#: 以「性质在前、无根音」开头的写法（`M7` = 大三和弦+七音，`-7` = 小七）。
#: 这些在通用记号里默认建在 **C** 上：`M7` → `Cmaj7`。可选地后面跟 `/低音`。
#: 之所以单独一张表：`M` 不在 A-G 里，主解析器第一段就会判它非法根音，
#: 得先归一化成「C + 性质」再进主流程 —— 否则只能靠正则回溯，太容易误伤。
_LEADING_QUALITY_ALIASES: dict[str, str] = {
    "M7": "maj7",
    "maj7": "maj7",
    "ma7": "maj7",
    "Δ7": "maj7",
    "Δ": "maj7",
    "M": "maj",
    "maj": "maj",
    "m7": "min7",
    "min7": "min7",
    "-7": "min7",
    "m": "min",
    "min": "min",
    "-": "min",
    "7": "dom7",
    "dom7": "dom7",
    "dim": "dim",
    "aug": "aug",
    "sus2": "sus2",
    "sus4": "sus4",
}

#: 无根音形态的重写：`M7` / `-7` / `Δ7`（可带 `/E`）。
_LEADING_QUALITY_RE = re.compile(
    r"^(?P<quality>M7|M|m7|m|ma7|maj7|maj|min7|min|-7|-|dom7|7|dim|aug|sus[24]|Δ7)"
    r"(?:/(?P<bass>[A-Ga-g][#b]?))?$"
)

#: 归一后的性质集合（校验与文档用）。
QUALITIES: tuple[str, ...] = (
    "maj", "min", "dom7", "maj7", "min7", "sus2", "sus4", "dim", "aug",
)

#: 合法半音（用于验证根音/低音）。
_PITCH_CLASSES = frozenset(
    ["C", "D", "E", "F", "G", "A", "B"]
    + [f"{n}{a}" for n in "CDEFGAB" for a in ("#", "b")]
)

#: 标记解析：可选小节号前缀 + 根音 + 可选性质 + 可选 `/低音`。
#: 顺序很重要 —— 性质太长会吃掉根音，所以音名单独一段、性质单独一段。
_CHORD_RE = re.compile(
    r"^(?P<root>[A-Ga-g])(?P<accidental>[#b]?)"
    r"(?P<quality>[A-Za-z+\-Δ°]*\d*)"
    r"(?:/(?P<bass>[A-Ga-g])(?P<bass_accidental>[#b]?))?$"
)

#: 行首的小节号前缀：`3`、`3:`、`3.`、`[3]`。
_BAR_PREFIX_RE = re.compile(r"^\s*\[?\s*(?P<bar>\d+)\s*\]?\s*[:.]?\s*(?P<rest>.*)$")

#: 行内分隔符（同一小节内多个和弦）。
_INLINE_SEP_RE = re.compile(r"[\s,|]+")


class ChordSyntaxError(ValueError):
    """和弦标记格式非法。

    属性：
      - `text`：原始标记
      - `line`：来源行号（1-based，`None` 表示非行式输入）
      - `index`：行内序号（1-based，`None` 表示整行一个标记）
      - `reason`：人读原因
    """

    def __init__(self, text: str, reason: str, line: int | None = None, index: int | None = None):
        self.text = text
        self.line = line
        self.index = index
        self.reason = reason
        super().__init__(str(self))

    def __str__(self) -> str:
        where = ""
        if self.line is not None:
            where = f"第 {self.line} 行"
            if self.index is not None and self.index > 1:
                where += f"第 {self.index} 个"
            where += ": "
        return f"{where}非法和弦 {self.text!r} —— {self.reason}"


@dataclass(frozen=True)
class Chord:
    """解析并归一后的和弦符号。`root`/`bass` 均为规范化音名（如 `Bb`、`F#`）。"""

    root: str
    quality: str = "maj"
    bass: str | None = None

    def __str__(self) -> str:
        text = f"{self.root}{'' if self.quality == 'maj' else self.quality}"
        if self.bass is not None:
            text += f"/{self.bass}"
        return text

    def transpose(self, target_root: str) -> "Chord":
        """换根音，性质与低音相对关系（`/` 是否与根同音）保留。"""
        bass = self.bass
        if bass is not None and bass == self.root:
            bass = target_root
        return Chord(target_root, self.quality, bass)


@dataclass(frozen=True)
class ChordEntry:
    """一个小节的记录：1-based 小节号 + 该小节内的和弦序列（≥1）。"""

    bar: int
    chords: tuple[Chord, ...]

    def __post_init__(self) -> None:
        if self.bar < 1:
            raise ChordSyntaxError(f"bar={self.bar}", "小节号必须 ≥ 1")
        if not self.chords:
            raise ChordSyntaxError(f"bar={self.bar}", "小节内至少需要一个和弦")

    def __str__(self) -> str:
        return f"{self.bar} " + " ".join(str(c) for c in self.chords)


@dataclass
class ChordSheet:
    """规范化后的整张和弦表。`entries` 按小节号升序。"""

    entries: list[ChordEntry] = field(default_factory=list)

    @property
    def bars(self) -> int:
        return len(self.entries)

    @property
    def first_bar(self) -> int:
        return self.entries[0].bar if self.entries else 1

    def to_text(self) -> str:
        """渲染为 `chords.txt` 文本（含头行，LF 结尾，无尾随空行）。"""
        lines = [HEADER]
        lines.extend(str(entry) for entry in self.entries)
        return "\n".join(lines) + "\n"

    def to_map(self) -> dict[int, str]:
        """→ `{0-based 小节: 和弦文本}`，供 `tools/midi.SongContext.chords` 消费。

        一个小节多个和弦时，用空格连接（`midi.bass.ChordSymbol.parse` 取不到时
        由生成侧兜底，与本包无关）。
        """
        return {entry.bar - 1: " ".join(str(c) for c in entry.chords) for entry in self.entries}

    def validate(self, *, bars: int | None = None, start: int = 1) -> "ChordSheet":
        """校验连续性/重复/越界。非法即抛 `ChordSyntaxError`。

        `bars` 给定总小节数时，还要求恰好覆盖 `[start, start+bars)`。
        """
        seen: set[int] = set()
        for entry in self.entries:
            if entry.bar in seen:
                raise ChordSyntaxError(f"bar={entry.bar}", f"小节 {entry.bar} 重复定义")
            seen.add(entry.bar)
        if not self.entries:
            return self
        expected = list(range(start, start + len(self.entries)))
        actual = sorted(seen)
        if actual != expected:
            missing = [b for b in expected if b not in seen]
            extra = [b for b in actual if b not in expected]
            problems = []
            if missing:
                problems.append(f"缺失小节 {missing}")
            if extra:
                problems.append(f"多余/越界小节 {extra}")
            raise ChordSyntaxError(
                f"第 {start}..{start + len(self.entries) - 1} 小节",
                "小节号必须连续且从 %d 起 —— %s" % (start, "；".join(problems)),
            )
        if bars is not None and len(self.entries) != bars:
            raise ChordSyntaxError(
                f"chords (n={len(self.entries)})",
                f"覆盖 {len(self.entries)} 小节，与期望总小节数 {bars} 不一致",
            )
        return self


# --------------------------------------------------------------------------
# 和弦解析
# --------------------------------------------------------------------------
def normalize_chord(text: str, *, line: int | None = None, index: int | None = None) -> Chord:
    """解析并归一化单个和弦标记。非法抛 :class:`ChordSyntaxError`。"""
    raw = text.strip()
    if not raw:
        raise ChordSyntaxError(text, "空标记", line, index)

    # 无根音形态（`M7` / `-7` / `Δ7`）：默认建在 C 上，先重写再走主解析。
    lead = _LEADING_QUALITY_RE.match(raw)
    if lead and raw[0].upper() not in "ABCDEFG":
        quality = _LEADING_QUALITY_ALIASES.get(lead.group("quality"))
        if quality is None:
            raise ChordSyntaxError(text, f"未知性质 {lead.group('quality')!r}", line, index)
        bass = lead.group("bass")
        return Chord(root="C", quality=quality, bass=(bass.upper() if bass else None))

    match = _CHORD_RE.match(raw)
    if not match:
        raise ChordSyntaxError(text, _explain(raw), line, index)

    root = match.group("root").upper() + match.group("accidental")
    if root not in _PITCH_CLASSES:
        raise ChordSyntaxError(text, f"非法根音 {root!r}（合法音名: A-G + 可选 #/b）", line, index)

    raw_quality = match.group("quality")
    if raw_quality not in QUALITY_ALIASES:
        raise ChordSyntaxError(
            text,
            f"未知性质 {raw_quality!r}（规范性质: {', '.join(QUALITIES)}）",
            line,
            index,
        )
    quality = QUALITY_ALIASES[raw_quality]

    bass = None
    if match.group("bass"):
        bass = match.group("bass").upper() + match.group("bass_accidental")
        if bass not in _PITCH_CLASSES:
            raise ChordSyntaxError(text, f"非法低音 {bass!r}", line, index)
    return Chord(root=root, quality=quality, bass=bass)


def _explain(raw: str) -> str:
    """给常见错法一句人话。"""
    if not raw[0].isalpha():
        return "标记应以音名 A-G 开头"
    if raw[0].upper() not in "ABCDEFG":
        return f"非法根音 {raw[0]!r}（合法音名: A-G + 可选 #/b）"
    for bad in ("##", "bb"):
        if bad in raw:
            return f"根音/低音用 {bad!r}（本项目只允许单个 # 或 b）"
    return "无法解析（形如 <小节号?> <根音><性质?></低音?>）"


# --------------------------------------------------------------------------
# 解析：自由文本 / JSON
# --------------------------------------------------------------------------
def _split_inline(rest: str) -> list[str]:
    """行内多个和弦：按空白/逗号/竖线切分，丢掉空段。"""
    return [token for token in _INLINE_SEP_RE.split(rest.strip()) if token]


def parse_chords(text: str) -> ChordSheet:
    """解析自由文本。

    支持两种行：显式小节号（`3 Dm7 G7`）与裸和弦（`Cmaj7`，按出现顺序编号）。
    显式小节号与裸行混用：裸行取「上一个小节号 + 1」。
    """
    entries: list[ChordEntry] = []
    cursor = 0  # 最近一次显式小节号
    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        bar: int | None = None
        rest = line
        prefix = _BAR_PREFIX_RE.match(line)
        if prefix and prefix.group("rest").strip() and prefix.group("bar"):
            bar = int(prefix.group("bar"))
            rest = prefix.group("rest")
        tokens = _split_inline(rest)
        if not tokens:
            continue
        chords = tuple(
            normalize_chord(tok, line=lineno, index=i if len(tokens) > 1 else None)
            for i, tok in enumerate(tokens, start=1)
        )
        if bar is None:
            bar = cursor + 1
        cursor = bar
        entries.append(ChordEntry(bar=bar, chords=chords))
    return ChordSheet(entries).validate_ordered()


def parse_json(payload: object, *, first_bar: int = 1) -> ChordSheet:
    """解析 JSON：字符串 / 字符串列表 / `{小节: 和弦}` / 对象列表。"""
    entries: list[ChordEntry] = []
    if isinstance(payload, str):
        return parse_chords(payload)

    if isinstance(payload, dict):
        if "chords" in payload and isinstance(payload["chords"], (list, str)):
            payload = payload["chords"]
        else:
            for key, value in payload.items():
                bar = _as_bar(key)
                entries.append(ChordEntry(bar, _as_chords(value, line=None)))
            return ChordSheet(entries).validate_ordered()

    if isinstance(payload, (list, tuple)):
        for index, item in enumerate(payload, start=1):
            if isinstance(item, dict):
                bar = _as_bar(item.get("bar", item.get("measure", index)))
                raw = item.get("chords", item.get("chord", item.get("symbol")))
                if raw is None:
                    raise ChordSyntaxError(str(item), "对象缺少 chord/chords 字段", index)
                entries.append(ChordEntry(bar, _as_chords(raw, line=index)))
            elif isinstance(item, str):
                tokens = _split_inline(item)
                if not tokens:
                    raise ChordSyntaxError(item, "空标记", index)
                entries.append(
                    ChordEntry(index, tuple(normalize_chord(t, line=index) for t in tokens))
                )
            elif isinstance(item, (list, tuple)):
                entries.append(ChordEntry(index, _as_chords(list(item), line=index)))
            else:
                raise ChordSyntaxError(str(item), f"不支持的列表元素类型 {type(item).__name__}", index)
        return ChordSheet(entries).validate_ordered()

    raise ChordSyntaxError(repr(payload), f"不支持的 JSON 顶层类型 {type(payload).__name__}")


def _as_bar(value: object) -> int:
    try:
        bar = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise ChordSyntaxError(str(value), f"小节号必须是整数: {value!r}") from None
    if bar < 1:
        raise ChordSyntaxError(str(value), f"小节号必须 ≥ 1（1-based）: {bar}")
    return bar


def _as_chords(value: object, *, line: int | None) -> tuple[Chord, ...]:
    if isinstance(value, (list, tuple)):
        return tuple(normalize_chord(str(v), line=line, index=i) for i, v in enumerate(value, 1))
    tokens = _split_inline(str(value))
    if not tokens:
        raise ChordSyntaxError(str(value), "空标记", line)
    return tuple(normalize_chord(t, line=line, index=i if len(tokens) > 1 else None)
                 for i, t in enumerate(tokens, 1))


#: `validate_ordered` 的别名 —— 语义就是排序 + 连续性校验。
def _validate_ordered(self: ChordSheet) -> ChordSheet:
    self.entries.sort(key=lambda entry: entry.bar)
    start = self.entries[0].bar if self.entries else 1
    return self.validate(start=start)


ChordSheet.validate_ordered = _validate_ordered  # type: ignore[attr-defined]


def parse_any(text: str) -> ChordSheet:
    """自动识别：以 `{` / `[` 开头当 JSON，否则当自由文本。"""
    stripped = text.lstrip()
    if stripped.startswith(("{", "[")):
        try:
            return parse_json(json.loads(text))
        except json.JSONDecodeError as exc:
            raise ChordSyntaxError(text[:40], f"JSON 解析失败: {exc.msg}（第 {exc.lineno} 行）") from None
    return parse_chords(text)


# --------------------------------------------------------------------------
# 与 `tools/midi` 的桥接（只依赖公开面，不重写生成逻辑）
# --------------------------------------------------------------------------
def load_chord_map(path: str | Path) -> dict[int, str]:
    """读 `chords.txt` → `{0-based 小节: 和弦}`。

    与 `tools.midi.sections.parse_chords_txt` 同口径（都转 0-based），
    但本函数**严格校验**：畸形行不会被静默跳过。
    """
    return parse_chords(Path(path).read_text(encoding="utf-8")).to_map()


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="chords",
        description="chords.txt 记录与规范化（#50）——把逐小节和弦标记变成可校验的文本资产",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            "  python -m music_rebuild_project.tools.arrangement.chords \\\n"
            "      --in 06_arrangement/song01/chords_raw.txt --out 06_arrangement/song01/chords.txt\n"
            "  python -m music_rebuild_project.tools.arrangement.chords --in chords_raw.md --validate\n"
            "\n"
            "退出码: 0=通过/写出成功, 1=校验失败(非法标记/小节不连续), 2=用法或读取错误"
        ),
    )
    parser.add_argument("--in", dest="src", help="输入文件（自由文本或 JSON）；缺省从 stdin 读")
    parser.add_argument("--out", dest="dst", help="写出规范化的 chords.txt（缺省只打印到 stdout）")
    parser.add_argument(
        "--first-bar", type=int, default=1,
        help="起始小节号（1-based，默认 1）——用于只规范化某一段落时对齐全局小节号",
    )
    parser.add_argument(
        "--validate", action="store_true",
        help="只校验不写文件；非法标记/小节不连续时以退出码 1 结束",
    )
    parser.add_argument(
        "--expect-bars", type=int, default=None,
        help="期望覆盖的小节总数；不符即以退出码 1 结束",
    )
    parser.add_argument(
        "--map", action="store_true",
        help="输出 `{0-based 小节: 和弦}` 的 JSON 映射（供 tools/midi 消费）",
    )
    return parser


def _read_input(src: str | None) -> str:
    if src:
        return Path(src).read_text(encoding="utf-8")
    return sys.stdin.read()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        text = _read_input(args.src)
    except OSError as exc:
        print(f"读取错误: {exc}", file=sys.stderr)
        return 2

    try:
        sheet = parse_any(text)
        if args.first_bar != 1 and sheet.entries:
            sheet = ChordSheet(
                [ChordEntry(entry.bar + args.first_bar - 1, entry.chords) for entry in sheet.entries]
            )
        start = sheet.first_bar if sheet.entries else 1
        sheet.validate(start=start, bars=args.expect_bars)
    except ChordSyntaxError as exc:
        print(f"校验失败: {exc}", file=sys.stderr)
        return 1

    if args.map:
        print(json.dumps(sheet.to_map(), ensure_ascii=False, indent=2))
        return 0

    rendered = sheet.to_text()
    if args.dst and not args.validate:
        try:
            dst = Path(args.dst)
            if dst.parent != Path(""):
                dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(rendered, encoding="utf-8", newline="\n")
        except OSError as exc:
            print(f"写入错误: {exc}", file=sys.stderr)
            return 2

    if not args.dst:
        sys.stdout.write(rendered)
    else:
        where = "（--validate 未写盘）" if args.validate else ""
        print(f"OK {sheet.bars} 小节 → {args.dst}{where}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
