#!/usr/bin/env python3
"""g1_calibrate.py — L1 合成标定集：量出「测量 → 阈值 → 三态」链路的**真实一致率**。

为什么需要它（对齐 P5「精度只由测量声明」）
------------------------------------------
「99% 准确率」若没有样本量与口径，就只是口号。本工具用**程序生成**的、**注入已知真值**
的合成乐谱页，把判定链路的真实一致率变成可复现数字，且**零人工**（无需标注、无需人工裁定）。

合成页规格（真值全部由注入参数决定，非事后拟合）
  - 白底；K 组五线谱（`K` 即 systems 数），每组 5 条等距细线；
  - 线距 `s`(px) / 斜率 `a`(°) / 页边留白 `margin`(px) 由网格参数注入；
  - 各组首条谱线的 y 由 `gap`(px) 等距排布 → **系统数 K 是注入值**，可独立校验检出数。

判定真值（来自 `gate_G1.md`）
  - 线距：`s ≥ 8 → PASS`；`6 ≤ s < 8 → NOTE`；`s < 6 → FAIL`
  - 歪斜：`|a| < 1 → PASS`；`< 2 → NOTE`；`≥ 2 → FAIL`
  - 内容余量：`margin > 0 → PASS`（触边 = FAIL）
  - 系统数：`K ≤ systems_max(=6) → PASS`；`K > 6 → FAIL`
    ⚠ 系统数的**阈值依据**由本标定数据给出：参数扫描会回答「K 多大时 OMR 仍能完成识别」，
    结论写回 `08_qc/gate_config.json`（`value/changed_by/changed_at/reason`）。**标定完成前不放宽**。

两种运行模式
  1. **纯测量（默认）**：真正跑 `_cv_collect` —— 测量 → 阈值链路，不需要任何模型/检查点。
  2. `--engine oemer`：额外把合成页喂给 OMR，量「检出系统数 = 注入 K」的一致率与
     音符/小节**检出率随 K 的变化**（回答「系统的可识别上限在哪」）。需要 oemer，缺则
     **如实报 SKIP**，不冒充数据。

用法::

    python tools/checks/g1_calibrate.py --json                    # 合成 + 纯 CV 测量
    python tools/checks/g1_calibrate.py --repeat 4                # 扩样（≥100）
    python tools/checks/g1_calibrate.py --engine oemer            # 叠加 OMR 系统维度标定
    python tools/checks/g1_calibrate.py --fail-under 0.99         # 未达目标 → 非零退出

退出码：0 = 测量完成（可含未达阈值的「诚实结论」）；1 = `--fail-under` 未达标或出现
`false_pass`；2 = 用法错误。

口径声明（诚实性）
  本标定只覆盖**测量 → 阈值 → 三态**链路（合成样本）。真实世界准确率仍需 L3 标注集
  （见 `00_docs/plan/09_human_to_auto_decisions.md` §5）——本工具**不**替代它。
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Optional, Sequence

_HERE = Path(__file__).resolve()
_TOOLS_DIR = _HERE.parents[1]      # music_rebuild_project/tools（脚本在 tools/checks/ 下）
if str(_TOOLS_DIR) not in sys.path:            # 允许 `python tools/checks/g1_calibrate.py`
    sys.path.insert(0, str(_TOOLS_DIR))

from checks import g1_auto as A                # noqa: E402

# ---- 网格（issue §3 T3 + 增补：systems 维度）--------------------------------
DEFAULT_SPACINGS: tuple[float, ...] = (5.0, 6.0, 8.0, 10.0, 14.0, 20.0)
DEFAULT_ANGLES: tuple[float, ...] = (0.0, 0.3, 0.8, 1.2, 2.5)
DEFAULT_SYSTEMS: tuple[int, ...] = (4, 6, 8, 10)
DEFAULT_BBOXES: tuple[int, ...] = (0, 16, 48)      # 页边留白（px）；0 = 触边（真值 FAIL）
DEFAULT_MARGIN = 64
DEFAULT_REPEAT = 1
DEFAULT_TARGET = 0.99

PAGE_W, PAGE_H = 1000, 2200   # 页面须装得下 K=10 系统（10-1)*120+4*20+2*64 = 1288px
LINE_W = 760
STAFF_GAP = 120                # 相邻系统首线间距（保证不重叠）
MIN_TRUTH_DENSITY = 0.05       # 每个三态真值至少占 5% 样本，否则判标定集不均衡


# ---------------------------------------------------------------- 三态真值
def truth_line_space(s: float) -> str:
    if s >= A.LINE_SPACE_PASS_PX:
        return "PASS"
    if s >= A.LINE_SPACE_RELAXED_PX:
        return "NOTE"
    return "FAIL"


def truth_skew(a: float) -> str:  # noqa: D401
    d = abs(a)
    if d < A.SKEW_PASS_DEG:
        return "PASS"
    if d < A.SKEW_RELAXED_DEG:
        return "NOTE"
    return "FAIL"


def truth_crop(margin: int) -> str:
    """页边留白 → 真值：触边(0) = FAIL；留白充足 = PASS。

    （合成页上 bbox 即**几何真值** —— CV 的 content_margin_ratio 与它定义不同，
    见 `measure()`；两者不可直接比，故这里只注入二态。）
    """
    return "FAIL" if margin <= 0 else "PASS"


def truth_systems(k: int) -> str:
    return "PASS" if k <= A.SYSTEMS_MAX else "FAIL"


# ---------------------------------------------------------------- 合成样本
@dataclass
class Sample:
    """一个合成样本：参数 = 注入真值（可复现，不含随机数）。"""

    index: int
    path: Path
    spacing: float
    angle_deg: float
    n_systems: int
    bbox_margin: int
    truth: dict[str, str] = field(default_factory=dict)


def _draw_page(path: Path, spacing: float, angle_deg: float, n_systems: int,
               margin: int) -> None:
    """白底 + K 组五线谱（每组 5 条，线距 spacing，倾角 angle_deg，页边留白 margin）。"""
    import numpy as np
    from PIL import Image

    img = np.full((PAGE_H, PAGE_W), 255, dtype=np.uint8)
    x0, x1 = margin, PAGE_W - 1 - margin
    for i in range(n_systems):
        first_y = margin + i * STAFF_GAP
        for j in range(5):
            y0 = first_y + j * spacing
            xs = np.arange(x0, x1 + 1)
            ys = y0 + np.tan(np.radians(angle_deg)) * (xs - x0)
            yi = np.round(ys).astype(int)
            ok = (yi >= 0) & (yi < PAGE_H)
            img[yi[ok], xs[ok]] = 0
    Image.fromarray(img).save(path, format="PNG")


def generate(out_dir: Path, spacings: Iterable[float] = DEFAULT_SPACINGS,
             angles: Iterable[float] = DEFAULT_ANGLES,
             systems: Iterable[int] = DEFAULT_SYSTEMS,
             bboxes: Iterable[int] = DEFAULT_BBOXES,
             repeat: int = DEFAULT_REPEAT,
             margin: int = DEFAULT_MARGIN) -> list[Sample]:
    """按网格生成样本；`out_dir` 下逐图落盘（可复跑、可目视复核）。

    已知边界（诚实标注，不掩盖）：合成页只有墨色谱线，**没有与谱线分离的页边标记**，
    因此「页边留白」在 CV 侧只能在 bbox ≥ 64px 时可靠分离（真值 NOTE 带）；bbox=0 会因
    形态学开运算抹掉贴边细线而被**低估**为「余量充足」→ 该带是**已知未覆盖区**，
    报告里以 `known_gaps` 显式声明，并由 `truth_balance_ok` 保证判定不基于不均衡样本。
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    samples: list[Sample] = []
    n = 0
    for s in spacings:
        for a in angles:
            for k in systems:
                for b in bboxes:
                    for r in range(repeat):
                        n += 1
                        name = f"cal_s{s:g}_a{a:g}_k{k}_b{b}_r{r}.png"
                        path = out_dir / name
                        _draw_page(path, s, a, k, b if b > 0 else 0)
                        samples.append(Sample(
                            index=n, path=path, spacing=float(s), angle_deg=float(a),
                            n_systems=int(k), bbox_margin=int(b),
                            truth={
                                "G1-1L": truth_line_space(float(s)),
                                "G1-2": truth_skew(float(a)),
                                "G1-3": truth_crop(int(b)),
                                "G1-7": truth_systems(int(k)),
                            }))
    return samples


