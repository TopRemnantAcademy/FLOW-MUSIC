"""`render_demo` CLI 的端到端自验：真写文件、真读回、真校验长度。"""

from __future__ import annotations

import json
import struct

from midi import render_demo
from midi.writer import read_notes

SECTIONS_MD = """
# song01 段落标注（#51）

| 段落 | 起始小节 | 结束小节 |
|---|---|---|
| Intro | 1 | 4 |
| Verse | 5 | 12 |
| Chorus | 13 | 20 |
| Outro | 21 | 24 |
"""

CHORDS_TXT = """
# 逐小节和弦（#50）
1: F
2: Bb
3: C
4: F
5: Dm
6: Bb
7: C
8: F
"""


def _header(data: bytes):
    assert data[:4] == b"MThd"
    return struct.unpack(">HHH", data[8:14])


def test_render_demo_writes_all_parts_and_valid_midi(tmp_path):
    sections_md = tmp_path / "sections.md"
    sections_md.write_text(SECTIONS_MD, encoding="utf-8")
    chords_txt = tmp_path / "chords.txt"
    chords_txt.write_text(CHORDS_TXT, encoding="utf-8")
    out = tmp_path / "midi"

    rc = render_demo.main(
        ["--sections", str(sections_md), "--chords", str(chords_txt),
         "--key", "F", "--tempo", "120", "--out", str(out)]
    )
    assert rc == 0
    for name in ("song_arrangement.mid", "drums.mid", "bass.mid",
                 "chords_piano.mid", "chords_guitar.mid", "chords_pad.mid"):
        data = (out / name).read_bytes()
        fmt, ntrks, tpq = _header(data)
        assert fmt == 1
        assert ntrks >= 2
        assert tpq == 480
        assert read_notes(data), f"{name} 没有音符"


def test_render_demo_preview_matches_bar_beat_bpm(tmp_path):
    sections_md = tmp_path / "sections.md"
    sections_md.write_text(SECTIONS_MD, encoding="utf-8")
    out = tmp_path / "midi"
    render_demo.main(
        ["--sections", str(sections_md), "--key", "F", "--tempo", "120",
         "--out", str(out)]
    )
    preview = json.loads((out / "arrangement_preview.json").read_text(encoding="utf-8"))
    assert preview["bars"] == 24
    assert preview["beats_per_bar"] == 4
    assert preview["bpm"] == 120
    # 24 × 4 × 60 / 120 = 48.0 秒
    assert preview["expected_seconds"] == 48.0
    assert [s["name"] for s in preview["sections"]] == ["Intro", "Verse", "Chorus", "Outro"]
    assert preview["sections"][1]["start_seconds"] == 8.0
    assert preview["sections"][1]["start_bar_0based"] == 4


def test_render_demo_without_inputs_uses_default_plan(tmp_path):
    out = tmp_path / "midi"
    render_demo.main(["--out", str(out)])
    preview = json.loads((out / "arrangement_preview.json").read_text(encoding="utf-8"))
    assert [s["name"] for s in preview["sections"]] == [
        "Intro", "Verse", "Pre-chorus", "Chorus", "Bridge", "Outro"
    ]
    assert preview["expected_seconds"] == 64.0


def test_render_demo_is_deterministic(tmp_path):
    out_a, out_b = tmp_path / "a", tmp_path / "b"
    render_demo.main(["--key", "G", "--tempo", "96", "--out", str(out_a)])
    render_demo.main(["--key", "G", "--tempo", "96", "--out", str(out_b)])
    for name in ("song_arrangement.mid", "drums.mid", "bass.mid", "chords_piano.mid"):
        assert (out_a / name).read_bytes() == (out_b / name).read_bytes()


def test_build_context_reads_chords(tmp_path):
    chords_txt = tmp_path / "chords.txt"
    chords_txt.write_text("1: C\n2: Am\n", encoding="utf-8")
    ctx = render_demo.build_context(
        None, chords_txt, key="C", tempo=100, beats_per_bar=4, bars=None
    )
    assert ctx.chords == {0: "C", 1: "Am"}
