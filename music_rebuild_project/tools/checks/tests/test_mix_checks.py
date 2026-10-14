"""mix_checks.py 回归测试（#58 混音最小检查）。

策略
  **不依赖任何真实媒体文件**：全部 WAV 在 ``tmp_path`` 里用 stdlib
  ``wave``/``struct``（经 ``render.wavio``）现场合成，确定性正弦/噪声，**无 RNG**。
  断言点严格对齐 issue #58 的五个维度与退出码契约：

    削波 peak ≥ -0.3dBFS → FAIL ／ 보컬比 < 0.6 → FAIL ／ 相关性 < 0.3 → WARN
    静音占比 > 0.5 → FAIL ／ 频率失衡 → WARN（不 FAIL）
    退出码：全 PASS→0，仅 WARN→0，有 FAIL→1，用法/读取错误→2

  另有一条硬约束用测试钉死：**不得引入 numpy/scipy**（沙箱无它们，且
  issue 要求纯 stdlib）。用 ``sys.modules`` 在隔离子进程里断言二者未被 import。
"""

from __future__ import annotations

import json
import math
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve()
_TOOLS_DIR = _HERE.parents[2]              # music_rebuild_project/tools
_REPO_ROOT = _HERE.parents[4]              # 仓库根
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from checks import mix_checks as M              # noqa: E402
from render import wavio as W                   # noqa: E402

SR = 44100


# ---------------------------------------------------------------- 合成工具
def tone(freq=440.0, seconds=1.0, amplitude=0.4, sr=SR, phase=0.0):
    """确定性正弦（无 RNG）。"""
    n = int(round(seconds * sr))
    return [amplitude * math.sin(2.0 * math.pi * freq * (i / sr) + phase) for i in range(n)]


def silence(seconds=1.0, sr=SR):
    return [0.0] * int(round(seconds * sr))


def noise(seconds=1.0, amplitude=0.3, sr=SR, seed=12345):
    """确定性 LCG 伪噪声（无 RNG 模块，跨平台字节一致）。

    频谱上是宽带 —— 用来构造「频率均衡」的正例，以及混音里各频段都有能量
    的场景。LCG 参数固定，不用 ``random``，因此重复执行结果完全一致。
    """
    n = int(round(seconds * sr))
    state = seed & 0x7FFFFFFF
    out = []
    for _ in range(n):
        state = (state * 1103515245 + 12345) & 0x7FFFFFFF
        out.append(amplitude * (state / 0x3FFFFFFF - 1.0))
    return out


def write(path, samples, *, sr=SR, sampwidth=3, channels=None):
    return W.write_wav(str(path), samples, framerate=sr, sampwidth=sampwidth,
                       channels=channels)


def write_stems(dirpath, *, vocal_amp=0.5, others_amp=0.25, sr=SR):
    """写四轨 stem（G4-9 规范名），各 1.0s。"""
    dirpath.mkdir(parents=True, exist_ok=True)
    for name, freq, amp in [
        ("drums", 110.0, others_amp),
        ("bass", 55.0, others_amp),
        ("harmony", 220.0, others_amp),
        ("vocal", 330.0, vocal_amp),
    ]:
        write(dirpath / f"song01_{name}_v001.wav", tone(freq, amplitude=amp), sr=sr)
    return dirpath


def cli(target, *extra):
    """跑 CLI，返回 CompletedProcess。"""
    return subprocess.run(
        [sys.executable, str(_TOOLS_DIR / "checks" / "mix_checks.py"), str(target), *extra],
        capture_output=True, text=True,
    )


