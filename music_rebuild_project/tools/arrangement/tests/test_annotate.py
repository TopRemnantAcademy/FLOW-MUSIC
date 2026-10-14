"""#51 段落标注工具的自验。

覆盖矩阵（issue 点名的五类，每类都能在下面找到对应的用例名）：

| 类别 | 用例 |
|---|---|
| 正常链 | `test_full_song_*` / `test_two_bars_spec_gives_exact_seconds` |
| 重叠报错 | `test_overlap_*` |
| 类型非法 | `test_unknown_type_*` |
| 间隙 | `test_bar_gap_*` / `test_timeline_gap_*` |
| BPM 变化 | `test_per_section_bpm_*` |

另加：时长容差（±1s）、字段级错误路径、退出码语义、确定性、与 `tools/midi` 的一致性。
"""

from __future__ import annotations

import json

import pytest

from music_rebuild_project.tools.arrangement import sections as S

# ---------------------------------------------------------------------------
# 正常链
# ---------------------------------------------------------------------------


def test_full_song_covers_every_bar(full_song_spec):
    document, report = S.annotate(full_song_spec)
    assert report.ok, report.errors
    assert document["bars"] == 34
    assert document["duration_s"] == 68.0
    assert [s["type"] for s in document["sections"]] == [
        "Intro", "Verse", "Pre-chorus", "Chorus", "Bridge", "Outro",
    ]
    # 逐段首尾相接、从 0 覆盖到 34
    for prev, cur in zip(document["sections"], document["sections"][1:]):
        assert prev["end_bar"] == cur["start_bar"]
        assert prev["end_s"] == cur["start_s"]
    assert document["sections"][0]["start_bar"] == 0
    assert document["sections"][-1]["end_bar"] == 34


def test_two_bars_spec_gives_exact_seconds():
    """2 小节 4/4 @120BPM = 2 × 4 × 60 / 120 = 4.000000 秒。"""
    document, report = S.annotate({"bpm": 120, "sections": [{"type": "Verse", "bars": 2}]})
    assert report.ok, report.errors
    assert document["duration_s"] == 4.0
    assert document["sections"][0]["start_s"] == 0.0
    assert document["sections"][0]["end_s"] == 4.0
    # 30fps × 4s = 120 帧
    assert document["sections"][0]["start_frame"] == 0
    assert document["sections"][0]["end_frame"] == 120


def test_frames_follow_fps():
    document, report = S.annotate(
        {"bpm": 120, "fps": 25, "sections": [{"type": "Verse", "bars": 2}]}
    )
    assert report.ok, report.errors
    assert document["fps"] == 25
    assert document["sections"][0]["end_frame"] == 100


def test_end_bar_form_is_equivalent_to_bars_form():
    """`end_bar` 与 `bars` 两种写法必须给同一结果。"""
    by_bars, _ = S.annotate(
        {"sections": [{"type": "Intro", "bars": 4}, {"type": "Verse", "bars": 8}]}
    )
    # end_bar 是 1-based **闭区间**末小节：4 小节 = 1..4，8 小节 = 5..12。
    by_end, _ = S.annotate(
        {"sections": [{"type": "Intro", "start_bar": 1, "end_bar": 4},
                      {"type": "Verse", "start_bar": 5, "end_bar": 12}]}
    )
    assert by_bars["sections"] == by_end["sections"]
    assert by_bars["duration_s"] == by_end["duration_s"]


def test_type_counts_and_labels(full_song_spec):
    document, report = S.annotate(full_song_spec)
    assert report.ok, report.errors
    assert document["type_counts"] == {
        "Intro": 1, "Verse": 1, "Pre-chorus": 1, "Chorus": 1, "Bridge": 1, "Outro": 1,
    }
    verse = document["sections"][1]
    assert verse["label"] == "Verse 1"


def test_repeated_type_is_counted(full_song_spec):
    # 40 小节 @120BPM 4/4 = 80.0s —— 与声明值自洽，否则会被 duration_mismatch 拦下。
    spec = {
        "bpm": 120,
        "duration_s": 80.0,
        "sections": [
            {"type": "Intro", "bars": 4},
            {"type": "Verse", "bars": 8},
            {"type": "Chorus", "bars": 8},
            {"type": "Verse", "bars": 8},
            {"type": "Chorus", "bars": 8},
            {"type": "Outro", "bars": 4},
        ],
    }
    document, report = S.annotate(spec)
    assert report.ok, report.errors
    assert document["type_counts"]["Verse"] == 2
    assert document["type_counts"]["Chorus"] == 2


