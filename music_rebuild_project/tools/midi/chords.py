"""和弦乐器 MIDI 生成（#54）。

三种织体，可单用也可分层叠（`voices=` 传入多个）：
  - `piano`  —— 钢琴：柱式/分解和弦，中高音区，主歌稀疏、副歌密集
  - `guitar` —— 吉他：分解音型（琶音），比钢琴更高的切分密度
  - `pad`    —— 铺底：长音持续，跨小节垫底，为混音留出中频空间

每小节的和弦来自 `chords.txt`（#50）；缺失时按段落默认级数走向兜底，
与 `bass.py` 使用同一份级数表，保证贝斯根音与和弦构成音一致。
"""

from __future__ import annotations

from dataclasses import dataclass

from .bass import STEPS_PER_BEAT, ChordSymbol, _DEFAULT_DEGREES
from .sections import SongContext
from .writer import Track, Writer

#: 各织体的 MIDI 通道（0-based）。
VOICE_CHANNELS = {"piano": 2, "guitar": 3, "pad": 4}


class ChordError(ValueError):
    """和弦生成输入不合法。"""


@dataclass(frozen=True)
class VoiceSpec:
    """一种织体的生成参数：音区、节奏型、力度、时值倍数。"""

    name: str
    octave: int
    #: 每小节内的落点（十六分步号）。`None` 表示「整小节长音」。
    steps: tuple[int, ...] | None
    base_velocity: int
    #: 时值 = step_ticks × duration_steps
    duration_steps: int
    #: 音区内的音程叠放（相对和弦根的半音，用于铺底留白）。
    spread: tuple[int, ...] = (0, 1, 2)


#: 各段落 × 织体的节奏设计。副歌更密、桥段更稀 —— 结构可辨（G4-6 前置）。
_VOICE_MATRIX: dict[str, dict[str, VoiceSpec]] = {
    "piano": {
        "Intro": VoiceSpec("piano", 4, (0, 8), 72, 6),
        "Verse": VoiceSpec("piano", 4, (0, 8), 84, 6),
        "Pre-chorus": VoiceSpec("piano", 4, (0, 4, 8, 12), 92, 3),
        "Chorus": VoiceSpec("piano", 4, (0, 2, 4, 6, 8, 10, 12, 14), 108, 1),
        "Bridge": VoiceSpec("piano", 4, (0,), 66, 14),
        "Outro": VoiceSpec("piano", 4, (0, 8), 78, 6),
    },
    "guitar": {
        "Intro": VoiceSpec("guitar", 5, (0, 4, 8, 12), 64, 3, (0, 1)),
        "Verse": VoiceSpec("guitar", 5, (2, 6, 10, 14), 74, 3, (0, 1)),
        "Pre-chorus": VoiceSpec("guitar", 5, (0, 3, 6, 8, 11, 14), 88, 2, (0, 1)),
        "Chorus": VoiceSpec("guitar", 5, (0, 2, 4, 8, 10, 12), 100, 2),
        "Bridge": VoiceSpec("guitar", 5, (0, 8), 58, 6, (0, 1)),
        "Outro": VoiceSpec("guitar", 5, (0, 6, 12), 68, 4, (0, 1)),
    },
    "pad": {
        "Intro": VoiceSpec("pad", 5, None, 58, 16, (0, 2)),
        "Verse": VoiceSpec("pad", 5, None, 64, 16, (0, 2)),
        "Pre-chorus": VoiceSpec("pad", 5, (0,), 72, 16, (0, 2)),
        "Chorus": VoiceSpec("pad", 5, (0, 8), 82, 8, (0, 1, 2)),
        "Bridge": VoiceSpec("pad", 5, None, 54, 16, (0,)),
        "Outro": VoiceSpec("pad", 5, None, 60, 16, (0, 2)),
    },
}