# ---------------------------------------------------------------- 削波维度
class TestClipping:
    def test_near_full_scale_sine_fails(self, tmp_path):
        """峰值贴近 0 dBFS 的正弦 → FAIL（疑似 clipping）。"""
        p = tmp_path / "clip.wav"
        write(p, tone(amplitude=0.999))
        r = M.check_clipping(W.read_wav(p)["samples"])
        assert r["status"] == M.FAIL
        assert r["peak_dbfs"] >= M.DEFAULT_PEAK_FAIL_DBFS
        assert r["max_abs_sample"] > 0.99

    def test_moderate_sine_passes(self, tmp_path):
        """正常电平（0.4）→ PASS，且峰值证据在报告里。"""
        p = tmp_path / "ok.wav"
        write(p, tone(amplitude=0.4))
        r = M.check_clipping(W.read_wav(p)["samples"])
        assert r["status"] == M.PASS
        assert r["peak_dbfs"] < M.DEFAULT_PEAK_FAIL_DBFS
        assert 0.39 < r["max_abs_sample"] < 0.41

    def test_threshold_is_configurable(self, tmp_path):
        """阈值可收紧：0.8 幅值正弦峰值 ≈ -1.94 dBFS，在 -3dBFS 门槛下即 FAIL。"""
        p = tmp_path / "loud.wav"
        write(p, tone(amplitude=0.8))
        r = M.check_clipping(W.read_wav(p)["samples"], threshold_dbfs=-3.0)
        assert r["status"] == M.FAIL
        assert r["threshold_dbfs"] == -3.0
        assert r["peak_dbfs"] > -3.0


# ---------------------------------------------------------------- 보컬优先
class TestVocalPriority:
    def test_loud_vocal_passes(self):
        mix = tone(220.0, amplitude=0.5)
        vocal = tone(330.0, amplitude=0.5)     # 比 = 1.0 ≥ 0.6
        r = M.check_vocal_priority(mix, vocal)
        assert r["status"] == M.PASS
        assert r["ratio"] >= M.DEFAULT_VOCAL_RATIO_MIN

    def test_buried_vocal_fails(self):
        mix = tone(220.0, amplitude=1.0)
        vocal = tone(330.0, amplitude=0.2)     # 比 = 0.2 < 0.6
        r = M.check_vocal_priority(mix, vocal)
        assert r["status"] == M.FAIL
        assert r["ratio"] < M.DEFAULT_VOCAL_RATIO_MIN
        assert r["vocal_rms_dbfs"] < r["mix_rms_dbfs"]

    def test_silent_vocal_fails(self):
        r = M.check_vocal_priority(tone(220.0, amplitude=0.5), silence())
        assert r["status"] == M.FAIL
        assert r["ratio"] == 0.0
        assert r["vocal_rms_dbfs"] == float("-inf")


# ---------------------------------------------------------------- 单声道兼容
class TestMonoCompat:
    def test_correlated_stereo_passes(self, tmp_path):
        """L == R（完全相关）→ PASS，相关系数 = 1.0。"""
        p = tmp_path / "corr.wav"
        t = tone(440.0, amplitude=0.4)
        write(p, [(v, v) for v in t], channels=2)
        info = W.read_wav(p)
        r = M.check_mono_compat(info["samples"], info["channels"])
        assert r["status"] == M.PASS
        assert r["correlation"] == pytest.approx(1.0, abs=1e-6)

    def test_antiphase_stereo_warns(self, tmp_path):
        """L = -R（相位相反）→ WARN；相关 = -1，折叠单声道会抵消。"""
        p = tmp_path / "anti.wav"
        t = tone(440.0, amplitude=0.4)
        write(p, [(v, -v) for v in t], channels=2)
        info = W.read_wav(p)
        r = M.check_mono_compat(info["samples"], info["channels"])
        assert r["status"] == M.WARN
        assert r["correlation"] == pytest.approx(-1.0, abs=1e-6)

    def test_mono_source_is_not_applicable(self, tmp_path):
        """单声道素材：相关性 n/a，PASS 而非 WARN/FAIL。"""
        p = tmp_path / "mono.wav"
        write(p, tone(440.0))
        info = W.read_wav(p)
        r = M.check_mono_compat(info["samples"], info["channels"])
        assert r["status"] == M.PASS
        assert r["correlation"] is None

    def test_correlation_math_is_signed(self):
        """相关性本身：同号为正、反号为负、正交≈0。"""
        a = [1.0, 2.0, 3.0, 4.0]
        assert M.correlation(a, a) == pytest.approx(1.0)
        assert M.correlation(a, [-v for v in a]) == pytest.approx(-1.0)
        assert abs(M.correlation(a, [1.0, -1.0, 1.0, -1.0])) < 0.5


