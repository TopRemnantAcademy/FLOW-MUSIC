#!/usr/bin/env python3
"""mix_checks.py · 混音最小检查（纯 stdlib，不碰 ffmpeg）

出处：#58「混音最小检查（增益 / 보컬优先 / EQ / 混响 / 单声道兼容）」，
把混音环节的人工 QC 变成 gate 前的自动守门员。阈值定义处见
``00_docs/gates/gate_G4.md``：G4-2 真峰 / G4-3 削波 / G4-7 声乐-伴奏关系 /
G4-8 单声道兼容；本脚本只引用阈值，不重复定义门的口径。

用法::

    # 四轨 stem（drums/bass/harmony/vocal）或单条 master WAV
    python tools/checks/mix_checks.py 07_mix/song01/stems/
    python tools/checks/mix_checks.py 07_mix/song01/masters/song01_mix_v001.wav --json

退出码语义（**保证**，测试逐条断言）::

    0  无 FAIL（全 PASS，或仅有 WARN —— WARN 不阻断，但报告中标出）
    1  至少一条 FAIL
    2  用法 / 读取错误（路径不存在、非 WAV、参数非法）

五个维度
  1. ``clipping``  峰值 ``peak_dbfs`` ≥ ``-0.3`` 判 FAIL（疑似削波），记录最大样本值。
  2. ``vocal``     보컬轨 RMS / 全混 RMS ≥ ``0.6``；低于判 FAIL（声乐被埋）。
  3. ``mono``      L/R 相关性（mid/side 口径）< ``0.3`` 判 WARN（相位风险）。
  4. ``silence``   -50dBFS 以下的时长占比 > ``0.5`` 判 FAIL（空轨/卡顿嫌疑）。
  5. ``spectrum``  低/中/高频能量分布极端失衡判 WARN（不硬性 FAIL，需人工判）。

为什么不用 numpy/scipy：沙箱内没有它们，且**可复现性**优先 —— 频谱用
``_band_energy`` 的 Goertzel 式单点功率估计，逐目标频点算，结果确定性好，
误差可由测试固定样例钉死。测试用 ``sys.modules`` 断言二者从未被 import。

为什么复用 ``tools/render/wavio``：WAV 头解析（尤其 24bit 位打包）已在
#59 的 ``wavio.read_wav`` 里做对过一次，另行实现会制造第二份「真相」，
也让「24bit 读错」这类缺陷有两个可能来源。本模块**只 import 复用**。
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys

# 复用 #59 的 WAV 读写（含 24bit 位打包）：不重复实现，只 import。
_HERE = os.path.dirname(os.path.abspath(__file__))
_TOOLS_DIR = os.path.dirname(_HERE)
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)

try:  # 包内导入（pytest 从仓库根跑）
    from music_rebuild_project.tools.render import wavio
except ImportError:  # 直接执行脚本时的兜底
    from render import wavio  # type: ignore

VERSION = "1.0.0"

PASS, FAIL, WARN = "PASS", "FAIL", "WARN"

#: 四轨 stem 的规范名（顺序 = 全混相加顺序，与 G4-9 一致）。
REQUIRED_STEMS = ("drums", "bass", "harmony", "vocal")

# ---------------------------------------------------------------- 默认阈值
# 全部可 CLI 覆盖；默认值即 issue #58 点名的口径。
DEFAULT_PEAK_FAIL_DBFS = -0.3      # 峰值 ≥ 此值 → 疑似 clipping → FAIL
DEFAULT_VOCAL_RATIO_MIN = 0.6      # vocal RMS / mix RMS 下限
DEFAULT_MONO_CORR_MIN = 0.3        # L/R 相关系数下限（含 mid/side 口径）
DEFAULT_SILENCE_DBFS = -50.0       # 整轨能量低于此值视为静音
DEFAULT_SILENCE_MAX_RATIO = 0.5    # 静音时长占比上限
DEFAULT_SPECTRUM_TOL = 0.60        # 低/中/高任一频段占比超过此值 → WARN
DEFAULT_SPECTRUM_FRAME_MS = 50     # 频谱分析帧长


# ---------------------------------------------------------------- 数值工具
def rms(samples) -> float:
    """归一化 RMS。空序列返回 0.0（不抛错 —— 0 帧 WAV 由 wavio 拦）。"""
    if not samples:
        return 0.0
    return math.sqrt(sum(v * v for v in samples) / len(samples))


def dbfs(amplitude: float) -> float:
    """线性幅度 → dBFS；0 幅值返回 -inf（JSON 里由调用方决定怎么写）。"""
    if amplitude <= 0:
        return float("-inf")
    return 20.0 * math.log10(amplitude)


def peak_dbfs(samples) -> float:
    """峰值 dBFS。"""
    return dbfs(max((abs(v) for v in samples), default=0.0))


def _deinterleave(samples, channels):
    """交错 PCM → 每声道序列列表。单声道返回 ``[samples]``。"""
    if channels <= 1:
        return [list(samples)]
    left = [samples[i] for i in range(0, len(samples), channels)]
    right = [samples[i] for i in range(1, len(samples), channels)]
    return [left, right]


def correlation(left, right) -> float:
    """皮尔逊相关系数 ∈ [-1, 1]。长度取短；零方差返回 0.0。

    相位相反的立体声（L = -R）会得到 -1.0 —— 折叠单声道后互相抵消，
    正是 G4-8 要拦的「关键元素消失」。
    """
    n = min(len(left), len(right))
    if n == 0:
        return 0.0
    ml = sum(left[:n]) / n
    mr = sum(right[:n]) / n
    num = 0.0
    dl = 0.0
    dr = 0.0
    for i in range(n):
        a = left[i] - ml
        b = right[i] - mr
        num += a * b
        dl += a * a
        dr += b * b
    if dl <= 0.0 or dr <= 0.0:
        return 0.0
    return num / math.sqrt(dl * dr)


def _goertzel_power(samples, rate, freq) -> float:
    """单频点功率（Goertzel）。比整段 FFT 便宜，且结果确定性好。"""
    if not samples or rate <= 0 or freq <= 0:
        return 0.0
    n = len(samples)
    w = 2.0 * math.pi * freq / rate
    coeff = 2.0 * math.cos(w)
    s0 = s1 = s2 = 0.0
    for v in samples:
        s0 = v + coeff * s1 - s2
        s2 = s1
        s1 = s0
    real = s1 - s2 * math.cos(w)
    imag = s2 * math.sin(w)
    return (real * real + imag * imag) / (n * n)


#: 三个频段的代表频点（Hz）。低频取 60/120，中频取 1k/2k，高频取 6k/12k。
_BANDS = {
    "low": (60.0, 120.0),
    "mid": (1000.0, 2000.0),
    "high": (6000.0, 12000.0),
}


def band_energies(samples, rate, *, bands=_BANDS) -> dict:
    """低/中/高代表频点功率之和。返回 ``{low, mid, high}``。"""
    out = {}
    for name, freqs in bands.items():
        out[name] = sum(_goertzel_power(samples, rate, f) for f in freqs)
    return out


def band_ratios(energies) -> dict:
    """频段占比（Σ=1）。总能量为 0 时各段返回 0.0。"""
    total = sum(energies.values())
    if total <= 0.0:
        return {k: 0.0 for k in energies}
    return {k: v / total for k, v in energies.items()}


# ---------------------------------------------------------------- 检查项
def check_clipping(samples, *, threshold_dbfs=DEFAULT_PEAK_FAIL_DBFS) -> dict:
    """峰值 ≥ ``-0.3 dBFS`` → FAIL（疑似 clipping）。记录最大样本值。"""
    samples = list(samples)
    peak = max((abs(v) for v in samples), default=0.0)
    pk_db = dbfs(peak)
    ok = pk_db < threshold_dbfs
    return {
        "name": "clipping",
        "status": PASS if ok else FAIL,
        "detail": "peak %.4f dBFS (bar %.2f), max |sample| %.6f"
        % (pk_db, threshold_dbfs, peak),
        "peak_dbfs": round(pk_db, 4),
        "threshold_dbfs": threshold_dbfs,
        "max_abs_sample": round(peak, 6),
        "samples": len(samples),
    }


def check_vocal_priority(mix_samples, vocal_samples, *, min_ratio=DEFAULT_VOCAL_RATIO_MIN) -> dict:
    """보컬 RMS / 全混 RMS ≥ 阈值；低于 → FAIL（声乐被埋，G4-7 前置）。"""
    mix_r = rms(mix_samples)
    vocal_r = rms(vocal_samples)
    ratio = (vocal_r / mix_r) if mix_r > 0 else 0.0
    ok = ratio >= min_ratio
    return {
        "name": "vocal",
        "status": PASS if ok else FAIL,
        "detail": "vocal/mix RMS ratio %.4f (min %.2f), vocal %.4f dBFS, mix %.4f dBFS"
        % (ratio, min_ratio, dbfs(vocal_r), dbfs(mix_r)),
        "ratio": round(ratio, 6),
        "min_ratio": min_ratio,
        "vocal_rms_dbfs": round(dbfs(vocal_r), 4),
        "mix_rms_dbfs": round(dbfs(mix_r), 4),
    }


def check_mono_compat(samples, channels, *, min_corr=DEFAULT_MONO_CORR_MIN) -> dict:
    """L/R 相关性（mid/side 口径）< 阈值 → WARN（相位风险）。

    单声道素材无从谈相关性 → PASS（不 WARN、不 FAIL），但字段里标 ``n/a``。
    """
    if channels < 2:
        return {
            "name": "mono",
            "status": PASS,
            "detail": "mono source, correlation n/a",
            "correlation": None,
            "side_energy_ratio": None,
            "min_corr": min_corr,
            "channels": channels,
        }
    left, right = _deinterleave(samples, channels)[:2]
    corr = correlation(left, right)
    mid = [(a + b) / 2.0 for a, b in zip(left, right)]
    side = [(a - b) / 2.0 for a, b in zip(left, right)]
    mid_e = sum(v * v for v in mid)
    side_e = sum(v * v for v in side)
    side_ratio = side_e / mid_e if mid_e > 0 else float("inf")
    return {
        "name": "mono",
        "status": PASS if corr >= min_corr else WARN,
        "detail": "L/R correlation %.4f (min %.2f), side/mid energy %.4f"
        % (corr, min_corr, side_ratio),
        "correlation": round(corr, 6),
        "side_energy_ratio": round(side_ratio, 6),
        "min_corr": min_corr,
        "channels": channels,
    }


def check_silence(samples, rate, *, silence_dbfs=DEFAULT_SILENCE_DBFS,
                  max_ratio=DEFAULT_SILENCE_MAX_RATIO,
                  frame_ms=DEFAULT_SPECTRUM_FRAME_MS) -> dict:
    """能量低于 ``-50dBFS`` 的时长占比 > 上限 → FAIL（空轨/卡顿嫌疑）。"""
    samples = list(samples)
    frame_len = max(1, int(rate * frame_ms / 1000.0))
    total = 0
    silent = 0
    for i in range(0, len(samples), frame_len):
        chunk = samples[i:i + frame_len]
        if not chunk:
            continue
        total += 1
        if dbfs(rms(chunk)) < silence_dbfs:
            silent += 1
    ratio = (silent / total) if total else 0.0
    ok = ratio <= max_ratio
    return {
        "name": "silence",
        "status": PASS if ok else FAIL,
        "detail": "silent %.1f%% (max %.1f%%), %d/%d frames below %.1f dBFS"
        % (ratio * 100, max_ratio * 100, silent, total, silence_dbfs),
        "silence_ratio": round(ratio, 6),
        "max_ratio": max_ratio,
        "silent_frames": silent,
        "total_frames": total,
        "frame_ms": frame_ms,
        "silence_dbfs": silence_dbfs,
    }


def check_spectrum(samples, rate, *, tol=DEFAULT_SPECTRUM_TOL) -> dict:
    """低/中/高频占比极端失衡 → WARN（不硬性 FAIL，需人工判）。"""
    energies = band_energies(samples, rate)
    ratios = band_ratios(energies)
    worst_band = max(ratios, key=ratios.get) if ratios else None
    worst = ratios.get(worst_band, 0.0) if worst_band else 0.0
    balanced = worst <= tol
    return {
        "name": "spectrum",
        "status": PASS if balanced else WARN,
        "detail": "band ratios low/mid/high = %.4f/%.4f/%.4f (max any band %.2f)"
        % (ratios.get("low", 0.0), ratios.get("mid", 0.0), ratios.get("high", 0.0), tol),
        "band_ratios": {k: round(v, 6) for k, v in ratios.items()},
        "band_energies": {k: round(v, 10) for k, v in energies.items()},
        "worst_band": worst_band,
        "max_band_ratio": round(worst, 6),
        "tol": tol,
    }


# ---------------------------------------------------------------- 组织与报告
def _canonical(name: str) -> str:
    """轨道别名收敛（只做最小映射，正式名表在 render.stems）。"""
    n = str(name).strip().lower()
    return {"vocals": "vocal", "voice": "vocal", "drum": "drums"}.get(n, n)


def _load(path):
    """读一条 WAV，返回 ``wavio.read_wav`` 的 info。"""
    return wavio.read_wav(path)


def _stem_map(target: str) -> dict:
    """target 是目录 → 用 render.stems 扫；是文件 → 单文件 ``master``。"""
    if os.path.isdir(target):
        try:
            from music_rebuild_project.tools.render import stems as _stems
        except ImportError:
            from render import stems as _stems  # type: ignore
        found = _stems.stem_paths(target)
        return {k: v for k, v in found.items()}
    return {"master": target}


def run(target: str, args) -> dict:
    paths = _stem_map(target)
    if not paths:
        raise wavio.MissingFileError(f"未发现任何 WAV：{target}")

    infos = {}
    for name, path in sorted(paths.items()):
        infos[_canonical(name)] = _load(path)

    # 全混 = 所有轨逐采样相加（复用 wavio.mix_down，长度不一致会直接抛错）。
    tracks = [infos[k]["samples"] for k in sorted(infos)]
    if len(tracks) == 1:
        mix = list(tracks[0])
    else:
        mix, _ = wavio.mix_down(tracks)

    mono_ref = infos.get("vocal", infos.get("master", next(iter(infos.values()))))
    rate = mono_ref["framerate"]
    channels = mono_ref["channels"]
    mix_mono = _downmix(mix, 1 if not any(i["channels"] > 1 for i in infos.values())
                        else max(i["channels"] for i in infos.values()))

    checks = [check_clipping(mix_mono, threshold_dbfs=args.peak_dbfs)]

    if "vocal" in infos:
        checks.append(check_vocal_priority(
            mix_mono, _downmix(infos["vocal"]["samples"], infos["vocal"]["channels"]),
            min_ratio=args.vocal_ratio_min))
    else:
        checks.append({
            "name": "vocal", "status": PASS,
            "detail": "no vocal stem supplied; ratio n/a",
            "ratio": None, "min_ratio": args.vocal_ratio_min,
        })

    # 单声道兼容：优先看全混（或 master）的立体声场。
    stereo_src = infos.get("vocal", mono_ref)
    checks.append(check_mono_compat(stereo_src["samples"], stereo_src["channels"],
                                    min_corr=args.mono_corr_min))
    checks.append(check_silence(mix_mono, rate, silence_dbfs=args.silence_dbfs,
                                max_ratio=args.silence_max_ratio,
                                frame_ms=args.frame_ms))
    checks.append(check_spectrum(mix_mono, rate, tol=args.spectrum_tol))

    failed = [c["name"] for c in checks if c["status"] == FAIL]
    warned = [c["name"] for c in checks if c["status"] == WARN]
    conclusion = FAIL if failed else (WARN if warned else PASS)
    return {
        "script": "mix_checks.py",
        "version": VERSION,
        "target": os.path.abspath(target),
        "stems": sorted(infos),
        "sample_rate": rate,
        "channels": channels,
        "conclusion": conclusion,
        "failed": failed,
        "warned": warned,
        "checks": checks,
    }


def _downmix(samples, channels) -> list:
    """多声道 → 单声道（各声道平均）。单声道原样返回（拷贝，避免别名）。"""
    samples = list(samples)
    if channels <= 1:
        return samples
    n = len(samples) // channels
    out = [0.0] * n
    for i in range(n):
        s = 0.0
        for c in range(channels):
            s += samples[i * channels + c]
        out[i] = s / channels
    return out


def render_human(report: dict) -> str:
    lines = [
        "mix_checks v%s · %s" % (report["version"], report["target"]),
        "stems: %s · %dHz/%dch" % (", ".join(report["stems"]),
                                   report["sample_rate"], report["channels"]),
        "-" * 72,
    ]
    for c in report["checks"]:
        lines.append("  [%s] %-8s %s" % (c["status"], c["name"], c["detail"]))
    lines.append("-" * 72)
    lines.append("CONCLUSION: %s" % report["conclusion"])
    if report["failed"]:
        lines.append("FAILED: %s" % ", ".join(report["failed"]))
    if report["warned"]:
        lines.append("WARNED: %s" % ", ".join(report["warned"]))
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="mix_checks.py",
        description="混音最小检查：削波/보컬优先/单声道兼容/静音占比/频率平衡。"
                    "退出码 0=无 FAIL，1=有 FAIL，2=用法/读取错误。",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("target", help="四轨 stem 目录，或单条 master WAV")
    p.add_argument("--json", action="store_true", help="输出 JSON 报告")
    p.add_argument("--peak-dbfs", type=float, default=DEFAULT_PEAK_FAIL_DBFS,
                   help="削波判定阈值 dBFS（峰值 ≥ 此值判 FAIL）")
    p.add_argument("--vocal-ratio-min", type=float, default=DEFAULT_VOCAL_RATIO_MIN,
                   help="보컬/全混 RMS 比下限")
    p.add_argument("--mono-corr-min", type=float, default=DEFAULT_MONO_CORR_MIN,
                   help="L/R 相关系数下限（低于判 WARN）")
    p.add_argument("--silence-dbfs", type=float, default=DEFAULT_SILENCE_DBFS,
                   help="静音判定阈值 dBFS")
    p.add_argument("--silence-max-ratio", type=float, default=DEFAULT_SILENCE_MAX_RATIO,
                   help="静音时长占比上限（0~1）")
    p.add_argument("--spectrum-tol", type=float, default=DEFAULT_SPECTRUM_TOL,
                   help="单一频段占比上限（超过判 WARN）")
    p.add_argument("--frame-ms", type=int, default=DEFAULT_SPECTRUM_FRAME_MS,
                   help="静音分析帧长（毫秒）")
    p.add_argument("--version", action="version", version=VERSION)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if not (0.0 <= args.silence_max_ratio <= 1.0):
        print("error: --silence-max-ratio must be within [0, 1]", file=sys.stderr)
        return 2
    if not (0.0 <= args.spectrum_tol <= 1.0):
        print("error: --spectrum-tol must be within [0, 1]", file=sys.stderr)
        return 2

    try:
        report = run(args.target, args)
    except (wavio.WavError, OSError) as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render_human(report))
    # WARN 不阻断：0=无 FAIL，1=有 FAIL。
    return 1 if report["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
