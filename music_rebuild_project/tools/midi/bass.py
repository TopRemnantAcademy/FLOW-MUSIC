"""贝斯 MIDI 生成（#53）。

两条硬规则（来自 issue）：
  1. **根音跟随和弦** —— 每小节的根音取 `chords.txt` 里的和弦符号；
     没有标记时回落到调内顺阶和弦（I-vi-IV-V 之类的段落走向）。
  2. **与底鼓对齐** —— 贝斯的每个落点必须在鼓轨 kick 的 tick 集合里，
     这是 issue 明确要求的可测试约束，`align_to_kick=True` 时强制成立。

`locked_to` 为正弦口径：鼓若为 None，则用段落默认 kick 落点。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from . import drums
from .sections import SongContext
from .writer import KICK, Track, Writer

#: 贝斯默认通道（0-based）。
BASS_CHANNEL = 1

#: 和弦符号解析：根音 + 可选品质 + 可选低音斜杠。
_CHORD = re.compile(
    r"^(?P<root>[A-Ga-g][#b]?)"
    r"(?P<quality>maj|min|m|M|dim|aug|sus[24]|7|maj7|m7|\+|-|°)?"
    r"(?:/(?P<bass>[A-Ga-g][#b]?))?$"
)

_PITCH_CLASS = {
    "C": 0, "C#": 1, "DB": 1, "D": 2, "D#": 3, "EB": 3, "E": 4, "FB": 4, "F": 5,
    "E#": 5, "F#": 6, "GB": 6, "G": 7, "G#": 8, "AB": 8, "A": 9, "A#": 10,
    "BB": 10, "B": 11, "CB": 11,
}

#: 段落默认和弦级数走向（0-based 顺阶级数），chords.txt 缺失时兜底。
_DEFAULT_DEGREES = {
    "Intro": (0, 3, 4, 3),
    "Verse": (0, 4, 5, 3),
    "Pre-chorus": (1, 4, 3, 4),
    "Chorus": (0, 3, 4, 0),
    "Bridge": (5, 3, 0, 4),
    "Outro": (0, 3, 0, 0),
}

#: 段落默认节奏型：**偏好**的十六分步落点。实际落点还会被 kick 过滤/吸附，
#: 因为 issue 的硬要求是「与底鼓对齐」—— 鼓没踩的地方贝斯就不该单独冒头。
_DEFAULT_STEPS = {
    "Intro": (0, 8),
    "Verse": (0, 8, 10),
    "Pre-chorus": (0, 8, 12, 14),
    "Chorus": (0, 6, 8, 14),
    "Bridge": (0, 8),
    "Outro": (0, 8),
}

#: 每个优先落点允许吸附到 kick 的最大十六分步距离（超过就丢弃该落点）。
_MAX_SNAP_STEPS = 2

STEPS_PER_BEAT = drums.STEPS_PER_BEAT


class BassError(ValueError):
    """贝斯生成输入不合法。"""


@dataclass(frozen=True)
class ChordSymbol:
    """解析后的和弦：根音半音位置 + 品质 + 可选斜杠低音。"""

    root: int
    quality: str = "maj"
    bass: int | None = None

    @classmethod
    def parse(cls, text: str) -> "ChordSymbol":
        match = _CHORD.match(text.strip())
        if not match:
            raise BassError(f"无法解析和弦符号: {text!r}")
        root_name = match.group("root").upper()
        if root_name not in _PITCH_CLASS:
            raise BassError(f"未知根音: {root_name!r}")
        quality = match.group("quality") or "maj"
        bass_name = match.group("bass")
        bass = _PITCH_CLASS[bass_name.upper()] if bass_name else None
        return cls(root=_PITCH_CLASS[root_name], quality=quality, bass=bass)

    def chord_tones(self) -> tuple[int, int, int]:
        """三和弦构成音（半音位置，未折叠八度）。"""
        root = self.root
        if self.quality in ("m", "min", "-"):
            return root, root + 3, root + 7
        if self.quality in ("dim", "°"):
            return root, root + 3, root + 6
        if self.quality == "aug":
            return root, root + 4, root + 8
        if self.quality == "sus2":
            return root, root + 2, root + 7
        if self.quality == "sus4":
            return root, root + 5, root + 7
        return root, root + 4, root + 7


def bass_pitch(pitch_class: int, octave: int = 2) -> int:
    """半音位置 → 贝斯音域 MIDI 音高（默认 E2 附近）。"""
    pitch = (octave + 1) * 12 + pitch_class % 12
    while pitch > 55:  # 不超过 G3，保持贝斯音区
        pitch -= 12
    while pitch < 28:  # 不低于 E1
        pitch += 12
    return pitch


def _kick_ticks(context: SongContext) -> set[int]:
    """鼓轨所有 kick 落点的 tick 集合（对齐基准）。

    直接读鼓模块生成的**实际事件**，不做二次推算 —— 复算 pattern 会漏掉
    「pattern 在小节间的重音差异」，先前正是这里对不齐。
    """
    drum_writer, _hits = drums.generate(context)
    ticks: set[int] = set()
    for note in drum_writer.tracks[0].notes:
        if note.pitch == KICK:
            ticks.add(note.start)
    return ticks


def _bar_root(context: SongContext, bar: int) -> ChordSymbol:
    symbol = context.chords.get(bar)
    if symbol:
        return ChordSymbol.parse(symbol)
    section = context.section_at(bar)
    degrees = _DEFAULT_DEGREES.get(section.name, (0,))
    degree = degrees[(bar - section.start_bar) % len(degrees)]
    root_pc = context.key.degree(degree) % 12
    return ChordSymbol(root=root_pc, quality="min" if context.is_minor() else "maj")


def generate(
    context: SongContext,
    writer: Writer | None = None,
    align_to_kick: bool = True,
    octave: int = 2,
) -> tuple[Writer, list[tuple[int, int, int]]]:
    """生成贝斯轨。

    返回 `(writer, notes)`，`notes` 为 `(tick, pitch, velocity)` 列表，
    便于测试直接断言「根音跟随和弦」与「与 kick 对齐」。
    """
    context.validate()
    assert context.plan is not None
    writer = writer or Writer(
        bpm=context.bpm,
        beats_per_bar=context.beats_per_bar,
        beat_unit=context.beat_unit,
    )
    track: Track = writer.add_track("bass", channel=BASS_CHANNEL, instrument="bass")
    step_ticks = writer.beat_ticks(1) // STEPS_PER_BEAT
    kicks = _kick_ticks(context) if align_to_kick else None
    notes: list[tuple[int, int, int]] = []

    for section in context.plan.sections:
        steps = _DEFAULT_STEPS.get(section.name, (0, 8))
        for bar in range(section.start_bar, section.end_bar):
            symbol = _bar_root(context, bar)
            base = writer.bar(bar)
            root_pc = symbol.bass if symbol.bass is not None else symbol.root
            pitch = bass_pitch(root_pc, octave)
            placed: set[int] = set()
            for step in steps:
                tick = base + step * step_ticks
                if kicks is not None and tick not in kicks:
                    # 该落点鼓没踩底鼓：吸附到最近的 kick；离太远就干脆不弹。
                    tick = _snap_to_kick(
                        tick, kicks, base, writer.bar_ticks, step_ticks
                    )
                    if tick is None:
                        continue
                if tick in placed:
                    # 吸附后与前一落点重合：同一 tick 不重复触发（避免 doubles）。
                    continue
                placed.add(tick)
                velocity = _bass_velocity(section.name, step)
                duration = step_ticks * (3 if section.name == "Chorus" else 1)
                track.note_on(tick, pitch, velocity, duration)
                notes.append((tick, pitch, velocity))
    notes.sort()
    return writer, notes


def _snap_to_kick(
    tick: int,
    kicks: set[int],
    bar_start: int,
    bar_ticks: int,
    step_ticks: int,
) -> int | None:
    """把 tick 吸附到同一小节内最近的 kick（最多 `_MAX_SNAP_STEPS` 步）。

    宁可少弹一个音，也不脱离底鼓 —— 对齐是硬约束，密度是软目标。
    """
    candidates = [k for k in kicks if bar_start <= k < bar_start + bar_ticks]
    if not candidates:
        return None
    nearest = min(candidates, key=lambda k: (abs(k - tick), k))
    if abs(nearest - tick) > _MAX_SNAP_STEPS * step_ticks:
        return None
    return nearest


def _bass_velocity(section_name: str, step: int) -> int:
    """段落基础力度 + 落点重音；保证序列非常量。"""
    base = {
        "Intro": 76,
        "Verse": 88,
        "Pre-chorus": 96,
        "Chorus": 112,
        "Bridge": 72,
        "Outro": 80,
    }.get(section_name, 88)
    return max(1, min(127, base + (6 if step == 0 else -4)))


def to_bytes(context: SongContext, align_to_kick: bool = True) -> bytes:
    writer, _ = generate(context, align_to_kick=align_to_kick)
    return writer.to_bytes()
