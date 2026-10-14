"""断言 ①：产物是合法 MIDI —— 头校验 + writer 回读。"""

from __future__ import annotations

import struct

import pytest

from midi import writer


def _smf_header(data: bytes) -> tuple[int, int, int]:
    assert data[:4] == b"MThd"
    length = struct.unpack(">I", data[4:8])[0]
    assert length == 6
    fmt, ntrks, tpq = struct.unpack(">HHH", data[8:14])
    return fmt, ntrks, tpq


def test_header_is_standard_smf():
    w = writer.Writer(bpm=120, beats_per_bar=4)
    track = w.add_track("drums", channel=writer.DRUM_CHANNEL, instrument="drums")
    track.note_on(w.beat_ticks(0), writer.KICK, 100, w.beat_ticks(1))
    data = w.to_bytes()
    fmt, ntrks, tpq = _smf_header(data)
    assert fmt == 1
    assert ntrks == 2  # conductor + drums
    assert tpq == writer.DEFAULT_TPQ
    # 文件长度与头声明一致
    assert len(data) > 14
    assert data[14:18] == b"MTrk"


def test_every_track_ends_with_eot():
    w = writer.Writer()
    t = w.add_track("bass", channel=1, instrument="bass")
    t.note_on(0, 40, 90, 480)
    data = w.to_bytes()
    pos = 14
    tracks = 0
    while pos < len(data):
        assert data[pos : pos + 4] == b"MTrk"
        length = struct.unpack(">I", data[pos + 4 : pos + 8])[0]
        chunk = data[pos + 8 : pos + 8 + length]
        assert chunk.endswith(b"\xFF\x2F\x00"), "每个 MTrk 必须以 EOT 收尾"
        pos += 8 + length
        tracks += 1
    assert tracks == 2
    assert pos == len(data), "不应有游离于 chunk 之外的字节"


def test_conductor_track_carries_tempo_and_time_signature():
    data = writer.Writer(bpm=90, beats_per_bar=3).to_bytes()
    assert b"\xFF\x51\x03" in data, "缺少 Tempo meta"
    tempo = round(60_000_000 / 90)
    assert tempo.to_bytes(3, "big") in data
    assert b"\xFF\x58\x04" in data, "缺少 TimeSignature meta"
    assert bytes([3, 2, 24, 8]) in data, "3/4 拍号编码错误"


def test_roundtrip_preserves_notes():
    w = writer.Writer(bpm=120)
    t = w.add_track("piano", channel=2, instrument="piano")
    t.note_on(0, 60, 80, 480)
    t.note_on(480, 64, 90, 240)
    t.note_on(960, 67, 100, 960)
    data = w.to_bytes()
    notes = w.roundtrip_notes(data)
    assert [(n.start, n.pitch, n.velocity, n.duration) for n in notes] == [
        (0, 60, 80, 480),
        (480, 64, 90, 240),
        (960, 67, 100, 960),
    ]
    assert all(n.channel == 2 for n in notes)


def test_roundtrip_is_channel_aware():
    w = writer.Writer()
    drums = w.add_track("drums", channel=writer.DRUM_CHANNEL)
    bass = w.add_track("bass", channel=1)
    drums.note_on(0, writer.KICK, 110, 120)
    bass.note_on(0, 36, 90, 480)  # 与鼓同音高、不同通道
    notes = w.roundtrip_notes(w.to_bytes())
    assert {(n.channel, n.pitch) for n in notes} == {(9, 36), (1, 36)}


def test_simultaneous_same_pitch_notes_do_not_mix_up():
    w = writer.Writer()
    t = w.add_track("piano", channel=2)
    t.note_on(0, 60, 70, 480)
    t.note_on(0, 60, 90, 960)  # 重叠同音（钢琴常见）
    notes = sorted(w.roundtrip_notes(w.to_bytes()), key=lambda n: n.duration)
    assert [n.duration for n in notes] == [480, 960]


def test_truncated_file_is_rejected():
    data = writer.Writer().to_bytes()
    with pytest.raises(writer.MidiError):
        writer.read_notes(data[:12])


def test_bad_magic_is_rejected():
    with pytest.raises(writer.MidiError):
        writer.read_notes(b"NOTMIDI\x00\x00\x00\x06\x00\x00\x00\x01\x01\xe0")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"bpm": 0},
        {"beats_per_bar": 0},
        {"beat_unit": 5},
        {"tpq": 0},
        {"tpq": 0x8000},
        {"fmt": 2},
    ],
)
def test_invalid_writer_params_rejected(kwargs):
    with pytest.raises(writer.MidiError):
        writer.Writer(**kwargs)


@pytest.mark.parametrize(
    "pitch,velocity,duration",
    [(128, 100, 10), (-1, 100, 10), (60, 0, 10), (60, 128, 10), (60, 100, 0),
     (60, 100, -5)],
)
def test_invalid_note_params_rejected(pitch, velocity, duration):
    w = writer.Writer()
    t = w.add_track("x", channel=0)
    with pytest.raises(writer.MidiError):
        t.note_on(0, pitch, velocity, duration)


@pytest.mark.parametrize("channel", [-1, 16])
def test_invalid_channel_rejected(channel):
    with pytest.raises(writer.MidiError):
        writer.Track("x", channel=channel)


def test_vlq_encoding_known_values():
    assert writer._vlq(0) == b"\x00"
    assert writer._vlq(0x7F) == b"\x7f"
    assert writer._vlq(0x80) == b"\x81\x00"
    assert writer._vlq(0x4000) == b"\x81\x80\x00"
    assert writer._vlq(0x0FFFFFFF) == b"\xff\xff\xff\x7f"