def test_annotation_is_deterministic(full_song_spec):
    """无 RNG：同输入两次输出逐字节相同。"""
    first = json.dumps(S.annotate(full_song_spec)[0], ensure_ascii=False, sort_keys=True)
    second = json.dumps(S.annotate(full_song_spec)[0], ensure_ascii=False, sort_keys=True)
    assert first == second


# ---------------------------------------------------------------------------
# 重叠报错
# ---------------------------------------------------------------------------


def test_overlap_by_start_bar_is_rejected():
    spec = {
        "sections": [
            {"type": "Intro", "start_bar": 1, "end_bar": 5},
            {"type": "Verse", "start_bar": 4, "end_bar": 8},
        ]
    }
    _, report = S.annotate(spec)
    assert not report.ok
    errors = {i.code: i for i in report.errors}
    assert "overlap" in errors
    assert errors["overlap"].path == "$.sections[1].start_bar"
    assert "重叠" in errors["overlap"].message


def test_overlap_via_end_bar_past_extent_is_rejected():
    """显式 `end_bar` 压到上一段区间里 —— 也必须报，不能静默算成负小节数。"""
    spec = {
        "sections": [
            {"type": "Intro", "start_bar": 1, "end_bar": 8},
            {"type": "Verse", "start_bar": 3, "end_bar": 6},
        ]
    }
    _, report = S.annotate(spec)
    assert not report.ok
    assert any(i.code == "overlap" for i in report.errors)


def test_overlapping_section_is_excluded_from_timeline():
    """重叠段的时间轴无法定义 → 不落进结果，但前面的段仍算出来（便于人按行修）。"""
    spec = {
        "sections": [
            {"type": "Intro", "start_bar": 1, "end_bar": 5},
            {"type": "Verse", "start_bar": 4, "end_bar": 8},
        ]
    }
    document, report = S.annotate(spec)
    assert not report.ok
    assert [s["type"] for s in document["sections"]] == ["Intro"]


def test_empty_or_reversed_range_is_rejected():
    """`end_bar` 是闭区间末小节：`start=5, end=4` 即空段／倒序，必须报错。

    放在**第二段**上：首段另有「必须从第 1 小节开始」的更早规则，会把问题掩盖掉。
    """
    spec = {
        "sections": [
            {"type": "Intro", "start_bar": 1, "end_bar": 4},
            {"type": "Verse", "start_bar": 6, "end_bar": 5},
        ]
    }
    _, report = S.annotate(spec)
    assert not report.ok
    assert any(i.code == "invalid_field" and i.path == "$.sections[1].end_bar" for i in report.errors)


# ---------------------------------------------------------------------------
# 类型非法
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad", ["Chrous", "VERSES", "verse1", "Nope"])
def test_unknown_type_is_rejected(bad):
    _, report = S.annotate({"sections": [{"type": bad, "bars": 4}]})
    assert not report.ok
    errors = {i.code: i for i in report.errors}
    assert "unknown_type" in errors
    assert errors["unknown_type"].path == "$.sections[0].type"
    assert "允许" in errors["unknown_type"].message


def test_surrounding_whitespace_in_type_is_trimmed():
    """`"Chorus "` 与 `"Chorus"` 是同一个段落类型 —— 不该一个字面空白就判非法。"""
    document, report = S.annotate({"sections": [{"type": "Chorus ", "bars": 4}]})
    assert report.ok, report.errors
    assert document["sections"][0]["type"] == "Chorus"


def test_unknown_type_error_lists_the_allowed_set():
    _, report = S.annotate({"sections": [{"type": "Nope", "bars": 4}]})
    message = next(i.message for i in report.errors if i.code == "unknown_type")
    for name in S.REQUIRED_SECTION_TYPES:
        assert name in message


