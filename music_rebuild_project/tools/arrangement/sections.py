"""段落标注（#51）：声明式 `sections.spec.json` → 机器可读 `sections.json`。

上游是「人写的段落声明」，下游是混音 / 母带 / 视频对齐 —— 它们只认**数字**，
不认「大概在第 8 小节副歌进来」这种句子。本模块把句子变成可校验的数字。

    spec（人写，可带缺省，见下）        →  sections.json（机器读，全量展开）
    {"bpm":120, "sections":[{"type":"Intro","bars":4}, ...]}
                                        →  {"schema_version":1, "bars":34, "duration_s":68.0,
                                           "sections":[{"index":0,"type":"Intro","start_bar":0,
                                                        "end_bar":4,"start_s":0.0,"end_s":8.0,
                                                        "start_frame":0,"end_frame":352800,
                                                        "bpm":120,"beats_per_bar":4}, ...]}

三条设计口径（都是刻意的）：

1. **段落模型不在这里重新定义。** 规范段落名、1-based ↔ 0-based 的换算、
   `SectionPlan` 的「无损覆盖」不变量，`tools/midi/sections.py` 已经有一份实现
   （#48/#50/#51 共用）。本包 `import` 复用，只做「声明式 spec → 时间轴」这一层；
   复制一份出来等于让两处规则各自漂移。
2. **BPM 以「段」为单位，不是全曲一个值。** 谱面里最常见的变速点就是
   「副歌加速 / 桥段渐慢」，所以每段可带自己的 `bpm` / `beats_per_bar`，
   时间按**顺序累加**（前段结束秒 = 后段起始秒）。不这样写，`duration_s` 在
   变速曲上就是错的，而 G4-5 恰好要拿它当预期时长。
3. **不静默吞掉任何异常。** 未知段落类型、没声明 `bars` 又没有 `end_bar`、
   中途出现的显式间隙 —— 每条都带 `path` + `code` 的字段级错误推入 errors，
   CLI 据此返回非 0 退出码。想放行必须显式声明（`allow_gap` / `extra_types`），
   而不是靠「工具没发现」。

帧率默认 30 fps（非 drop-frame）—— 下游是**剪辑时间轴**，30 是通行默认值；
它只影响 `start_frame` / `end_frame` 两个派生字段，不影响秒与小节。
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

#: 复用 #48/#50/#51 的段落模型：规范段落名、别名收敛、SectionPlan 的不变量。
#: 两种跑法都要能 import：仓库根在 path 上（CI / `python -m music_rebuild_project.tools...`），
#: 以及 `cd music_rebuild_project` 把 `tools/` 当顶层包（与 `tools/midi/render_demo.py` 一致）。
#: 注意**没有** `tools/__init__.py`，所以这两种是不同模块路径，不能只留一个。
try:
    from music_rebuild_project.tools.midi import sections as midi_sections
except ImportError:  # pragma: no cover - 第二种跑法的兜底
    from tools.midi import sections as midi_sections  # type: ignore[no-redef]

#: 输出格式版本。字段增删时递增，下游据此判断要不要重新解析。
SCHEMA_VERSION = 1

#: 默认 BPM / 拍号。spec 没写时按这个算 —— 「默认 120 BPM 4/4」是编曲工具的通行约定。
DEFAULT_BPM = 120
DEFAULT_BEATS_PER_BAR = 4

#: 默认帧率（非 drop-frame）。只影响派生的帧号字段。
DEFAULT_FPS = 30

#: #51 点名的段落类型。规范名以 `tools/midi/sections.py` 的 `SECTION_ORDER` 为唯一来源。
REQUIRED_SECTION_TYPES = midi_sections.SECTION_ORDER

#: 附带放行的类型：`Instrumental` 出现在 #51 正文的枚举里，
#: `Pre-Chorus` 只是大小写变体（比较时统一小写），其余是常见编曲写法。
#: 注意「规范名」与「放行名」是两件事：放行进来的名字会**原样保留**在输出里，
#: 不会被改写成规范名 —— 下游拿到的必须就是人声明的那个名字。
EXTRA_SECTION_TYPES = ("Instrumental", "Pre-Chorus", "Post-chorus", "Solo", "Interlude", "Break")

#: 允许的类型全集（比较时小写化）。
ALLOWED_TYPES = tuple(REQUIRED_SECTION_TYPES) + EXTRA_SECTION_TYPES

#: 时长容差（秒）。G4-5 要的是 ±1s，这里默认取同一数值；
#: 但它校验的是**声明值之间**的一致性（预期时长 vs 逐段累加），不是 master 实际时长。
DEFAULT_TOLERANCE = 1.0


class SectionSpecError(ValueError):
    """spec 在结构层面就无法解析（不是 JSON 对象、缺 sections 等）。"""


@dataclass(frozen=True)
class Issue:
    """一条字段级问题。`path` 直接指向 spec 里的位置，便于人按行号去改。"""

    code: str
    path: str
    message: str
    fatal: bool = True

    def to_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "path": self.path,
            "message": self.message,
            "fatal": self.fatal,
        }

    def __str__(self) -> str:  # pragma: no cover - 只为 CLI 打印好看
        return f"[{self.code}] {self.path}: {self.message}"


@dataclass
class TimedSection:
    """一段带时间的标注：小节 + 秒 + 帧，三套坐标一次给全。

    `start_bar` / `end_bar` 是 **0-based 左闭右开**（沿用 `tools/midi/sections.py` 口径），
    `start_s` / `end_s` 由本段 BPM 决定，`bpm` / `beats_per_bar` 是**本段生效值**
    （段内无变速：spec 的粒度就是「一段一个 BPM」）。
    """

    index: int
    type: str
    start_bar: int
    end_bar: int
    start_s: float
    end_s: float
    bpm: int
    beats_per_bar: int
    label: str = ""
    gap_before_s: float = 0.0
    start_frame: int = 0
    end_frame: int = 0

    @property
    def bars(self) -> int:
        return self.end_bar - self.start_bar

    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s

    def to_dict(self) -> dict[str, object]:
        data: dict[str, object] = {
            "index": self.index,
            "type": self.type,
            "start_bar": self.start_bar,
            "end_bar": self.end_bar,
            "start_s": round(self.start_s, 6),
            "end_s": round(self.end_s, 6),
            "duration_s": round(self.duration_s, 6),
            "start_frame": self.start_frame,
            "end_frame": self.end_frame,
            "bpm": self.bpm,
            "beats_per_bar": self.beats_per_bar,
        }
        if self.label:
            data["label"] = self.label
        if self.gap_before_s:
            data["gap_before_s"] = round(self.gap_before_s, 6)
        return data


@dataclass
class Report:
    """校验结果。`ok` 只看 fatal 项 —— 警告（fatal=False）不影响退出码。"""

    issues: list[Issue] = field(default_factory=list)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.fatal]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if not i.fatal]

    @property
    def ok(self) -> bool:
        return not self.errors

    def add(self, code: str, path: str, message: str, fatal: bool = True) -> None:
        self.issues.append(Issue(code, path, message, fatal))

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "errors": [i.to_dict() for i in self.errors],
            "warnings": [i.to_dict() for i in self.warnings],
        }


# ---------------------------------------------------------------------------
# 类型校验
# ---------------------------------------------------------------------------


def is_allowed_type(name: str, extra_types: tuple[str, ...] = ()) -> bool:
    """段落类型是否放行。比较**大小写不敏感**（`#51` 正文自己就写过 `Pre-Chorus`）。"""
    key = name.strip().lower()
    allowed = [t.lower() for t in ALLOWED_TYPES] + [t.lower() for t in extra_types]
    return key in allowed


def canonical_type(name: str, extra_types: tuple[str, ...] = ()) -> str | None:
    """规范名（能收敛到 `SECTION_ORDER` 才有）；放行但非规范的返回 `None`。

    只作**提示**用（`type_canonical_hint`），不参与改写 —— 声明的名字保持原样。
    """
    try:
        return midi_sections.normalize_section_name(name)
    except midi_sections.SectionError:
        return None


# ---------------------------------------------------------------------------
# spec 解析
# ---------------------------------------------------------------------------


@dataclass
class _Segment:
    """一段的中间表示：小节区间已定，时间轴待算。"""

    index: int
    type: str
    start_bar: int
    end_bar: int
    bpm: int
    beats_per_bar: int
    label: str = ""
    gap_before_s: float = 0.0  # 本段之前显式声明的静默时长


@dataclass
class _Parsed:
    segments: list[_Segment]
    bpm: int
    beats_per_bar: int
    fps: int
    tolerance: float
    expected_duration_s: float | None
    allow_gap: bool


def _positive_int(
    container: dict, key: str, default: int, path: str, report: Report
) -> int:
    """读一个必须为正的整数字段；缺失取默认值，非法记错并**回落默认值**继续算。

    回落是刻意的：报错之外还给出一份能看的报告，比整份空白有用。
    """
    raw = container.get(key)
    if raw is None:
        return default
    value = _as_int(raw, path, report)
    if value is None:
        return default
    if value <= 0:
        report.add("invalid_field", path, f"{key} 必须为正，收到 {value}")
        return default
    return value


def _as_int(value: object, path: str, report: Report) -> int | None:
    """整数或整数值的浮点数字段。bool 是 int 的子类，得先挡掉（`True` 不是 1）。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        # `value:type` 比纯 `type` 好认：报错里直接能看到 4.5 / "4" / True 的区别。
        report.add("invalid_field", path, f"需要数字，收到 {value!r}（{type(value).__name__}）")
        return None
    if isinstance(value, float) and not value.is_integer():
        report.add("invalid_field", path, f"需要整数，收到 {value!r}")
        return None
    return int(value)


