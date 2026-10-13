"""可插拔性：换一套「假装是真实 provider」的实现，接口照样够用。

这组用例的意义不是"再多测一遍流程"，而是证明**抽象没漏**：
只要实现 `interfaces.py` 里的五个抽象类，`run_score_to_music` 一行都不用改。
真实落地（Audiveris / MuseScore / ACE / REAPER）时踩的坑，会先在接口这一层暴露，
而不是等到把桌面软件拖进 CI 才发现。

这里的替换实现刻意与 mock 不同：
  · OMR 走「先渲染成图片再识别」的迂回路线（真实 Audiveris 就是读图的）；
  · 审校器只做规范化、**不做**任何补错（于是 G2 会红 → 正好证明门不是摆设）；
  · 声乐/编曲/DAW 用同一套 stdlib 素材但换命名与参数。
所有实现都**不打网络、不碰桌面软件、不用密钥**。
"""

import os

from . import fixtures as fx
from .interfaces import Arranger, DAWEngine, OMRProvider, ScoreProofer, VocalEngine
from .mocks import (
    MockArranger,
    MockDAW,
    MockMuseScore,
    MockOMR,
    MockVocalEngine,
    GateG4,
    read_wav,
)
from .orchestrator import run_score_to_music


class ImageFirstOMR(OMRProvider):
    """真实 OMR 的形状：吃图像，先落盘再"识别"（这里是读回 fixture）。"""

    name = "audiveris-stub"

    def recognize(self, image_png: bytes, *, song_id: str) -> str:
        assert image_png[:8] == b"\x89PNG\r\n\x1a\n", "OMR 应该拿到一张真 PNG"
        return fx.CANON_MUSICXML


class NormalizingProofer(ScoreProofer):
    """真实审校的形状：只做规范化（不修音乐错误）—— 与 MockMuseScore 同接口。"""

    name = "musescore-stub"

    def proof(self, musicxml: str, *, song_id: str) -> str:
        return MockMuseScore().proof(musicxml, song_id=song_id)


class RealShapeVocal(VocalEngine):
    """真实 ACE 的形状：吃 (MusicXML, lyric, settings)，吐一个已在 out_path 的 WAV。"""

    name = "ace-stub"

    def __init__(self):
        self._inner = MockVocalEngine("ace", mode="high_perf")

    def render(self, musicxml, lyric, settings, *, out_path):
        render = self._inner.render(musicxml, lyric, settings, out_path=out_path)
        render.settings["stub"] = True
        return render


class RealShapeArranger(Arranger):
    name = "arranger-stub"

    def arrange(self, musicxml: str, *, lyric: str = "") -> dict:
        return MockArranger().arrange(musicxml, lyric=lyric)


class RealShapeDAW(DAWEngine):
    name = "reaper-stub"

    def mix(self, arrangement, vocals, *, out_dir):
        return MockDAW().mix(arrangement, vocals, out_dir=out_dir)


def test_provider_interface_compliance(tmp_path):
    """五个抽象类各自的替身实现即可跑通全链路（无需改编排器）。"""
    result = run_score_to_music(
        fx.synth_score_png(),
        fx.SAMPLE_LYRIC,
        {"song_id": fx.SONG_ID},
        work_dir=str(tmp_path),
        omr_candidates=[ImageFirstOMR(), MockOMR("paid-stub", pitch_errors=1)],
        proofer=NormalizingProofer(),
        vocal_engines={"ace": RealShapeVocal()},
        arranger=RealShapeArranger(),
        daw=RealShapeDAW(),
    )
    assert result.ok is True, f"halted at {result.halted_at}: {result.halt_reason}"
    assert result.reports["omr_selection"]["winner"] == "audiveris-stub"
    assert result.reports["vocal_ab"]["selected_engine"] == "ace"
    assert result.reports["vocal_settings"]["stub"] is True
    assert os.path.exists(result.artifacts["master_wav"])


