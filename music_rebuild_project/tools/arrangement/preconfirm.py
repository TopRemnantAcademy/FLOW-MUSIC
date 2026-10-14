"""编曲前确认校验器（#48）—— 规格完整性 / 一致性判定。

在编曲 agent 动手前，把一份「编曲规格 JSON」过一遍：缺字段、类型错、逻辑矛盾，
一次全报出来（而不是修一个跑一次）。本模块**不生成 MIDI**，也不写任何数据目录。

## 输入规格 schema（JSON）

```json
{
  "song_id": "song01",
  "key": "F#m",
  "time_sig": "4/4",
  "tempo": 120,
  "bars": 34,
  "repeats": 1,
  "sections": [{"name": "Intro", "bars": 4}, {"name": "Verse", "bars": 8}],
  "chord_marks": {"1": "F#m", "2": "Bm", "3": "C#7"}
}
```

字段口径：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `key` | str | 是 | 调号，如 `C` / `Am` / `F#m`；大小写与别名归一 |
| `time_sig` | str | 是 | 拍号，如 `4/4` / `3/4` / `6/8` |
| `tempo` | int | 是 | BPM，合理区间 40–240 |
| `bars` | int | 是 | 全曲总小节数（正整数） |
| `sections` | list | 否 | 段落列表，`bars` 之和须等于 `bars`（一致校验） |
| `repeats` | int | 否 | 反复次数；给出时 `bars` 须等于各段落小节和 |
| `chord_marks` | dict\\|list | 否 | 逐小节和弦；键为 1-based 小节号或顺序列表；值可为 `"TBD"` |

## 退出码语义（与 `tools/render/cli.py` 对齐）

- `0` = PASS：无 FAIL（可含 WARN）
- `1` = FAIL：至少一项 FAIL（缺字段 / 类型错 / 小节数不一致 / 和弦非法）
- `2` = 用法错误：参数错、文件不存在、JSON 解析失败

`--strict` 让 WARN 也升级为 FAIL（退出码 1）。stdout 恒为 JSON 报告。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------
# 词汇表：**复用** tools/midi 的实现，本模块不另立一份规则
# --------------------------------------------------------------------------

try:  # 作为包导入（python -m music_rebuild_project.tools.arrangement.preconfirm）
    from ..midi.bass import ChordSymbol, BassError
    from ..midi.sections import SECTION_ORDER, normalize_section_name, SectionError
    from ..midi.sections import KeySignature
except ImportError:  # pragma: no cover - 直接按文件路径加载时的兜底
    _TOOLS = Path(__file__).resolve().parents[1]
    if str(_TOOLS) not in sys.path:
        sys.path.insert(0, str(_TOOLS))
    from midi.bass import ChordSymbol, BassError  # type: ignore[no-redef]
    from midi.sections import (  # type: ignore[no-redef]
        SECTION_ORDER,
        KeySignature,
        SectionError,
        normalize_section_name,
    )

#: 合法拍号白名单。**刻意收窄**：编曲落地的是这几种常见拍号，
#: 12/8、5/4 等要到有人真的需要时再开（开一个词表容易，收回难）。
VALID_TIME_SIGS = ("4/4", "3/4", "6/8")

#: BPM 合理区间（issue #48 正文）。
TEMPO_MIN, TEMPO_MAX = 40, 240

#: 每段/整体小节数的上限：超过 999 基本是填错了单位（秒当小节？）。
MAX_BARS = 999

#: 调外和弦占比超过此值时提示 WARN —— 大概率是调号写错，但**不 FAIL**：
#: 借用和弦（modal interchange）是合法手法，判定权交给人。
OUT_OF_KEY_WARN_RATIO = 0.5

#: `chord_marks` 里表示「本小节和弦待定」的记号。
TBD_MARKERS = frozenset({"tbd", "?", "待定", "-", "n/a", "none"})

#: 调号文本：根音 + 可选 #/b + 可选 mode（m / min / minor / maj / major）。
_KEY_RE = re.compile(
    r"^(?P<root>[A-Ga-g][#b]?)(?P<mode>m|min|minor|maj|major)?$",
    re.IGNORECASE,
)

#: 拍号文本：`4/4`（两侧可有空白）。
_TIME_SIG_RE = re.compile(r"^(?P<beats>\d{1,2})\s*/\s*(?P<unit>\d{1,2})$")


# --------------------------------------------------------------------------
# 报告模型
# --------------------------------------------------------------------------


@dataclass
class Finding:
    """一条校验结论。`level` ∈ {FAIL, WARN}；`field` 是字段路径，便于定位。"""

    level: str
    field: str
    code: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {
            "level": self.level,
            "field": self.field,
            "code": self.code,
            "message": self.message,
        }


@dataclass
class Report:
    """校验报告：一次收齐所有 FAIL / WARN。"""

    findings: list[Finding] = field(default_factory=list)

    def fail(self, field_name: str, code: str, message: str) -> None:
        self.findings.append(Finding("FAIL", field_name, code, message))

    def warn(self, field_name: str, code: str, message: str) -> None:
        self.findings.append(Finding("WARN", field_name, code, message))

    @property
    def fails(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "FAIL"]

    @property
    def warns(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "WARN"]

    @property
    def ok(self) -> bool:
        return not self.fails

    def to_dict(self, strict: bool = False) -> dict[str, Any]:
        exit_code = 0 if self.ok else 1
        if strict and self.warns:
            exit_code = 1
        return {
            "status": "PASS" if exit_code == 0 else "FAIL",
            "exit_code": exit_code,
            "summary": {
                "fail": len(self.fails),
                "warn": len(self.warns),
                "strict": strict,
            },
            "findings": [f.to_dict() for f in self.findings],
        }


# --------------------------------------------------------------------------
# 字段级校验
# --------------------------------------------------------------------------


def _is_int(value: Any) -> bool:
    """真整数判定：`True` 是 `int` 的实例，但不是小节数。"""
    return isinstance(value, int) and not isinstance(value, bool)


def normalize_key(raw: str) -> str:
    """调号归一：`am`→`Am`、`f#min`→`F#m`、`Db`→`Db`。

    返回规范化后的字符串（根音首字母大写，升/降号小写，mode 收敛为 m/空）。
    不合法的调号抛 `ValueError` —— 调用方捕获后落成 FAIL。
    """
    text = raw.strip()
    match = _KEY_RE.match(text)
    if not match:
        raise ValueError(f"调号格式非法: {raw!r}（示例: C / Am / F#m）")
    root = match.group("root")
    root = root[0].upper() + root[1:].lower()
    mode_raw = (match.group("mode") or "").lower()
    if mode_raw in ("m", "min", "minor"):
        suffix = "m"
    else:
        suffix = ""
    normalized = root + suffix
    try:
        KeySignature(root, "minor" if suffix else "major")
    except SectionError as exc:
        raise ValueError(f"调号根音非法: {raw!r}（{exc}）") from exc
    return normalized


def key_signature(normalized_key: str) -> KeySignature:
    """归一调号 → `tools.midi.sections.KeySignature`（唯一权威的调号模型）。"""
    if normalized_key.endswith("m"):
        return KeySignature(normalized_key[:-1], "minor")
    return KeySignature(normalized_key, "major")


def key_tonic(normalized_key: str) -> tuple[int, str]:
    """归一调号 → (主音半音位置, mode)。"""
    sig = key_signature(normalized_key)
    return sig.tonic, sig.mode


def parse_time_sig(raw: Any) -> str:
    """拍号归一：返回 `beats/unit` 规范化文本；非法抛 `ValueError`。"""
    if not isinstance(raw, str):
        raise ValueError(f"拍号必须是字符串: {raw!r}")
    match = _TIME_SIG_RE.match(raw.strip())
    if not match:
        raise ValueError(f"拍号格式非法: {raw!r}（示例: 4/4 / 3/4 / 6/8）")
    normalized = f"{int(match.group('beats'))}/{int(match.group('unit'))}"
    if normalized not in VALID_TIME_SIGS:
        raise ValueError(
            f"不支持的拍号: {normalized}（允许: {', '.join(VALID_TIME_SIGS)}）"
        )
    return normalized


def is_tbd(raw: Any) -> bool:
    """判断和弦标记是否为「待定」。"""
    return isinstance(raw, str) and raw.strip().lower() in TBD_MARKERS


def chord_root_in_scale(chord_text: str, signature: KeySignature) -> bool:
    """和弦**根音**是否落在调内自然音阶上。

    只判根音（不判和弦性质）—— 这是 WARN 级提示，不是乐理裁判：
    `Dm` 在 C 大调里是 ii 级，`D`（大三）就带上了一丝离调味，但两者根音同级。
    查不到的交给格式校验去报，此处返回 `True` 不重复计错。
    """
    try:
        symbol = ChordSymbol.parse(chord_text)
    except BassError:
        return True
    return (symbol.root - signature.tonic) % 12 in signature.scale


# --------------------------------------------------------------------------
# 顶层校验入口
# --------------------------------------------------------------------------


def validate_spec(spec: Any, report: Report | None = None) -> Report:
    """校验一份编曲规格（已解析为 Python 对象），返回报告。

    **不抛异常**（除 `spec` 根本不是 dict）：所有问题落成 FAIL/WARN，
    让调用方一次拿到全貌。
    """
    rep = report or Report()
    if not isinstance(spec, dict):
        rep.fail("$", "SPEC_TYPE", f"规格必须是 JSON 对象，实际是 {type(spec).__name__}")
        return rep

    _check_key(spec, rep)
    _check_time_sig(spec, rep)
    _check_tempo(spec, rep)
    bars = _check_bars(spec, rep)
    _check_sections(spec, rep, bars)
    _check_chord_marks(spec, rep, bars)
    return rep


def _require(spec: dict, name: str, rep: Report) -> Any:
    """取必填字段；缺失时记一条 FAIL 并返回哨兵 `_MISSING`。"""
    if name not in spec:
        rep.fail(name, "MISSING", f"缺少必填字段 {name!r}")
        return _MISSING
    return spec[name]


class _Missing:
    """哨兵：区分「字段缺失」与「字段值是 None」。"""

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return "<MISSING>"


_MISSING = _Missing()


def _check_key(spec: dict, rep: Report) -> str | None:
    value = _require(spec, "key", rep)
    if value is _MISSING:
        return None
    if not isinstance(value, str):
        rep.fail("key", "TYPE", f"key 必须是字符串，实际是 {type(value).__name__}")
        return None
    try:
        normalized = normalize_key(value)
    except ValueError as exc:
        rep.fail("key", "INVALID", str(exc))
        return None
    if normalized != value.strip():
        rep.warn("key", "NORMALIZED", f"调号 {value!r} 已归一为 {normalized!r}")
    return normalized


def _check_time_sig(spec: dict, rep: Report) -> str | None:
    value = _require(spec, "time_sig", rep)
    if value is _MISSING:
        return None
    try:
        return parse_time_sig(value)
    except ValueError as exc:
        rep.fail("time_sig", "INVALID", str(exc))
        return None


def _check_tempo(spec: dict, rep: Report) -> int | None:
    value = _require(spec, "tempo", rep)
    if value is _MISSING:
        return None
    if not _is_int(value):
        rep.fail("tempo", "TYPE", f"tempo 必须是整数 BPM，实际是 {value!r}")
        return None
    if not TEMPO_MIN <= value <= TEMPO_MAX:
        rep.fail(
            "tempo",
            "RANGE",
            f"tempo={value} 超出合理区间 [{TEMPO_MIN}, {TEMPO_MAX}]",
        )
        return value
    return value


def _check_bars(spec: dict, rep: Report) -> int | None:
    value = _require(spec, "bars", rep)
    if value is _MISSING:
        return None
    if not _is_int(value):
        rep.fail("bars", "TYPE", f"bars 必须是整数，实际是 {value!r}")
        return None
    if value <= 0:
        rep.fail("bars", "RANGE", f"bars 必须是正整数，实际是 {value}")
        return None
    if value > MAX_BARS:
        rep.fail("bars", "RANGE", f"bars={value} 超过上限 {MAX_BARS}（单位填错？）")
        return value
    return value


def _check_sections(spec: dict, rep: Report, bars: int | None) -> None:
    """`sections` + `repeats` 的小节数一致性。

    规则（issue #48 第 3 项）：`bars`（总小节数）应等于各段落小节之和。
    没有 `sections` 时不判 —— 只给 `bars` 也是合法的极简规格。
    """
    sections = spec.get("sections")
    repeats = spec.get("repeats")

    if repeats is not None:
        if not _is_int(repeats):
            rep.fail("repeats", "TYPE", f"repeats 必须是整数，实际是 {repeats!r}")
        elif repeats < 0:
            rep.fail("repeats", "RANGE", f"repeats 不能为负，实际是 {repeats}")

    if sections is None:
        return
    if not isinstance(sections, list):
        rep.fail("sections", "TYPE", f"sections 必须是数组，实际是 {type(sections).__name__}")
        return
    if not sections:
        rep.fail("sections", "EMPTY", "sections 不能为空数组")
        return

    total = 0
    seen: set[str] = set()
    for index, item in enumerate(sections):
        path = f"sections[{index}]"
        if not isinstance(item, dict):
            rep.fail(path, "TYPE", f"段落必须是对象，实际是 {type(item).__name__}")
            continue
        name = item.get("name", _MISSING)
        length = item.get("bars", _MISSING)
        if name is _MISSING:
            rep.fail(f"{path}.name", "MISSING", "段落缺少 name")
        elif not isinstance(name, str):
            rep.fail(f"{path}.name", "TYPE", f"段落 name 必须是字符串，实际是 {name!r}")
        else:
            try:
                seen.add(normalize_section_name(name))
            except SectionError as exc:
                rep.fail(f"{path}.name", "INVALID", str(exc))
        if length is _MISSING:
            rep.fail(f"{path}.bars", "MISSING", "段落缺少 bars")
        elif not _is_int(length):
            rep.fail(f"{path}.bars", "TYPE", f"段落 bars 必须是整数，实际是 {length!r}")
        elif length <= 0:
            rep.fail(f"{path}.bars", "RANGE", f"段落 bars 必须是正整数，实际是 {length}")
        else:
            total += length

    if bars is None:
        return
    if total != bars:
        rep.fail(
            "bars",
            "BAR_MISMATCH",
            f"总小节数 bars={bars} 与各段落小节和 {total} 不一致（差 {bars - total:+d}）",
        )
    elif repeats:
        # repeats 只影响演奏遍数，不改变记谱小节数 —— 记一条说明，不算问题。
        rep.warn(
            "repeats",
            "REPEAT_NOTE",
            f"repeats={repeats}：记谱 {bars} 小节，实际演奏约 {bars * (repeats + 1)} 小节",
        )


def _check_chord_marks(spec: dict, rep: Report, bars: int | None) -> None:
    """逐小节和弦标记：格式合法 + 数量 = 总小节数（或显式标注「待定」）。"""
    marks = spec.get("chord_marks")
    if marks is None:
        rep.warn("chord_marks", "OMITTED", "未提供 chord_marks：编曲前建议补齐或标注待定")
        return

    items = _coerce_chord_marks(marks, rep)
    if items is None:
        return

    signature: KeySignature | None = None
    key_text = spec.get("key")
    if isinstance(key_text, str):
        try:
            signature = key_signature(normalize_key(key_text))
        except ValueError:
            signature = None  # key 的错已单独报过，这里不再叠加

    total_chords = 0
    in_key = 0
    tbd_count = 0
    for bar_label, value, path in items:
        if is_tbd(value):
            tbd_count += 1
            continue
        if not isinstance(value, str):
            rep.fail(path, "TYPE", f"第 {bar_label} 小节和弦必须是字符串，实际是 {value!r}")
            continue
        try:
            ChordSymbol.parse(value)
        except BassError as exc:
            rep.fail(path, "INVALID", f"第 {bar_label} 小节和弦格式非法：{exc}")
            continue
        total_chords += 1
        if signature is not None and chord_root_in_scale(value, signature):
            in_key += 1

    if bars is not None and len(items) != bars:
        rep.fail(
            "chord_marks",
            "COUNT_MISMATCH",
            f"chord_marks 覆盖 {len(items)} 小节，与总小节数 {bars} 不一致",
        )

    if total_chords and signature is not None:
        out_ratio = (total_chords - in_key) / total_chords
        if out_ratio > OUT_OF_KEY_WARN_RATIO:
            rep.warn(
                "chord_marks",
                "OUT_OF_KEY",
                f"调外和弦占比 {out_ratio:.0%}（>{OUT_OF_KEY_WARN_RATIO:.0%}），"
                f"确认调号 {key_text!r} 是否正确",
            )
    if tbd_count:
        rep.warn(
            "chord_marks",
            "TBD",
            f"{tbd_count} 个小节和弦待定 —— 编曲固化前须人工批准（#49）",
        )


def _coerce_chord_marks(
    marks: Any, rep: Report
) -> list[tuple[str, Any, str]] | None:
    """把 `chord_marks` 归一为 `[(bar_label, value, field_path), ...]`（按小节序）。

    支持两种写法：
      - dict：键为 1-based 小节号字符串/整数（`{"1": "C", "2": "G"}`）
      - list：按顺序逐小节（`["C", "G"]`，缺失位填 `"TBD"`）
    """
    items: list[tuple[str, Any, str]] = []
    if isinstance(marks, dict):
        for raw_bar, value in sorted(marks.items(), key=_bar_sort_key):
            try:
                bar_no = int(raw_bar)
            except (TypeError, ValueError):
                rep.fail("chord_marks", "KEYS", f"和弦键必须是 1-based 小节号，实际是 {raw_bar!r}")
                continue
            if bar_no <= 0:
                rep.fail("chord_marks", "KEYS", f"小节号必须从 1 开始，实际是 {bar_no}")
                continue
            items.append((str(bar_no), value, f"chord_marks[{bar_no}]"))
        return items
    if isinstance(marks, list):
        for index, value in enumerate(marks):
            items.append((str(index + 1), value, f"chord_marks[{index}]"))
        return items
    rep.fail("chord_marks", "TYPE", f"chord_marks 必须是对象或数组，实际是 {type(marks).__name__}")
    return None


def _bar_sort_key(pair: tuple[Any, Any]) -> tuple[int, str]:
    """按键排序：能转成整数的按键值排，其余按字符串兜底（避免排序时炸）。"""
    try:
        return (int(pair[0]), "")
    except (TypeError, ValueError):
        return (1 << 30, str(pair[0]))


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def load_spec(path: str | Path) -> Any:
    """读一份规格 JSON。文件缺失 / 非法 JSON → 抛 `ValueError`（调用方落成退出码 2）。"""
    file = Path(path)
    if not file.is_file():
        raise ValueError(f"规格文件不存在: {file}")
    try:
        return json.loads(file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"规格 JSON 解析失败: {exc}") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="preconfirm",
        description="编曲前确认校验器（#48）：校验编曲规格 JSON 的完整性/一致性",
    )
    parser.add_argument("--spec", required=True, help="编曲规格 JSON 文件路径")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="WARN 也视为失败（退出码 1）",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        spec = load_spec(args.spec)
    except ValueError as exc:
        print(json.dumps({"status": "ERROR", "exit_code": 2, "message": str(exc)},
                         ensure_ascii=False, indent=2))
        return 2

    report = validate_spec(spec)
    payload = report.to_dict(strict=args.strict)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return int(payload["exit_code"])


if __name__ == "__main__":
    sys.exit(main())
