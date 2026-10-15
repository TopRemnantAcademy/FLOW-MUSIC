"""无和弦标记时的和弦候选生成器（#49）。

给定调号 + 逐小节旋律（MIDI 音高），按**自然音阶三和弦**为每小节提名候选和弦。
产物一律标记 ``status=CANDIDATES`` + ``requires_human_approval=True`` ——
人工批准前**禁编曲固化**（原则 P3，见 ``00_docs/plan/01_pipeline_and_gates.md``）。

约束（与 tools/arrangement 其余工具一致）：
- 纯 stdlib，无第三方依赖；无 RNG（确定性，同输入同输出）；
- **不写 approved/**、不产 MIDI、不依赖 ffmpeg / 真实媒体；
- 只读输入 spec，输出候选 JSON；固化与否由人决定，本工具永不代劳。

退出码：0 = 候选已生成；1 = 校验失败（不静默退出）；2 = 用法 / 读取错误。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# ---- 音乐理论常量（纯查表，无随机） ------------------------------------------

NOTE_PC = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
PC_NAME = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

MAJOR_STEPS = (0, 2, 4, 5, 7, 9, 11)   # 自然大调（Ionian）
MINOR_STEPS = (0, 2, 3, 5, 7, 8, 10)   # 自然小调（Aeolian）

# 七级自然三和弦的级数（1-based，三度堆叠）；顺序即确定性的平分打破次序
TRIAD_DEGREES = {
    "I": (1, 3, 5), "ii": (2, 4, 6), "iii": (3, 5, 7),
    "IV": (4, 6, 1), "V": (5, 7, 2), "vi": (6, 1, 3), "vii": (7, 2, 4),
}
TRIAD_ORDER = ("I", "ii", "iii", "IV", "V", "vi", "vii")

# 各级性质：大调 / 自然小调
QUALITY = {
    "major": {"I": "maj", "ii": "min", "iii": "min", "IV": "maj",
              "V": "maj", "vi": "min", "vii": "dim"},
    "minor": {"i": "min", "ii": "dim", "III": "maj", "iv": "min",
              "v": "min", "VI": "maj", "VII": "maj"},
}
# 小调用小写级名展示（i ii° III iv v VI VII）
MINOR_ROMAN = {"I": "i", "ii": "ii", "iii": "III", "IV": "iv",
               "V": "v", "vi": "VI", "vii": "VII"}
QUALITY_SUFFIX = {"maj": "", "min": "m", "dim": "dim"}


def parse_key(key: str) -> tuple[int, str, str]:
    """``"C"`` / ``"Am"`` / ``"F#m"`` → ``(root_pc, mode, canonical)``。

    mode: ``"major"`` / ``"minor"``。canonical 用于回显（如 ``Am`` → ``A`` + minor）。
    """
    if not isinstance(key, str) or len(key) < 1:
        raise ValueError(f"调号必须是非空字符串，收到 {key!r}")
    root = key[0].upper()
    if root not in NOTE_PC:
        raise ValueError(f"非法调号根音 {key!r}（合法根音: A-G）")
    pc = NOTE_PC[root]
    rest = key[1:]
    accidental = ""
    while rest and rest[0] in "#b":
        if rest[0] == "#":
            pc = (pc + 1) % 12
            accidental += "#"
        else:  # b
            pc = (pc - 1) % 12
            accidental += "b"
        rest = rest[1:]
    low = rest.lower()
    if low in ("", "maj", "major"):
        mode = "major"
    elif low in ("m", "min", "minor"):
        mode = "minor"
    else:
        raise ValueError(
            f"调号后缀无法识别: {key[1:]!r}（大调留空/maj，小调 m/min）")
    canonical = PC_NAME[pc] + ("m" if mode == "minor" else "")
    return pc, mode, canonical


def scale_pcs(root_pc: int, mode: str) -> list[int]:
    steps = MAJOR_STEPS if mode == "major" else MINOR_STEPS
    return [(root_pc + s) % 12 for s in steps]


def triad_pcs(scale: list[int], degrees: tuple[int, ...]) -> list[int]:
    """级数元组 → 去重排序的音高集合。"""
    return sorted({scale[d - 1] for d in degrees})


def chord_label(scale: list[int], roman_major: str, mode: str) -> tuple[str, str, str]:
    """返回 ``(roman_display, root_name, chord_name)``。"""
    pcs = triad_pcs(scale, TRIAD_DEGREES[roman_major])
    root_pc = pcs[0]
    root_name = PC_NAME[root_pc]
    quality = QUALITY[mode][roman_major if mode == "major" else MINOR_ROMAN[roman_major]]
    roman_display = roman_major if mode == "major" else MINOR_ROMAN[roman_major]
    if mode == "minor" and roman_display == "ii":
        roman_display = "ii°"
    chord_name = root_name + QUALITY_SUFFIX[quality]
    return roman_display, root_name, chord_name


def score_bar(melody_pcs: set[int], scale: list[int], mode: str) -> list[dict]:
    """对一个小节，按「覆盖旋律音数」给七个自然三和弦打分（降序，确定性）。"""
    scored = []
    for idx, roman_major in enumerate(TRIAD_ORDER):
        pcs = triad_pcs(scale, TRIAD_DEGREES[roman_major])
        covered = melody_pcs & set(pcs)
        roman_display, root_name, chord_name = chord_label(scale, roman_major, mode)
        scored.append({
            "roman": roman_display,
            "chord": chord_name,
            "root": root_name,
            "degrees": list(TRIAD_DEGREES[roman_major]),
            "chord_pcs": pcs,
            "covered_pcs": sorted(covered),
            "covered_count": len(covered),
            "melody_count": len(melody_pcs),
            "root_in_melody": root_pc_of(roman_major, scale) in melody_pcs,
            "_order": idx,
        })
    # 平分打破：覆盖多 → 根音在旋律 → 级数次序（I<ii<…）
    scored.sort(key=lambda c: (-c["covered_count"], not c["root_in_melody"], c["_order"]))
    for c in scored:
        del c["_order"]
    return scored


def root_pc_of(roman_major: str, scale: list[int]) -> int:
    return triad_pcs(scale, TRIAD_DEGREES[roman_major])[0]


def generate_candidates(spec: dict) -> dict:
    """spec → 候选报告（status=CANDIDATES, requires_human_approval=True）。"""
    for field in ("song_id", "key", "melody"):
        if field not in spec:
            raise ValueError(f"spec 缺少必填字段 {field!r}")
    root_pc, mode, canonical = parse_key(spec["key"])
    scale = scale_pcs(root_pc, mode)

    melody = spec["melody"]
    if not isinstance(melody, list) or not melody:
        raise ValueError("melody 必须是非空 list（每项 {\"bar\": int, \"notes\": [midi,...]}）")

    seen_bars: set[int] = set()
    candidates = []
    for i, item in enumerate(melody):
        if not isinstance(item, dict):
            raise ValueError(f"melody[{i}] 必须是对象，收到 {type(item).__name__}")
        bar = item.get("bar")
        notes = item.get("notes")
        if not isinstance(bar, int) or isinstance(bar, bool) or bar < 1:
            raise ValueError(f"melody[{i}].bar 必须为正整数，收到 {bar!r}")
        if bar in seen_bars:
            raise ValueError(f"小节 {bar} 重复定义")
        seen_bars.add(bar)
        if not isinstance(notes, list) or not all(
                isinstance(n, int) and not isinstance(n, bool) and 0 <= n <= 127 for n in notes):
            raise ValueError(f"melody[{i}].notes 必须为 MIDI 整数(0-127) list，收到 {notes!r}")

        if not notes:
            candidates.append({"bar": bar, "melody_pcs": [], "best": None,
                               "note": "该小节无旋律音，无候选", "alternatives": []})
            continue
        melody_pcs = {n % 12 for n in notes}
        ranked = score_bar(melody_pcs, scale, mode)
        best = dict(ranked[0])
        best["coverage"] = (f"{best['covered_count']}/{best['melody_count']}"
                            if best["melody_count"] else "0/0")
        alternatives = [
            {"roman": c["roman"], "chord": c["chord"],
             "covered_pcs": c["covered_pcs"], "covered_count": c["covered_count"]}
            for c in ranked[1:]
        ]
        candidates.append({
            "bar": bar,
            "melody_pcs": sorted(melody_pcs),
            "best": best,
            "alternatives": alternatives,
        })

    return {
        "song_id": spec["song_id"],
        "key": canonical,
        "mode": mode,
        "scale_pcs": scale,
        "status": "CANDIDATES",
        "requires_human_approval": True,
        "candidates": candidates,
    }


def _emit(report: dict, out_path: str | None) -> None:
    text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if out_path:
        p = Path(out_path)
        if p.parent and not p.parent.exists():
            raise FileNotFoundError(f"输出目录不存在: {p.parent}")
        p.write_text(text + "\n", encoding="utf-8")
    else:
        sys.stdout.write(text + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="chord_candidates",
        description="无和弦标记时按旋律生成和弦候选（#49；候选须人工批准，禁自动固化）")
    parser.add_argument("--spec", required=True, help="输入 spec JSON 路径")
    parser.add_argument("--out", help="输出候选 JSON 路径（缺省打印 stdout）")
    parser.add_argument("--json", action="store_true", help="stdout 恒为 JSON（默认即是）")
    args = parser.parse_args(argv)

    try:
        spec_path = Path(args.spec)
        if not spec_path.exists():
            raise FileNotFoundError(f"spec 文件不存在: {args.spec}")
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"读取错误: {e}", file=sys.stderr)
        return 2
    except ValueError as e:
        print(f"spec 非法: {e}", file=sys.stderr)
        return 2

    try:
        report = generate_candidates(spec)
    except ValueError as e:
        print(f"校验失败: {e}", file=sys.stderr)
        return 1

    try:
        _emit(report, args.out)
    except (FileNotFoundError, OSError) as e:
        print(f"写入错误: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
