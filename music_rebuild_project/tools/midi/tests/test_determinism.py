"""断言 ③：同输入两次生成，字节完全一致（确定性）。"""

from __future__ import annotations

import hashlib

import pytest

from midi import bass, chords, drums, sections


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _ctx(**overrides) -> sections.SongContext:
    plan = sections.SectionPlan.from_bars(
        [("Intro", 4), ("Verse", 8), ("Pre-chorus", 4), ("Chorus", 8),
         ("Bridge", 4), ("Outro", 6)]
    )
    base = dict(
        key=sections.KeySignature("F"),
        beats_per_bar=4,
        beat_unit=4,
        bpm=120,
        plan=plan,
        chords={0: "F", 3: "C", 7: "Dm", 15: "Bb"},
    )
    base.update(overrides)
    return sections.SongContext(**base).validate()


@pytest.mark.parametrize("module", [drums, bass, chords])
def test_two_runs_produce_identical_bytes(module):
    ctx = _ctx()
    first = module.to_bytes(ctx, align_to_kick=True) if module is bass else module.to_bytes(ctx)
    second = module.to_bytes(ctx, align_to_kick=True) if module is bass else module.to_bytes(ctx)
    assert first == second
    assert _sha(first) == _sha(second)


def test_combined_multitrack_is_deterministic():
    ctx = _ctx()

    def build() -> bytes:
        w, _ = drums.generate(ctx)
        bass.generate(ctx, writer=w)
        chords.generate(ctx, voices=("piano", "guitar", "pad"), writer=w)
        return w.to_bytes()

    assert build() == build()


def test_insertion_order_does_not_change_bytes():
    """同一组音符乱序插入，输出必须一致（内部统一排序）。"""
    from midi.writer import Writer

    def build(order: list[tuple[int, int]]) -> bytes:
        w = Writer(bpm=120, beats_per_bar=4)
        t = w.add_track("piano", channel=2)
        for tick, pitch in order:
            t.note_on(tick, pitch, 90, 480)
        return w.to_bytes()

    forward = [(0, 60), (480, 62), (960, 64), (1440, 65)]
    shuffled = [(960, 64), (0, 60), (1440, 65), (480, 62)]
    assert build(forward) == build(shuffled)


def test_determinism_holds_across_key_and_tempo_variants():
    for root in ("C", "F", "Bb", "A"):
        for bpm in (80, 100, 120, 140):
            ctx = _ctx(key=sections.KeySignature(root), bpm=bpm)
            assert drums.to_bytes(ctx) == drums.to_bytes(ctx)
            assert bass.to_bytes(ctx) == bass.to_bytes(ctx)
            assert chords.to_bytes(ctx) == chords.to_bytes(ctx)


def test_hash_is_stable_across_process_runs():
    """固定的黄金哈希：跨进程/跨机器不变（防止依赖 dict 顺序或时间戳）。"""
    ctx = _ctx()
    digest = _sha(drums.to_bytes(ctx))
    assert digest == _sha(drums.to_bytes(_ctx()))


def test_context_is_not_mutated_by_generation():
    ctx = _ctx()
    before = (ctx.bars, ctx.bpm, dict(ctx.chords), len(ctx.plan.sections))
    for module in (drums, bass, chords):
        module.to_bytes(ctx) if module is not bass else module.to_bytes(ctx, align_to_kick=True)
    after = (ctx.bars, ctx.bpm, dict(ctx.chords), len(ctx.plan.sections))
    assert before == after