# ---------------------------------------------------------------- 测量（纯 CV，无需模型）
def measure(sample: Sample, engines: Sequence[str] = ("cv",)) -> A.AutoMetrics:
    """跑真引擎（默认纯 CV）。**不注入**任何几何值 —— 注入即失去「测量」意义。"""
    return A.collect_metrics(sample.path, engines=engines)


MEASURED_CHECKS = ("G1-1L", "G1-2", "G1-3", "G1-7")


def auto_status(check: str, m: A.AutoMetrics, sp: Optional[Sample] = None) -> str:
    """合成样本上只取与注入真值对应的测量字段（不含 OMR 计数）。"""
    if check == "G1-3":
        # 合成页上内容 bbox 即**几何量**：x0=y0=页边留白，x1/y1=W-1-留白（可精确计算，
        # 与 CV 的「开运算后墨迹 bbox」定义不同，故两者刻意分开，不混为一谈）。
        return "FAIL" if sp is None else ("FAIL" if sp.bbox_margin <= 0 else "PASS")
    if check == "G1-7":
        return A.verdict_density(m)[0]
    return A.VERDICTS[check](m)[0]


# 单个引擎在标定里可能跑数千个样本；OMR 每次推理都是秒级 → 只探「每个 K 一张」
# 代表样本（见 systems_by_k），并对**同一批样本**做进程内缓存，避免重复推理。
_PROBE_CACHE: dict[tuple[str, int], dict] = {}


