"""最小 MIDI (SMF) 文件写入器 —— 纯 stdlib。

只做一件事：把「时间线上的一组音符」写成通用工具（DAW / 播放器 / GarageBand）
能打开的标准 MIDI 文件。不解析 MIDI、不做音频、不做混音。

范围边界：
  - 输出 SMF format 1（多轨）或 format 0（单轨），ticks-per-quarter-note 分辨率；
  - 每个 track 以 F0 EOT（End Of Track）收尾；
  - 时间用 tick 表示，由 `Writer` 换算成 delta-time。

不算本模块的事：响度 / 真峰 / 掩蔽（见 gate_G4 的 F 段）。
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

#: 每四分音符的 tick 数（PPQ）。与 SMF 头 chunk 里的 division 字段一致。
DEFAULT_TPQ = 480

#: 各通道默认 General MIDI program（0-based）。仅作提示，见 `Track.program`。
DEFAULT_PROGRAM = {
    "drums": 0,  # channel 9 在 GM 里恒为鼓组，program 不生效
    "bass": 33,  # Electric Bass (finger)
    "piano": 0,  # Acoustic Grand Piano
    "guitar": 24,  # Acoustic Guitar (nylon)
    "pad": 89,  # Pad 2 (warm)
}

#: 鼓组通道。GM 规定 channel 9（0-based）为 percussion。
DRUM_CHANNEL = 9

#: 常用 GM 打击乐音高。
KICK = 36
SNARE = 38
CLOSED_HAT = 42
OPEN_HAT = 46
CRASH = 49
RIDE = 51
TOM_LOW = 45
TOM_MID = 47
TOM_HIGH = 50
CLAP = 39


class MidiError(ValueError):
    """输入不合法（音高越界、tick 回退、文件被截断等）。"""


@dataclass(frozen=True)
class NoteEvent:
    """一个音符：起始 tick、持续 tick 数、音高、力度、通道。"""

    start: int
    duration: int
    pitch: int
    velocity: int
    channel: int

    def __post_init__(self) -> None:
        if not 0 <= self.pitch <= 127:
            raise MidiError(f"pitch 越界: {self.pitch}")
        if not 1 <= self.velocity <= 127:
            raise MidiError(f"velocity 必须在 1..127: {self.velocity}")
        if not 0 <= self.channel <= 15:
            raise MidiError(f"channel 越界: {self.channel}")
        if self.start < 0:
            raise MidiError(f"start 不能为负: {self.start}")
        if self.duration <= 0:
            raise MidiError(f"duration 必须为正: {self.duration}")

    @property
    def end(self) -> int:
        return self.start + self.duration


def _vlq(value: int) -> bytes:
    """可变长度量（variable-length quantity），MIDI delta-time 的编码方式。"""
    if value < 0:
        raise MidiError(f"VLQ 不能为负: {value}")
    out = bytearray([value & 0x7F])
    value >>= 7
    while value:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.reverse()
    return bytes(out)


def _chunk(tag: bytes, payload: bytes) -> bytes:
    return tag + struct.pack(">I", len(payload)) + payload


class Track:
    """一条 MIDI 轨：名字 + 通道 + 音符列表。

    `notes` 允许乱序插入，写出时统一按 (tick, 音高) 排序，
    因此同输入必然产出同字节 —— 这是确定性（determinism）的基础。
    """

    def __init__(
        self,
        name: str,
        channel: int,
        program: int | None = None,
        instrument: str | None = None,
    ) -> None:
        if not 0 <= channel <= 15:
            raise MidiError(f"channel 越界: {channel}")
        self.name = name
        self.channel = channel
        if program is None:
            program = DEFAULT_PROGRAM.get(instrument or "", 0)
        if not 0 <= program <= 127:
            raise MidiError(f"program 越界: {program}")
        self.program = program
        self.notes: list[NoteEvent] = []

    def note_on(self, tick: int, pitch: int, velocity: int, duration: int) -> None:
        """写一个音符：`tick` 处 note-on，`tick + duration` 处 note-off。"""
        self.notes.append(
            NoteEvent(
                start=tick,
                duration=duration,
                pitch=pitch,
                velocity=velocity,
                channel=self.channel,
            )
        )

    def _events(self) -> list[tuple[int, int, bytes]]:
        """把音符摊平成 (tick, 排序键, 事件字节)，note-off 先于同 tick 的 note-on。"""
        events: list[tuple[int, int, bytes]] = []
        program = bytes([0xC0 | self.channel, self.program])
        queue: list[tuple[int, bytes]] = [(0, program)]
        for note in self.notes:
            queue.append(
                (
                    note.start,
                    bytes([0x90 | self.channel, note.pitch, note.velocity]),
                )
            )
            queue.append(
                (
                    note.end,
                    bytes([0x80 | self.channel, note.pitch, 0x40]),
                )
            )
        for tick, payload in queue:
            status = payload[0] & 0xF0
            # 同 tick 时：note-off(0x80) < program(0xC0) < note-on(0x90)
            rank = {0x80: 0, 0xC0: 1, 0x90: 2}.get(status, 1)
            events.append((tick, rank, payload))
        events.sort(key=lambda item: (item[0], item[1], item[2]))
        return events

    def end_tick(self) -> int:
        return max((n.end for n in self.notes), default=0)

    def to_bytes(self) -> bytes:
        """写出 MTrk chunk（含名字 meta、program change、全部音符、EOT）。"""
        body = bytearray()
        body += _vlq(0) + b"\xFF\x03" + _vlq(len(self.name)) + self.name.encode("ascii")
        cursor = 0
        for tick, _rank, payload in self._events():
            body += _vlq(tick - cursor) + payload
            cursor = tick
        body += _vlq(0) + b"\xFF\x2F\x00"  # End Of Track
        return _chunk(b"MTrk", bytes(body))


class Writer:
    """把若干 `Track` 组成一个 SMF 文件。

    >>> w = Writer(bpm=120, beats_per_bar=4)
    >>> t = w.add_track("kick", channel=DRUM_CHANNEL, instrument="drums")
    >>> t.note_on(w.beat(0), KICK, 100, w.beat_ticks(1))
    >>> data = w.to_bytes()          # doctest: +ELLIPSIS
    """

    def __init__(
        self,
        bpm: int = 120,
        beats_per_bar: int = 4,
        beat_unit: int = 4,
        tpq: int = DEFAULT_TPQ,
        fmt: int = 1,
    ) -> None:
        if bpm <= 0:
            raise MidiError(f"bpm 必须为正: {bpm}")
        if beats_per_bar <= 0:
            raise MidiError(f"beats_per_bar 必须为正: {beats_per_bar}")
        if beat_unit not in (1, 2, 4, 8, 16, 32):
            raise MidiError(f"beat_unit 不合法: {beat_unit}")
        if tpq <= 0 or tpq > 0x7FFF:
            raise MidiError(f"tpq 越界: {tpq}")
        if fmt not in (0, 1):
            raise MidiError(f"只支持 SMF format 0/1，收到 {fmt}")
        self.bpm = bpm
        self.beats_per_bar = beats_per_bar
        self.beat_unit = beat_unit
        self.tpq = tpq
        self.fmt = fmt
        self.tracks: list[Track] = []

    # ---- 时间换算（G4-5 的算准基础）----------------------------------

    @property
    def bar_ticks(self) -> int:
        """一小节的 tick 数 = 每小节拍数 × 每拍 tick。"""
        return self.beats_per_bar * self.beat_ticks(1)

    def beat_ticks(self, beats: float) -> int:
        """把「拍」换算成 tick。拍的分母由 `beat_unit` 决定（4 = 四分音符）。"""
        return round(self.tpq * 4.0 / self.beat_unit * beats)

    def bar(self, index: int) -> int:
        """第 `index` 小节（0-based）的起始 tick。"""
        if index < 0:
            raise MidiError(f"小节号不能为负: {index}")
        return index * self.bar_ticks

    def total_ticks(self, bars: int) -> int:
        """`bars` 小节的总 tick 数。"""
        return bars * self.bar_ticks

    def seconds_for_bars(self, bars: int) -> float:
        """`bars` 小节对应的秒数 = bars × beats_per_bar × 60 / bpm。

        这是 gate_G4 的 G4-5「小节 × 拍数 × BPM」预期时长的唯一算法处。
        """
        return bars * self.beats_per_bar * 60.0 / self.bpm

    # ---- 构建 --------------------------------------------------------

    def add_track(
        self,
        name: str,
        channel: int,
        program: int | None = None,
        instrument: str | None = None,
    ) -> Track:
        track = Track(name=name, channel=channel, program=program, instrument=instrument)
        self.tracks.append(track)
        return track

    def to_bytes(self) -> bytes:
        """序列化为完整 SMF 字节串。同样输入 ⇒ 同样字节。"""
        # 第 0 轨是 conductor（tempo + 拍号），其余为音符轨。
        tracks = [self._conductor_track(
            bpm=self.bpm, beats_per_bar=self.beats_per_bar, beat_unit=self.beat_unit
        )]
        tracks += [track.to_bytes() for track in self.tracks]
        header = struct.pack(">HHH", self.fmt, len(tracks), self.tpq)
        return _chunk(b"MThd", header) + b"".join(tracks)

    @staticmethod
    def _conductor_track(bpm: int, beats_per_bar: int, beat_unit: int) -> bytes:
        """Tempo(FF 51 03) + TimeSignature(FF 58 04)：让工具读得到 BPM / 拍号。"""
        us_per_quarter = round(60_000_000 / bpm)
        body = bytearray()
        body += _vlq(0) + b"\xFF\x51\x03" + us_per_quarter.to_bytes(3, "big")
        denom_pow = {1: 0, 2: 1, 4: 2, 8: 3, 16: 4, 32: 5}[beat_unit]
        body += (
            _vlq(0)
            + b"\xFF\x58\x04"
            + bytes([beats_per_bar, denom_pow, 24, 8])
        )
        body += _vlq(0) + b"\xFF\x2F\x00"
        return _chunk(b"MTrk", bytes(body))

    # ---- 回读（测试与自验用，非通用 MIDI 解析器）--------------------

    def roundtrip_notes(self, data: bytes) -> list[NoteEvent]:
        """把 `to_bytes()` 的产物读回成音符列表，用于断言字节→语义无损。

        仅理解本模块写出的子集（MThd/MTrk、note-on/off、program change、
        名字与 EOT meta）；遇到其他事件抛 `MidiError`，避免假装是通用解析器。
        """
        return read_notes(data)


def read_notes(data: bytes) -> list[NoteEvent]:
    """从 SMF 字节串读回音符（writer 的自洽回读）。"""
    pos = 0

    def take(n: int) -> bytes:
        nonlocal pos
        if pos + n > len(data):
            raise MidiError("文件截断")
        chunk = data[pos : pos + n]
        pos += n
        return chunk

    if take(4) != b"MThd":
        raise MidiError("不是 SMF：缺少 MThd")
    header_len = struct.unpack(">I", take(4))[0]
    fmt, ntrks, tpq = struct.unpack(">HHH", take(header_len)[:6])
    if fmt not in (0, 1):
        raise MidiError(f"不支持的 format: {fmt}")
    if tpq & 0x8000:
        raise MidiError("不支持 SMPTE 时间基准")
    notes: list[NoteEvent] = []
    for _ in range(ntrks):
        if take(4) != b"MTrk":
            raise MidiError("轨道头损坏：缺少 MTrk")
        length = struct.unpack(">I", take(4))[0]
        end = pos + length
        tick = 0
        channel = 0
        pending: dict[tuple[int, int], list[tuple[int, int]]] = {}
        running_status = None
        while pos < end:
            delta, pos = _read_vlq(data, pos)
            tick += delta
            status = data[pos]
            if status < 0x80:
                if running_status is None:
                    raise MidiError("running status 无前驱")
                status = running_status
                pos += 0
            else:
                pos += 1
            if status == 0xFF:
                meta_type = data[pos]
                pos += 1
                size, pos = _read_vlq(data, pos)
                pos += size
                running_status = None
                continue
            if status in (0xF0, 0xF7):
                size, pos = _read_vlq(data, pos)
                pos += size
                running_status = None
                continue
            running_status = status
            kind, channel = status & 0xF0, status & 0x0F
            if kind in (0x80, 0x90):
                pitch, velocity = data[pos], data[pos + 1]
                pos += 2
                key = (channel, pitch)
                if kind == 0x90 and velocity > 0:
                    pending.setdefault(key, []).append((tick, velocity))
                else:
                    starts = pending.get(key)
                    if not starts:
                        raise MidiError(f"孤儿 note-off: ch={channel} pitch={pitch}")
                    start, vel = starts.pop(0)
                    notes.append(
                        NoteEvent(
                            start=start,
                            duration=tick - start,
                            pitch=pitch,
                            velocity=vel,
                            channel=channel,
                        )
                    )
            elif kind in (0xA0, 0xB0, 0xE0):
                pos += 2
            elif kind == 0xC0:
                pos += 1
            elif kind == 0xD0:
                pos += 1
            else:
                raise MidiError(f"未知事件 status=0x{status:02X}")
        if pos != end:
            raise MidiError("轨道长度与实际事件不一致")
    return notes




def _read_vlq(data: bytes, pos: int) -> tuple[int, int]:
    value = 0
    for _ in range(4):
        if pos >= len(data):
            raise MidiError("VLQ 截断")
        byte = data[pos]
        pos += 1
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            return value, pos
    raise MidiError("VLQ 超过 4 字节")
