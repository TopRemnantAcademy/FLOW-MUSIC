#!/usr/bin/env python3
"""score_xml_validate.py — MusicXML 结构校验（music21 / BSD-3-Clause）。

用途：把 OMR 产出的 MusicXML 做**结构级**校验，供 G1/G2 复用：
  - 能否被 music21 正常解析（解析失败 = OMR 输出结构坏）
  - 各声部小节数是否一致
  - 每小节（末小节除外，末小节允许弱起/不完整）的**音长合计是否等于拍号小节时长**
  - 拍号序列

依赖：music21（BSD-3-Clause，见 deploy/omr-qa/requirements-omr.txt）

用法::
    python tools/checks/score_xml_validate.py path/to/file.musicxml [--json]

退出码：0 = 结构通过；1 = 发现结构问题；2 = 用法/解析错误。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

TOL = 0.01  # 音长浮点容差（quarterLength 单位）


def validate(path: Path) -> dict:
    from music21 import converter

    score = converter.parse(str(path))
    parts = list(score.parts)
    problems: list[str] = []
    if not parts:
        return {"ok": False, "problems": ["未解析到任何声部(part)"], "parts": 0}

    part_info = []
    measure_counts = []
    notes: list[str] = []
    dur_checked = 0
    dur_total = 0
    for pi, part in enumerate(parts):
        measures = list(part.getElementsByClass("Measure"))
        measure_counts.append(len(measures))
        ts_seq = []
        cur_ts = None
        for mi, m in enumerate(measures):
            ts = m.timeSignature or cur_ts
            if ts is not None:
                cur_ts = ts
                sig = f"{ts.numerator}/{ts.denominator}"
                if not ts_seq or ts_seq[-1] != sig:
                    ts_seq.append(sig)
            dur_total += 1
            if ts is None:
                continue          # 无拍号 → 无法判定该小节应有多长（记 note，不静默通过）
            dur_checked += 1
            expected = float(ts.barDuration.quarterLength)
            actual = float(m.highestTime)
            is_last = mi == len(measures) - 1
            if abs(actual - expected) > TOL and not (is_last and actual < expected):
                problems.append(
                    f"part{pi + 1} 第{mi + 1}小节 音长合计={actual:.3f} ≠ 拍号时长={expected:.3f}"
                )
        if not ts_seq:
            notes.append(
                f"part{pi + 1} **未检出拍号（`<time>` 缺失）** → 该声部「小节音长=拍号」校验**未执行**"
                "（OMR 输出未携带拍号；G2 节奏校验需外部补拍号）"
            )
        part_info.append({"part": pi + 1, "measures": len(measures), "time_signatures": ts_seq})

    if len(set(measure_counts)) > 1:
        problems.append(f"各声部小节数不一致：{measure_counts}")

    return {
        "ok": not problems,
        "verdict": "FAIL" if problems else ("PASS_WITH_NOTES" if notes else "PASS"),
        "file": str(path),
        "parts": len(parts),
        "measure_counts": measure_counts,
        "part_info": part_info,
        "duration_checks_run": dur_checked,
        "duration_checks_total": dur_total,
        "problems": problems,
        "notes": notes,
    }


def _render(rep: dict) -> str:
    lines = [
        "MusicXML structure validation (music21)",
        f"  file: {rep['file']}",
        f"  parts: {rep['parts']}  measures: {rep['measure_counts']}",
        f"  小节音长校验覆盖: {rep.get('duration_checks_run', 0)}/{rep.get('duration_checks_total', 0)}",
    ]
    for pi in rep["part_info"]:
        lines.append(f"    part{pi['part']}: measures={pi['measures']} 拍号={pi['time_signatures']}")
    for n in rep.get("notes", []):
        lines.append(f"  [note] {n}")
    if rep["problems"]:
        lines.append(f"  problems ({len(rep['problems'])}):")
        for p in rep["problems"][:20]:
            lines.append(f"    ! {p}")
        verdict = "FAIL"
    elif rep.get("notes"):
        lines.append("  problems: 0")
        verdict = "PASS_WITH_NOTES"
    else:
        lines.append("  problems: 0（结构通过）")
        verdict = "PASS"
    lines.append(f"  verdict: {verdict}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="score_xml_validate.py",
                                 description="MusicXML 结构校验（music21）")
    ap.add_argument("path")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    p = Path(args.path)
    if not p.exists():
        sys.stderr.write(f"path not found: {p}\n")
        return 2
    try:
        rep = validate(p)
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
