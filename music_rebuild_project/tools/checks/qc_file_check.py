#!/usr/bin/env python3
"""qc_file_check.py · WAV 交付件五项验证（纯 stdlib）

设计出处（阈值定义处，本脚本只引用不重复定义）:
  - ``00_docs/gates/gate_G4.md``: G4-3 削波计数 / G4-4 静音占比 /
    G4-5 时长误差 ≤ ±1s / G4-9 stem 完整性 / G4-10 输出规格
  - ``00_docs/gates/gate_G3.md``: G3-1 可用渲染产物（存在·可解码·非静音）

把上面几条「目测项」变成可复现命令:

    python tools/checks/qc_file_check.py master.wav \\
        --min-bytes 1024 --duration-expected 210.0 --duration-tol 1.0

退出码: 0 = 全部通过；1 = 有 FAIL；2 = 用法/读取错误。
报告: stdout 人类可读；``--json`` 输出机器可读（供门控脚本调用）。

能力范围（只做能测的五项）
  1. 存在       —— 路径是否是常规文件
  2. 大小       —— 字节数 > ``--min-bytes``，且 > 0
  3. 时长       —— WAV 帧数 / 采样率（用采样率本身，不做 ffprobe 估计）
  4. 静音占比   —— 逐帧 RMS(dBFS) 低于 ``--silence-dbfs`` 的帧占比
  5. 削波计数   —— 绝对值 ≥ ``--clipping-dbfs``（默认 0 dBFS）的采样点数

明确不支持（见本目录 README「不支持项与原因」）:
  - 响度 LUFS / 真峰 dBTP (G4-1/G4-2)、频谱掩蔽 (G4-7)、单声道兼容 (G4-8)、
    频率加权 SILENCE、MP3/FLAC 解码 —— 需要 EBU R128 滤波器组、相位折叠、
    心理声学模型或额外解码器，沙箱内无 numpy/ffmpeg，故不实现也不假装实现。
    这些项必须由 ``ffmpeg``/``ffprobe`` 命令 + 人工试听勾选承担。
"""

from __future__ import annotations

import argparse
import json
import math
import os
import struct
import sys
import wave

VERSION = "1.0.0"

# ---------------------------------------------------------------- 默认阈值
# 均取自 gate_G4.md；此处只是默认值，可用 CLI 覆盖（阈值快照仍以 08_qc/gate_config.json 为准）。
DEFAULT_SILENCE_DBFS = -60.0      # 低于此值的帧视为静音帧
DEFAULT_SILENCE_FRAME_MS = 50     # 帧长
DEFAULT_SILENCE_MAX_RATIO = 0.05  # 全轨静音占比上限（含首尾淡入淡出，故非 0）
DEFAULT_MIN_SILENCE_SEC = 1.0     # 连续静音段达到该长度才算「异常段」（忽略淡入淡出间隙）
DEFAULT_CLIPPING_DBFS = 0.0       # G4-3: 达 0 dBFS 采样数必须 = 0
DEFAULT_MIN_BYTES = 1
DEFAULT_DURATION_TOL = 1.0        # G4-5: ±1 秒
DEFAULT_SPEC_RATE = 44100         # G4-10

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"


# ---------------------------------------------------------------- 工具函数
def dbfs_abs(value: float, full_scale: float) -> float:
    """单个采样值的绝对 dBFS（0 dBFS = 满幅）。"""
    if value == 0:
        return -math.inf
    return 20.0 * math.log10(abs(value) / full_scale)