# ---------------------------------------------------------------- 静音占比
class TestSilence:
    def test_normal_signal_passes(self, tmp_path):
        p = tmp_path / "ok.wav"
        write(p, tone(amplitude=0.4))
        r = M.check_silence(W.read_wav(p)["samples"], SR)
        assert r["status"] == M.PASS
        assert r["silence_ratio"] == 0.0

    def test_silent_track_fails(self, tmp_path):
        p = tmp_path / "silent.wav"
        write(p, silence())
        r = M.check_silence(W.read_wav(p)["samples"], SR)
        assert r["status"] == M.FAIL
        assert r["silence_ratio"] == 1.0
        assert r["silent_frames"] == r["total_frames"]

    def test_half_silent_is_at_boundary(self, tmp_path):
        """一半静音：占比 = 0.5 恰在上限（≤ 判 PASS）。"""
        p = tmp_path / "half.wav"
        write(p, tone(seconds=0.5, amplitude=0.4) + silence(seconds=0.5))
        r = M.check_silence(W.read_wav(p)["samples"], SR)
        assert r["status"] == M.PASS
        assert r["silence_ratio"] == pytest.approx(0.5, abs=1e-6)


# ---------------------------------------------------------------- 频率平衡
class TestSpectrum:
    def test_low_heavy_warns(self):
        """纯低频正弦 → 低频占比压倒性，判 WARN（不 FAIL）。"""
        r = M.check_spectrum(tone(60.0, amplitude=0.4), SR)
        assert r["status"] == M.WARN
        assert r["band_ratios"]["low"] > M.DEFAULT_SPECTRUM_TOL

    def test_broadband_noise_passes(self):
        """宽带噪声 → 各频段都有能量，不过度集中，PASS。"""
        r = M.check_spectrum(noise(amplitude=0.3), SR)
        assert r["status"] == M.PASS
        assert r["max_band_ratio"] <= M.DEFAULT_SPECTRUM_TOL

    def test_ratios_sum_to_one(self):
        ratios = M.band_ratios(M.band_energies(tone(440.0), SR))
        assert sum(ratios.values()) == pytest.approx(1.0, abs=1e-9)

    def test_empty_band_ratios_are_zero(self):
        """全静音：能量为 0 → 占比全 0，不抛 ZeroDivisionError。"""
        assert M.band_ratios(M.band_energies(silence(), SR)) == {
            "low": 0.0, "mid": 0.0, "high": 0.0,
        }


# ---------------------------------------------------------------- RMS / dBFS
class TestScalars:
    def test_rms_of_tone(self):
        # 0.4 幅值正弦的 RMS ≈ 0.4/√2
        assert M.rms(tone(amplitude=0.4)) == pytest.approx(0.4 / math.sqrt(2), abs=1e-3)

    def test_rms_empty_is_zero(self):
        assert M.rms([]) == 0.0

    def test_dbfs_full_scale_is_zero(self):
        assert M.dbfs(1.0) == pytest.approx(0.0)
        assert M.dbfs(0.5) == pytest.approx(-6.0206, abs=1e-3)

    def test_dbfs_zero_is_negative_inf(self):
        assert M.dbfs(0.0) == float("-inf")


# ---------------------------------------------------------------- 端到端 & 退出码
class TestEndToEnd:
    def test_four_stems_run_reports_all_five_dimensions(self, tmp_path):
        d = write_stems(tmp_path / "stems")
        report = M.run(str(d), M.build_parser().parse_args([str(d)]))
        names = [c["name"] for c in report["checks"]]
        assert names == ["clipping", "vocal", "mono", "silence", "spectrum"]
        assert set(report["stems"]) == {"drums", "bass", "harmony", "vocal"}
        assert report["conclusion"] in (M.PASS, M.WARN, M.FAIL)

    def test_healthy_mix_has_no_fail(self, tmp_path):
        """各频段均衡的四轨（宽带噪声）→ 无 FAIL。"""
        d = tmp_path / "stems"
        d.mkdir()
        # 各轨用**互不相同**的种子，避免伪噪声同相叠加（那会把 mix RMS 抬高、
        # 压低 보컬比，是夹具陷阱而非被测缺陷）。
        for name, amp, seed in [
            ("drums", 0.20, 11), ("bass", 0.20, 22),
            ("harmony", 0.20, 33), ("vocal", 0.35, 44),
        ]:
            write(d / f"s{name}.wav", noise(amplitude=amp, seed=seed))
        report = M.run(str(d), M.build_parser().parse_args([str(d)]))
        assert report["failed"] == []

    def test_master_single_file(self, tmp_path):
        """单条 master WAV 也能跑（无 vocal 轨 → vocal 检查 n/a PASS）。"""
        p = tmp_path / "master.wav"
        write(p, tone(440.0, amplitude=0.4))
        report = M.run(str(p), M.build_parser().parse_args([str(p)]))
        vocal = next(c for c in report["checks"] if c["name"] == "vocal")
        assert vocal["ratio"] is None
        assert vocal["status"] == M.PASS

    def test_missing_path_is_read_error(self, tmp_path):
        with pytest.raises(Exception):
            M.run(str(tmp_path / "nope.wav"), M.build_parser().parse_args(["x"]))


