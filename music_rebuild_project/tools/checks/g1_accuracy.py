#!/usr/bin/env python3
"""g1_accuracy.py — G1 自动判定的**准确率测量**工具（让「99%」可被测量，而非口头声明）。

原则（对齐 P5：精度只由测量声明）
  1. **有标注集才有准确率**：本工具把「自动判定报告」与「标注真值」逐项比对。
  2. **样本量护栏**：比对样本 < ``--min-samples``（默认 100）时一律输出
     ``INSUFFICIENT_SAMPLES``，**绝不**在小样本上宣称置信率。
  3. **安全优先**：``false_pass``（真值=FAIL 而自动=PASS）是致命错误，必须为 0；
     一旦出现，无论一致率多高都判 ``TARGET_NOT_MET``。
  4. 自动 ``SKIP``（引擎缺失）计为 **abstain**（弃权），单列 ``coverage``，
     不混入一致率（弃权既不算对也不算错）。

用法::

    python tools/checks/g1_accuracy.py \
        --truth 08_qc/g1_ground_truth.json \
        --report 08_qc/g1_auto_song01_report.json \
        --target 0.99 --min-samples 100

退出码：0 = TARGET_MET 或 INSUFFICIENT_SAMPLES；1 = TARGET_NOT_MET；2 = 用法/读取错误。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

DEFAULT_TARGET = 0.99
DEFAULT_MIN_SAMPLES = 100
CHECKS = ("G1-1L", "G1-2", "G1-3", "G1-4", "G1-5", "G1-6", "G1-7")


def load_truth(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if "pages" not in data:
        raise ValueError("ground truth 缺少 pages 字段")
    return data


def extract_auto(report: dict) -> dict[str, dict[str, str]]:
    """从 g1_image_quality.py --auto --json 的报告提取 {文件名: {检查: status}}。"""
    out: dict[str, dict[str, str]] = {}
    for f in report.get("files", []):
        name = Path(f["path"]).name
        statuses = {k: v["status"] for k, v in f.get("checks", {}).items()}
        out[name] = statuses
    if "G1-4" in report:
        out.setdefault("__dir__", {})["G1-4"] = report["G1-4"]["status"]
    return out


def evaluate(truth: dict, auto: dict[str, dict[str, str]],
             target: float = DEFAULT_TARGET,
             min_samples: int = DEFAULT_MIN_SAMPLES) -> dict:
    n_cmp = n_agree = n_abstain = 0
    false_pass: list[dict] = []
    mismatches: list[dict] = []
    per_check: dict[str, dict[str, int]] = {c: {"cmp": 0, "agree": 0, "abstain": 0}
                                            for c in CHECKS}

    for page in truth["pages"]:
        fname = page["file"]
        expected = page.get("expected", {})
        got = auto.get(fname)
        if got is None:
            continue
        for check, exp in expected.items():
            a = got.get(check)
            if a is None:
                continue
            if a == "SKIP":
                n_abstain += 1
                per_check.setdefault(check, {"cmp": 0, "agree": 0, "abstain": 0})["abstain"] += 1
                continue
            n_cmp += 1
            per_check.setdefault(check, {"cmp": 0, "agree": 0, "abstain": 0})["cmp"] += 1
            if a == exp:
                n_agree += 1
                per_check[check]["agree"] += 1
            else:
                mismatches.append({"file": fname, "check": check, "expected": exp, "auto": a})
                if exp == "FAIL" and a == "PASS":
                    false_pass.append({"file": fname, "check": check})

    agreement = (n_agree / n_cmp) if n_cmp else None
    total_slots = n_cmp + n_abstain
    coverage = (n_cmp / total_slots) if total_slots else None

    if n_cmp < min_samples:
        verdict = "INSUFFICIENT_SAMPLES"
    elif false_pass:
        verdict = "TARGET_NOT_MET"
    elif agreement is not None and agreement >= target:
        verdict = "TARGET_MET"
    else:
        verdict = "TARGET_NOT_MET"

    return {
        "verdict": verdict,
        "target": target,
        "min_samples": min_samples,
        "n_comparisons": n_cmp,
        "n_abstain": n_abstain,
        "n_agreement": n_agree,
        "agreement_rate": agreement,
        "coverage": coverage,
        "false_pass": false_pass,
        "mismatches": mismatches,
        "per_check": per_check,
    }


def _render(rep: dict) -> str:
    lines = [
        "G1 auto-accuracy report",
        "  verdict: %s (target=%.2f, min_samples=%d)"
        % (rep["verdict"], rep["target"], rep["min_samples"]),
        "  comparisons=%d abstain=%d agreement=%d rate=%s coverage=%s"
        % (rep["n_comparisons"], rep["n_abstain"], rep["n_agreement"],
           ("%.4f" % rep["agreement_rate"]) if rep["agreement_rate"] is not None else "n/a",
           ("%.4f" % rep["coverage"]) if rep["coverage"] is not None else "n/a"),
        "  false_pass (致命: 真值FAIL→自动PASS): %d" % len(rep["false_pass"]),
        "  per-check:",
    ]
    for c, v in rep["per_check"].items():
        lines.append("    %-7s cmp=%d agree=%d abstain=%d" % (c, v["cmp"], v["agree"], v["abstain"]))
    for m in rep["mismatches"][:20]:
        lines.append("    ! %s %s expected=%s auto=%s"
                     % (m["file"], m["check"], m["expected"], m["auto"]))
    if rep["verdict"] == "INSUFFICIENT_SAMPLES":
        lines.append("  note: 样本不足 %d，按护栏**不宣称**任何准确率（需扩充标注集）"
                     % rep["min_samples"])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="g1_accuracy.py",
                                 description="G1 自动判定准确率测量（标注集比对）。")
    ap.add_argument("--truth", required=True, help="标注真值 JSON")
    ap.add_argument("--report", required=True, action="append",
                    help="g1_image_quality.py --auto --json 报告（可多次）")
    ap.add_argument("--target", type=float, default=DEFAULT_TARGET)
    ap.add_argument("--min-samples", type=int, default=DEFAULT_MIN_SAMPLES)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    try:
        truth = load_truth(Path(args.truth))
        auto: dict[str, dict[str, str]] = {}
        for rp in args.report:
            auto.update(extract_auto(json.loads(Path(rp).read_text(encoding="utf-8"))))
    except (OSError, ValueError, KeyError) as e:
        sys.stderr.write("read error: %s\n" % e)
        return 2

    rep = evaluate(truth, auto, target=args.target, min_samples=args.min_samples)
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    else:
        print(_render(rep))
    return 0 if rep["verdict"] in ("TARGET_MET", "INSUFFICIENT_SAMPLES") else 1


if __name__ == "__main__":
    raise SystemExit(main())
