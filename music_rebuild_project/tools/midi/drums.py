"""鼓 MIDI 生成（#52）。

要点：
  - **主歌 / 副歌各自独立 pattern**（不是同一 pattern 换音量）；
  - **力度有变化**：每一下都有明确的 accent 规则，velocity 非恒定；
  - 段落切换处 pattern 不同 —— 测试里直接被断言（#52 + G4-6 结构可辨的前置）。

产物是「可导入 DAW 的 MIDI 骨架」，不是混音成品。
"""

from __future__ import annotations

from dataclasses import dataclass

from .sections import SongContext
from .writer import (
    CLAP,
    CLOSED_HAT,
    CRASH,
    DRUM_CHANNEL,
    KICK,
    OPEN_HAT,
    RIDE,
    SNARE,
    TOM_HIGH,
    TOM_LOW,
    TOM_MID,
    Track,
    Writer,
)

#: 一套 pattern 的「一步」= 十六分音符。
STEPS_PER_BEAT = 4


@dataclass(frozen=True)
class DrumHit:
    """pattern 中的一步：相对小节内的十六分位置 + 音高 + 力度。"""

    step: int
    pitch: int
    velocity: int


@dataclass(frozen=True)
class DrumPattern:
    """一个段落用的鼓型。

    `hits` 里的 `step` 是**小节内**的十六分步号（0..steps_per_bar-1）；
    生成时对每个小节平铺一次，因此 pattern 本身与段落长度无关。
    """

    name: str
    hits: tuple[DrumHit, ...]
    steps_per_bar: int = 16

    def display(self) -> str:
        return self.name


def _hit(beat: float, sub: int, pitch: int, velocity: int) -> DrumHit:
    return DrumHit(step=int(round(beat * STEPS_PER_BEAT)) + sub, pitch=pitch, velocity=velocity)


# --------------------------------------------------------------------------
# 内置 pattern（主歌 / 副歌 / 前副歌 / 桥 / 头尾）
# --------------------------------------------------------------------------

def verse_pattern() -> DrumPattern:
    """主歌：留白、克制。底鼓 1 与 3 拍半，小鼓 2/4 拍，八分踩镲，力度内敛。"""
    hits = [
        _hit(0, 0, KICK, 96),
        _hit(1, 0, SNARE, 84),
        _hit(1, 2, CLOSED_HAT, 62),
        _hit(2, 0, KICK, 88),
        _hit(2, 2, KICK, 70),
        _hit(3, 0, SNARE, 86),
        _hit(3, 2, CLOSED_HAT, 58),
    ]
    for step in range(0, 16, 2):
        if step % 4:
            hits.append(DrumHit(step, CLOSED_HAT, 54))
    return DrumPattern("verse", tuple(sorted(hits, key=lambda h: (h.step, h.pitch))))


def chorus_pattern() -> DrumPattern:
    """副歌：密度更高、backbeat 加重、开镲与撞击镲撑开。"""
    hits = [
        _hit(0, 0, KICK, 118),
        _hit(0, 0, CRASH, 120),
        _hit(1, 0, SNARE, 112),
        _hit(1, 2, OPEN_HAT, 92),
        _hit(2, 0, KICK, 108),
        _hit(2, 2, KICK, 104),
        _hit(3, 0, SNARE, 114),
        _hit(3, 2, SNARE, 78),
    ]
    for step in range(0, 16, 2):
        hits.append(DrumHit(step, CLOSED_HAT, 78 if step % 4 == 0 else 66))
    return DrumPattern("chorus", tuple(sorted(hits, key=lambda h: (h.step, h.pitch))))


def pre_chorus_pattern() -> DrumPattern:
    """前副歌：反向留白 + 末尾过门感（底鼓递进、小鼓渐强）。"""
    hits = [
        _hit(0, 0, KICK, 92),
        _hit(1, 0, SNARE, 88),
        _hit(2, 0, KICK, 96),
        _hit(2, 2, SNARE, 96),
        _hit(3, 0, SNARE, 104),
        _hit(3, 1, SNARE, 110),
        _hit(3, 2, SNARE, 116),
        _hit(3, 3, SNARE, 122),
    ]
    for step in range(0, 16, 4):
        hits.append(DrumHit(step, RIDE, 72))
    return DrumPattern("pre-chorus", tuple(sorted(hits, key=lambda h: (h.step, h.pitch))))