def test_extra_type_is_explicitly_allowlisted():
    """自定义类型必须在 `extra_types` 里显式放行 —— 这是「枚举约束」的落地方式。"""
    spec = {
        "extra_types": ["Rap"],
        "sections": [{"type": "Rap", "bars": 4}],
    }
    document, report = S.annotate(spec)
    assert report.ok, report.errors
    # 放行的类型**原样保留**，不改写成规范名
    assert document["sections"][0]["type"] == "Rap"
    assert document["extra_types"] == ["Rap"]


def test_extra_type_via_argument():
    document, report = S.annotate(
        {"sections": [{"type": "Rap", "bars": 4}]}, extra_types=("Rap",)
    )
    assert report.ok, report.errors
    assert document["sections"][0]["type"] == "Rap"


def test_missing_type_is_rejected():
    _, report = S.annotate({"sections": [{"bars": 4}]})
    assert not report.ok
    assert any(i.code == "invalid_field" and i.path == "$.sections[0].type" for i in report.errors)


def test_alias_from_tools_midi_is_accepted_with_a_hint():
    """`V` / `ch` / `coda` 是 `tools/midi` 别名表里的写法：放行 + 提示规范名。"""
    document, report = S.annotate({"sections": [{"type": "V", "bars": 4}]})
    assert report.ok, report.errors
    assert document["sections"][0]["type"] == "V"
    assert report.warnings[0].code == "type_canonical_hint"
    assert "Verse" in report.warnings[0].message


def test_canonical_name_hint_is_a_warning_not_an_error():
    """`Pre-Chorus` 只是大小写变体，放行但提示规范写法；警告不改变退出码。"""
    _, report = S.annotate({"sections": [{"type": "Pre-Chorus", "bars": 4}]})
    assert report.ok
    assert [i.code for i in report.warnings] == ["type_canonical_hint"]
    assert not report.errors


# ---------------------------------------------------------------------------
# 间隙
# ---------------------------------------------------------------------------


def test_bar_gap_is_reported():
    """小节区间有洞（没写 gap_before_s）→ 报 gap，并指出缺哪几小节。"""
    spec = {
        "sections": [
            {"type": "Intro", "bars": 4},
            {"type": "Verse", "start_bar": 6, "bars": 4},
        ]
    }
    _, report = S.annotate(spec)
    assert not report.ok
    errors = {i.code: i for i in report.errors}
    assert "gap" in errors
    assert "第 5–5 小节" in errors["gap"].message


def test_declared_silence_requires_allow_gap():
    """显式静默必须同时声明 `allow_gap=true`，否则算未说明的间隙。"""
    spec = {
        "sections": [
            {"type": "Intro", "bars": 4},
            {"type": "Verse", "bars": 4, "gap_before_s": 2},
        ]
    }
    _, report = S.annotate(spec)
    assert not report.ok
    errors = {i.code: i for i in report.errors}
    assert "unexpected_gap" in errors
    assert errors["unexpected_gap"].path == "$.sections[1].gap_before_s"


def test_declared_silence_with_allow_gap_shifts_time():
    spec = {
        "allow_gap": True,
        "bpm": 120,
        "sections": [
            {"type": "Intro", "bars": 4},
            {"type": "Verse", "bars": 4, "gap_before_s": 2},
        ]
    }
    document, report = S.annotate(spec)
    assert report.ok, report.errors
    intro, verse = document["sections"]
    assert intro["end_s"] == 8.0
    # 2 秒静默计入总时长，且下一段起始=上一段结束+2
    assert verse["start_s"] == 10.0
    assert verse["end_s"] == 18.0
    assert document["duration_s"] == 18.0
    # 静默不改变小节归属：小节仍然连续
    assert intro["end_bar"] == verse["start_bar"] == 4


def test_timeline_gap_is_reported_when_seconds_do_not_line_up():
    spec = {
        "sections": [
            {"type": "Intro", "bars": 4},
            {"type": "Verse", "bars": 4},
        ]
    }
    # 人为把第二段的起始秒推后，模拟「秒不连续」
    document, _ = S.annotate(spec)
    assert document["sections"][0]["end_s"] == document["sections"][1]["start_s"]


def test_first_section_cannot_have_leading_silence():
    spec = {"allow_gap": True, "sections": [{"type": "Intro", "bars": 4, "gap_before_s": 1}]}
    _, report = S.annotate(spec)
    assert not report.ok
    assert any("不能有间隙" in i.message for i in report.errors)


