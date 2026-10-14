"""音乐规则自验：贝斯根音随和弦 + 与底鼓对齐；和弦在调内。"""

from __future__ import annotations

import pytest

from midi import bass, chords, drums, sections
from midi.writer import KICK, Writer, read_notes


@pytest.fixture
def ctx_with_chords() -> sections.SongContext:
    plan = sections.SectionPlan.from_bars([("Verse", 4), ("Chorus", 4)])
    return sections.SongContext(
        key=sections.KeySignature("C"),
        beats_per_bar=4,
        bpm=120,
        plan=plan,
        chords={0: "C", 1: "Am", 2: "F", 3: "G", 4: "C", 5: "F", 6: "G", 7: "C"},
    ).validate()


def test_chord_symbol_parsing():
    assert bass.ChordSymbol.parse("C").chord_tones() == (0, 4, 7)
    assert bass.ChordSymbol.parse("Am").chord_tones() == (9, 12, 16)
    assert bass.ChordSymbol.parse("F#m7").root == 6
    assert bass.ChordSymbol.parse("Cmaj7").chord_tones() == (0, 4, 7)
    assert bass.ChordSymbol.parse("G/B").bass == 11
    assert bass.ChordSymbol.parse("Csus4").chord_tones() == (0, 5, 7)
    with pytest.raises(bass.BassError):
        bass.ChordSymbol.parse("H7")


def test_bass_root_follows_written_chords(ctx_with_chords):
    """#53 规则 1：贝斯根音 = chords.txt 里该小节的和弦根音。"""
    _w, notes = bass.generate(ctx_with_chords)
    writer = Writer(bpm=120, beats_per_bar=4)
    expected = {0: 0, 1: 9, 2: 5, 3: 7, 4: 0, 5: 5, 6: 7, 7: 0}
    for bar, root_pc in expected.items():
        lo, hi = writer.bar(bar), writer.bar(bar + 1)
        in_bar = [p for t, p, _v in notes if lo <= t < hi]
        assert in_bar, f"第 {bar} 小节贝斯缺失"
        assert {p % 12 for p in in_bar} == {root_pc}, f"第 {bar} 小节根音不对"


def test_bass_lands_on_kick_ticks(ctx_with_chords):
    """#53 规则 2：每个贝斯落点都必须落在底鼓 tick 上。"""
    w, notes = bass.generate(ctx_with_chords, align_to_kick=True)
    drum_writer, _hits = drums.generate(
        ctx_with_chords, Writer(bpm=120, beats_per_bar=4)
    )
    kick_ticks = {
        n.start for n in read_notes(drum_writer.to_bytes()) if n.pitch == KICK
    }
    assert kick_ticks
    assert {t for t, _p, _v in notes} <= kick_ticks


def test_bass_aligns_to_kick_across_all_sections(full_song):
    """回归：整曲（六段落混排）下每个贝斯落点仍必须落在底鼓 tick 上。

    先前 `_kick_ticks` 会拿 pattern 的 step 在每小节重复推算，导致
    Pre-chorus / Bridge 段出现「假 kick」而实际未对齐。
    """
    _w_bass, notes = bass.generate(full_song)
    drum_writer, _hits = drums.generate(full_song)
    kicks = {n.start for n in read_notes(drum_writer.to_bytes()) if n.pitch == KICK}
    assert kicks
    assert {t for t, _p, _v in notes} <= kicks
    # 同一 tick 不应重复触发（吸附后可能撞车）
    ticks = [t for t, _p, _v in notes]
    assert len(ticks) == len(set(ticks))


def test_bass_never_invents_a_kick(full_song):
    """反向校验：贝斯落点集合是 kick 集合的子集，且覆盖率合理（不是全丢弃）。"""
    _w_bass, notes = bass.generate(full_song)
    drum_writer, _hits = drums.generate(full_song)
    kicks = {n.start for n in read_notes(drum_writer.to_bytes()) if n.pitch == KICK}
    assert len(notes) >= len(kicks) * 0.5, "贝斯丢点过多，说明吸附阈值过严"


