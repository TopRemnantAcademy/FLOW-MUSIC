"""每阶段产物的契约断言：MusicXML 合法 / WAV 头合法 / 非静音 / 时长 = 预期 / stem 一致。

与 test_e2e_full_pipeline 的分工：那个证明「能跑通」，这个证明「产物本身是合格的」
—— 一份「跑通但时长不对」的报告比红灯更危险。
"""

import xml.etree.ElementTree as ET

from . import fixtures as fx
from .mocks import (
    CHANNELS,
    SAMPLE_RATE,
    SAMPLE_WIDTH,
    read_wav,
    seconds_for,
    validate_arrangement,
)
from .orchestrator import run_score_to_music


def _run(tmp_path):
    return run_score_to_music(
        fx.synth_score_png(),
        fx.SAMPLE_LYRIC,
        {"song_id": fx.SONG_ID, "bpm": fx.BPM, "measures": fx.MEASURES},
        work_dir=str(tmp_path),
    )


def test_stage_contracts(tmp_path):
    result = _run(tmp_path)
    assert result.ok

    # ---- A：源图是可解析的 PNG，且尺寸达标（G1-1 的输入侧）----
    with open(result.artifacts["source_png"], "rb") as f:
        png = f.read()
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert int.from_bytes(png[16:20], "big") >= 2400  # 短边
    assert result.reports["preprocess_log"]["source_sha256"]  # G1-9 的哈希记录

    # ---- B/C：ground_truth 是合法 XML，且确实被冻结（哈希可复算）----
    gt = result.artifacts["ground_truth_musicxml"]
    root = ET.parse(gt).getroot()
    assert root.tag == "score-partwise"
    assert len(root.findall(".//measure")) == fx.MEASURES
    from .mocks import sha256_file

    assert sha256_file(gt) == result.reports["ground_truth"]["sha256"]
    # 冻结记录文件按 gate_G2.md 的格式落位：<sha256>  <file>  <utc_time>  <approver>
    frozen = result.artifacts["ground_truth_musicxml"].replace(
        "ground_truth.musicxml", "ground_truth.musicxml.sha256"
    )
    with open(frozen, encoding="utf-8") as f:
        fields = f.read().split()
    assert fields[0] == result.reports["ground_truth"]["sha256"]
    assert fields[1] == "ground_truth.musicxml"
    assert fields[2].endswith("Z") and fields[3]

    # ---- D：보컬 WAV 三件套（头 / 采样率 / 非静音 / 时长=小节×拍×BPM）----
    vocal = read_wav(result.artifacts["approved_vocal_wav"])
    assert (vocal["channels"], vocal["sampwidth"], vocal["framerate"]) == (
        CHANNELS,
        SAMPLE_WIDTH,
        SAMPLE_RATE,
    )
    assert vocal["rms"] > 0, "보컬 WAV 是静音"
    assert abs(vocal["duration"] - fx.BASELINE_SECONDS) < 0.01

    # ---- E：编曲结构合法，且段落铺满全曲 ----
    arr = result.reports["arrangement"]
    assert validate_arrangement(arr) == []
    assert arr["sections"][0]["start_bar"] == 1
    assert arr["sections"][-1]["end_bar"] == arr["measures"]
    assert arr["bpm"] == fx.BPM and arr["time_signature"] == f"{fx.BEATS}/{fx.BEAT_TYPE}"

    # ---- F：master + 4 stem，时长彼此一致，且等于「8 小节基准」口径 ----
    master = read_wav(result.artifacts["master_wav"])
    assert master["clipped_samples"] == 0
    stems = {k: read_wav(v) for k, v in result.artifacts.items() if k.startswith("stem_")}
    assert set(stems) == {"stem_drums", "stem_bass", "stem_harmony", "stem_vocal"}
    durations = {k: v["duration"] for k, v in stems.items()}
    durations["master"] = master["duration"]
    assert max(durations.values()) - min(durations.values()) < 0.01, durations
    assert all(abs(d - fx.BASELINE_SECONDS) < 0.01 for d in durations.values()), durations
    assert all(v["rms"] > 0 for v in stems.values()), "有 stem 是静音"

    # 时长口径 = 小节 × 拍数 × 60/BPM（gate_G3-7 / gate_G4-5 的唯一定义）
    assert seconds_for(fx.BASELINE_MEASURES, fx.BEATS, fx.BPM) == fx.BASELINE_SECONDS


def test_qc_file_check_covers_every_track(tmp_path):
    """#60 的文件验证清单必须覆盖 master + 全部 stem + 보컬（漏一轨就是漏一个缺陷面）。"""
    result = _run(tmp_path)
    qc = result.reports["qc_file_check"]
    assert qc["master"]["exists"] and qc["master"]["bytes"] > 0
    assert set(qc["stems"]) == {"drums", "bass", "harmony", "vocal"}
    assert qc["vocal"]["clipped_samples"] == 0
    assert qc["expected_seconds"] == fx.BASELINE_SECONDS
    for probe in [qc["master"], qc["vocal"], *qc["stems"].values()]:
        assert probe["silent_blocks"] == 0
        assert probe["duration"] > 0