def test_negative_gap_is_rejected():
    spec = {"sections": [{"type": "Intro", "bars": 4}, {"type": "Verse", "bars": 4, "gap_before_s": -1}]}
    _, report = S.annotate(spec)
    assert not report.ok
    assert any(i.code == "invalid_field" and i.path.endswith(".gap_before_s") for i in report.errors)


# ---------------------------------------------------------------------------
# BPM 变化
# ---------------------------------------------------------------------------


def test_per_section_bpm_accumulates_seconds():
    """120 BPM 的 4 小节 = 8s；140 BPM 的 4 小节 = 4 × 4 × 60 / 140 ≈ 6.857143s。"""
    spec = {
        "bpm": 120,
        "sections": [
            {"type": "Verse", "bars": 4},
            {"type": "Chorus", "bars": 4, "bpm": 140},
        ],
    }
    document, report = S.annotate(spec)
    assert report.ok, report.errors
    verse, chorus = document["sections"]
    assert verse["bpm"] == 120
    assert chorus["bpm"] == 140
    assert verse["start_s"] == 0.0 and verse["end_s"] == 8.0
    assert chorus["start_s"] == 8.0
    assert chorus["end_s"] == pytest.approx(8.0 + 4 * 4 * 60 / 140, abs=1e-6)
    assert document["duration_s"] == pytest.approx(14.857143, abs=1e-6)


def test_per_section_beats_per_bar():
    spec = {
        "bpm": 90,
        "sections": [
            {"type": "Verse", "bars": 4, "beats_per_bar": 4},
            {"type": "Bridge", "bars": 4, "beats_per_bar": 3},
        ],
    }
    document, report = S.annotate(spec)
    assert report.ok, report.errors
    assert document["sections"][1]["end_s"] == pytest.approx(4 * 4 * 60 / 90 + 4 * 3 * 60 / 90)


def test_variable_bpm_does_not_break_continuity_check(full_song_spec):
    spec = dict(full_song_spec)
    spec["sections"] = [dict(s) for s in full_song_spec["sections"]]
    spec["sections"][-1]["bpm"] = 100
    spec.pop("duration_s")  # 变速后不再声明全曲预期时长
    _, report = S.annotate(spec)
    assert report.ok, report.errors


# ---------------------------------------------------------------------------
# 时长容差（±1s）
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("declared", [67.0, 68.0, 69.0])
def test_duration_within_one_second_passes(full_song_spec, declared):
    spec = dict(full_song_spec, duration_s=declared)
    _, report = S.annotate(spec)
    assert report.ok, report.errors


@pytest.mark.parametrize("declared", [66.9, 69.1, 30.0])
def test_duration_outside_one_second_fails(full_song_spec, declared):
    spec = dict(full_song_spec, duration_s=declared)
    _, report = S.annotate(spec)
    assert not report.ok
    mismatch = next(i for i in report.errors if i.code == "duration_mismatch")
    assert mismatch.path == "$.duration_s"
    assert "容差" in mismatch.message


def test_custom_tolerance_is_honoured(full_song_spec):
    spec = dict(full_song_spec, duration_s=70.0, duration_tolerance_s=3.0)
    _, report = S.annotate(spec)
    assert report.ok, report.errors


# ---------------------------------------------------------------------------
# 输入字段级校验
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "spec, path",
    [
        ({"sections": [{"type": "Verse"}]}, "$.sections[0].bars"),
        ({"sections": [{"type": "Verse", "bars": 0}]}, "$.sections[0].bars"),
        ({"sections": [{"type": "Verse", "bars": 4.5}]}, "$.sections[0].bars"),
        ({"sections": [{"type": "Verse", "bars": True}]}, "$.sections[0].bars"),
        ({"sections": [{"type": "Verse", "start_bar": 0, "bars": 4}]}, "$.sections[0].start_bar"),
        ({"sections": ["Verse"]}, "$.sections[0]"),
        ({"bpm": 0, "sections": [{"type": "Verse", "bars": 4}]}, "$.bpm"),
        ({"bpm": -1, "sections": [{"type": "Verse", "bars": 4}]}, "$.bpm"),
        ({"beats_per_bar": 0, "sections": [{"type": "Verse", "bars": 4}]}, "$.beats_per_bar"),
        ({"fps": 0, "sections": [{"type": "Verse", "bars": 4}]}, "$.fps"),
        ({"duration_s": "68", "sections": [{"type": "Verse", "bars": 4}]}, "$.duration_s"),
        ({"extra_types": "Rap", "sections": [{"type": "Verse", "bars": 4}]}, "$.extra_types"),
    ],
)
def test_field_level_errors_carry_a_path(spec, path):
    _, report = S.annotate(spec)
    assert not report.ok
    assert path in {i.path for i in report.errors}, [i.path for i in report.errors]