def test_bass_align_off_keeps_pattern_positions(ctx_with_chords):
    """关闭对齐时保留 pattern 原落点（对照组，确认对齐逻辑真的在起作用）。"""
    _w, aligned = bass.generate(ctx_with_chords, align_to_kick=True)
    _w2, free = bass.generate(ctx_with_chords, align_to_kick=False)
    assert {t for t, _p, _v in free} != {t for t, _p, _v in aligned}
    assert {t for t, _p, _v in free} - {t for t, _p, _v in aligned}


def test_bass_slash_chord_uses_bass_note(ctx_with_chords):
    ctx = ctx_with_chords
    ctx.chords[0] = "C/E"  # 转位：低音应为 E(4)，而非 C(0)
    _w, notes = bass.generate(ctx)
    first_bar = [p for t, p, _v in notes if t < Writer(bpm=120).bar(1)]
    assert {p % 12 for p in first_bar} == {4}


def test_bass_stays_in_bass_register(ctx_with_chords):
    _w, notes = bass.generate(ctx_with_chords)
    assert all(28 <= p <= 55 for _t, p, _v in notes), "贝斯音高越出低音区"


def test_bass_falls_back_to_diatonic_when_chords_missing():
    ctx = sections.SongContext(
        key=sections.KeySignature("C"), beats_per_bar=4, bpm=120, bars=4
    ).validate()
    _w, notes = bass.generate(ctx)
    assert notes
    c_major = sections.KeySignature("C")
    allowed = {c_major.degree(i) % 12 for i in range(7)}
    assert {p % 12 for _t, p, _v in notes} <= allowed


def test_chord_tones_stay_in_key_when_no_chords_written(full_song):
    full_song.chords.clear()
    _w, collected = chords.generate(full_song, voices=("piano",))
    key = full_song.key
    allowed = {key.degree(i) % 12 for i in range(7)}
    used = {p % 12 for _t, p, _v, _d in collected["piano"]}
    # 允许少量经过音，但绝大多数音必须在调内
    inside = len(used & allowed)
    assert inside / len(used) >= 0.75, f"调外音过多: {sorted(used - allowed)}"


def test_written_chords_are_honoured_by_chord_voices(ctx_with_chords):
    """写了 C/Am/F/G，和弦轨就必须出现这些和弦的构成音。"""
    _w, collected = chords.generate(ctx_with_chords, voices=("piano",))
    for bar, symbol in ((0, "C"), (1, "Am"), (2, "F"), (3, "G")):
        writer = Writer(bpm=120, beats_per_bar=4)
        lo, hi = writer.bar(bar), writer.bar(bar + 1)
        pcs = {
            p % 12 for t, p, _v, _d in collected["piano"] if lo <= t < hi
        }
        tones = {t % 12 for t in bass.ChordSymbol.parse(symbol).chord_tones()}
        assert pcs & tones, f"第 {bar} 小节未体现和弦 {symbol}"


def test_chord_voice_pitches_are_playable(full_song):
    _w, collected = chords.generate(full_song, voices=("piano", "guitar", "pad"))
    for voice, notes in collected.items():
        assert all(36 <= p <= 96 for _t, p, _v, _d in notes), f"{voice} 音高不可演奏"


def test_durations_are_positive_and_within_bar(full_song):
    _w, collected = chords.generate(full_song, voices=("piano", "guitar", "pad"))
    writer = Writer(bpm=full_song.bpm, beats_per_bar=full_song.beats_per_bar)
    for voice, notes in collected.items():
        for _t, _p, _v, duration in notes:
            assert duration > 0
            assert duration <= writer.bar_ticks * 2, f"{voice} 时值过长"


def test_unknown_voice_is_rejected(full_song):
    with pytest.raises(chords.ChordError):
        chords.generate(full_song, voices=("theremin",))


def test_empty_voice_list_is_rejected(full_song):
    with pytest.raises(chords.ChordError):
        chords.generate(full_song, voices=())


def test_unknown_section_has_no_drum_pattern():
    with pytest.raises(KeyError):
        drums.pattern_for("Solo")


def test_sections_describe_is_readable(full_song):
    text = full_song.plan.describe()
    assert "Intro" in text and "Chorus" in text and "Outro" in text
    assert text.count("\n") == 5