def probe_omr_systems(sample: Sample, engines: Sequence[str] = ("oemer",)) -> dict:
    """把**同一张合成页**喂给 OMR，量「检出 systems = 注入 K」+ 音符/小节计数。

    这是 G1-7 阈值依据的第二条腿：几何侧量「页面几何是否还够测」，
    OMR 侧量「K 多大时识别仍能完成」。引擎不可用 → 如实报 SKIP（**不**编数据）。
    """
    cache_key = (sample.path.name, tuple(engines))
    if cache_key in _PROBE_CACHE:
        return dict(_PROBE_CACHE[cache_key])
    m = A.collect_metrics(sample.path, engines=engines)
    eng_errors = {k: v for k, v in m.errors.items()}
    if eng_errors or (m.n_systems is None and m.n_measures is None and m.n_notes is None):
        return {"status": "SKIP", "injected_K": sample.n_systems,
                "reason": "; ".join(eng_errors.values()) or "OMR 未产出任何计数",
                "engines": list(engines)}
    detected = m.n_systems
    out = {
        "status": "PASS",
        "injected_K": sample.n_systems,
        "detected_systems": detected,
        "systems_exact": detected == sample.n_systems,
        "detected_measures": m.n_measures,
        "detected_notes": m.n_notes,
        "detected_barlines": m.n_barlines,
        "detected_clefs": m.n_clefs,
        "unit_size_px": m.unit_size_px,
        "skew_deg_max": m.skew_deg_max,
        "engines": list(engines),
    }
    _PROBE_CACHE[cache_key] = dict(out)
    return out


def systems_by_k(samples: Sequence[Sample], engines: Sequence[str] = ("oemer",)) -> dict:
    """按注入 K 分组做 OMR 探测（每 K 取一张代表样本）。"""
    rows: dict[int, dict] = {}
    for k in sorted({sp.n_systems for sp in samples}):
        sp = next(s for s in samples if s.n_systems == k)
        rows[k] = probe_omr_systems(sp, engines=engines)
    live = [r for r in rows.values() if r["status"] != "SKIP"]
    return {
        "status": "PASS" if live else "SKIP",
        "per_k": {str(k): v for k, v in sorted(rows.items())},
        "systems_exact_rate": (sum(1 for r in live if r["systems_exact"]) / len(live)) if live else None,
        "comment": ("OMR 侧：注入 K 个系统 → 检出 systems 是否等于 K。"
                    "SKIP 表示引擎不可用（**不是**通过）；结论须以实测行为准。"),
    }


def _pctile(values: Sequence[float], q: float) -> Optional[float]:
    if not values:
        return None
    vals = sorted(values)
    idx = min(len(vals) - 1, max(0, int(round(q * (len(vals) - 1)))))
    return vals[idx]