def bridge_pattern() -> DrumPattern:
    """桥段：几乎裸鼓，落点稀疏，靠力度差制造呼吸。"""
    hits = [
        _hit(0, 0, KICK, 84),
        _hit(2, 0, SNARE, 90),
        _hit(3, 2, TOM_LOW, 76),
        _hit(3, 3, TOM_MID, 82),
    ]
    return DrumPattern("bridge", tuple(sorted(hits, key=lambda h: (h.step, h.pitch))))


def intro_pattern() -> DrumPattern:
    """Intro：只给拍点，避免开场就砸。"""
    hits = [
        _hit(0, 0, KICK, 88),
        _hit(2, 0, KICK, 80),
        _hit(1, 2, CLOSED_HAT, 56),
        _hit(3, 2, CLOSED_HAT, 60),
    ]
    return DrumPattern("intro", tuple(sorted(hits, key=lambda h: (h.step, h.pitch))))


def outro_pattern() -> DrumPattern:
    """Outro：收尾，撞击镲 + 底鼓，末拍给 tom 下行。"""
    hits = [
        _hit(0, 0, CRASH, 104),
        _hit(0, 0, KICK, 100),
        _hit(2, 0, KICK, 94),
        _hit(3, 0, TOM_HIGH, 88),
        _hit(3, 1, TOM_MID, 84),
        _hit(3, 2, TOM_LOW, 80),
    ]
    return DrumPattern("outro", tuple(sorted(hits, key=lambda h: (h.step, h.pitch))))


#: 段落名 → pattern。段落没有专属 pattern 时回落到主歌。
PATTERNS: dict[str, DrumPattern] = {
    "Intro": intro_pattern(),
    "Verse": verse_pattern(),
    "Pre-chorus": pre_chorus_pattern(),
    "Chorus": chorus_pattern(),
    "Bridge": bridge_pattern(),
    "Outro": outro_pattern(),
}


def pattern_for(section_name: str) -> DrumPattern:
    try:
        return PATTERNS[section_name]
    except KeyError as exc:
        raise KeyError(f"没有为段落 {section_name!r} 定义鼓 pattern") from exc


# --------------------------------------------------------------------------
# 生成
# --------------------------------------------------------------------------

def _accent(velocity: int, bar: int, step: int) -> int:
    """小节层面的重音修饰：每 4 小节一次的落点略微抬起，第一小节最弱。

    保证即使同一 pattern 反复，力度序列也不是常量。
    """
    if step == 0:
        velocity += (0, 4, 6, 8)[bar % 4]
    return max(1, min(127, velocity))


def generate(context: SongContext, writer: Writer | None = None) -> tuple[Writer, list[DrumHit]]:
    """按段落生成鼓轨。

    返回 `(writer, hits)`；`hits` 里保留每个实际落点的上下文，便于测试断言。
    同一 `context` 两次调用产出完全相同的事件序列。
    """
    context.validate()
    assert context.plan is not None
    writer = writer or Writer(
        bpm=context.bpm,
        beats_per_bar=context.beats_per_bar,
        beat_unit=context.beat_unit,
    )
    track: Track = writer.add_track("drums", channel=DRUM_CHANNEL, instrument="drums")
    step_ticks = writer.beat_ticks(1) // STEPS_PER_BEAT
    recorded: list[DrumHit] = []

    for section in context.plan.sections:
        pattern = pattern_for(section.name)
        for bar in range(section.start_bar, section.end_bar):
            base = writer.bar(bar)
            for hit in pattern.hits:
                pitch_steps = hit.step
                # 跨小节的 pattern（steps_per_bar 与拍号不符）时按取模落位。
                if pattern.steps_per_bar != context.beats_per_bar * STEPS_PER_BEAT:
                    pitch_steps %= context.beats_per_bar * STEPS_PER_BEAT
                velocity = _accent(hit.velocity, bar, hit.step)
                tick = base + pitch_steps * step_ticks
                track.note_on(tick, hit.pitch, velocity, step_ticks)
                recorded.append(DrumHit(hit.step, hit.pitch, velocity))
    return writer, recorded


def to_bytes(context: SongContext) -> bytes:
    """便捷入口：直接产出 SMF 字节。"""
    writer, _ = generate(context)
    return writer.to_bytes()
