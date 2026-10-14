"""qc_file_check.py 五项判定回归测试。

策略
  **不依赖任何真实媒体文件**（仓库不放二进制）。所有 WAV 都在 ``tmp_path``
  里用 stdlib ``wave`` + ``struct`` 现场合成：纯静音、满幅削波、正常正弦、
  时长不符。断言点严格对齐 ``00_docs/gates/gate_G4.md``：
  G4-3 削波计数=0 / G4-4 静音占比 / G4-5 时长误差 ≤ ±1s / G4-9 存在·大小·时长 /
  G4-10 采样率。

  脚本自身是 stdlib-only，所以这些用例在沙箱（无 numpy/ffmpeg）里必须全绿。
"""

from __future__ import annotations

import importlib.util
import json
import math
import struct
import subprocess
import sys
import wave
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "qc_file_check.py"


def _load_module():
    """以文件路径导入被测脚本（它不在包里，也无 __init__.py）。"""
    spec = importlib.util.spec_from_file_location("qc_file_check", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


qc = _load_module()


# ---------------------------------------------------------------- WAV 合成
def write_wav(path: Path, *, seconds: float, rate: int = 44100,
              amplitude: float = 0.5, freq: float = 440.0,
              channels: int = 1, sampwidth: int = 2,
              silent: bool = False, dc_full_scale: bool = False,
              tail_silence: float = 0.0) -> Path:
    """合成一个 WAV。

    silent          全零采样（纯静音）
    dc_full_scale   满幅直流（等价削波：每个采样都顶在 1.0 / -1.0）
    tail_silence    结尾追加的静音秒数（用于构造「异常静音段」）
    """
    total = int(rate * seconds)
    tail = int(rate * tail_silence)
    max_int = 2 ** (sampwidth * 8 - 1) - 1
    frames = bytearray()
    for i in range(total + tail):
        if silent or i >= total:
            value = 0.0
        elif dc_full_scale:
            value = 1.0
        else:
            value = amplitude * math.sin(2.0 * math.pi * freq * i / rate)
        iv = int(max(-1.0, min(1.0, value)) * max_int)
        for _ in range(channels):
            if sampwidth == 2:
                frames += struct.pack("<h", iv)
            elif sampwidth == 1:
                frames += struct.pack("<B", iv + 128)
            elif sampwidth == 4:
                frames += struct.pack("<i", iv)
            else:
                raise ValueError(sampwidth)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sampwidth)
        wf.setframerate(rate)
        wf.writeframes(bytes(frames))
    return path


def run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True, text=True,
    )


def check_names(report: dict) -> set[str]:
    return {c["name"] for c in report["checks"]}


def status_of(report: dict, name: str) -> str:
    return next(c["status"] for c in report["checks"] if c["name"] == name)


# ---------------------------------------------------------------- 1. 存在
def test_missing_file_fails_and_skips_rest(tmp_path):
    report = qc.run_checks(str(tmp_path / "nope.wav"), qc.build_parser().parse_args(
        [str(tmp_path / "nope.wav")]))
    assert report["conclusion"] == qc.FAIL
    assert status_of(report, "exists") == qc.FAIL
    # 文件都不存在，其余项必须 SKIP，而不是伪造 PASS
    for name in ("size", "duration", "silence", "clipping", "spec"):
        assert status_of(report, name) == qc.SKIP


def test_directory_is_not_a_file(tmp_path):
    report = qc.run_checks(str(tmp_path), qc.build_parser().parse_args([str(tmp_path)]))
    assert report["conclusion"] == qc.FAIL
    assert status_of(report, "exists") == qc.FAIL


def test_corrupt_wav_is_undecodable(tmp_path):
    bad = tmp_path / "truncated.wav"
    bad.write_bytes(b"RIFF\x00\x00\x00\x00WAVEjunk")
    report = qc.run_checks(str(bad), qc.build_parser().parse_args([str(bad)]))
    assert report["conclusion"] == qc.FAIL
    assert status_of(report, "exists") == qc.PASS


# ---------------------------------------------------------------- 2. 大小
def test_size_threshold(tmp_path):
    small = write_wav(tmp_path / "small.wav", seconds=0.1)
    report = qc.run_checks(str(small), qc.build_parser().parse_args(
        [str(small), "--min-bytes", "999999999"]))
    assert status_of(report, "size") == qc.FAIL

    report_ok = qc.run_checks(str(small), qc.build_parser().parse_args(
        [str(small), "--min-bytes", "100"]))
    assert status_of(report_ok, "size") == qc.PASS


# ---------------------------------------------------------------- 3. 时长
@pytest.mark.parametrize("seconds", [0.5, 1.0, 2.5])
def test_duration_from_frames_and_rate(tmp_path, seconds):
    f = write_wav(tmp_path / f"d{seconds}.wav", seconds=seconds)
    report = qc.run_checks(str(f), qc.build_parser().parse_args([str(f)]))
    got = next(c for c in report["checks"] if c["name"] == "duration")
    assert got["duration_sec"] == pytest.approx(seconds, abs=1e-6)
    assert got["sample_rate"] == 44100
    assert got["frames"] == int(44100 * seconds)