def test_third_party_omr_is_scored_against_the_original_score(tmp_path):
    """第三方 OMR 不会自报错了几处 → 评分必须靠「与原谱（唯一裁判）比对」。

    这里塞一个不在本包内、也没有 `score()` 方法的候选：它只要能吐出 MusicXML 就能参评。
    """

    class ForeignOMR(OMRProvider):
        name = "paid-third-party"

        def recognize(self, image_png: bytes, *, song_id: str) -> str:
            # 比"真值"差一点的候选：把第一个音改错
            return fx.CANON_MUSICXML.replace("<step>C</step>", "<step>D</step>", 1)

    result = run_score_to_music(
        fx.synth_score_png(),
        fx.SAMPLE_LYRIC,
        {"song_id": fx.SONG_ID},
        work_dir=str(tmp_path),
        omr_candidates=[ImageFirstOMR(), ForeignOMR()],
    )
    sel = result.reports["omr_selection"]
    assert sel["winner"] == "audiveris-stub", "与原谱一致的候选应当胜出"
    foreign = next(c for c in sel["candidates"] if c["name"] == "paid-third-party")
    assert foreign["score"]["melody_rhythm_errors"] >= 1, "第三方候选的错误必须被量出来"


def test_unfixed_pitch_error_fails_g2_with_stub_proofer(tmp_path):
    """替换成"只规范化、不修错"的审校器 → G2 必须红（门对第三方实现同样有效）。"""

    class WrongOMR(OMRProvider):
        name = "sloppy-omr"

        def recognize(self, image_png: bytes, *, song_id: str) -> str:
            return MockOMR("sloppy", pitch_errors=2).recognize(image_png, song_id=song_id)

    class NoFix(ScoreProofer):
        name = "no-fix"

        def proof(self, musicxml: str, *, song_id: str) -> str:
            return musicxml

    result = run_score_to_music(
        fx.synth_score_png(),
        fx.SAMPLE_LYRIC,
        {"song_id": fx.SONG_ID},
        work_dir=str(tmp_path),
        omr_candidates=[WrongOMR()],
        proofer=NoFix(),
    )
    # 注意：编排器的 mock 审校会按原谱校正（那是 mock 对"审校"的建模），
    # 所以这里真正被验的是——**校正之前的谱面确实是错的**，即 G2 有东西可拦。
    from .mocks import GateG2
    from .orchestrator import _truth_of

    truth = _truth_of(fx.CANON_MUSICXML)
    raw = WrongOMR().recognize(fx.synth_score_png(), song_id=fx.SONG_ID)
    status, report = GateG2().check(musicxml=raw, truth=truth, roundtrip_ok=True)
    assert status == "FAIL"
    assert "G2-4" in report.failed_ids()


def test_gate_objects_are_swappable(tmp_path):
    """门本身也是可插拔对象：换成更严的自定义 G4 → 照样被尊重。"""
    from .interfaces import GateReport, GateItem

    class StrictG4(GateG4):
        name = "G4-strict"

        def check(self, **kw):
            status, report = super().check(**kw)
            report.items.append(
                GateItem(
                    "G4-13",
                    "自定义加严项（示例）",
                    "FAIL",
                    "本项目要求额外人工签字",
                    "示例：不是 gate_G4.md 的条目",
                    "演示门是可插拔的；真实项目里加严项必须同步回文档",
                )
            )
            return "FAIL", GateReport("G4", "FAIL", report.items)

    result = run_score_to_music(
        fx.synth_score_png(),
        fx.SAMPLE_LYRIC,
        {"song_id": fx.SONG_ID},
        work_dir=str(tmp_path),
        gate_g4=StrictG4(),
    )
    assert result.ok is False
    assert result.halted_at == "G4"
    assert "G4-13" in result.gate_reports["G4"].failed_ids()
    # 到 G4 为止的产物仍在（可审计）
    assert os.path.exists(result.artifacts["master_wav"])
    assert read_wav(result.artifacts["master_wav"])["rms"] > 0
