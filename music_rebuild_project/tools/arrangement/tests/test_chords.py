"""#50 chords.txt 记录与规范化 —— 覆盖矩阵。

覆盖矩阵（issue 正文逐项对照）

| 需求 | 用例 |
|---|---|
| 合法根音（A-G + 可选 #/b） | `test_valid_roots` |
| 非法根音（H7 / C## / 非字母开头） | `test_invalid_roots_report_line` |
| 性质枚举（maj/min/7/maj7/min7/dom7/sus4/dim/aug） | `test_quality_enumeration` |
| 同义写法归一（M7→maj7、m→min、-7→min7、°→dim、+→aug） | `test_quality_aliases_normalize` |
| 未知性质 | `test_unknown_quality_reports_line` |
| 转位 / 低音 | `test_slash_bass`、`test_invalid_bass_reports_line` |
| 一行多和弦（同小节内变更） | `test_multiple_chords_per_bar` |
| 空行 / 注释 / 尾随空格 | `test_blank_and_comment_lines` |
| 行号报错 | `test_invalid_roots_report_line`、`test_error_carries_index` |
| 小节不连续 / 重复 | `test_gap_rejected`、`test_duplicate_bar_rejected` |
| 乱序小节排序 | `test_out_of_order_lines_are_sorted` |
| 裸和弦按序编号 | `test_bare_chords_auto_number` |
| JSON 输入（映射 / 列表 / 对象 / 字符串） | `test_json_*` |
| 输出契约（头行 / LF / 无尾随空行 / 幂等） | `test_output_contract`、`test_normalize_is_idempotent` |
| 与 tools/midi 的分工（parity + 桥接） | `test_parity_with_midi_bass`、`test_map_feeds_song_context` |
| CLI 退出码 0/1/2 | `test_cli_exit_*` |
| 纯 stdlib / 无 RNG / 无第三方 | `test_no_third_party_imports`、`test_deterministic_bytes` |
"""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from arrangement import chords as C

REPO_ROOT = Path(__file__).resolve().parents[4]


# ---------------------------------------------------------------- 辅助
def sheet(text: str) -> C.ChordSheet:
    return C.parse_chords(text)


# ---------------------------------------------------------------- 根音
@pytest.mark.parametrize("root", ["C", "c", "F#", "Bb", "a", "g#", "Eb"])
def test_valid_roots(root):
    chord = C.normalize_chord(f"{root}maj7")
    assert chord.root == root[0].upper() + root[1:]
    assert chord.quality == "maj7"


@pytest.mark.parametrize(
    ("bad", "hint"),
    [("H7", "非法根音"), ("C##", "##"), ("1X", "音名"), ("X", "音名"), ("Q7", "音名")],
)
def test_invalid_roots_report_line(bad, hint):
    with pytest.raises(C.ChordSyntaxError) as exc:
        sheet(f"1 Cmaj7\n2 {bad}\n")
    err = exc.value
    assert err.line == 2, f"{bad} 的行号应为 2"
    assert err.text == bad
    if hint:
        assert hint in err.reason or hint in str(err)
    assert "第 2 行" in str(err)


def test_error_carries_index():
    """同一行多个和弦时，错误要指出行内第几个。"""
    with pytest.raises(C.ChordSyntaxError) as exc:
        sheet("1 Cmaj7 H7\n")
    err = exc.value
    assert err.line == 1
    assert err.index == 2
    assert "第 2 个" in str(err)


# ---------------------------------------------------------------- 性质
@pytest.mark.parametrize(
    ("written", "expected"),
    [
        ("C", "maj"), ("Cmaj", "maj"), ("Cmajor", "maj"), ("CM", "maj"),
        ("Cm", "min"), ("Cmin", "min"), ("C-", "min"),
        ("C7", "dom7"), ("Cdom7", "dom7"),
        ("Cmaj7", "maj7"), ("CM7", "maj7"), ("Cma7", "maj7"),
        ("Cm7", "min7"), ("Cmin7", "min7"), ("C-7", "min7"),
        ("Csus4", "sus4"), ("Csus", "sus4"), ("Csus2", "sus2"),
        ("Cdim", "dim"), ("C°", "dim"), ("Co", "dim"),
        ("Caug", "aug"), ("C+", "aug"),
    ],
)
def test_quality_enumeration(written, expected):
    assert C.normalize_chord(written).quality == expected