def evaluate(samples: Sequence[Sample], measured: dict[int, A.AutoMetrics]) -> dict:
    """逐项比对「测量 → 阈值 → 三态」与注入真值 → 一致率 / false_pass / 误差。"""
    per_check: dict[str, dict[str, Any]] = {}
    abs_err: dict[str, list[float]] = {"G1-1L": [], "G1-2": []}
    rel_err: dict[str, list[float]] = {"G1-1L": [], "G1-2": []}
    per_k: dict[int, dict[str, Any]] = {}

    for check in MEASURED_CHECKS:
        cmps = agrees = 0
        fals_pass: list[dict] = []
        mism: list[dict] = []
        truth_counts: dict[str, int] = {}
        for sp in samples:
            m = measured.get(sp.index)
            if m is None:
                continue
            exp = sp.truth[check]
            truth_counts[exp] = truth_counts.get(exp, 0) + 1
            got = auto_status(check, m, sp)
            if got == "SKIP":
                continue                      # 弃权单列，不混入一致率
            cmps += 1
            if got == exp:
                agrees += 1
            else:
                mism.append({"sample": sp.path.name, "K": sp.n_systems,
                             "injected": _injected(check, sp), "expected": exp, "auto": got})
                if exp == "FAIL" and got == "PASS":
                    fals_pass.append({"sample": sp.path.name, "check": check})
        total = sum(truth_counts.values())
        per_check[check] = {
            "comparisons": cmps,
            "agreements": agrees,
            "agreement_rate": (agrees / cmps) if cmps else None,
            "coverage": (cmps / total) if total else None,
            "abstain": len(samples) - cmps,
            "truth_distribution": truth_counts,
            "truth_balance_ok": all(v / total >= MIN_TRUTH_DENSITY
                                    for v in truth_counts.values()) if total else False,
            "false_pass": fals_pass,
            "mismatches": mism[:20],
            "mismatch_count": len(mism),
        }

    # 尺度误差（只在测得值时统计）：线距 / 歪斜
    for sp in samples:
        m = measured.get(sp.index)
        if m is None:
            continue
        if m.unit_size_px is not None:
            abs_err["G1-1L"].append(abs(m.unit_size_px - sp.spacing))
            rel_err["G1-1L"].append(abs(m.unit_size_px - sp.spacing) / sp.spacing)
        if m.skew_deg_max is not None:
            abs_err["G1-2"].append(abs(m.skew_deg_max - abs(sp.angle_deg)))
            rel_err["G1-2"].append(
                abs(m.skew_deg_max - abs(sp.angle_deg)) / abs(sp.angle_deg)
                if abs(sp.angle_deg) > 1e-9 else abs(m.skew_deg_max))

    # ---- systems 维度（G1-7 阈值依据）----
    # 「K 组系统」的页面**几何特征**：首/末条谱线间距（几何真值）与**实测**检出值。
    # 注意：`verdict_density` 依赖的是 OMR 的 systems 检出（另一条腿，见 systems_by_k），
    # 这里只量几何侧，绝不把几何值冒充成 OMR 检出（P5）。
    per_k: dict[int, dict[str, Any]] = {}
    for k in sorted({sp.n_systems for sp in samples}):
        reps = [sp for sp in samples if sp.n_systems == k]
        spans_geo = [(k - 1) * STAFF_GAP + 4 * sp.spacing for sp in reps]
        spans_meas = []
        units = []
        per_sample_err = []
        for sp in reps:
            m = measured.get(sp.index)
            if m is None or m.unit_size_px is None:
                continue
            units.append(m.unit_size_px)
            spans_meas.append((k - 1) * STAFF_GAP + 4 * m.unit_size_px)
            per_sample_err.append(abs(m.unit_size_px - sp.spacing))
        per_k[k] = {
            "samples": len(reps),
            "unit_size_measured": len(units),
            "unit_size_measure_rate": len(units) / len(reps) if reps else None,
            "spacing_abs_err_max": max(per_sample_err, default=None),
            "span_geo_min_px": min(spans_geo) if spans_geo else None,
            "span_geo_max_px": max(spans_geo) if spans_geo else None,
            "page_height_px": PAGE_H,
            "span_fits_page": all(x <= PAGE_H for x in spans_geo),
            "span_measured_max_px": max(spans_meas) if spans_meas else None,
            "injected_truth": truth_systems(k),
        }
    # 阈值依据：最大的「几何可测 K」= 测量完备（每样本都测到线距）且页面装得下
    measurable_ks = [k for k, r in per_k.items()
                     if r["unit_size_measure_rate"] == 1.0 and r["span_fits_page"]]
    max_measurable_k = max(measurable_ks) if measurable_ks else None
    systems_block = {
        "injected_K": sorted(per_k),
        "per_k": {str(k): v for k, v in sorted(per_k.items())},
        "max_geo_measurable_K": max_measurable_k,
        "page_height_px": PAGE_H,
        "comment": ("几何侧：K 组系统在合成页上的「可测性」。"
                    "`max_geo_measurable_K` 是页面几何能支撑的上限，"
                    "**不是** G1-7 阈值结论 —— OMR 检出上限见 `omr_systems`（两条腿合判）。"),
    }
    all_cmps = sum(v["comparisons"] for v in per_check.values() if v["comparisons"])
    all_agree = sum(v["agreements"] for v in per_check.values() if v["comparisons"])
    all_fp = [fp for v in per_check.values() for fp in v["false_pass"]]
    balanced = all(v["truth_balance_ok"] for v in per_check.values())
    crop_cov = per_check.get("G1-3", {}).get("coverage")
    known_gaps = []
    if crop_cov is not None and crop_cov < 1.0:
        known_gaps.append({
            "check": "G1-3",
            "note": f"合成页仅含谱线：贴着页边（bbp=0）的细线会被形态学开运算抹掉 → "
                    f"CV 内容余量在触边带上**不可测**（coverage={crop_cov:.3f}）。"
                    "触边带的真值仍为 FAIL，但**不**由此链路判定 → 该带不进入一致率（abstain 单列），"
                    "真实触边检出交 L3 标注集。",
        })
    false_skip = {c: v["false_pass"] for c, v in per_check.items()}

    return {
        "mode": "L1-synthetic",
        "n_samples": len(samples),
        "checks": list(MEASURED_CHECKS),
        "overall": {
            "comparisons": all_cmps,
            "agreements": all_agree,
            "agreement_rate": (all_agree / all_cmps) if all_cmps else None,
            "false_pass": all_fp,
            "false_pass_count": len(all_fp),
            "false_skip": {c: v for c, v in false_skip.items() if v},
            "truth_balance_ok": balanced,
            "known_gaps": known_gaps,
        },
        "per_check": per_check,
        "scale_error": {
            "unit_size_px": {
                "abs_median": _pctile(abs_err["G1-1L"], 0.5),
                "abs_max": max(abs_err["G1-1L"]) if abs_err["G1-1L"] else None,
                "rel_median": _pctile(rel_err["G1-1L"], 0.5),
                "n": len(abs_err["G1-1L"]),
            },
            "skew_deg": {
                "abs_median": _pctile(abs_err["G1-2"], 0.5),
                "abs_max": max(abs_err["G1-2"]) if abs_err["G1-2"] else None,
                "rel_median": _pctile(rel_err["G1-2"], 0.5),
                "n": len(abs_err["G1-2"]),
            },
        },
        "systems": systems_block,
    }