class TestExitCodes:
    def test_all_pass_is_zero(self, tmp_path):
        """构造无 FAIL 场景（宽带噪声 + 高 vocal）→ 退出码 0。"""
        d = tmp_path / "stems"
        d.mkdir()
        for name, seed in [("drums", 101), ("bass", 202), ("harmony", 303)]:
            write(d / f"s{name}.wav", noise(amplitude=0.2, seed=seed))
        write(d / "svocal.wav", noise(amplitude=0.3, seed=404))
        r = cli(d)
        assert r.returncode == 0, r.stdout + r.stderr

    def test_fail_is_one(self, tmp_path):
        """峰值削波 → 退出码 1。"""
        p = tmp_path / "clip.wav"
        write(p, tone(amplitude=0.999))
        r = cli(p)
        assert r.returncode == 1
        assert "CONCLUSION: FAIL" in r.stdout

    def test_warn_only_is_zero_but_marked(self, tmp_path):
        """低频过重的素材：仅 spectrum WARN → 退出码 0，但报告标 WARN。"""
        p = tmp_path / "low.wav"
        write(p, tone(60.0, amplitude=0.4))
        r = cli(p)
        assert r.returncode == 0, r.stdout
        assert "WARN" in r.stdout

    def test_usage_error_is_two(self, tmp_path):
        """非法参数（占比 > 1）→ 退出码 2。"""
        r = cli(tmp_path, "--silence-max-ratio", "2")
        assert r.returncode == 2
        assert "error" in r.stderr

    def test_missing_file_is_two(self, tmp_path):
        r = cli(tmp_path / "nope.wav")
        assert r.returncode == 2
        assert "error" in r.stderr

    def test_json_report_is_machine_readable(self, tmp_path):
        d = write_stems(tmp_path / "stems")
        r = cli(d, "--json")
        payload = json.loads(r.stdout)
        assert payload["conclusion"] in (M.PASS, M.WARN, M.FAIL)
        assert {"failed", "warned", "checks"} <= set(payload)


# ---------------------------------------------------------------- 无新依赖
def test_no_numpy_scipy_imported(tmp_path):
    """纯 stdlib 契约：导入/运行 mix_checks 后 numpy/scipy 不得在 sys.modules 里。

    在**隔离子进程**里断言，避免被其它测试预导入污染。
    """
    probe = textwrap.dedent(
        f"""
        import sys
        sys.path.insert(0, {str(_TOOLS_DIR)!r})
        sys.path.insert(0, {str(_REPO_ROOT)!r})
        import checks.mix_checks as M
        from render import wavio as W
        import math
        # 真跑一遍五个检查，确保不通过惰性 import 拉进三方库
        sr = 44100
        tone = [0.4 * math.sin(2 * math.pi * 440 * i / sr) for i in range(sr)]
        M.check_clipping(tone)
        M.check_vocal_priority(tone, tone)
        M.check_mono_compat(tone, 1)
        M.check_silence(tone, sr)
        M.check_spectrum(tone, sr)
        assert "numpy" not in sys.modules, "numpy was imported!"
        assert "scipy" not in sys.modules, "scipy was imported!"
        print("CLEAN")
        """
    )
    r = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "CLEAN" in r.stdout