def _parse_spec(spec: object, report: Report, extra_types: tuple[str, ...] = ()) -> _Parsed:
    """把 spec 摊平成 `_Parsed`。所有能继续的错误都记进 report，尽量一次报全。

    非法值一律**回落到默认值继续算**（同时记一条 fatal 错误）：这样人拿到的
    是一份「除了这个字段以外都对」的结果，而不是一片空白。
    """
    if not isinstance(spec, dict):
        raise SectionSpecError(f"spec 顶层必须是 JSON 对象，收到 {type(spec).__name__}")

    bpm = _positive_int(spec, "bpm", DEFAULT_BPM, "$.bpm", report)
    beats_per_bar = _positive_int(spec, "beats_per_bar", DEFAULT_BEATS_PER_BAR, "$.beats_per_bar", report)
    fps = _positive_int(spec, "fps", DEFAULT_FPS, "$.fps", report)
    raw_tol = spec.get("duration_tolerance_s", DEFAULT_TOLERANCE)
    if isinstance(raw_tol, bool) or not isinstance(raw_tol, (int, float)):
        report.add(
            "invalid_field", "$.duration_tolerance_s",
            f"duration_tolerance_s 需要数字（秒），收到 {raw_tol!r}",
        )
        tolerance = DEFAULT_TOLERANCE
    else:
        tolerance = float(raw_tol)
    if tolerance < 0:
        report.add("invalid_field", "$.duration_tolerance_s", f"容差不能为负: {tolerance}")
        tolerance = DEFAULT_TOLERANCE
    allow_gap = bool(spec.get("allow_gap", False))
    expected = spec.get("duration_s")
    expected_duration_s = (
        float(expected)
        if isinstance(expected, (int, float)) and not isinstance(expected, bool)
        else None
    )
    if expected is not None and expected_duration_s is None:
        report.add("invalid_field", "$.duration_s", f"duration_s 需要数字（秒），收到 {expected!r}")

    raw_extra_types = spec.get("extra_types", ()) or ()
    if not isinstance(raw_extra_types, (list, tuple)) or not all(
        isinstance(t, str) and t.strip() for t in raw_extra_types
    ):
        report.add("invalid_field", "$.extra_types", "extra_types 必须是非空字符串数组")
        spec_extra_types: tuple[str, ...] = ()
    else:
        spec_extra_types = tuple(t.strip() for t in raw_extra_types)
    extra_types = spec_extra_types + tuple(extra_types)

    raw_sections = spec.get("sections")
    if not isinstance(raw_sections, list) or not raw_sections:
        raise SectionSpecError("spec.sections 必须是非空数组")

    segments: list[_Segment] = []
    # `cursor` = 「已覆盖到的 1-based 末小节号」（与 `last_bar` 同口径），0 表示还没覆盖。
    # 于是下一段的起点 = `cursor + 1`；首段起点 = 1。
    cursor = 0
    for index, raw in enumerate(raw_sections):
        path = f"$.sections[{index}]"
        if not isinstance(raw, dict):
            report.add("invalid_field", path, f"每段必须是 JSON 对象，收到 {type(raw).__name__}")
            continue
        name = raw.get("type", raw.get("name"))
        if not isinstance(name, str) or not name.strip():
            report.add("invalid_field", f"{path}.type", f"缺少段落类型，收到 {name!r}")
            continue
        name = name.strip()

        hint = canonical_type(name, extra_types)
        if not is_allowed_type(name, extra_types) and hint is None:
            report.add(
                "unknown_type",
                f"{path}.type",
                f"未知段落类型 {name!r}；允许: {', '.join(ALLOWED_TYPES)}"
                f"（自定义类型走 extra_types 显式放行）",
            )
            continue
        if hint is not None and name != hint:
            report.add(
                "type_canonical_hint",
                f"{path}.type",
                f"{name!r} 的规范写法是 {hint!r}（沿用 tools/midi 的别名表）",
                fatal=False,
            )

        seg_bpm = _positive_int(raw, "bpm", bpm, f"{path}.bpm", report)
        seg_bpb = _positive_int(raw, "beats_per_bar", beats_per_bar, f"{path}.beats_per_bar", report)

        raw_gap = raw.get("gap_before_s", 0)
        if raw_gap is None:
            gap_before_s = 0.0
        elif isinstance(raw_gap, bool) or not isinstance(raw_gap, (int, float)):
            report.add("invalid_field", f"{path}.gap_before_s", f"需要数字（秒），收到 {raw_gap!r}")
            gap_before_s = 0.0
        else:
            gap_before_s = float(raw_gap)
        if gap_before_s < 0:
            report.add("invalid_field", f"{path}.gap_before_s", f"gap_before_s 不能为负: {gap_before_s}")
            continue
        if gap_before_s > 0 and not allow_gap:
            report.add(
                "unexpected_gap",
                f"{path}.gap_before_s",
                f"第 {index} 段声明了 {gap_before_s}s 间隙，但顶层未设 allow_gap=true"
                "（间隙必须显式说明，不能在 spec 里悄悄出现）",
            )
            continue
        if index == 0 and gap_before_s > 0:
            report.add("invalid_field", f"{path}.gap_before_s", "第一段之前不能有间隙（曲目从 0 开始）")
            continue

        # 缺省起点与校验用同一个表达式，否则「缺省即合法」这条会自相矛盾。
        expected_start = cursor + 1
        raw_start = raw.get("start_bar")
        if raw_start is None:
            start_bar = expected_start
        else:
            start_bar = _as_int(raw_start, f"{path}.start_bar", report)
            if start_bar is None:
                continue
        if start_bar < 1:
            report.add("invalid_field", f"{path}.start_bar", f"start_bar 是 1-based，须 ≥ 1，收到 {start_bar}")
            continue

        # 单位约定（本文件唯一容易错的地方，只在这里解释一次）：
        #   `start_bar` / `last_bar` —— **1-based**，就是 spec 里人写的那个数，
        #                               校验与报错文案全程用它，别提前换算。
        #   `_Segment.start_bar` / `_Segment.end_bar` —— **0-based 左闭右开**，
        #                               与 `tools/midi.Section` 同口径，只在写入那一刻换算。
        # spec 里 `end_bar` 是 1-based **闭区间**末小节：`start_bar:3, end_bar:6`
        # 即「第 3、4、5、6 小节，共 4 小节」—— 换算与 `parse_sections_md` 的
        # `Section(name, start-1, end)` 逐字一致。
        raw_end = raw.get("end_bar")
        if raw_end is not None:
            last_bar = _as_int(raw_end, f"{path}.end_bar", report)
            if last_bar is None:
                continue
            if last_bar < start_bar:
                report.add(
                    "invalid_field",
                    f"{path}.end_bar",
                    f"end_bar ({last_bar}) 早于 start_bar ({start_bar})，本段为空或倒序"
                    "（end_bar 是闭区间末小节：第 3 小节一段应写 end_bar=3）",
                )
                continue
        else:
            raw_bars = raw.get("bars", raw.get("length_bars"))
            if raw_bars is None:
                report.add(
                    "missing_bars",
                    f"{path}.bars",
                    "缺少 bars，也没有 end_bar —— 段落长度必须能确定（bars 或 end_bar 二选一）",
                )
                continue
            bars = _as_int(raw_bars, f"{path}.bars", report)
            if bars is None:
                continue
            if bars <= 0:
                report.add("invalid_field", f"{path}.bars", f"bars 必须为正，收到 {bars}")
                continue
            last_bar = start_bar + bars - 1  # 1-based 闭区间末小节

        if index == 0 and start_bar != 1:
            report.add(
                "invalid_field",
                f"{path}.start_bar",
                f"第一段必须从第 1 小节开始，声明为 {start_bar}；"
                "曲目开头留空应写进第一段的 gap_before_s（且须 allow_gap=true）",
            )
            continue

        # 注：`raw.get("start_bar")` 的默认值只在键**缺失**时生效，显式 `null` 会原样返回，
        # 所以上面用 `is None` 把「没写」与「写了 null」都收敛到缺省行为。
        if start_bar < expected_start:
            # 重叠段的时间轴无法定义（同一小节属于两段）：报错并跳过，别把脏数据算进时间轴。
            report.add(
                "overlap",
                f"{path}.start_bar",
                f"第 {index} 段从第 {start_bar} 小节开始，与上一段（结束于第 {cursor} 小节）重叠；"
                "段落必须首尾相接，重叠处归属无法判定",
            )
            continue
        if start_bar > expected_start:
            report.add(
                "gap",
                f"{path}.start_bar",
                f"第 {index} 段从第 {start_bar} 小节开始，上一段结束于第 {cursor} 小节，"
                f"中间第 {expected_start}–{start_bar - 1} 小节没有归属；"
                "留空须回收进相邻段落，或改用 gap_before_s 显式声明静默",
            )

        label = raw.get("label")
        segments.append(
            _Segment(
                index=index,
                type=name,
                start_bar=start_bar - 1,  # 1-based 闭区间 → 0-based 左闭右开（唯一换算处）
                end_bar=last_bar,
                bpm=seg_bpm,
                beats_per_bar=seg_bpb,
                label=label.strip() if isinstance(label, str) else "",
                gap_before_s=gap_before_s,
            )
        )
        cursor = max(cursor, last_bar)

    return _Parsed(
        segments=segments,
        bpm=bpm,
        beats_per_bar=beats_per_bar,
        fps=fps,
        tolerance=tolerance,
        expected_duration_s=expected_duration_s,
        allow_gap=allow_gap,
    )