def test_quality_aliases_normalize():
    """同义写法收敛到同一规范化文本。"""
    aliases = {
        "CM7": "Cmaj7", "CΔ7": "Cmaj7", "Cma7": "Cmaj7",
        "Cm": "Cmin", "C-": "Cmin", "Cmin": "Cmin",
        "Cm7": "Cmin7", "C-7": "Cmin7",
        "C7": "Cdom7", "Cdom7": "Cdom7",
        "C°": "Cdim", "Co": "Cdim", "C+": "Caug",
        # `maj` 是默认性质，输出时不写后缀（裸根音）—— 三写法收敛到同一个 `C`
        "CM": "C", "Cmaj": "C", "C": "C", "Cmajor": "C",
    }
    for written, canonical in aliases.items():
        assert str(C.normalize_chord(written)) == canonical, written


def test_lowercase_bb_is_valid_flat_root():
    """`bb` 是 B♭ 而不是重复降号 —— 别把它误判成 `bb` 双降。"""
    assert C.normalize_chord("bb").quality == "maj"
    assert str(C.normalize_chord("bb")) == "Bb"
    assert str(C.normalize_chord("Bbm")) == "Bbmin"


def test_leading_quality_without_root():
    """`M7` / `-7` 这类「性质在前」的写法默认建在 C 上。"""
    assert str(C.normalize_chord("M7")) == "Cmaj7"
    assert str(C.normalize_chord("-7")) == "Cmin7"
    assert str(C.normalize_chord("Δ7")) == "Cmaj7"
    assert str(C.normalize_chord("M7/E")) == "Cmaj7/E"


def test_unknown_quality_reports_line():
    with pytest.raises(C.ChordSyntaxError) as exc:
        sheet("1 Cmaj7\n2 Cxyz\n")
    err = exc.value
    assert err.line == 2
    assert "未知性质" in err.reason
    assert "maj7" in err.reason  # 允许集合列出来了


# ---------------------------------------------------------------- 转位
@pytest.mark.parametrize(
    ("written", "root", "quality", "bass"),
    [
        ("C/E", "C", "maj", "E"),
        ("Cmaj7/G", "C", "maj7", "G"),
        ("F#/A#", "F#", "maj", "A#"),
        ("Bbm7/Db", "Bb", "min7", "Db"),
    ],
)
def test_slash_bass(written, root, quality, bass):
    chord = C.normalize_chord(written)
    assert (chord.root, chord.quality, chord.bass) == (root, quality, bass)
    # 输出是规范化的：`Bbm7/Db` → `Bbmin7/Db`
    assert str(chord) == f"{root}{'' if quality == 'maj' else quality}/{bass}"


def test_invalid_bass_reports_line():
    with pytest.raises(C.ChordSyntaxError) as exc:
        sheet("1 Cmaj7\n2 C/H\n")
    err = exc.value
    assert err.line == 2
    # `C/H` 不匹配 → 整个标记按「无法解析」报；根音仍是 C
    assert "H" in str(err) or "低音" in str(err)


# ---------------------------------------------------------------- 多和弦 / 空行
def test_multiple_chords_per_bar():
    result = sheet("3 Dm7 G7 Cmaj7\n")
    assert result.entries[0].bar == 3
    assert [str(c) for c in result.entries[0].chords] == ["Dmin7", "Gdom7", "Cmaj7"]
    assert result.to_map() == {2: "Dmin7 Gdom7 Cmaj7"}


def test_multiple_chords_accept_commas_and_pipes():
    for line in ("1 Dm7, G7", "1 Dm7|G7", "1 Dm7   G7"):
        assert sheet(line).entries[0].bar == 1
        assert len(sheet(line).entries[0].chords) == 2


def test_blank_and_comment_lines():
    result = sheet(
        """
        # 这是注释

        1 Cmaj7
           # 缩进注释
        2 Am7

        3 Dm7 G7
        """
    )
    assert result.bars == 3
    assert [e.bar for e in result.entries] == [1, 2, 3]


