#!/usr/bin/env python3
"""score_xml_validate.py — MusicXML 结构校验（music21 / BSD-3-Clause）。

用途：把 OMR 产出的 MusicXML 做**结构级**校验，供 G1/G2 复用：
  - 能否被 music21 正常解析（解析失败 = OMR 输出结构坏）
  - 各声部小节数是否一致
  - 每小节（末小节除外，末小节允许弱起/不完整）的**音长合计是否等于拍号小节时长**
  - 拍号来源与置信（**T7**：声明 / 源侧 OCR / 结构推断）

拍号（T7）—— 为什么需要外部来源
--------------------------------
实测 oemer 输出 `<time>` 计数 = 0 → 谱面自身无拍号，音长校验**无从执行**
（本工具此前如实报 `PASS_WITH_NOTES` + 覆盖 0/23，**未**静默判过）。因此引入
`time_signature_source`，按可靠性降序补拍号：元数据声明 → 曲首 OCR → 结构推断。

诚实性硬约束（issue §增补 T7）：
  - 报告必须写明拍号**来源与置信**（`time_signature.source/confidence`），不得静默填充；
  - **推断值（inferred）只可 `PASS_WITH_NOTES`**，不得判 PASS，即使音长全部对上；
  - 无任何来源 → 该声部音长校验保持未执行，如实报 `PASS_WITH_NOTES`。

依赖：music21（BSD-3-Clause）、time_signature_source（本仓，零重依赖）

用法::
    python tools/checks/score_xml_validate.py path/to/file.musicxml [--json]
    python tools/checks/score_xml_validate.py score.musicxml \
        --manifest 02_source/song01/manifest.md \
        --image 02_source/song01/preprocessed/song01_preprocessed_v001.jpeg
    python tools/checks/score_xml_validate.py score.musicxml --infer-time    # 允许结构推断

退出码：0 = 结构通过（含 PASS_WITH_NOTES）；1 = 发现结构问题；2 = 用法/解析错误。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve()
_TOOLS_DIR = _HERE.parents[1]                 # music_rebuild_project/tools
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

TOL = 0.01  # 音长浮点容差（quarterLength 单位）


def _part_measures(part):
    return list(part.getElementsByClass("Measure"))


def _measure_highest_times(measures) -> list[float | None]:
    return [float(m.highestTime) for m in measures]


def validate(path: Path,
             manifest_path: Path | None = None,
             image_path: Path | None = None,
             allow_inferred: bool = False,
             engines: tuple[str, ...] = ("rapidocr", "ocr")) -> dict:
    from music21 import converter

    from checks import time_signature_source as TSS

    score = converter.parse(str(path))
    parts = list(score.parts)
    problems: list[str] = []
    if not parts:
        return {"ok": False, "verdict": "FAIL", "problems": ["未解析到任何声部(part)"],
                "parts": 0}

    # ---- 外部拍号来源（T7）：先声明、后 OCR、最后推断（可选）----
    external = None
    highest_times: list[float] = []
    for part in parts:
        highest_times += [ht for ht in _measure_highest_times(_part_measures(part)) if ht]
    external = TSS.resolve(manifest_path=manifest_path, image_path=image_path,
                           part_highest_times=highest_times if allow_inferred else None,
                           engines=engines)

    part_info = []
    measure_counts = []
    notes: list[str] = []
    dur_checked = 0
    dur_total = 0
    notes_used: list[dict] = []
    for pi, part in enumerate(parts):
        measures = _part_measures(part)
        measure_counts.append(len(measures))
        ts_seq = []
        cur_ts = None
        part_used_external = False
        for mi, m in enumerate(measures):
            ts = m.timeSignature or cur_ts
            if ts is not None:
                cur_ts = ts
                sig = f"{ts.numerator}/{ts.denominator}"
                if not ts_seq or ts_seq[-1] != sig:
                    ts_seq.append(sig)
            dur_total += 1
            if ts is None and external.numerator is not None:
                # 谱面自身无拍号 → 用外部来源的拍号（幂次：quarterLength）
                expected = float(external.numerator) * (4.0 / external.denominator)
                sig_used = external.value
            elif ts is not None:
                expected = float(ts.barDuration.quarterLength)
                sig_used = f"{ts.numerator}/{ts.denominator}"
            else:
                continue              # 无任何来源 → 不判定（记 note，不静默通过）
            dur_checked += 1
            part_used_external = part_used_external or (ts is None)
            actual = float(m.highestTime)
            is_last = mi == len(measures) - 1
            if abs(actual - expected) > TOL and not (is_last and actual < expected):
                problems.append(
                    f"part{pi + 1} 第{mi + 1}小节 音长合计={actual:.3f} ≠ 拍号({sig_used})时长={expected:.3f}"
                )
        if not ts_seq and external.numerator is None:
            notes.append(
                f"part{pi + 1} **未检出拍号（`<time>` 缺失）且**外部来源不可用 → "
                "该声部「小节音长=拍号」校验**未执行**"
                "（OMR 输出未携带拍号；可用 --manifest/--image/--infer-time 补来源）"
            )
        elif part_used_external:
            notes.append(
                f"part{pi + 1} 谱面未含 `<time>` → 音长校验使用**外部来源**拍号 "
                f"{external.value}（source={external.source} conf={external.confidence:.2f}）"
            )
            notes_used.append({"part": pi + 1, "time_signature": external.value,
                               "source": external.source, "confidence": external.confidence})
        part_info.append({"part": pi + 1, "measures": len(measures), "time_signatures": ts_seq})

    if len(set(measure_counts)) > 1:
        problems.append(f"各声部小节数不一致：{measure_counts}")

    if external.numerator is not None and dur_total == 0:
        notes.append("外部拍号来源可用，但谱面**没有任何小节**可比对 → 音长校验未执行")
    # ---- 判定上限：推断值只可 PASS_WITH_NOTES，绝不 PASS ----
    cap = external.verdict_cap
    if problems:
        verdict = "FAIL"
    elif notes:
        verdict = "PASS_WITH_NOTES"
    elif cap == "PASS_WITH_NOTES":
        verdict = "PASS_WITH_NOTES"
    else:
        verdict = "PASS"
    if not problems and external.source == "inferred":
        notes.append("拍号为**结构推断值**（非声明）→ 按纪律本结果上限为 PASS_WITH_NOTES；"
                     "需人工/声明确认后方可升级为 PASS")

    return {
        "ok": not problems,
        "verdict": verdict,
        "file": str(path),
        "parts": len(parts),
        "measure_counts": measure_counts,
        "part_info": part_info,
        "duration_checks_run": dur_checked,
        "duration_checks_total": dur_total,
        "time_signature": external.as_dict(),
        "time_signatures_used": notes_used,
        "problems": problems,
        "notes": notes,
    }


def _render(rep: dict) -> str:
    ts = rep.get("time_signature") or {}
    lines = [
        "MusicXML structure validation (music21)",
        f"  file: {rep['file']}",
        f"  parts: {rep['parts']}  measures: {rep['measure_counts']}",
        f"  小节音长校验覆盖: {rep.get('duration_checks_run', 0)}/{rep.get('duration_checks_total', 0)}",
        f"  拍号来源: {ts.get('value') or '(none)'}  source={ts.get('source')}"
        f"  confidence={ts.get('confidence')}  判定上限={ts.get('verdict_cap')}",
    ]
    if ts.get("detail"):
        lines.append(f"    {ts['detail']}")
    for pi in rep["part_info"]:
        lines.append(f"    part{pi['part']}: measures={pi['measures']} 拍号={pi['time_signatures']}")
    for n in rep.get("notes", []):
        lines.append(f"  [note] {n}")
    if rep["problems"]:
        lines.append(f"  problems ({len(rep['problems'])}):")
        for p in rep["problems"][:20]:
            lines.append(f"    ! {p}")
    else:
        lines.append("  problems: 0")
    lines.append(f"  verdict: {rep['verdict']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="score_xml_validate.py",
                                 description="MusicXML 结构校验（music21；含 T7 拍号来源）")
    ap.add_argument("path")
    ap.add_argument("--manifest", help="曲目元数据（声明拍号；最高可信度）")
    ap.add_argument("--image", help="源侧图像（曲首标记区 OCR 抽拍号）")
    ap.add_argument("--infer-time", action="store_true",
                    help="允许结构推断拍号（结果上限 PASS_WITH_NOTES）")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    p = Path(args.path)
    if not p.exists():
        sys.stderr.write(f"path not found: {p}\n")
        return 2
    try:
        rep = validate(p,
                       manifest_path=Path(args.manifest) if args.manifest else None,
                       image_path=Path(args.image) if args.image else None,
                       allow_inferred=args.infer_time)
    except ImportError as e:
        sys.stderr.write(f"music21 不可用：{e}\n")
        return 2
    except Exception as e:
        sys.stderr.write(f"解析失败：{type(e).__name__}: {e}\n")
        return 2

    print(json.dumps(rep, ensure_ascii=False, indent=2) if args.json else _render(rep))
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