# ---------------------------------------------------------------------------
# 时间轴
# ---------------------------------------------------------------------------


def _build_timeline(parsed: _Parsed, report: Report) -> list[TimedSection]:
    """逐段累加时间。段内时长 = bars × beats_per_bar × 60 / bpm。"""
    timeline: list[TimedSection] = []
    clock = 0.0
    for segment in parsed.segments:
        clock += segment.gap_before_s
        bars = segment.end_bar - segment.start_bar
        span = bars * segment.beats_per_bar * 60.0 / segment.bpm
        timeline.append(
            TimedSection(
                index=segment.index,
                type=segment.type,
                start_bar=segment.start_bar,
                end_bar=segment.end_bar,
                start_s=clock,
                end_s=clock + span,
                bpm=segment.bpm,
                beats_per_bar=segment.beats_per_bar,
                label=segment.label,
                gap_before_s=segment.gap_before_s,
                start_frame=int(round(clock * parsed.fps)),
                end_frame=int(round((clock + span) * parsed.fps)),
            )
        )
        clock += span
    return timeline


def _check_consistency(parsed: _Parsed, timeline: list[TimedSection], report: Report) -> None:
    """段间连续性与对 `midi_sections.SectionPlan` 的交叉校验。"""
    for prev, current in zip(timeline, timeline[1:]):
        # 声明了静默的两段之间，秒本来就该差一个 gap —— 那不是缺陷，是 spec 的意图。
        if current.gap_before_s <= 0 and not _approx(prev.end_s, current.start_s, 1e-6):
            report.add(
                "discontinuous",
                f"$.sections[{current.index}]",
                f"上一段结束于 {prev.end_s:.6f}s，本段起始 {current.start_s:.6f}s —— 时间轴不连续",
            )
        # 小节区间的洞在解析阶段已按 `$.sections[i].start_bar` 报过（带缺哪几小节），
        # 这里不再重复；此处只兜住「时间轴」层面的一致性。

    if not timeline:
        return
    try:
        plan = midi_sections.SectionPlan(
            [
                midi_sections.Section(
                    name=s.type, start_bar=s.start_bar, end_bar=s.end_bar, label=s.label
                )
                for s in timeline
            ]
        )
        plan.validate()
    except midi_sections.SectionError as exc:
        # 与 `tools/midi` 的规则对齐：只要它认为这份结构不合法，这里就 FAIL。
        report.add("structure_invalid", "$.sections", f"tools/midi 的段落不变量不通过: {exc}")


