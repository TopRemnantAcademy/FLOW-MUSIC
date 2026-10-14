"""段落模型（#51）与其文件读取（#48/#50）的自验。"""

from __future__ import annotations

import pytest

from midi import sections


def test_full_plan_covers_bars_without_gaps():
    plan = sections.SectionPlan.from_bars(
        [("Intro", 4), ("Verse", 8), ("Chorus", 8), ("Outro", 4)]
    )
    plan.validate(total_bars=24)
    assert plan.total_bars == 24
    assert [s.name for s in plan.sections] == ["Intro", "Verse", "Chorus", "Outro"]
    for bar in range(24):
        assert plan.section_at(bar) is not None


def test_gap_is_rejected():
    plan = sections.SectionPlan(
        [
            sections.Section("Intro", 0, 4),
            sections.Section("Verse", 5, 9),  # 第 4 小节缺失
        ]
    )
    with pytest.raises(sections.SectionError):
        plan.validate()


def test_overlap_is_rejected():
    plan = sections.SectionPlan(
        [sections.Section("Intro", 0, 5), sections.Section("Verse", 4, 8)]
    )
    with pytest.raises(sections.SectionError):
        plan.validate()


def test_mismatch_with_total_bars_is_rejected():
    plan = sections.SectionPlan.from_bars([("Verse", 8)])
    with pytest.raises(sections.SectionError):
        plan.validate(total_bars=10)


def test_section_bar_bounds_are_half_open():
    section = sections.Section("Verse", 4, 12)
    assert section.contains(4)
    assert section.contains(11)
    assert not section.contains(12)
    assert section.bars == 8


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("verse", "Verse"),
        ("V", "Verse"),
        ("Pre_Chorus", "Pre-chorus"),
        ("pre chorus", "Pre-chorus"),
        ("CHORUS", "Chorus"),
        ("coda", "Outro"),
        ("Intro", "Intro"),
    ],
)
def test_section_name_aliases(raw, expected):
    assert sections.normalize_section_name(raw) == expected


def test_unknown_section_name_rejected():
    with pytest.raises(sections.SectionError):
        sections.normalize_section_name("Solo")


def test_parse_sections_md_colon_form():
    plan = sections.parse_sections_md(
        """
        # 段落
        Intro: 1-4
        Verse: 5-12
        Chorus: 13-20
        Outro: 21-24
        """
    )
    assert plan.total_bars == 24
    assert plan.sections[1].name == "Verse"
    assert (plan.sections[1].start_bar, plan.sections[1].end_bar) == (4, 12)


def test_parse_sections_md_table_form_and_chinese():
    plan = sections.parse_sections_md(
        """
        | 段落 | 起始小节 | 结束小节 |
        |---|---|---|
        | Intro | 1 | 2 |
        | Verse | 3 | 10 |
        | Chorus | 11 | 18 |
        """
    )
    plan.validate(total_bars=18)
    assert plan.sections[0].name == "Intro"
    assert plan.sections[0].bars == 2


def test_parse_sections_md_rejects_empty():
    with pytest.raises(sections.SectionError):
        sections.parse_sections_md("# 只有标题\n")


def test_parse_chords_txt_converts_to_zero_based():
    chords = sections.parse_chords_txt(
        """
        # 逐小节和弦
        1: F
        2: F
        3: Bb
        4: C
        """
    )
    assert chords == {0: "F", 1: "F", 2: "Bb", 3: "C"}


def test_parse_chords_txt_rejects_duplicates():
    with pytest.raises(sections.SectionError):
        sections.parse_chords_txt("1: F\n1: C\n")


def test_key_degrees_and_triads():
    c_major = sections.KeySignature("C")
    assert [c_major.degree(i) for i in range(7)] == [0, 2, 4, 5, 7, 9, 11]
    assert c_major.triad(0) == (0, 4, 7)  # C
    assert c_major.triad(5) == (9, 12, 16)  # Am（跨八度上行）
    assert c_major.pitch(0, octave=4) == 60
    a_minor = sections.KeySignature("A", "minor")
    assert [a_minor.degree(i) for i in range(7)] == [9, 11, 12, 14, 16, 17, 19]


@pytest.mark.parametrize("bad", [("H",), ("C", "dorian")])
def test_invalid_key_rejected(bad):
    with pytest.raises(sections.SectionError):
        sections.KeySignature(*bad)


def test_key_from_fifths():
    assert sections.key_from_fifths(0).root == "C"
    assert sections.key_from_fifths(-1).root == "F"
    assert sections.key_from_fifths(1, "minor") == sections.KeySignature("E", "minor")
    with pytest.raises(sections.SectionError):
        sections.key_from_fifths(9)


def test_read_musicxml_context_minimal(tmp_path):
    """只读所需字段：调号 / 拍号 / tempo / 小节数。"""
    xml = """<?xml version="1.0" encoding="UTF-8"?>
    <score-partwise version="4.0">
      <part id="P1">
        <measure number="1">
          <attributes>
            <divisions>2</divisions>
            <key><fifths>-1</fifths><mode>major</mode></key>
            <time><beats>3</beats><beat-type>4</beat-type></time>
          </attributes>
          <direction><sound tempo="90"/></direction>
          <note><pitch><step>C</step><octave>4</octave></pitch><duration>1</duration></note>
        </measure>
        <measure number="2"><note><rest/></note></measure>
      </part>
    </score-partwise>
    """
    path = tmp_path / "ground_truth.musicxml"
    path.write_text(xml, encoding="utf-8")
    info = sections.read_musicxml_context(path)
    assert info["beats_per_bar"] == 3
    assert info["beat_unit"] == 4
    assert info["bpm"] == 90
    assert info["fifths"] == -1
    assert info["bars"] == 2
    assert info["key"] == sections.KeySignature("F", "major")


def test_context_seconds_matches_bar_beat_bpm():
    ctx = sections.SongContext(
        key=sections.KeySignature("C"), beats_per_bar=4, bpm=120, bars=32
    ).validate()
    # 32 小节 × 4 拍 × 60 / 120 = 64.0 秒
    assert ctx.seconds() == pytest.approx(64.0)


def test_context_rejects_out_of_range_chord():
    ctx = sections.SongContext(
        key=sections.KeySignature("C"), bars=8, chords={9: "F"}
    )
    with pytest.raises(sections.SectionError):
        ctx.validate()


def test_context_with_explicit_plan_derives_bars():
    plan = sections.SectionPlan.from_bars([("Intro", 2), ("Verse", 6)])
    ctx = sections.SongContext(key=sections.KeySignature("G"), plan=plan)
    assert ctx.bars == 8
    assert ctx.section_at(0).name == "Intro"
    assert ctx.section_at(7).name == "Verse"
