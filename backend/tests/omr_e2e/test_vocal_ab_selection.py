"""보컬 A/B 选引擎（mirror #43/#44/#45）+ G3 冻结。

要点：
  · 两个引擎（ACE ↔ SynthV）都产出可用的 8 小节 pilot（#43/#44 的要求）；
  · 7 项评测表全部打分，**不唯总分**（#45）：SynthV 总分不低，但「韩语发音明度」
    只有 3 → 按 gate_G3-5 一律不采用；
  · 选定结果 + 理由 + 参数 + 哈希都要落下来（G3-6/G3-8/G3-10）。
"""

import json
import os

from . import fixtures as fx
from .mocks import GateG3, MockVocalEngine, read_wav, sha256_file, write_wav
from .orchestrator import _fingerprint, _settings_complete, run_score_to_music


def _run(tmp_path):
    return run_score_to_music(
        fx.synth_score_png(),
        fx.SAMPLE_LYRIC,
        {"song_id": fx.SONG_ID},
        work_dir=str(tmp_path),
    )


def test_vocal_ab_selection(tmp_path):
    """ACE 胜出，理由是 G3-5 那条硬门槛（不是"总分更高"）。"""
    result = _run(tmp_path)
    ab = result.reports["vocal_ab"]

    assert set(ab["renders"]) == {"ace", "synthv"}
    assert ab["selected_engine"] == "ace"
    assert set(ab["scores"]) == {"ace", "synthv"}

    # 7 项必须全打（gate_G3-6「评测 7 项全部打分完成」）
    for engine, scores in ab["scores"].items():
        assert set(scores) == set(GateG3.AB_DIMENSIONS), f"{engine} 评测项不全"

    # 理由里必须点出「不唯总分」这件事，否则选型无法复核
    assert "韩语发音明度" in ab["reason"]
    assert "不唯总分" in ab["reason"]
    assert ab["scores"]["synthv"]["韩语发音明度"] <= 3 < ab["scores"]["ace"]["韩语发音明度"]


def test_both_engines_render_usable_pilots(tmp_path):
    """#43/#44：两个引擎都要真的产出 8 小节 pilot（存在 / 非静音 / 长度对）。"""
    result = _run(tmp_path)
    for name, render in result.reports["vocal_ab"]["renders"].items():
        assert os.path.exists(render["wav_path"]), f"{name} 没产出 WAV"
        info = read_wav(render["wav_path"])
        assert info["rms"] > 0, f"{name} 是静音"
        assert abs(info["duration"] - fx.BASELINE_SECONDS) < 0.01, f"{name} 时长不是 8 小节"
        assert render["mode"] in ("cloud", "offline", "high_perf")


def test_vocal_settings_are_reproducible(tmp_path):
    """G3-8：引擎+版本 / 声库+版本 / 语言 / 主要参数 / 渲染模式 全在，且落盘。"""
    result = _run(tmp_path)
    settings = result.reports["vocal_settings"]
    assert _settings_complete(settings)
    assert settings["engine"] == "ace"
    assert settings["language"] == "ko"
    assert settings["voicebank_version"]
    assert settings["breathiness"] is not None and settings["phoneme_mode"]

    path = os.path.join(
        os.path.dirname(os.path.dirname(result.artifacts["approved_vocal_wav"])), "vocal_settings.json"
    )
    assert os.path.exists(path), f"vocal_settings.json 没落盘：{path}"
    with open(path, encoding="utf-8") as f:
        on_disk = json.load(f)
    assert on_disk["language"] == "ko"


def test_frozen_vocal_matches_settings_fingerprint(tmp_path):
    """参数可复现 = 同参数再渲一遍，PCM 指纹一致（不是"看着像"）。"""
    settings = {"measures": 8, "bpm": fx.BPM, "beats": fx.BEATS, "seed": 20261013}
    a = MockVocalEngine("ace").render(
        fx.CANON_MUSICXML, fx.SAMPLE_LYRIC, settings, out_path=str(tmp_path / "a.wav")
    )
    b = MockVocalEngine("ace").render(
        fx.CANON_MUSICXML, fx.SAMPLE_LYRIC, settings, out_path=str(tmp_path / "b.wav")
    )
    assert _fingerprint(a.wav_path) == _fingerprint(b.wav_path)

    # 换引擎 → 指纹必须不同（否则「A/B 对比」根本没在比不同东西）
    c = MockVocalEngine("synthv").render(
        fx.CANON_MUSICXML, fx.SAMPLE_LYRIC, settings, out_path=str(tmp_path / "c.wav")
    )
    assert _fingerprint(c.wav_path) != _fingerprint(a.wav_path)


def test_approved_vocal_is_frozen_with_hash(tmp_path):
    """G3-10：approved_vocal_pilot.wav 固定 + SHA-256 记录。"""
    result = _run(tmp_path)
    approved = result.artifacts["approved_vocal_wav"]
    assert approved.endswith("approved_vocal_pilot.wav")
    # 哈希记录与 WAV 同目录（<wav>.sha256），与 `sha256sum` 的落盘习惯一致
    recorded = approved + ".sha256"
    assert os.path.exists(recorded), f"缺少冻结哈希记录：{recorded}"
    with open(recorded, encoding="utf-8") as f:
        fields = f.read().split()
    assert fields[0] == sha256_file(approved)
    assert fields[1] == "approved_vocal_pilot.wav"


def test_mapping_errors_are_counted(tmp_path):
    """G3-3：音节-音符映射是可计数的（本 fixture 为 1 音符 ↔ 1 音节）。"""
    from .orchestrator import _lyric_mapping_errors, _syllables

    assert _lyric_mapping_errors(fx.CANON_MUSICXML, fx.SAMPLE_LYRIC) == 0
    assert len(_syllables(fx.CANON_MUSICXML)) == len(fx.SAMPLE_LYRIC.split()) == 16

    # 少一个音节 → 立刻计为错（宁严勿松）
    short = fx.SAMPLE_LYRIC.split()[:-1]
    assert _lyric_mapping_errors(fx.CANON_MUSICXML, " ".join(short)) >= 1
