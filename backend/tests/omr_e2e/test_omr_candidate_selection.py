"""OMR 候选选优（mirror #34）。

#34 的要求是「逐曲重测选胜者，禁假设全局同优」，顺序是：
  1 次基准 = 旋律 + 节奏错误最少；
  2 次 = 需歌词时看歌词错误 + 校正时间；
  3 次 = 价格 / 导出格式 / 批处理。
这里测的是**第 1、2 次基准的排序语义**，以及「换一首曲/换一组候选，胜者会变」
（= 不假设全局同优）。
"""

from . import fixtures as fx
from .mocks import MockOMR, _pitch_errors, _measure_durations, select_winner
from .orchestrator import _truth_of, run_score_to_music


def _scored(*candidates):
    truth = _truth_of(fx.CANON_MUSICXML)
    out = []
    for c in candidates:
        xml = c.recognize(b"", song_id=fx.SONG_ID)
        out.append(
            {
                "name": c.name,
                "score": c.score(truth) if hasattr(c, "score") else None,
                "xml": xml,
            }
        )
    return out


def test_omr_candidate_selection(tmp_path):
    """综合错得少的那一个胜出（#34 第 1 基准）。"""
    a = MockOMR("audiveris", pitch_errors=1, rhythm_errors=0, lyric_errors=0)
    b = MockOMR("paid_api", pitch_errors=2, rhythm_errors=1, lyric_errors=1)
    truth = _truth_of(fx.CANON_MUSICXML)
    entered = [
        {"name": a.name, "score": a.score(truth)},
        {"name": b.name, "score": b.score(truth)},
    ]
    result = select_winner(entered)
    assert result["winner"]["name"] == "audiveris"
    assert result["ranking"] == ["audiveris", "paid_api"]

    # 换一组候选 → 胜者随之改变（不假设全局同优）
    entered2 = [
        {"name": "audiveris", "score": {"melody_rhythm_errors": 4, "lyric_errors": 0, "duration_errors": 0}},
        {"name": "paid_api", "score": {"melody_rhythm_errors": 1, "lyric_errors": 0, "duration_errors": 0}},
    ]
    assert select_winner(entered2)["winner"]["name"] == "paid_api"


def test_melody_rhythm_outranks_lyrics():
    """第 1 基准优先于第 2 基准：歌词全对但旋律更差，仍排后面。"""
    good_melody = {"name": "m", "score": {"melody_rhythm_errors": 1, "lyric_errors": 3, "duration_errors": 0}}
    bad_melody = {"name": "l", "score": {"melody_rhythm_errors": 2, "lyric_errors": 0, "duration_errors": 0}}
    assert select_winner([bad_melody, good_melody])["winner"]["name"] == "m"


def test_selection_is_deterministic_on_ties():
    """同分不靠"注册顺序"这种隐规则：结果稳定且可复现。"""
    tie = [
        {"name": "b", "score": {"melody_rhythm_errors": 1, "lyric_errors": 1, "duration_errors": 0}},
        {"name": "a", "score": {"melody_rhythm_errors": 1, "lyric_errors": 1, "duration_errors": 0}},
    ]
    assert select_winner(tie)["winner"]["name"] == "a"
    assert select_winner(list(reversed(tie)))["winner"]["name"] == "a"


def test_injected_errors_are_actually_measurable():
    """注入的错误必须真的能在产物里数出来 —— 否则"选优"是自说自话。"""
    truth = _truth_of(fx.CANON_MUSICXML)
    clean = fx.CANON_MUSICXML
    assert _pitch_errors(_truth_of(clean)["pitches"], truth["pitches"]) == 0
    assert _measure_durations(clean) == [fx.BEATS] * fx.MEASURES

    pitched = MockOMR("x", pitch_errors=3).recognize(b"", song_id=fx.SONG_ID)
    assert _pitch_errors(_truth_of(pitched)["pitches"], truth["pitches"]) == 3

    duration = MockOMR("y", duration_errors=2).recognize(b"", song_id=fx.SONG_ID)
    bad = [i + 1 for i, d in enumerate(_measure_durations(duration)) if d != fx.BEATS]
    assert len(bad) == 2

    lyric = MockOMR("z", lyric_errors=2).recognize(b"", song_id=fx.SONG_ID)
    assert _truth_of(lyric)["lyrics"].count("") == 2


def test_pipeline_records_selection_evidence(tmp_path):
    """流水线必须留下「谁赢了、凭什么」的记录（#34 的选定记录是要归档的交付物）。"""
    result = run_score_to_music(
        fx.synth_score_png(),
        fx.SAMPLE_LYRIC,
        {"song_id": fx.SONG_ID},
        work_dir=str(tmp_path),
    )
    sel = result.reports["omr_selection"]
    assert sel["winner"] == "audiveris"
    assert sel["ranking"] == ["audiveris", "paid_api"]
    assert "basis" in sel and "#34" in sel["basis"]
    for cand in sel["candidates"]:
        assert cand["raw_path"].endswith(".musicxml")
        assert len(cand["xml_sha256"]) == 64
        assert "score" in cand
    # 候选原始产物按 music_rebuild_project 的目录约定落位（03_omr/<song>/raw）
    assert "03_omr_raw" in sel["candidates"][0]["raw_path"]
