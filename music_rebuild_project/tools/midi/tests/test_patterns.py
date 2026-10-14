"""断言 ④：段落切换处 pattern 确实不同；⑤：力度有变化（非全同值）。"""

from __future__ import annotations

import pytest

from midi import bass, chords, drums, sections
from midi.writer import DRUM_CHANNEL, KICK, read_notes


def _section_fingerprints(ctx, module, voices=None):
    """按段落切出 (step, pitch, velocity) 指纹，用于比较 pattern 差异。"""
    if module is chords:
        w, collected = chords.generate(ctx, voices=voices or ("piano",))
        notes = [
            (tick, pitch, velocity)
            for voice in voices or ("piano",)
            for tick, pitch, velocity, _d in collected[voice]
        ]
    elif module is bass:
        w, notes = bass.generate(ctx)
    else:
        w, _hits = drums.generate(ctx)
        notes = [(n.start, n.pitch, n.velocity) for n in read_notes(w.to_bytes())]
    step_ticks = w.beat_ticks(1) // 4
    out: dict[str, set[tuple[int, int, int]]] = {}
    for section in ctx.plan.sections:
        lo, hi = w.bar(section.start_bar), w.bar(section.end_bar)
        bucket: set[tuple[int, int, int]] = set()
        for tick, pitch, velocity in notes:
            if lo <= tick < hi:
                beat_ticks = w.beat_ticks(1)
                bucket.add((
                    (tick - lo) % (beat_ticks * ctx.beats_per_bar) // step_ticks,
                    pitch,
                    velocity,
                ))
        out[section.display] = bucket
    return out


def test_verse_and_chorus_drum_patterns_differ(full_song):
    """#52 的核心：主歌/副歌必须是两套 pattern，而不是同一套换音量。"""
    verse = drums.pattern_for("Verse")
    chorus = drums.pattern_for("Chorus")
    assert verse.hits != chorus.hits
    verse_shape = {(h.step, h.pitch) for h in verse.hits}
    chorus_shape = {(h.step, h.pitch) for h in chorus.hits}
    assert verse_shape != chorus_shape
    # 副歌更密：落点数严格多于主歌
    assert len(chorus.hits) > len(verse.hits)
    # 副歌力度整体更强
    assert max(h.velocity for h in chorus.hits) > max(h.velocity for h in verse.hits)
    # 副歌用了主歌没有的乐器（开镲/撞击镲）
    assert {h.pitch for h in chorus.hits} - {h.pitch for h in verse.hits}


def test_all_six_sections_have_distinct_drum_patterns():
    shapes = {
        name: {(h.step, h.pitch) for h in drums.pattern_for(name).hits}
        for name in sections.SECTION_ORDER
    }
    assert len(shapes) == 6
    for a in shapes:
        for b in shapes:
            if a < b:
                assert shapes[a] != shapes[b], f"{a} 与 {b} 的鼓 pattern 雷同"


def test_pattern_actually_changes_at_section_boundary(full_song):
    """在 Verse→Pre-chorus→Chorus 边界两侧，实际产出的落点形状必须不同。"""
    fps = _section_fingerprints(full_song, drums)
    assert fps["Verse"] != fps["Chorus"]
    assert fps["Verse"] != fps["Pre-chorus"]
    assert fps["Chorus"] != fps["Bridge"]


def test_chord_voice_texture_differs_between_verse_and_chorus(full_song):
    fps = _section_fingerprints(full_song, chords, voices=("piano",))
    assert fps["Verse"] != fps["Chorus"], "副歌和弦织体应与主歌不同"
    assert len(fps["Chorus"]) > len(fps["Verse"]), "副歌落点应更密"


def test_pad_and_guitar_are_distinct_voices(full_song):
    w, collected = chords.generate(full_song, voices=("piano", "guitar", "pad"))
    piano = {(t, p) for t, p, _v, _d in collected["piano"]}
    guitar = {(t, p) for t, p, _v, _d in collected["guitar"]}
    pad = {(t, p) for t, p, _v, _d in collected["pad"]}
    assert piano != guitar
    assert pad != piano
    assert len(collected["pad"]) < len(collected["piano"]), "铺底应比钢琴稀疏（长音）"
    channels = {tr.channel for tr in w.tracks}
    assert len(channels) == 3, "三种织体必须各占一条轨道/通道"


# ---- 力度变化（断言 ⑤）------------------------------------------------

@pytest.mark.parametrize(
    "module_name",
    ["drums", "bass", "chords"],
)
def test_velocities_are_not_constant(full_song, module_name):
    module = {"drums": drums, "bass": bass, "chords": chords}[module_name]
    if module is chords:
        w, collected = chords.generate(full_song, voices=("piano", "guitar", "pad"))
        velocities = [v for notes in collected.values() for _t, _p, v, _d in notes]
    elif module is bass:
        w, notes = bass.generate(full_song)
        velocities = [v for _t, _p, v in notes]
    else:
        w, _hits = drums.generate(full_song)
        velocities = [n.velocity for n in read_notes(w.to_bytes())]
    assert velocities, f"{module_name} 没有产出音符"
    assert len(set(velocities)) > 1, f"{module_name} 力度恒定，缺少变化"
    # 变化幅度要有意义（不是 1~2 的抖动）
    assert max(velocities) - min(velocities) >= 8, f"{module_name} 力度动态范围过小"


def test_drum_accent_varies_by_bar(full_song):
    """同一 pattern 在不同小节的重音不同 —— 结构才有推进感。"""
    w, _hits = drums.generate(full_song)
    kicks = [n for n in read_notes(w.to_bytes()) if n.pitch == KICK]
    verse_kicks = [
        n.velocity
        for n in kicks
        if w.bar(4) <= n.start < w.bar(12)  # Verse 段（小节 4..11）
    ]
    assert len(set(verse_kicks)) > 1, "主歌底鼓力度应随小节变化"


def test_bass_velocity_reflects_section_dynamics(full_song):
    _w, notes = bass.generate(full_song)
    from midi.writer import Writer

    ref = Writer(bpm=full_song.bpm, beats_per_bar=full_song.beats_per_bar)
    verse = [v for t, _p, v in notes if ref.bar(4) <= t < ref.bar(12)]
    chorus = [v for t, _p, v in notes if ref.bar(16) <= t < ref.bar(24)]
    assert max(chorus) > max(verse), "副歌贝斯应比主歌更有力"


def test_chorus_has_more_drum_hits_than_verse(full_song):
    """段落密度差可辨（G4-6 的可测前置）。"""
    w, _hits = drums.generate(full_song)
    notes = read_notes(w.to_bytes())
    verse = [n for n in notes if w.bar(4) <= n.start < w.bar(12)]
    chorus = [n for n in notes if w.bar(16) <= n.start < w.bar(24)]
    assert len(chorus) > len(verse)
    assert all(n.channel == DRUM_CHANNEL for n in verse + chorus)
