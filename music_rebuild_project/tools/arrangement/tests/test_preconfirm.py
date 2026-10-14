"""#48 编曲前确认校验器 —— 字段级覆盖矩阵。

三条纪律（沿用 `tools/naming/tests` 的口径）：

1. **不碰真实媒体、不碰数据目录。** 全部在 `tmp_path` 造 JSON；校验器本身只读，
   但测试也不把它指向仓库数据目录 —— 免得哪天工具"顺手"写点什么就没人发现。
2. **逐字段断言。** 不只断言 `status == FAIL`，还要钉住 `field` / `code`，
   否则"报错报在别的字段上"也会让测试变绿，等于没测。
3. **退出码用真的子进程量。** CLI 的退出码是验收项，就 `subprocess` 跑一遍看 `returncode`，
   不从 Python 内部猜 —— 内部函数返回 1、`main()` 忘了 `sys.exit` 也是能全绿的。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from arrangement import preconfirm

CLI = [sys.executable, "-m", "music_rebuild_project.tools.arrangement.preconfirm"]


# --------------------------------------------------------------------------
# 辅助
# --------------------------------------------------------------------------


def base_spec(**overrides) -> dict:
    """一份**完全合法**的规格，作为各处覆盖的起点。"""
    spec = {
        "song_id": "song01",
        "key": "F#m",
        "time_sig": "4/4",
        "tempo": 120,
        "bars": 12,
        "sections": [{"name": "Intro", "bars": 4}, {"name": "Verse", "bars": 8}],
        "chord_marks": {
            "1": "F#m", "2": "Bm", "3": "C#7", "4": "F#m",
            "5": "D", "6": "A", "7": "E", "8": "F#m",
            "9": "Bm", "10": "C#m", "11": "F#m", "12": "Bm",
        },
    }
    spec.update(overrides)
    return spec


def codes(report: preconfirm.Report, level: str = "FAIL") -> set[str]:
    findings = report.fails if level == "FAIL" else report.warns
    return {f.code for f in findings}


def fields(report: preconfirm.Report, level: str = "FAIL") -> set[str]:
    findings = report.fails if level == "FAIL" else report.warns
    return {f.field for f in findings}


def write_spec(tmp_path: Path, spec) -> Path:
    path = tmp_path / "spec.json"
    if isinstance(spec, str):
        path.write_text(spec, encoding="utf-8")
    else:
        path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
    return path


def run_cli(path: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [*CLI, "--spec", str(path), *extra],
        capture_output=True,
        text=True,
        cwd=Path(__file__).resolve().parents[4],
    )


# --------------------------------------------------------------------------
# ① 合法规格：PASS，且不留噪声
# --------------------------------------------------------------------------


def test_valid_spec_passes_clean():
    report = preconfirm.validate_spec(base_spec())
    assert report.ok
    assert report.findings == []  # 合法规格不该有任何 WARN —— 有就是误报


@pytest.mark.parametrize(
    "key,expected",
    [
        ("C", "C"),
        ("c", "C"),
        ("am", "Am"),
        ("AM", "Am"),
        ("f#m", "F#m"),
        ("F#min", "F#m"),
        ("Bb", "Bb"),
        ("Db", "Db"),
        ("Cmajor", "C"),
        ("cMaj", "C"),
    ],
)
def test_key_normalization_accepts_aliases(key, expected):
    assert preconfirm.normalize_key(key) == expected


@pytest.mark.parametrize("key", ["H", "Hm", "", "C##", "123", "C/", "Xb"])
def test_key_normalization_rejects_garbage(key):
    with pytest.raises(ValueError):
        preconfirm.normalize_key(key)


@pytest.mark.parametrize("sig", ["4/4", "3/4", "6/8", " 4 / 4 ", "06/08"])
def test_time_sig_accepts_whitelist(sig):
    assert preconfirm.parse_time_sig(sig) in preconfirm.VALID_TIME_SIGS


@pytest.mark.parametrize("sig", ["5/4", "7/8", "4/0", "4", "4/4/4", 44, None])
def test_time_sig_rejects_out_of_whitelist(sig):
    with pytest.raises(ValueError):
        preconfirm.parse_time_sig(sig)


# --------------------------------------------------------------------------
# ② 必填字段缺失：每个字段点出自己
# --------------------------------------------------------------------------


@pytest.mark.parametrize("missing", ["key", "time_sig", "tempo", "bars"])
def test_missing_required_field_is_field_level_fail(missing):
    spec = base_spec()
    spec.pop(missing)
    # 顺手去掉依赖字段，保证报的是"缺失"而不是连带错误
    spec.pop("sections", None)
    spec.pop("chord_marks", None)
    report = preconfirm.validate_spec(spec)
    assert not report.ok
    assert "MISSING" in codes(report)
    assert missing in fields(report)


def test_type_errors_are_reported_per_field():
    report = preconfirm.validate_spec(
        base_spec(key=42, time_sig=44, tempo="fast", bars="twelve",
                  sections=None, chord_marks=None)
    )
    assert not report.ok
    assert {"key", "time_sig", "tempo", "bars"} <= fields(report)
    assert "TYPE" in codes(report)


def test_int_fields_reject_bool():
    """`True` 是 `int` 的实例，但 `bars: true` 明显是填错了。"""
    report = preconfirm.validate_spec(
        base_spec(bars=True, tempo=True, sections=None, chord_marks=None)
    )
    assert not report.ok
    assert {"bars", "tempo"} <= fields(report)


def test_top_level_non_object_is_fail_not_crash():
    report = preconfirm.validate_spec(["not", "an", "object"])
    assert not report.ok
    assert "SPEC_TYPE" in codes(report)


# --------------------------------------------------------------------------
# ③ tempo 区间
# --------------------------------------------------------------------------


@pytest.mark.parametrize("tempo", [40, 120, 240])
def test_tempo_boundaries_pass(tempo):
    assert preconfirm.validate_spec(base_spec(tempo=tempo)).ok


@pytest.mark.parametrize("tempo", [39, 241, 0, -1, 999])
def test_tempo_out_of_range_fails(tempo):
    report = preconfirm.validate_spec(base_spec(tempo=tempo))
    assert not report.ok
    assert "RANGE" in codes(report)
    assert "tempo" in fields(report)


# --------------------------------------------------------------------------
# ④ bars / sections / repeats 一致性
# --------------------------------------------------------------------------


@pytest.mark.parametrize("bars", [0, -3])
def test_non_positive_bars_fails(bars):
    report = preconfirm.validate_spec(base_spec(bars=bars, sections=None, chord_marks=None))
    assert not report.ok
    assert "bars" in fields(report)


def test_bars_too_large_is_suspicious():
    report = preconfirm.validate_spec(base_spec(bars=1000, sections=None, chord_marks=None))
    assert not report.ok
    assert "bars" in fields(report)


def test_sections_sum_mismatch_reports_delta():
    spec = base_spec(sections=[{"name": "Intro", "bars": 4}, {"name": "Verse", "bars": 5}])
    report = preconfirm.validate_spec(spec)
    mismatch = [f for f in report.fails if f.code == "BAR_MISMATCH"]
    assert len(mismatch) == 1
    assert mismatch[0].field == "bars"
    assert "+3" in mismatch[0].message  # bars=12 比段落和 9 多 3


def test_sections_sum_matching_passes_without_warning():
    report = preconfirm.validate_spec(base_spec())
    assert report.ok
    assert report.warns == []


def test_unknown_section_name_fails_at_index():
    spec = base_spec(
        sections=[{"name": "Intro", "bars": 4}, {"name": "Vamp", "bars": 8}]
    )
    report = preconfirm.validate_spec(spec)
    assert "sections[1].name" in fields(report)
    assert "INVALID" in codes(report)


@pytest.mark.parametrize("bad", [{"name": "Verse"}, {"bars": 4}, {"name": "Verse", "bars": 0}])
def test_section_missing_or_bad_bars(bad):
    spec = base_spec(sections=[bad])
    report = preconfirm.validate_spec(spec)
    assert not report.ok
    assert any(f.field.startswith("sections[0]") for f in report.fails)


def test_empty_sections_fails():
    report = preconfirm.validate_spec(base_spec(sections=[]))
    assert not report.ok
    assert "EMPTY" in codes(report)


def test_repeats_negative_fails_and_positive_warns():
    negative = preconfirm.validate_spec(base_spec(repeats=-1))
    assert not negative.ok
    assert "repeats" in fields(negative)

    positive = preconfirm.validate_spec(base_spec(repeats=1))
    assert positive.ok
    assert "REPEAT_NOTE" in codes(positive, "WARN")


def test_sections_absent_is_legal_minimal_spec():
    spec = base_spec()
    spec.pop("sections")
    report = preconfirm.validate_spec(spec)
    assert report.ok


# --------------------------------------------------------------------------
# ⑤ chord_marks：格式 / 数量 / 待定
# --------------------------------------------------------------------------


def test_chord_marks_list_form_counts_by_position():
    spec = {
        "key": "C", "time_sig": "4/4", "tempo": 100, "bars": 4,
        "chord_marks": ["C", "F", "G", "Am"],
    }
    assert preconfirm.validate_spec(spec).ok


def test_chord_marks_count_mismatch_fails():
    report = preconfirm.validate_spec(
        base_spec(chord_marks={"1": "F#m", "2": "Bm"})
    )
    assert not report.ok
    assert "COUNT_MISMATCH" in codes(report)
    assert "chord_marks" in fields(report)


@pytest.mark.parametrize("bad", ["H", "H#zz", "C/", "Cmaj7add9xyz"])
def test_illegal_chord_symbols_fail_with_bar_label(bad):
    marks = dict(base_spec()["chord_marks"])
    marks["5"] = bad
    report = preconfirm.validate_spec(base_spec(chord_marks=marks))
    assert not report.ok
    assert "chord_marks[5]" in fields(report)
    assert "INVALID" in codes(report)


def test_non_string_chord_value_fails():
    marks = dict(base_spec()["chord_marks"])
    marks["7"] = 7
    report = preconfirm.validate_spec(base_spec(chord_marks=marks))
    assert not report.ok
    assert "chord_marks[7]" in fields(report)


def test_chord_key_must_be_positive_bar_number():
    report = preconfirm.validate_spec(
        base_spec(chord_marks={"0": "F#m", "1": "Bm"})
    )
    assert not report.ok
    assert "KEYS" in codes(report)


def test_chord_marks_wrong_container_type_fails():
    report = preconfirm.validate_spec(base_spec(chord_marks="F#m Bm"))
    assert not report.ok
    assert "TYPE" in codes(report)


@pytest.mark.parametrize("marker", ["TBD", "tbd", "?", "待定", "-", "N/A"])
def test_tbd_markers_are_accepted_but_warn(marker):
    bars = base_spec()["bars"]
    marks = {str(i + 1): marker for i in range(bars)}
    report = preconfirm.validate_spec(base_spec(chord_marks=marks))
    assert report.ok  # 待定不算 FAIL —— 只要求"显式标注"
    assert "TBD" in codes(report, "WARN")


def test_chord_marks_absent_warns():
    spec = base_spec()
    spec.pop("chord_marks")
    report = preconfirm.validate_spec(spec)
    assert report.ok
    assert "OMITTED" in codes(report, "WARN")


# --------------------------------------------------------------------------
# ⑥ 调外和弦占比：WARN 不 FAIL
# --------------------------------------------------------------------------


def test_mostly_out_of_key_chords_warn_not_fail():
    spec = base_spec(
        key="C",
        bars=4,
        sections=None,
        chord_marks={"1": "C#", "2": "F#", "3": "G#", "4": "D"},
    )
    report = preconfirm.validate_spec(spec)
    assert report.ok
    assert "OUT_OF_KEY" in codes(report, "WARN")


def test_chord_root_in_scale_basic():
    cmaj = preconfirm.key_signature("C")
    assert preconfirm.chord_root_in_scale("Am", cmaj)
    assert preconfirm.chord_root_in_scale("F#m", cmaj) is False
    amin = preconfirm.key_signature("Am")
    assert preconfirm.chord_root_in_scale("F", amin)
    assert preconfirm.chord_root_in_scale("F#", amin) is False


# --------------------------------------------------------------------------
# ⑦ CLI：退出码语义 + JSON 契约
# --------------------------------------------------------------------------


def test_cli_valid_exit_0_and_json(tmp_path):
    path = write_spec(tmp_path, base_spec())
    proc = run_cli(path)
    assert proc.returncode == 0
    payload = json.loads(proc.stdout)
    assert payload["status"] == "PASS"
    assert payload["exit_code"] == 0
    assert payload["findings"] == []


def test_cli_invalid_exit_1_with_findings(tmp_path):
    path = write_spec(tmp_path, base_spec(tempo=999))
    proc = run_cli(path)
    assert proc.returncode == 1
    payload = json.loads(proc.stdout)
    assert payload["status"] == "FAIL"
    assert payload["summary"]["fail"] >= 1
    assert any(f["field"] == "tempo" for f in payload["findings"])


def test_cli_strict_promotes_warn_to_exit_1(tmp_path):
    spec = base_spec(repeats=1)  # 只会产生 REPEAT_NOTE 一条 WARN
    path = write_spec(tmp_path, spec)

    lenient = run_cli(path)
    assert lenient.returncode == 0

    strict = run_cli(path, "--strict")
    assert strict.returncode == 1
    payload = json.loads(strict.stdout)
    assert payload["summary"]["warn"] >= 1
    assert payload["summary"]["strict"] is True


def test_cli_missing_file_exit_2(tmp_path):
    proc = run_cli(tmp_path / "nope.json")
    assert proc.returncode == 2
    assert json.loads(proc.stdout)["exit_code"] == 2


def test_cli_broken_json_exit_2(tmp_path):
    path = write_spec(tmp_path, "{ not json ")
    proc = run_cli(path)
    assert proc.returncode == 2
    assert "解析失败" in json.loads(proc.stdout)["message"]


def test_cli_requires_spec_arg():
    proc = subprocess.run(
        CLI, capture_output=True, text=True,
        cwd=Path(__file__).resolve().parents[4],
    )
    assert proc.returncode == 2  # argparse 用法错误


# --------------------------------------------------------------------------
# ⑧ 确定性 / 纯函数性（无 RNG、无副作用）
# --------------------------------------------------------------------------


def test_validation_is_deterministic():
    spec = base_spec()
    first = preconfirm.validate_spec(spec).to_dict()
    second = preconfirm.validate_spec(json.loads(json.dumps(spec))).to_dict()
    assert first == second


def test_validate_does_not_mutate_input():
    spec = base_spec()
    snapshot = json.loads(json.dumps(spec))
    preconfirm.validate_spec(spec)
    assert spec == snapshot


def test_report_dict_shape_is_stable():
    payload = preconfirm.validate_spec(base_spec(tempo=999)).to_dict()
    assert set(payload) == {"status", "exit_code", "summary", "findings"}
    assert set(payload["summary"]) == {"fail", "warn", "strict"}
    for finding in payload["findings"]:
        assert set(finding) == {"level", "field", "code", "message"}

# --------------------------------------------------------------------------
# ⑨ 边界守卫：只读、纯净、不碰后端
# --------------------------------------------------------------------------


def test_module_import_is_stdlib_only():
    """本包只允许 stdlib + tools.midi 复用；引入 ffmpeg / numpy 之类即算越界。"""
    source = Path(preconfirm.__file__).read_text(encoding="utf-8")
    disallowed = ("numpy", "scipy", "ffmpeg", "subprocess", "random", "librosa")
    for name in disallowed:
        assert f"import {name}" not in source, f"preconfirm.py 不应 import {name}"


def test_cli_writes_nothing(tmp_path):
    """校验器只读：跑完 CLI 后临时目录里除输入外不应多出文件。"""
    spec_path = write_spec(tmp_path, base_spec())
    before = sorted(p.name for p in tmp_path.iterdir())
    proc = run_cli(spec_path)
    assert proc.returncode == 0
    after = sorted(p.name for p in tmp_path.iterdir())
    assert before == after


def test_no_rng_in_findings_order():
    """同规格多次校验，findings 顺序必须稳定（无集合遍历 → 无随机序）。"""
    spec = base_spec(tempo=999, key="Hm", time_sig="5/4")
    runs = [preconfirm.validate_spec(spec).to_dict() for _ in range(5)]
    assert all(run == runs[0] for run in runs)