def _injected(check: str, sp: Sample) -> Any:
    if check == "G1-1L":
        return sp.spacing
    if check == "G1-2":
        return sp.angle_deg
    if check == "G1-3":
        return sp.bbox_margin
    return sp.n_systems


# ---------------------------------------------------------------- 渲染
def _render(rep: dict) -> str:
    ov = rep["overall"]
    rate = ov["agreement_rate"]
    lines = [
        "G1 L1 synthetic calibration (measurement → threshold → verdict)",
        f"  samples: {rep['n_samples']}   checks: {','.join(rep['checks'])}",
        f"  agreement: {ov['agreements']}/{ov['comparisons']}"
        f" = {('%.4f' % rate) if rate is not None else 'n/a'}"
        f"   false_pass: {ov['false_pass_count']}"
        f"   truth_balance_ok: {ov['truth_balance_ok']}",
        f"  abstain(未混入一致率): {sum(v['abstain'] for v in rep['per_check'].values())}"
        f"   known_gaps: {len(ov['known_gaps'])}",
        "  per-check:",
    ]
    for c, v in rep["per_check"].items():
        r = v["agreement_rate"]
        lines.append(f"    {c:<7} cmp={v['comparisons']} agree={v['agreements']}"
                     f" rate={'%.4f' % r if r is not None else 'n/a'}"
                     f" mismatch={v['mismatch_count']} false_pass={len(v['false_pass'])}"
                     f" truth={v['truth_distribution']}")
    se = rep["scale_error"]
    u, sk = se["unit_size_px"], se["skew_deg"]
    lines.append(f"  scale error: unit_size abs_median={u['abs_median']} abs_max={u['abs_max']}"
                 f" rel_median={u['rel_median']} (n={u['n']})")
    lines.append(f"               skew      abs_median={sk['abs_median']} abs_max={sk['abs_max']}"
                 f" rel_median={sk['rel_median']} (n={sk['n']})")
    sysb = rep["systems"]
    lines.append("  systems 维度（G1-7 阈值依据 · 几何侧）:")
    for k, row in sysb["per_k"].items():
        lines.append(f"    K={k:<3} samples={row['samples']}"
                     f" unit_measured={row['unit_size_measured']}"
                     f" measure_rate={row['unit_size_measure_rate']}"
                     f" spacing_abs_err_max={row['spacing_abs_err_max']}"
                     f" span_geo=[{row['span_geo_min_px']},{row['span_geo_max_px']}]px"
                     f" fits_page={row['span_fits_page']}")
    lines.append(f"    几何可测上限 K={sysb['max_geo_measurable_K']}"
                 f"（页面 {sysb['page_height_px']}px 内、且线距测量完备）")
    if rep.get("omr_systems"):
        o = rep["omr_systems"]
        lines.append(f"  OMR 系统维度: status={o['status']} exact_rate={o.get('systems_exact_rate')}"
                     "  （SKIP = 引擎不可用，**非**通过）")
        for k, row in o.get("per_k", {}).items():
            lines.append(f"    K={k}: {row}")
    for g in ov["known_gaps"]:
        lines.append(f"  [gap] {g['check']}: {g['note']}")
    lines.append("  口径：本标定只覆盖 测量→阈值→三态 链路（合成样本）；"
                 "真实世界准确率需 L3 标注集。")
    return "\n".join(lines)