def _decode_samples(raw: bytes, sampwidth: int):
    """把 WAV 帧字节解成 float 采样序列（-1.0 ~ 1.0）。纯 stdlib。"""
    n = len(raw)
    if sampwidth == 1:  # 8-bit WAV 是无符号偏移存储
        return [((b - 128) / 128.0) for b in raw]
    if sampwidth == 2:
        count = n // 2
        ints = struct.unpack("<%dh" % count, raw[: count * 2])
        return [v / 32768.0 for v in ints]
    if sampwidth == 3:
        out = []
        for i in range(0, n - n % 3, 3):
            b0, b1, b2 = raw[i], raw[i + 1], raw[i + 2]
            v = b0 | (b1 << 8) | (b2 << 16)
            if v & 0x800000:
                v -= 1 << 24
            out.append(v / 8388608.0)
        return out
    if sampwidth == 4:
        count = n // 4
        ints = struct.unpack("<%di" % count, raw[: count * 4])
        return [v / 2147483648.0 for v in ints]
    raise ValueError("unsupported sample width: %d bytes" % sampwidth)


def _iter_frames(path: str, frame_ms: int):
    """流式读 WAV，按 frame_ms 切帧，产出 (帧序号, 帧 RMS dBFS, 帧内峰值 dBFS)。

    先探测声道布局与采样率，再对每帧做单声道下混（各声道平均）算 RMS。
    """
    with wave.open(path, "rb") as wf:
        nch, sw, rate, nframes = (
            wf.getnchannels(), wf.getsampwidth(), wf.getframerate(), wf.getnframes(),
        )
        frame_len = max(1, int(rate * frame_ms / 1000.0))
        idx = 0
        while True:
            raw = wf.readframes(frame_len)
            if not raw:
                break
            samples = _decode_samples(raw, sw)
            if nch > 1:
                # 下混为单声道：按声道求平均（比取首声道更接近听感能量）
                mono = [0.0] * (len(samples) // nch)
                for i, v in enumerate(samples):
                    mono[i // nch] += v
                samples = [v / nch for v in mono]
            if samples:
                peak = max(abs(v) for v in samples)
                rms = math.sqrt(sum(v * v for v in samples) / len(samples))
                yield (
                    idx,
                    dbfs_abs(rms, 1.0),
                    dbfs_abs(peak, 1.0),
                    len(samples),
                )
            idx += 1
        yield ("_header", nch, sw, rate, nframes)


# ---------------------------------------------------------------- 检查实现
def check_exists(path: str) -> dict:
    ok = os.path.isfile(path)
    return {
        "name": "exists",
        "status": PASS if ok else FAIL,
        "detail": "regular file" if ok else "not found or not a regular file",
        "path": path,
        "bytes": os.path.getsize(path) if ok else None,
    }


def check_size(path: str, min_bytes: int) -> dict:
    size = os.path.getsize(path)
    ok = size > 0 and size >= min_bytes
    return {
        "name": "size",
        "status": PASS if ok else FAIL,
        "detail": "%d bytes (min %d)" % (size, min_bytes),
        "bytes": size,
        "min_bytes": min_bytes,
    }


def check_clipping(path: str, clipping_dbfs: float) -> dict:
    """G4-3: 达 0 dBFS（可由 --clipping-dbfs 放宽）的采样点数，必须 = 0。

    比较在**归一化浮点域**做：采样先除以自身满幅（8-bit 128 / 16-bit 32768 /
    24-bit 8388608 / 32-bit 2147483648），故 16-bit 的最大码值 32767 归一后是
    -0.00027 dBFS，**不计为削波**——这是刻意的：门要求的是「顶到 0 dBFS」，
    不是「用满了位深」。若需按「达到满幅码值」判定，用 `--clipping-dbfs -0.1`。
    """
    threshold = 10.0 ** (clipping_dbfs / 20.0)
    count = 0
    peak = 0.0
    total = 0
    with wave.open(path, "rb") as wf:
        sw = wf.getsampwidth()
        raw = wf.readframes(wf.getnframes())
    for v in _decode_samples(raw, sw):
        total += 1
        av = abs(v)
        if av > peak:
            peak = av
        if av >= threshold:
            count += 1
    return {
        "name": "clipping",
        "status": PASS if count == 0 else FAIL,
        "detail": "clipped samples=%d (threshold %.1f dBFS), peak=%.2f dBFS"
                  % (count, clipping_dbfs, dbfs_abs(peak, 1.0)),
        "clipped_samples": count,
        "threshold_dbfs": clipping_dbfs,
        "peak_dbfs": round(dbfs_abs(peak, 1.0), 4),
        "total_samples": total,
    }


def check_duration(path: str, expected: float | None, tol: float) -> dict:
    """G4-5: 时长 = 帧数 / 采样率；有 --duration-expected 时校验 ±tol。"""
    with wave.open(path, "rb") as wf:
        rate, nframes = wf.getframerate(), wf.getnframes()
    duration = nframes / float(rate)
    res = {
        "name": "duration",
        "status": PASS,
        "detail": "%d frames / %d Hz = %.3f s" % (nframes, rate, duration),
        "duration_sec": round(duration, 6),
        "sample_rate": rate,
        "frames": nframes,
    }
    if expected is not None:
        err = abs(duration - expected)
        res["expected_sec"] = expected
        res["tolerance_sec"] = tol
        res["error_sec"] = round(err, 6)
        res["status"] = PASS if err <= tol else FAIL
        res["detail"] += "; expected %.3f ±%.3f, error %.3f" % (expected, tol, err)
    return res


def check_silence(path: str, silence_dbfs: float, frame_ms: int,
                  max_ratio: float, min_sec: float) -> dict:
    """G4-4: 逐帧 RMS 低于阈值的帧占比 + 最长连续静音段。

    连续静音段 ≥ ``min_sec`` 判定为「异常静音」——淡入淡出的零星静音帧
    不会触发，符合 gate_G4「淡入淡出间隙静音须在白名单内」的口径。
    """
    total = 0
    silent = 0
    run = 0
    longest = 0
    frame_sec = frame_ms / 1000.0
    for item in _iter_frames(path, frame_ms):
        if item[0] == "_header":
            break
        total += 1
        if item[1] < silence_dbfs:
            silent += 1
            run += 1
            longest = max(longest, run)
        else:
            run = 0
    ratio = silent / total if total else 0.0
    longest_sec = longest * frame_sec
    too_much = ratio > max_ratio
    long_gap = longest_sec >= min_sec
    ok = not long_gap and not too_much
    return {
        "name": "silence",
        "status": PASS if ok else FAIL,
        "detail": "silent frames %d/%d (%.1f%%), longest run %.3f s "
                  "(max ratio %.1f%%, min anomalous gap %.1f s)"
                  % (silent, total, ratio * 100, longest_sec, max_ratio * 100, min_sec),
        "total_frames": total,
        "silent_frames": silent,
        "silence_ratio": round(ratio, 6),
        "max_ratio": max_ratio,
        "longest_silence_sec": round(longest_sec, 4),
        "min_anomalous_sec": min_sec,
        "frame_ms": frame_ms,
        "threshold_dbfs": silence_dbfs,
    }


def check_spec(path: str, expect_rate: int) -> dict:
    """G4-10 中脚本能测的部分：容器/位深/采样率/声道数（MP3 320k 不在范围内）。"""
    with wave.open(path, "rb") as wf:
        rate, nch, sw = wf.getframerate(), wf.getnchannels(), wf.getsampwidth()
    ok = rate == expect_rate
    return {
        "name": "spec",
        "status": PASS if ok else FAIL,
        "detail": "wav %d Hz / %d-bit / %d ch (expected %d Hz)"
                  % (rate, sw * 8, nch, expect_rate),
        "sample_rate": rate,
        "bit_depth": sw * 8,
        "channels": nch,
        "expected_rate": expect_rate,
        "container": "RIFF/WAVE",
    }


def run_checks(path: str, args: argparse.Namespace) -> dict:
    checks: list[dict] = []

    c = check_exists(path)
    checks.append(c)
    if c["status"] == FAIL:
        # 文件不存在时后续检查无意义，直接短路（诚实报错，不伪造 SKIP 结论）
        checks.append({"name": "size", "status": SKIP, "detail": "skipped: file missing"})
        for name in ("duration", "silence", "clipping", "spec"):
            checks.append({"name": name, "status": SKIP, "detail": "skipped: file missing"})
        return _wrap(path, checks)

    checks.append(check_size(path, args.min_bytes))

    try:
        with wave.open(path, "rb") as wf:
            wf.readframes(1)
    except wave.Error as exc:
        for name in ("duration", "silence", "clipping", "spec"):
            checks.append(
                {"name": name, "status": FAIL, "detail": "undecodable WAV: %s" % exc}
            )
        return _wrap(path, checks)

    checks.append(check_duration(path, args.duration_expected, args.duration_tol))
    checks.append(
        check_silence(path, args.silence_dbfs, args.silence_frame_ms,
                      args.silence_max_ratio, args.min_silence_sec)
    )
    checks.append(check_clipping(path, args.clipping_dbfs))
    checks.append(check_spec(path, args.expect_rate))
    return _wrap(path, checks)


def _wrap(path: str, checks: list[dict]) -> dict:
    failed = [c["name"] for c in checks if c["status"] == FAIL]
    return {
        "script": "qc_file_check.py",
        "version": VERSION,
        "path": os.path.abspath(path),
        "conclusion": PASS if not failed else FAIL,
        "failed": failed,
        "checks": checks,
    }


def render_human(report: dict) -> str:
    lines = [
        "qc_file_check v%s · %s" % (report["version"], report["path"]),
        "-" * 72,
    ]
    label = {PASS: "PASS", FAIL: "FAIL", SKIP: "SKIP"}
    for c in report["checks"]:
        lines.append("  [%s] %-9s %s" % (label[c["status"]], c["name"], c["detail"]))
    lines.append("-" * 72)
    lines.append("CONCLUSION: %s" % report["conclusion"])
    if report["failed"]:
        lines.append("FAILED: %s" % ", ".join(report["failed"]))
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="qc_file_check.py",
        description="WAV 交付件五项验证（存在/大小/时长/静音/削波），退出码 0=通过。",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("path", help="待检查的 WAV 路径")
    p.add_argument("--json", action="store_true", help="输出 JSON 报告")
    p.add_argument("--min-bytes", type=int, default=DEFAULT_MIN_BYTES,
                   help="最小字节数阈值")
    p.add_argument("--duration-expected", type=float, default=None,
                   help="预期时长（秒）；省略则只报时长不做判定")
    p.add_argument("--duration-tol", type=float, default=DEFAULT_DURATION_TOL,
                   help="时长容差（秒）")
    p.add_argument("--silence-dbfs", type=float, default=DEFAULT_SILENCE_DBFS,
                   help="静音判定阈值（帧 RMS dBFS）")
    p.add_argument("--silence-frame-ms", type=int, default=DEFAULT_SILENCE_FRAME_MS,
                   help="静音分析帧长（毫秒）")
    p.add_argument("--silence-max-ratio", type=float, default=DEFAULT_SILENCE_MAX_RATIO,
                   help="全轨静音帧占比上限（0~1）")
    p.add_argument("--min-silence-sec", type=float, default=DEFAULT_MIN_SILENCE_SEC,
                   help="连续静音达到该秒数才算异常静音段")
    p.add_argument("--clipping-dbfs", type=float, default=DEFAULT_CLIPPING_DBFS,
                   help="削波判定阈值 dBFS（默认 0.0，即 G4-3）")
    p.add_argument("--expect-rate", type=int, default=DEFAULT_SPEC_RATE,
                   help="G4-10 预期采样率")
    p.add_argument("--version", action="version", version=VERSION)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not (0.0 <= args.silence_max_ratio <= 1.0):
        print("error: --silence-max-ratio must be within [0, 1]", file=sys.stderr)
        return 2
    try:
        report = run_checks(args.path, args)
    except OSError as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2
    except (wave.Error, ValueError) as exc:
        print("error: undecodable WAV: %s" % exc, file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render_human(report))
    return 0 if report["conclusion"] == PASS else 1


if __name__ == "__main__":
    sys.exit(main())
