"""门必须真的会拦：喂坏输入 → 对应门 FAIL + 流水线 halt（fail-closed）。

这是整套 mock 里**最重要**的一组用例。一个「永远绿灯」的门和没有门是一回事，
而且更糟 —— 它给的是虚假的通过感。所以这里逐门构造失败输入，
断言「门红了、红在哪一项、流水线没有继续往下走」。
"""

import array
import os

import pytest

from . import fixtures as fx
from .mocks import (
    GATE_FAIL,
    GateG4,
    MockArranger,
    MockMuseScore,
    MockOMR,
    MockPreprocessor,
    MockVocalEngine,
    read_wav,
    write_wav,
)
from .orchestrator import run_score_to_music
from .platform import MOCK_AUDIO_URL


def _run(tmp_path, **kw):
    return run_score_to_music(
        kw.pop("image", fx.synth_score_png()),
        fx.SAMPLE_LYRIC,
        {"song_id": fx.SONG_ID},
        work_dir=str(tmp_path),
        **kw,
    )


# ---------------------------------------------------------------------------
# G1
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "preset, expected_item",
    [
        ({"dpi": 150, "staff_line_spacing_px": 4}, "G1-1"),  # 分辨率/线距不够
        ({"skew_deg": 2.5}, "G1-2"),  # 歪斜 ≥2° 一律 FAIL
        ({"cropped": True}, "G1-3"),  # 首末小节被切
        ({"missing_pages": 1}, "G1-4"),  # 缺页
        ({"unreadable_lines": 2}, "G1-5"),  # 词行不可辨
        ({"symbol_confusions": 1}, "G1-6"),  # 符号不可区分
        ({"systems_per_page": 8}, "G1-7"),  # 单页系统数超限
        ({"classify": "handwritten"}, "G1-8"),  # 手写页不得进自动 OMR
    ],
)
def test_g1_fail_items_halt_pipeline(tmp_path, preset, expected_item):
    pre = MockPreprocessor(**preset).preprocess(fx.synth_score_png(), song_id=fx.SONG_ID)
    result = _run(tmp_path, preprocess=pre)

    assert result.ok is False
    assert result.halted_at == "G1"
    report = result.gate_reports["G1"]
    assert report.status == GATE_FAIL
    assert expected_item in report.failed_ids()
    # 关键：没有继续往下走（B 段产物不该存在）
    assert "ground_truth_musicxml" not in result.artifacts


def test_g1_note_item_degrades_instead_of_failing(tmp_path):
    """G1-1 的可调档：200dpi + 线距 6px → PASS_WITH_NOTES（不是 FAIL，也不是静默 PASS）。"""
    pre = MockPreprocessor(dpi=240, staff_line_spacing_px=7).preprocess(
        fx.synth_score_png(), song_id=fx.SONG_ID
    )
    result = _run(tmp_path, preprocess=pre)
    assert result.gate_reports["G1"].status == "PASS_WITH_NOTES"
    assert result.gate_reports["G1"].item("G1-1").status == "PASS_WITH_NOTES"
    assert result.ok is True, "降级通过不应阻断流水线"


# ---------------------------------------------------------------------------
# G2
# ---------------------------------------------------------------------------
def test_broken_musicxml_fails_g2_and_halts(tmp_path):
    """损坏 MusicXML（XML 都闭合不了 + 音长 5/4）→ G2 FAIL，D/E/F 一律不跑。

    审校步骤（MockMuseScore.proof）会先在 `ET.fromstring` 上抛解析错误 ——
    这正是真实世界 MuseScore 打开损坏文件的表现。编排器把它转成 G2 的
    「未解错误 ≠ 0」，而不是让异常冒到调用方（调用方拿到的应该是**报告**）。
    """
    result = _run(tmp_path, omr_candidates=[MockOMR("broken", broken=True)])
    assert result.ok is False
    assert result.halted_at == "G2"
    report = result.gate_reports["G2"]
    assert report.status == GATE_FAIL
    # 音长合计=5（拍号 4）→ 节奏项必红
    assert "G2-3" in report.failed_ids()
    for key in ("approved_vocal_wav", "master_wav", "stem_vocal"):
        assert key not in result.artifacts, f"{key} 不该在 G2 失败后存在"


def test_wrong_pitch_fails_g2(tmp_path):
    """审校前注入音高错误、且**跳过校正**（proofer 不修谱 = 审校没做完）→ G2-4 红。"""

    class NoFixProofer(MockMuseScore):
        def proof(self, musicxml, *, song_id):
            return musicxml  # 只做往返导出，不修错

    # 用一个"校正不生效"的路径：让真值变成另一份，使候选与原谱不一致
    result = _run(tmp_path, proofer=NoFixProofer())
    assert result.gate_reports["G2"].status == "PASS", "先确认无错候选本来是绿的"

    # 现在把"原谱真值"换掉（模拟候选与真值不符）—— 用注入错误的候选 + 不允许校正
    from . import orchestrator as orch

    truth = orch._truth_of(fx.CANON_MUSICXML)
    truth = dict(truth, pitches=[("C", 4)] * len(truth["pitches"]))
    musicxml = MockOMR("audiveris", pitch_errors=1).recognize(b"", song_id=fx.SONG_ID)
    g2 = orch.GateG2().check(musicxml=musicxml, truth=truth, roundtrip_ok=True)
    assert g2[1].status == GATE_FAIL
    assert "G2-4" in g2[1].failed_ids()