def _symbol_for(context: SongContext, bar: int) -> ChordSymbol:
    written = context.chords.get(bar)
    if written:
        return ChordSymbol.parse(written)
    section = context.section_at(bar)
    degrees = _DEFAULT_DEGREES.get(section.name, (0,))
    degree_index = degrees[(bar - section.start_bar) % len(degrees)]
    root_pc = context.key.degree(degree_index) % 12
    quality = "min" if (degree_index in (1, 2, 5) or context.is_minor()) else "maj"
    if degree_index in (1, 2, 5) and context.is_minor():
        quality = "dim" if degree_index == 1 and context.is_minor() else quality
    return ChordSymbol(root=root_pc, quality=quality)


def _voice_pitches(symbol: ChordSymbol, octave: int, spread: tuple[int, ...]) -> list[int]:
    """把和弦摊到指定音区：三和弦音 + spread 选中的叠放层。"""
    tones = symbol.chord_tones()
    pitches: list[int] = []
    for layer in spread:
        for offset, tone in enumerate(tones):
            # 逐音上行排列，避免同八度撞在一起
            pitch = (octave + 1) * 12 + tone % 12 + 12 * (layer + (1 if offset else 0))
            while pitch > 96:
                pitch -= 12
            while pitch < 48:
                pitch += 12
            if pitch not in pitches:
                pitches.append(pitch)
    return sorted(pitches)


def generate_voice(
    context: SongContext,
    voice: str,
    writer: Writer | None = None,
) -> tuple[Writer, list[tuple[int, int, int, int]]]:
    """生成单一织体的和弦轨。

    返回 `(writer, notes)`，`notes` 为 `(tick, pitch, velocity, duration)`。
    """
    if voice not in VOICE_CHANNELS:
        raise ChordError(f"未知织体: {voice!r}（可选: {', '.join(VOICE_CHANNELS)}）")
    context.validate()
    assert context.plan is not None
    writer = writer or Writer(
        bpm=context.bpm,
        beats_per_bar=context.beats_per_bar,
        beat_unit=context.beat_unit,
    )
    track: Track = writer.add_track(voice, channel=VOICE_CHANNELS[voice], instrument=voice)
    step_ticks = writer.beat_ticks(1) // STEPS_PER_BEAT
    notes: list[tuple[int, int, int, int]] = []

    for section in context.plan.sections:
        spec = _VOICE_MATRIX[voice][section.name]
        for bar in range(section.start_bar, section.end_bar):
            symbol = _symbol_for(context, bar)
            pitches = _voice_pitches(symbol, spec.octave, spec.spread)
            base = writer.bar(bar)
            steps = spec.steps if spec.steps is not None else (0,)
            duration = (
                writer.bar_ticks if spec.steps is None else step_ticks * spec.duration_steps
            )
            for step in steps:
                tick = base + step * step_ticks
                for index, pitch in enumerate(pitches):
                    velocity = _voice_velocity(spec.base_velocity, section.name, step, index)
                    track.note_on(tick, pitch, velocity, duration)
                    notes.append((tick, pitch, velocity, duration))
    notes.sort()
    return writer, notes


def _voice_velocity(base: int, section_name: str, step: int, index: int) -> int:
    """力度分层：顶部音稍亮、每小节第一拍重音，段落间也有差异。"""
    value = base
    if step == 0:
        value += 6
    value -= index * 3
    if section_name == "Bridge":
        value -= 6
    return max(1, min(127, value))


def generate(
    context: SongContext,
    voices: tuple[str, ...] = ("piano",),
    writer: Writer | None = None,
) -> tuple[Writer, dict[str, list[tuple[int, int, int, int]]]]:
    """按织体列表生成，分层叠在同一份 SMF 里（每织体一条轨）。"""
    if not voices:
        raise ChordError("至少要指定一种织体")
    writer = writer or Writer(
        bpm=context.bpm,
        beats_per_bar=context.beats_per_bar,
        beat_unit=context.beat_unit,
    )
    collected: dict[str, list[tuple[int, int, int, int]]] = {}
    for voice in voices:
        writer, notes = generate_voice(context, voice, writer=writer)
        collected[voice] = notes
    return writer, collected


def to_bytes(context: SongContext, voices: tuple[str, ...] = ("piano",)) -> bytes:
    writer, _ = generate(context, voices=voices)
    return writer.to_bytes()