def test_duration_within_tolerance_passes(tmp_path):
    """G4-5: 误差 ≤ ±1s 通过。"""
    f = write_wav(tmp_path / "d.wav", seconds=2.0)
    report = qc.run_checks(str(f), qc.build_parser().parse_args(
        [str(f), "--duration-expected", "2.8", "--duration-tol", "1.0"]))
    assert status_of(report, "duration") == qc.PASS


def test_duration_beyond_tolerance_fails(tmp_path):
    f = write_wav(tmp_path / "d.wav", seconds=3.0)
    report = qc.run_checks(str(f), qc.build_parser().parse_args(
        [str(f), "--duration-expected", "1.0", "--duration-tol", "1.0"]))
    assert status_of(report, "duration") == qc.FAIL
    assert report["conclusion"] == qc.FAIL


def test_duration_expected_optional(tmp_path):
    """不给 --duration-expected 时只报数不判定。"""
    f = write_wav(tmp_path / "d.wav", seconds=1.0)
    report = qc.run_checks(str(f), qc.build_parser().parse_args([str(f)]))
    assert status_of(report, "duration") == qc.PASS
    got = next(c for c in report["checks"] if c["name"] == "duration")
    assert "expected_sec" not in got


# ---------------------------------------------------------------- 4. 静音
def test_pure_silence_fails(tmp_path):
    f = write_wav(tmp_path / "silent.wav", seconds=1.0, silent=True)
    report = qc.run_checks(str(f), qc.build_parser().parse_args([str(f)]))
    got = next(c for c in report["checks"] if c["name"] == "silence")
    assert status_of(report, "silence") == qc.FAIL
    assert got["silence_ratio"] == pytest.approx(1.0)
    assert got["longest_silence_sec"] == pytest.approx(1.0, abs=0.06)


def test_tone_is_not_silent(tmp_path):
    f = write_wav(tmp_path / "tone.wav", seconds=1.0)
    report = qc.run_checks(str(f), qc.build_parser().parse_args([str(f)]))
    got = next(c for c in report["checks"] if c["name"] == "silence")
    assert status_of(report, "silence") == qc.PASS
    assert got["silence_ratio"] == 0.0


def test_long_tail_silence_is_anomalous(tmp_path):
    """整轨末尾 1.5s 静音 → 连续静音段 ≥ --min-silence-sec，判 FAIL。"""
    f = write_wav(tmp_path / "tail.wav", seconds=1.0, tail_silence=1.5)
    report = qc.run_checks(str(f), qc.build_parser().parse_args([str(f)]))
    got = next(c for c in report["checks"] if c["name"] == "silence")
    assert status_of(report, "silence") == qc.FAIL
    assert got["longest_silence_sec"] >= 1.0


def test_short_fade_gap_is_whitelisted(tmp_path):
    """短静音间隙（< --min-silence-sec）不判 FAIL —— 对应门里淡入淡出白名单口径。"""
    f = write_wav(tmp_path / "gap.wav", seconds=2.0, tail_silence=0.3)
    report = qc.run_checks(str(f), qc.build_parser().parse_args(
        [str(f), "--min-silence-sec", "1.0", "--silence-max-ratio", "1.0"]))
    got = next(c for c in report["checks"] if c["name"] == "silence")
    assert got["longest_silence_sec"] < 1.0
    assert status_of(report, "silence") == qc.PASS


def test_silence_ratio_limit_alone_can_fail(tmp_path):
    """连续段不够长，但占比超阈值时也要 FAIL（双阈值口径）。"""
    f = write_wav(tmp_path / "many.wav", seconds=1.0, tail_silence=1.4)
    report = qc.run_checks(str(f), qc.build_parser().parse_args(
        [str(f), "--min-silence-sec", "99", "--silence-max-ratio", "0.5"]))
    assert status_of(report, "silence") == qc.FAIL


def test_silence_threshold_is_adjustable(tmp_path):
    """正常但很轻的信号：默认 -60 dBFS 下非静音，调到 -10 dBFS 后算静音。"""
    f = write_wav(tmp_path / "quiet.wav", seconds=1.0, amplitude=0.002)
    quiet_default = qc.run_checks(str(f), qc.build_parser().parse_args([str(f)]))
    assert status_of(quiet_default, "silence") == qc.PASS
    loud_thresh = qc.run_checks(str(f), qc.build_parser().parse_args(
        [str(f), "--silence-dbfs", "-10.0"]))
    assert status_of(loud_thresh, "silence") == qc.FAIL