def test_roundtrip_break_fails_g2(tmp_path):
    """往返不一致（歌词丢失）→ G2-6 红，G2 FAIL。"""
    from .orchestrator import GateG2

    truth = {
        "measures": fx.MEASURES,
        "times": (fx.BEATS, fx.BEAT_TYPE),
        "title": fx.TITLE,
        "pitches": [],
        "lyrics": ["찬양하라"],
    }
    status, report = GateG2().check(musicxml=fx.CANON_MUSICXML, truth=truth, roundtrip_ok=False)
    assert status == GATE_FAIL
    assert "G2-6" in report.failed_ids()


# ---------------------------------------------------------------------------
# G3
# ---------------------------------------------------------------------------
def test_silent_vocal_fails_g3_and_halts(tmp_path):
    """静音 보컬（合法 WAV 头但全零）→ G3-1 红，流水线在 D 段后停住。"""
    engines = {
        "ace": MockVocalEngine("ace", silent=True),
        "synthv": MockVocalEngine("synthv"),
    }
    result = _run(tmp_path, vocal_engines=engines)
    assert result.ok is False
    assert result.halted_at == "G3"
    report = result.gate_reports["G3"]
    assert report.status == GATE_FAIL
    assert "G3-1" in report.failed_ids()
    assert "master_wav" not in result.artifacts, "静音声乐不该被拿去混音"


def test_low_pronunciation_engine_is_not_selected(tmp_path):
    """G3-5：被采用的引擎明度 ≤3 → 门红（「不唯总分」不是口号，是这条断言）。"""
    from .mocks import GateG3

    engines = {"ace": MockVocalEngine("ace"), "synthv": MockVocalEngine("synthv")}
    scores, winner, reason = GateG3.score_ab(engines)
    # 正常路径：ACE 明度 4 胜出，SynthV 明度 3 出局（总分更高也不采用 —— 不唯总分）
    assert winner == "ace"
    assert max(scores["synthv"].values()) >= 3 and scores["synthv"]["韩语发音明度"] == 3

    # 门槛本身：把被采用引擎的明度压到 3 → FAIL
    r = MockVocalEngine("ace").render(
        fx.CANON_MUSICXML, fx.SAMPLE_LYRIC, {"measures": 8}, out_path=os.path.join(tmp_path, "a.wav")
    )
    status, report = GateG3().check(
        renders={"ace": r},
        expected_seconds=fx.BASELINE_SECONDS,
        syllable_count=16,
        mapping_errors=0,
        onset_deviation_ms=0.0,
        pronunciation_scores={"ace": 3},
        selected_engine="ace",
        selection_reason="测试",
        settings_ok=True,
        frozen_sha256="deadbeef",
    )
    assert status == GATE_FAIL
    assert "G3-5" in report.failed_ids()


def test_vocal_duration_mismatch_fails_g3(tmp_path):
    """보컬 时长不等于「小节 × 拍 × BPM」→ G3-1 红（速度/调性一致是硬项）。"""
    from .mocks import GateG3, write_wav as _w

    path = os.path.join(tmp_path, "short.wav")
    _w(path, [1000] * 4410)  # 0.1s，远短于 20s
    render = MockVocalEngine("ace").render(
        fx.CANON_MUSICXML, fx.SAMPLE_LYRIC, {"measures": 8}, out_path=os.path.join(tmp_path, "ok.wav")
    )
    render.wav_path = path  # 换成一个时长不对劲的文件
    status, report = GateG3().check(
        renders={"ace": render},
        expected_seconds=fx.BASELINE_SECONDS,
        syllable_count=16,
        mapping_errors=0,
        onset_deviation_ms=0.0,
        pronunciation_scores={"ace": 4},
        selected_engine="ace",
        selection_reason="测试",
        settings_ok=True,
        frozen_sha256="deadbeef",
    )
    assert status == GATE_FAIL
    assert "G3-1" in report.failed_ids()


# ---------------------------------------------------------------------------
# G4
# ---------------------------------------------------------------------------
def _mk_vocal(tmp_path, *, silent=False):
    path = os.path.join(tmp_path, "vocal.wav")
    engine = MockVocalEngine("ace", silent=silent)
    return engine.render(fx.CANON_MUSICXML, fx.SAMPLE_LYRIC, {"measures": 8}, out_path=path)