def test_whitespace_and_delimiters_normalized():
    result = sheet("  1:   Cmaj7  \n 2 . Am7 \n")
    assert result.to_text().splitlines()[1:] == ["1 Cmaj7", "2 Amin7"]


# ---------------------------------------------------------------- 小节号
def test_gap_rejected():
    with pytest.raises(C.ChordSyntaxError) as exc:
        sheet("1 Cmaj7\n3 Am7\n")
    assert "缺失小节 [2]" in exc.value.reason


def test_duplicate_bar_rejected():
    with pytest.raises(C.ChordSyntaxError) as exc:
        sheet("1 Cmaj7\n1 Am7\n")
    assert "重复定义" in exc.value.reason


def test_leading_bar_number_must_be_positive():
    with pytest.raises(C.ChordSyntaxError):
        C.parse_json({"0": "Cmaj7"})


def test_out_of_order_lines_are_sorted():
    result = sheet("3 Dm7\n1 Cmaj7\n2 Am7\n")
    assert [e.bar for e in result.entries] == [1, 2, 3]
    assert result.to_text().splitlines()[1:] == ["1 Cmaj7", "2 Amin7", "3 Dmin7"]


def test_bare_chords_auto_number():
    result = sheet("Cmaj7\nAm7\nDm7 G7\n")
    assert [e.bar for e in result.entries] == [1, 2, 3]
    assert result.bars == 3


def test_explicit_then_bare_continues():
    result = sheet("5 Cmaj7\nAm7\n")
    assert [e.bar for e in result.entries] == [5, 6]


# ---------------------------------------------------------------- JSON
def test_json_mapping():
    result = C.parse_json({"1": "Cmaj7", "2": "Am7", "3": "Dm7 G7"})
    assert result.bars == 3
    assert result.to_text().splitlines()[3] == "3 Dmin7 Gdom7"


def test_json_list_of_strings():
    result = C.parse_json(["Cmaj7", "Am7", "Dm7 G7"])
    assert [e.bar for e in result.entries] == [1, 2, 3]


def test_json_list_of_objects():
    result = C.parse_json(
        [{"bar": 2, "chord": "M7"}, {"bar": 1, "chords": ["F", "C"]}]
    )
    assert [e.bar for e in result.entries] == [1, 2]
    assert str(result.entries[0].chords[0]) == "F"
    assert str(result.entries[1].chords[0]) == "Cmaj7"


def test_json_list_of_lists():
    result = C.parse_json([["Cmaj7", "F"], ["Am7"]])
    assert result.bars == 2
    assert len(result.entries[0].chords) == 2


def test_json_bad_element_type_rejected():
    with pytest.raises(C.ChordSyntaxError):
        C.parse_json([1, 2])


def test_json_object_missing_field_rejected():
    with pytest.raises(C.ChordSyntaxError) as exc:
        C.parse_json([{"bar": 1}])
    assert "缺少" in exc.value.reason


def test_parse_any_detects_json_and_text():
    assert C.parse_any('{"1": "Cmaj7"}').bars == 1
    assert C.parse_any("1 Cmaj7\n").bars == 1


def test_parse_any_bad_json_reports():
    with pytest.raises(C.ChordSyntaxError) as exc:
        C.parse_any("{not json")
    assert "JSON 解析失败" in exc.value.reason


# ---------------------------------------------------------------- 输出契约
def test_output_contract():
    text = sheet("2 Am7\n1 Cmaj7\n").to_text()
    lines = text.split("\n")
    assert lines[0] == C.HEADER
    assert lines[1:3] == ["1 Cmaj7", "2 Amin7"]
    assert text.endswith("\n") and not text.endswith("\n\n")
    assert "\r" not in text
    assert all(line == line.rstrip() for line in lines)


def test_validate_contract():
    result = sheet("1 Cmaj7\n2 Am7\n")
    assert result.validate(bars=2).bars == 2
    with pytest.raises(C.ChordSyntaxError) as exc:
        sheet("1 Cmaj7\n2 Am7\n").validate(bars=4)
    assert "期望总小节数 4" in exc.value.reason