# ---------------------------------------------------------------- 5. 削波
def test_full_scale_dc_counts_as_clipping_when_asked(tmp_path):
    """满幅码值(±1.0 归一化) 对 0 dBFS 严格阈值不算削波，放宽到 -0.1 才计。"""
    f = write_wav(tmp_path / "clip.wav", seconds=0.5, dc_full_scale=True)
    strict = qc.run_checks(str(f), qc.build_parser().parse_args([str(f)]))
    assert status_of(strict, "clipping") == qc.PASS
    loose = qc.run_checks(str(f), qc.build_parser().parse_args(
        [str(f), "--clipping-dbfs", "-0.1"]))
    got = next(c for c in loose["checks"] if c["name"] == "clipping")
    assert status_of(loose, "clipping") == qc.FAIL
    assert got["clipped_samples"] == got["total_samples"]


def test_soft_clip_at_minus_20dbfs_flagged_with_loose_threshold(tmp_path):
    f = write_wav(tmp_path / "hot.wav", seconds=0.5, amplitude=1.0)
    report = qc.run_checks(str(f), qc.build_parser().parse_args(
        [str(f), "--clipping-dbfs", "-20.0"]))
    got = next(c for c in report["checks"] if c["name"] == "clipping")
    assert status_of(report, "clipping") == qc.FAIL
    assert got["clipped_samples"] > 0


def test_normal_signal_has_no_clipping(tmp_path):
    f = write_wav(tmp_path / "ok.wav", seconds=1.0, amplitude=0.5)
    report = qc.run_checks(str(f), qc.build_parser().parse_args([str(f)]))
    got = next(c for c in report["checks"] if c["name"] == "clipping")
    assert got["clipped_samples"] == 0
    assert got["peak_dbfs"] == pytest.approx(-6.02, abs=0.05)


def test_sample_widths_are_decoded(tmp_path):
    """8/16/32-bit 都要能解（纯 stdlib），位深体现在 spec 里。"""
    for sw, expect_bits in ((2, 16), (4, 32)):
        f = write_wav(tmp_path / f"w{sw}.wav", seconds=0.2, sampwidth=sw)
        report = qc.run_checks(str(f), qc.build_parser().parse_args([str(f)]))
        got = next(c for c in report["checks"] if c["name"] == "spec")
        assert got["bit_depth"] == expect_bits
        assert status_of(report, "spec") == qc.PASS


# ---------------------------------------------------------------- 规格
def test_spec_rate_mismatch_fails(tmp_path):
    f = write_wav(tmp_path / "48k.wav", seconds=0.5, rate=48000)
    report = qc.run_checks(str(f), qc.build_parser().parse_args([str(f)]))
    assert status_of(report, "spec") == qc.FAIL
    report_ok = qc.run_checks(str(f), qc.build_parser().parse_args(
        [str(f), "--expect-rate", "48000"]))
    assert status_of(report_ok, "spec") == qc.PASS


def test_stereo_downmix(tmp_path):
    f = write_wav(tmp_path / "st.wav", seconds=0.5, channels=2)
    report = qc.run_checks(str(f), qc.build_parser().parse_args([str(f)]))
    got = next(c for c in report["checks"] if c["name"] == "spec")
    assert got["channels"] == 2
    assert status_of(report, "clipping") == qc.PASS


# ---------------------------------------------------------------- CLI 契约
def test_cli_exit_code_pass_is_zero(tmp_path):
    f = write_wav(tmp_path / "ok.wav", seconds=1.0)
    r = run_cli(str(f), "--duration-expected", "1.0")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "CONCLUSION: PASS" in r.stdout


def test_cli_exit_code_fail_is_nonzero(tmp_path):
    f = write_wav(tmp_path / "silent.wav", seconds=1.0, silent=True)
    r = run_cli(str(f))
    assert r.returncode == 1
    assert "CONCLUSION: FAIL" in r.stdout
    assert "silence" in r.stdout


def test_cli_usage_error_exit_code_two(tmp_path):
    f = write_wav(tmp_path / "ok.wav", seconds=0.2)
    r = run_cli(str(f), "--silence-max-ratio", "5")
    assert r.returncode == 2


def test_cli_json_is_machine_readable(tmp_path):
    f = write_wav(tmp_path / "ok.wav", seconds=1.0)
    r = run_cli(str(f), "--json")
    payload = json.loads(r.stdout)
    assert r.returncode == 0
    assert payload["conclusion"] == "PASS"
    assert check_names(payload) == {"exists", "size", "duration", "silence",
                                    "clipping", "spec"}


def test_cli_missing_file_json_exit_one(tmp_path):
    r = run_cli(str(tmp_path / "ghost.wav"), "--json")
    assert r.returncode == 1
    payload = json.loads(r.stdout)
    assert payload["failed"] == ["exists"]


def test_no_third_party_imports():
    """硬约束：脚本只许用 stdlib，不得引入 numpy/scipy/pydub/librosa/ffmpeg。"""
    banned = ("numpy", "scipy", "pydub", "librosa", "soundfile",
              "audioread", "soxr", "musdb")
    src = SCRIPT.read_text(encoding="utf-8")
    for name in banned:
        assert f"import {name}" not in src, name
        assert f"from {name}" not in src, name
    assert "subprocess" not in src  # 脚本自身不 shell-out 调外部二进制（不依赖 ffmpeg）