def _approx(a: float, b: float, tol: float) -> bool:
    return abs(a - b) <= tol


# ---------------------------------------------------------------------------
# 公开 API
# ---------------------------------------------------------------------------


def annotate(spec: dict, extra_types: tuple[str, ...] = ()) -> tuple[dict, Report]:
    """spec（已解析的 dict）→ (`sections.json` 结构, 校验报告)。

    有 fatal 错误时第二个返回值 `ok is False`，但仍然给出**部分结果** ——
    调用方要能一次看到「哪几段算出来了、哪几段没算出来」，而不是拿到空列表重跑一轮。
    """
    report = Report()
    try:
        parsed = _parse_spec(spec, report, extra_types=extra_types)
    except SectionSpecError as exc:
        report.add("spec_invalid", "$", str(exc))
        return {}, report

    timeline = _build_timeline(parsed, report)
    _check_consistency(parsed, timeline, report)

    duration_s = timeline[-1].end_s if timeline else 0.0
    total_bars = timeline[-1].end_bar if timeline else 0

    if parsed.expected_duration_s is not None:
        delta = duration_s - parsed.expected_duration_s
        if not _approx(duration_s, parsed.expected_duration_s, parsed.tolerance):
            report.add(
                "duration_mismatch",
                "$.duration_s",
                f"逐段累加时长 {duration_s:.3f}s 与声明 duration_s "
                f"{parsed.expected_duration_s:.3f}s 相差 {delta:+.3f}s，"
                f"超出容差 ±{parsed.tolerance}s",
            )

    document: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "extra_types": list(_merged_extra_types(spec, extra_types)),
        "fps": parsed.fps,
        "bpm": parsed.bpm,
        "beats_per_bar": parsed.beats_per_bar,
        "bars": total_bars,
        "duration_s": round(duration_s, 6),
        "type_counts": _type_counts(timeline),
        "sections": [s.to_dict() for s in timeline],
        "validation": report.to_dict(),
    }
    if parsed.expected_duration_s is not None:
        document["expected_duration_s"] = parsed.expected_duration_s
        document["duration_tolerance_s"] = parsed.tolerance
    return document, report