def test_normalize_is_idempotent():
    one = sheet("  1: cmaj7  \n 2 . -7\n").to_text()
    two = C.parse_any(one).to_text()
    assert one == two


def test_deterministic_bytes():
    src = "3 Dm7 G7\n1 Cmaj7\n2 Am7\n"
    assert sheet(src).to_text() == sheet(src).to_text() == C.parse_any(src).to_text()


def test_empty_input_yields_header_only():
    result = sheet("# 空\n\n")
    assert result.bars == 0
    assert result.to_text() == C.HEADER + "\n"


# ---------------------------------------------------------------- 与 tools/midi 的边界
def test_chord_class_is_frozen_and_hashable():
    """`Chord` 不可变 —— 规范化结果是值，不是可变状态。"""
    chord = C.normalize_chord("Cmaj7")
    with pytest.raises(Exception):
        chord.root = "D"  # type: ignore[misc]
    assert chord in {chord}


def test_transpose_preserves_slash_relationship():
    assert str(C.normalize_chord("Cmaj7/E").transpose("D")) == "Dmaj7/E"
    assert str(C.normalize_chord("Cmaj7/C").transpose("D")) == "Dmaj7/D"
    assert str(C.normalize_chord("Cmaj7").transpose("D")) == "Dmaj7"


def test_parity_with_midi_bass():
    """与 `tools/midi/bass.ChordSymbol` 对**合法规范**输入的口径一致。

    两包各有独立实现（本包带行号错误 + 别名归一），但半音语义必须一致，
    否则「校验通过 → 生成结果」会错位。
    """
    from midi import bass as B

    cases = ["C", "Cm", "Cmaj7", "Cm7", "C7", "Cdim", "Caug", "Csus4", "Csus2", "C/E", "F#m7", "Bbmaj7/D"]
    pitch = {"C": 0, "C#": 1, "Db": 1, "D": 2, "D#": 3, "Eb": 3, "E": 4, "F": 5,
             "F#": 6, "Gb": 6, "G": 7, "G#": 8, "Ab": 8, "A": 9, "A#": 10, "Bb": 10, "B": 11}
    for text in cases:
        mine = C.normalize_chord(text)
        theirs = B.ChordSymbol.parse(text)
        assert pitch[mine.root] == theirs.root, text
        assert theirs.bass is None if mine.bass is None else pitch[mine.bass] == theirs.bass


def test_map_feeds_song_context():
    """`to_map()` / `load_chord_map()` 的输出能直接喂 `midi.SongContext.chords`。"""
    from midi import sections as S

    mapping = sheet("1 Cmaj7\n2 Am7\n3 Dm7\n4 G7\n").to_map()
    assert mapping == {0: "Cmaj7", 1: "Amin7", 2: "Dmin7", 3: "Gdom7"}
    ctx = S.SongContext(
        key=S.KeySignature("C"), beats_per_bar=4, bpm=120, bars=4, chords=mapping
    ).validate()
    assert ctx.chords[2] == "Dmin7"


def test_load_chord_map_reads_file(tmp_path):
    path = tmp_path / "chords.txt"
    path.write_text(sheet("1 Cmaj7\n2 Am7\n").to_text(), encoding="utf-8")
    assert C.load_chord_map(path) == {0: "Cmaj7", 1: "Amin7"}


def test_load_chord_map_is_strict(tmp_path):
    """与 midi 侧的宽松读取器不同：畸形行不会被静默跳过。"""
    path = tmp_path / "chords.txt"
    path.write_text("1 Cmaj7\n2 H7\n", encoding="utf-8")
    with pytest.raises(C.ChordSyntaxError):
        C.load_chord_map(path)


# ---------------------------------------------------------------- CLI
def _run(args, cwd=None):
    return subprocess.run(
        [sys.executable, "-m", "music_rebuild_project.tools.arrangement.chords", *args],
        cwd=str(cwd or REPO_ROOT),
        capture_output=True,
        text=True,
    )