def test_clipped_master_fails_g4(tmp_path):
    """master 削波（满量程采样）→ G4-3 红 —— 这是「不得以整轨降增益掩盖」的锚点。"""
    from .mocks import MockDAW

    vocal = _mk_vocal(tmp_path)
    mix_dir = os.path.join(tmp_path, "mix")
    mix = MockDAW().mix({"sections": []}, {"approved_vocal_wav": vocal.wav_path}, out_dir=mix_dir)
    # 手工把 master 顶到满量程
    write_wav(mix["master"], [32767] * 4410 + [0] * 100)
    # 交付物清单**故意留空**：G4-12 要能报出"缺了哪些"，而不是把空表当齐全
    status, report = GateG4().check(
        master_path=mix["master"],
        stems=mix["stems"],
        vocal_path=vocal.wav_path,
        expected_seconds=fx.BASELINE_SECONDS,
        arrangement={"sections": [{"name": "Intro"}]},
        listen_checklist={"Intro": True},
        deliverables={
            "source_image": "",
            "ground_truth_musicxml": "",
            "approved_vocal_wav": "",
            "arrangement_json": "",
            "master_wav": "",
        },
    )
    assert status == GATE_FAIL
    assert "G4-3" in report.failed_ids()
    assert "G4-5" in report.failed_ids()  # 时长也被改了 → 时长项一起红
    assert "G4-12" in report.failed_ids()  # 交付物路径全空 → 齐全性必须报缺


def test_silence_gap_fails_g4(tmp_path):
    """整轨 0.5s 以上的异常静音 → G4-4 红。"""
    from .mocks import MockDAW, write_wav as _w

    vocal = _mk_vocal(tmp_path)
    mix_dir = os.path.join(tmp_path, "mix")
    mix = MockDAW().mix({"sections": []}, {"approved_vocal_wav": vocal.wav_path}, out_dir=mix_dir)
    # 中间插 1 秒静音（合法 WAV，但违反「有效乐段内异常静音 = 0」）
    samples = read_wav(vocal.wav_path)["samples"]
    mid = len(samples) // 2
    patched = array.array("h", samples[:mid]) + array.array("h", bytes(2 * 44100)) + array.array("h", samples[mid:])
    _w(mix["master"], patched)
    status, report = GateG4().check(
        master_path=mix["master"],
        stems=mix["stems"],
        vocal_path=vocal.wav_path,
        expected_seconds=fx.BASELINE_SECONDS,
        arrangement={"sections": [{"name": "Intro"}]},
        listen_checklist={"Intro": True},
        deliverables={"master_wav": mix["master"]},
    )
    assert status == GATE_FAIL
    assert "G4-4" in report.failed_ids()


def test_stem_set_incomplete_fails_g4(tmp_path):
    """少一条 stem（例如忘导 harmony）→ G4-9 红。"""
    from .mocks import MockDAW

    vocal = _mk_vocal(tmp_path)
    mix_dir = os.path.join(tmp_path, "mix")
    mix = MockDAW().mix({"sections": []}, {"approved_vocal_wav": vocal.wav_path}, out_dir=mix_dir)
    stems = dict(mix["stems"])
    stems.pop("harmony")
    status, report = GateG4().check(
        master_path=mix["master"],
        stems=stems,
        vocal_path=vocal.wav_path,
        expected_seconds=fx.BASELINE_SECONDS,
        arrangement={"sections": [{"name": "Intro"}]},
        listen_checklist={"Intro": True},
        deliverables={"master_wav": mix["master"]},
    )
    assert status == GATE_FAIL
    assert "G4-9" in report.failed_ids()


def test_unchecked_listen_item_blocks_g4(tmp_path):
    """未听完就判 PASS？G4-11 明确禁止（对应 #14「仅轨道存在即批准」）。"""
    from .mocks import MockDAW

    vocal = _mk_vocal(tmp_path)
    mix_dir = os.path.join(tmp_path, "mix")
    mix = MockDAW().mix({"sections": []}, {"approved_vocal_wav": vocal.wav_path}, out_dir=mix_dir)
    status, report = GateG4().check(
        master_path=mix["master"],
        stems=mix["stems"],
        vocal_path=vocal.wav_path,
        expected_seconds=fx.BASELINE_SECONDS,
        arrangement={"sections": [{"name": "Intro"}, {"name": "Chorus"}]},
        listen_checklist={"Intro": True, "Chorus": False},
        deliverables={"master_wav": mix["master"]},
    )
    assert status == GATE_FAIL
    assert "G4-11" in report.failed_ids()


# ---------------------------------------------------------------------------
# 编排层的 halt 语义
# ---------------------------------------------------------------------------
def test_halt_keeps_earlier_artifacts_and_reports_gates(tmp_path):
    """halt 不等于"什么都没留下"：已过门的产物与报告必须保留（可审计、可续跑）。"""
    pre = MockPreprocessor(skew_deg=3.0).preprocess(fx.synth_score_png(), song_id=fx.SONG_ID)
    result = _run(tmp_path, preprocess=pre)

    assert result.ok is False and result.halted_at == "G1"
    assert "G1" in result.gate_reports
    assert "G2" not in result.gate_reports  # 没到 G2 就不该有 G2 的结论（不编造）
    assert result.artifacts["source_png"]  # A 段产物还在
    assert result.stage_reports["A"]["status"]

    # 平台侧：没有 register 调用 → 没有 Song
    assert result.registered_song_id is None