def _merged_extra_types(spec: object, extra_types: tuple[str, ...]) -> tuple[str, ...]:
    """CLI 传入的 `--extra-type` 与 spec 自带的 `extra_types` 取并集（回显到输出）。"""
    merged = list(extra_types)
    if isinstance(spec, dict):
        raw = spec.get("extra_types", ()) or ()
        if isinstance(raw, (list, tuple)):
            for item in raw:
                if isinstance(item, str) and item.strip() and item.strip() not in merged:
                    merged.append(item.strip())
    return tuple(merged)


def _type_counts(timeline: list[TimedSection]) -> dict[str, int]:
    """每种类型出现几次 —— 混音端按类型套模板时要先知道有没有 Bridge。"""
    counts: dict[str, int] = {}
    for section in timeline:
        counts[section.type] = counts.get(section.type, 0) + 1
    return counts


def load_spec(path: str | Path) -> dict:
    """读 spec JSON。文件不存在 / 不是合法 JSON 时抛 `SectionSpecError`。"""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise SectionSpecError(f"无法读取 spec 文件 {path}: {exc}") from exc
    try:
        spec = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SectionSpecError(f"spec 不是合法 JSON（{path}）: {exc}") from exc
    return spec


def to_markdown(document: dict) -> str:
    """`sections.json` → 交给混音 / 视频对齐看的段落表。"""
    lines = [
        "# 段落标注（#51 自动生成）",
        "",
        f"共 {document.get('bars', 0)} 小节 / {document.get('duration_s', 0.0)} 秒，"
        f"{document.get('bpm')} BPM（默认）、{document.get('fps')} fps。",
        "",
        "| # | 段落 | 小节 | 时间（s） | 帧 | BPM | 拍号 |",
        "|---|---|---|---|---|---|---|",
    ]
    for section in document.get("sections", []):
        bars = f"{section['start_bar']}–{section['end_bar']}"
        span = f"{section['start_s']:.3f}–{section['end_s']:.3f}"
        frames = f"{section['start_frame']}–{section['end_frame']}"
        lines.append(
            f"| {section['index']} | {section['type']} | {bars} | {span} | {frames} "
            f"| {section['bpm']} | {section['beats_per_bar']}/4 |"
        )
    counts = document.get("type_counts") or {}
    if counts:
        lines += ["", "类型计数：" + "、".join(f"{k}×{v}" for k, v in counts.items())]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m music_rebuild_project.tools.arrangement.sections",
        description="段落标注（#51）：sections.spec.json → sections.json（不产媒体、不碰后端）",
    )
    parser.add_argument("--spec", required=True, help="段落声明 JSON（见 README 的 spec 契约）")
    parser.add_argument("--out", help="输出 sections.json 的路径；`-` 表示只打到 stdout")
    parser.add_argument("--md", help="顺带输出人读段落表（markdown）的路径")
    parser.add_argument(
        "--extra-type",
        action="append",
        default=[],
        metavar="NAME",
        help="额外放行的段落类型（可重复）；等价于 spec 里的 extra_types",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="把警告（如类型规范名提示）也当作失败，退出码 1",
    )
    parser.add_argument("--json", action="store_true", help="把校验报告打到 stdout")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    extra_types = tuple(args.extra_type)

    try:
        spec = load_spec(args.spec)
    except SectionSpecError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 2

    document, report = annotate(spec, extra_types=extra_types)

    for warning in report.warnings:
        print(f"WARN: {warning}", file=sys.stderr)
    for error in report.errors:
        print(f"FAIL: {error}", file=sys.stderr)

    if args.md and document.get("sections"):
        Path(args.md).write_text(to_markdown(document), encoding="utf-8")

    payload = json.dumps(document, ensure_ascii=False, indent=2)
    if args.out and args.out != "-":
        out_path = Path(args.out)
        if out_path.parent and str(out_path.parent) not in ("", "."):
            out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(payload + "\n", encoding="utf-8")

    if args.json or not args.out or args.out == "-":
        print(payload)

    if not report.ok:
        return 1
    if args.strict and report.warnings:
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI 入口
    raise SystemExit(main())
