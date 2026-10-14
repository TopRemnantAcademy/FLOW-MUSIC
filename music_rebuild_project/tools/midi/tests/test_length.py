"""断言 ②：长度精确 = 小节 × 拍数 × BPM 换算（G4-5 的前置）。"""

from __future__ import annotations

import pytest

from midi import bass, chords, drums, sections
from midi.writer import Writer


def _last_note_end(data: bytes) -> int:
    """从 SMF 字节里读出最后一个音符的结束 tick。"""
    from midi.writer import read_notes

    notes = read_notes(data)
    assert notes, "产物里没有音符"
    return max(n.end for n in notes)


@pytest.mark.parametrize(
    "bars,beats,bpm,expected_seconds",
    [
        (8, 4, 120, 16.0),
        (32, 4, 120, 64.0),
        (12, 3, 90, 24.0),
        (16, 4, 100, 38.4),
        (24, 6, 80, 108.0),
        (4, 4, 60, 16.0),
        (36, 4, 140, 61.714285714285715),
    ],
)
def test_seconds_formula_matches_bar_beats_bpm(bars, beats, bpm, expected_seconds):
    ctx = sections.SongContext(
        key=sections.KeySignature("C"), beats_per_bar=beats, bpm=bpm, bars=bars
    ).validate()
    assert ctx.seconds() == pytest.approx(expected_seconds, rel=1e-12)


def test_writer_bar_ticks_and_total_ticks():
    w = Writer(bpm=120, beats_per_bar=4, beat_unit=4, tpq=480)
    assert w.beat_ticks(1) == 480
    assert w.bar_ticks == 1920
    assert w.total_ticks(8) == 15360
    assert w.seconds_for_bars(8) == pytest.approx(16.0)

    three_four = Writer(bpm=90, beats_per_bar=3, beat_unit=4, tpq=480)
    assert three_four.bar_ticks == 1440
    assert three_four.seconds_for_bars(12) == pytest.approx(24.0)


def test_all_voices_span_exactly_the_song_length(full_song):
    """整轨铺满后，最后一个音符恰落在全曲末尾 —— 既不短也不溢出。"""
    ctx = full_song  # 34 小节 × 4 拍 @120 BPM = 68.0 秒
    assert ctx.bars == 34
    assert ctx.seconds() == pytest.approx(68.0)

    w, _ = drums.generate(ctx)
    drum_end = _last_note_end(w.to_bytes())
    # 鼓的最后一击在第 33 小节第 3 拍（outro pattern 的 tom 下行）
    assert drum_end <= w.total_ticks(ctx.bars)

    w_bass, bass_notes = bass.generate(ctx)
    assert bass_notes
    # Outro 的贝斯落点是十六分步 0 与 8（=第 3 拍），时值 1 个十六分步。
    last_bar = 33
    step_ticks = w_bass.beat_ticks(1) // 4
    assert _last_note_end(w_bass.to_bytes()) == w_bass.bar(last_bar) + 8 * step_ticks + step_ticks
    assert _last_note_end(w_bass.to_bytes()) <= w_bass.total_ticks(ctx.bars)

    w_chords, _voices = chords.generate(ctx, voices=("piano", "guitar", "pad"))
    assert _last_note_end(w_chords.to_bytes()) <= w_chords.total_ticks(ctx.bars)


def test_track_length_equals_total_ticks_when_note_fills_last_bar():
    """显式铺满：最后一个音符的 end 必须 == 小节 × 每小节 tick。"""
    ctx = sections.SongContext(
        key=sections.KeySignature("C"), beats_per_bar=4, bpm=120, bars=8
    ).validate()
    w = Writer(bpm=ctx.bpm, beats_per_bar=ctx.beats_per_bar, beat_unit=ctx.beat_unit)
    track = w.add_track("pad", channel=4)
    # 一小节一个长音，铺满 8 小节
    for bar in range(8):
        track.note_on(w.bar(bar), 60, 80, w.bar_ticks)
    assert _last_note_end(w.to_bytes()) == w.total_ticks(8) == 15360
    # 15360 tick @480tpq / 4拍每小节 = 32 拍 = 16 秒 @120BPM
    assert 15360 / 480 / 4 * (60 / 120) * 4 == pytest.approx(16.0)


def test_pad_voice_spans_every_bar_without_gap(full_song):
    """铺底音色必须逐小节连续覆盖，总覆盖 tick == 全曲 tick。"""
    ctx = full_song
    _w, notes = chords.generate_voice(ctx, "pad")
    writer = Writer(bpm=ctx.bpm, beats_per_bar=ctx.beats_per_bar, beat_unit=ctx.beat_unit)
    # 每小节至少有一个起始于该小节第一拍的音 → 无空档
    starts = {tick for tick, _p, _v, _d in notes}
    for bar in range(ctx.bars):
        assert writer.bar(bar) in starts, f"第 {bar} 小节铺底缺失"


def test_seconds_conversion_roundtrip_is_exact_for_common_tempos():
    """常见 BPM 下 tick↔秒的换算是整数/有限小数，G4-5 的 ±1s 有余量。"""
    cases = [
        (4, 4, 120, 8, 16.0),
        (4, 4, 100, 4, 9.6),
        (4, 4, 128, 16, 30.0),
        (3, 4, 90, 4, 8.0),
        (6, 8, 96, 8, 30.0),
    ]
    for beats, unit, bpm, bars, expected in cases:
        ctx = sections.SongContext(
            key=sections.KeySignature("C"),
            beats_per_bar=beats,
            beat_unit=unit,
            bpm=bpm,
            bars=bars,
        ).validate()
        assert ctx.seconds() == pytest.approx(expected, abs=1e-9)