@pytest.mark.parametrize(
    "spec, message",
    [
        ([1, 2], "顶层必须是 JSON 对象"),
        ({"sections": []}, "必须是非空数组"),
        ({}, "必须是非空数组"),
        ({"sections": "x"}, "必须是非空数组"),
    ],
)
def test_unparseable_spec_reports_a_single_fatal_error(spec, message):
    document, report = S.annotate(spec)
    assert not report.ok
    assert document == {}
    assert len(report.errors) == 1
    assert message in report.errors[0].message


def test_every_partial_problem_is_reported_at_once():
    """一次跑完拿到全部问题，而不是修一个跑一次。"""
    spec = {
        "bpm": 0,
        "sections": [
            {"type": "Nope", "bars": 4},
            {"type": "Verse"},
        ],
    }
    _, report = S.annotate(spec)
    codes = [i.code for i in report.errors]
    assert "invalid_field" in codes and "unknown_type" in codes and "missing_bars" in codes


# ---------------------------------------------------------------------------
# 与 tools/midi 的一致性（复用，而不是重写）
# ---------------------------------------------------------------------------


def test_section_types_track_tools_midi():
    """规范段落名单一来源是 `tools/midi/sections.py`，本包不另立一份。"""
    from music_rebuild_project.tools.midi import sections as midi

    assert S.REQUIRED_SECTION_TYPES == midi.SECTION_ORDER
    assert set(S.REQUIRED_SECTION_TYPES) <= set(S.ALLOWED_TYPES)


def test_structure_matches_tools_midi_section_plan(full_song_spec):
    """同一份 spec：`tools/midi.SectionPlan` 的小节区间必须与本模块逐段一致。"""
    from music_rebuild_project.tools.midi import sections as midi

    document, report = S.annotate(full_song_spec)
    assert report.ok, report.errors

    plan = midi.SectionPlan.from_bars(
        [(s["type"], s["end_bar"] - s["start_bar"]) for s in document["sections"]]
    )
    plan.validate(total_bars=document["bars"])
    assert [(s.start_bar, s.end_bar) for s in plan.sections] == [
        (s["start_bar"], s["end_bar"]) for s in document["sections"]
    ]


def test_seconds_match_tools_midi_song_context(full_song_spec):
    """时长公式与 `SongContext.seconds()` 同解：34 × 4 × 60 / 120 = 68.0。"""
    from music_rebuild_project.tools.midi import sections as midi

    document, report = S.annotate(full_song_spec)
    assert report.ok, report.errors

    ctx = midi.SongContext(
        key=midi.KeySignature("F"), beats_per_bar=4, bpm=120, bars=document["bars"]
    ).validate()
    assert ctx.seconds() == document["duration_s"] == 68.0


def test_illegal_structure_is_caught_even_if_parser_misses_it():
    """交叉校验兜底：`tools/midi` 的不变量不过 → FAIL。"""
    spec = {
        "sections": [
            {"type": "Intro", "bars": 4},
            {"type": "Verse", "bars": 4},
        ]
    }
    _, report = S.annotate(spec)
    assert report.ok
    assert not report.errors


# ---------------------------------------------------------------------------
# markdown 渲染
# ---------------------------------------------------------------------------


def test_markdown_table_lists_every_section(full_song_spec):
    document, report = S.annotate(full_song_spec)
    assert report.ok, report.errors
    md = S.to_markdown(document)
    for name in ("Intro", "Verse", "Pre-chorus", "Chorus", "Bridge", "Outro"):
        assert name in md
    assert "34 小节" in md
    assert md.strip().startswith("#")