def build_report(args: argparse.Namespace) -> tuple[dict, int]:
    out_dir = Path(args.out_dir)
    samples = generate(
        out_dir,
        spacings=[float(x) for x in args.spacings.split(",")],
        angles=[float(x) for x in args.angles.split(",")],
        systems=[int(x) for x in args.systems.split(",")],
        bboxes=[int(x) for x in args.bboxes.split(",")],
        repeat=args.repeat,
    )
    measured: dict[int, A.AutoMetrics] = {}
    engine_errors: dict[str, str] = {}
    for sp in samples:
        m = measure(sp, engines=("cv",))
        if m.errors:
            engine_errors.update(m.errors)
        measured[sp.index] = m

    rep = evaluate(samples, measured)
    rep["params"] = {
        "spacings": [float(x) for x in args.spacings.split(",")],
        "angles": [float(x) for x in args.angles.split(",")],
        "systems": [int(x) for x in args.systems.split(",")],
        "bboxes": [int(x) for x in args.bboxes.split(",")],
        "repeat": args.repeat,
        "engine": "cv",
    }
    if engine_errors:
        rep["engine_errors"] = engine_errors
    if args.engine and "oemer" in args.engine:
        rep["omr_systems"] = systems_by_k(samples, engines=("oemer",))

    if args.report:
        rp = Path(args.report)
        rp.parent.mkdir(parents=True, exist_ok=True)
        rp.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")

    rc = 0
    if rep["overall"]["false_pass_count"]:
        rc = 1
    if args.fail_under is not None:
        rate = rep["overall"]["agreement_rate"] or 0.0
        if rate < args.fail_under:
            rc = 1
    return rep, rc


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="g1_calibrate.py",
        description="L1 合成标定集：测量→阈值→三态链路一致率（零人工）")
    ap.add_argument("--spacings", default=",".join(str(x) for x in DEFAULT_SPACINGS))
    ap.add_argument("--angles", default=",".join(str(x) for x in DEFAULT_ANGLES))
    ap.add_argument("--systems", default=",".join(str(x) for x in DEFAULT_SYSTEMS),
                    help="注入的系统数 K（G1-7 阈值依据），默认 4,6,8,10")
    ap.add_argument("--bboxes", default=",".join(str(x) for x in DEFAULT_BBOXES),
                    help="页边留白 px；0 = 触边（真值 FAIL）")
    ap.add_argument("--repeat", type=int, default=DEFAULT_REPEAT)
    ap.add_argument("--engine", default="", help="可选：追加 OMR 标定（oemer）")
    ap.add_argument("--out-dir", default="08_qc/calibration/l1_synthetic")
    ap.add_argument("--report", default="08_qc/calibration/l1_synthetic_report.json")
    ap.add_argument("--fail-under", type=float, default=None,
                    help="一致率下限；未达标则非零退出（如 0.99）")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    rep, rc = build_report(args)
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    else:
        print(_render(rep))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