def test_cli_exit_0_and_writes(tmp_path):
    src = tmp_path / "raw.txt"
    src.write_text("1 Cmaj7\n2 Am7\n3 Dm7 G7\n", encoding="utf-8")
    dst = tmp_path / "out" / "chords.txt"
    proc = _run(["--in", str(src), "--out", str(dst)])
    assert proc.returncode == 0, proc.stderr
    assert dst.read_text(encoding="utf-8") == sheet(src.read_text()).to_text()


def test_cli_exit_1_on_invalid(tmp_path):
    src = tmp_path / "raw.txt"
    src.write_text("1 Cmaj7\n2 H7\n", encoding="utf-8")
    proc = _run(["--in", str(src)])
    assert proc.returncode == 1
    assert "第 2 行" in proc.stderr


def test_cli_exit_1_on_gap(tmp_path):
    src = tmp_path / "raw.txt"
    src.write_text("1 Cmaj7\n3 Am7\n", encoding="utf-8")
    proc = _run(["--in", str(src)])
    assert proc.returncode == 1
    assert "缺失小节" in proc.stderr


def test_cli_exit_1_on_expect_bars(tmp_path):
    src = tmp_path / "raw.txt"
    src.write_text("1 Cmaj7\n2 Am7\n", encoding="utf-8")
    proc = _run(["--in", str(src), "--expect-bars", "4"])
    assert proc.returncode == 1
    assert "期望总小节数 4" in proc.stderr


def test_cli_exit_2_on_missing_file(tmp_path):
    proc = _run(["--in", str(tmp_path / "nope.txt")])
    assert proc.returncode == 2
    assert "读取错误" in proc.stderr


def test_cli_stdout_is_the_artifact(tmp_path):
    src = tmp_path / "raw.txt"
    src.write_text("1 Cmaj7\n", encoding="utf-8")
    proc = _run(["--in", str(src)])
    assert proc.returncode == 0
    assert proc.stdout == sheet("1 Cmaj7\n").to_text()


def test_cli_map_flag_is_json(tmp_path):
    src = tmp_path / "raw.txt"
    src.write_text("1 Cmaj7\n2 Am7\n", encoding="utf-8")
    proc = _run(["--in", str(src), "--map"])
    assert proc.returncode == 0
    assert json.loads(proc.stdout) == {"0": "Cmaj7", "1": "Amin7"}


def test_cli_validate_does_not_write(tmp_path):
    src = tmp_path / "raw.txt"
    src.write_text("1 Cmaj7\n", encoding="utf-8")
    dst = tmp_path / "should_not_exist.txt"
    proc = _run(["--in", str(src), "--out", str(dst), "--validate"])
    assert proc.returncode == 0
    assert not dst.exists()


def test_cli_first_bar_offsets(tmp_path):
    src = tmp_path / "raw.txt"
    src.write_text("1 Cmaj7\n2 Am7\n", encoding="utf-8")
    proc = _run(["--in", str(src), "--first-bar", "5", "--map"])
    assert json.loads(proc.stdout) == {"4": "Cmaj7", "5": "Amin7"}


def test_cli_reads_stdin():
    proc = subprocess.run(
        [sys.executable, "-m", "music_rebuild_project.tools.arrangement.chords"],
        cwd=str(REPO_ROOT),
        input="1 Cmaj7\n",
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0
    assert "1 Cmaj7" in proc.stdout


# ---------------------------------------------------------------- 依赖与纯净度
def test_no_third_party_imports():
    """纯 stdlib：源码里不得出现 pytest / numpy / ffmpeg 等第三方依赖。"""
    source = (Path(C.__file__)).read_text(encoding="utf-8")
    imports = re.findall(r"^\s*(?:import|from)\s+([A-Za-z_][\w.]*)", source, re.MULTILINE)
    tops = {name.split(".")[0] for name in imports}
    allowed = {
        "argparse", "json", "re", "sys", "pathlib", "dataclasses", "typing",
        "__future__",
    }
    assert tops <= allowed, f"出现非 stdlib 依赖: {sorted(tops - allowed)}"


def test_no_random_source():
    """无 RNG：同一输入两次解析必须逐字节一致（已在上方断言），
    这里再静态确认源码没引 random/time/uuid。"""
    source = Path(C.__file__).read_text(encoding="utf-8")
    for banned in ("import random", "import time", "import uuid", "from random"):
        assert banned not in source
