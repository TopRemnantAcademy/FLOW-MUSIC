"""`stems`：G4-9 四轨组织 —— 齐全 / 可重开 / 时长一致 / 可同播。

失败路径是本文件的重点：**时长不一致必须被抓出**（不是静默通过）。
"""

from __future__ import annotations

import os

import pytest

from music_rebuild_project.tools.render import stems as S
from music_rebuild_project.tools.render import wavio as W

from .conftest import tone


# --- 及格线 ----------------------------------------------------------------
def test_four_compliant_stems_pass(four_stems):
    r = S.check_stems(four_stems)
    assert r.ok, r.problems
    assert set(r.durations) == set(S.REQUIRED_STEMS)
    assert all(d == pytest.approx(1.0, abs=1e-9) for d in r.durations.values())
    assert r.mix_peak > 0


def test_report_to_dict_matches_gate_shape(four_stems):
    d = S.check_stems(four_stems).to_dict()
    assert d["gate"] == "G4-9" and d["status"] == "PASS"
    assert len(d["stems"]) == 4
    assert {"name", "present", "duration", "bit_depth", "bytes", "peak"} <= set(d["stems"][0])


def test_accepts_stems_dir_path(stems_dir):
    assert S.check_stems(stems_dir).ok


def test_aliases_resolve_to_canonical_names(four_stems):
    renamed = {
        "drum": four_stems["drums"],
        "bassline": four_stems["bass"],
        "chords": four_stems["harmony"],
        "vocals": four_stems["vocal"],
    }
    r = S.check_stems(renamed)
    assert r.ok, r.problems
    assert {c.name for c in r.checks} == set(S.REQUIRED_STEMS)


def test_present_order_is_stable(stems_dir):
    assert list(S.stem_paths(stems_dir)) == sorted(S.REQUIRED_STEMS)


def test_frozen_vocal_origin_is_allowed(four_stems):
    """보컬 允许来自 `05_vocals/` 冻结件（origin='frozen'），不要求复制到 stems/。"""
    refs = [S.StemRef(n, p, origin="frozen" if n == "vocal" else "stem")
            for n, p in four_stems.items()]
    assert S.check_stems(refs).ok


def test_bad_origin_rejected(four_stems):
    with pytest.raises(ValueError, match="origin"):
        S.StemRef("vocal", four_stems["vocal"], origin="copied")


# --- 失败路径 ①：时长不一致 -------------------------------------------------
def test_duration_mismatch_is_reported_not_silently_passed(tmp_path, four_stems):
    """核心失败路径：vocal 短了 0.5s → FAIL，且错误信息里要有**具体数字**。"""
    short_vocal = tmp_path / "song01_vocal_v001.wav"
    W.write_wav(short_vocal, tone(seconds=0.5, amplitude=0.25), sampwidth=3)

    refs = dict(four_stems)
    refs["vocal"] = str(short_vocal)
    r = S.check_stems(refs)

    assert not r.ok
    assert any("时长不一致" in p for p in r.problems), r.problems
    joined = " ".join(r.problems)
    assert "1.000000s" in joined and "0.500000s" in joined, (
        f"错误信息必须点出两个具体时长，实际：{joined}"
    )
    assert r.to_dict()["status"] == "FAIL"


def test_tiny_mismatch_below_strict_tolerance_is_caught(tmp_path, four_stems):
    """5ms 错位：比 G4-5 的 ±1s 严得多，必须抓到（否则 G4-9 形同虚设）。"""
    nudge = tmp_path / "song01_bass_v001.wav"
    W.write_wav(nudge, tone(seconds=1.050, amplitude=0.25), sampwidth=3)
    refs = dict(four_stems)
    refs["bass"] = str(nudge)

    r = S.check_stems(refs)
    assert not r.ok and any("时长不一致" in p for p in r.problems)

    # 放宽到 100ms 就应当放行 —— 证明容差是**真的生效的参数**，不是写死的常量
    assert S.check_stems(refs, tolerance=0.1).ok


def test_mix_to_master_refuses_when_stems_mismatched(tmp_path, four_stems):
    """G4-9 不过就不许出 master：没有「先产出再让人去猜」这条路。"""
    W.write_wav(tmp_path / "short.wav", tone(seconds=0.25), sampwidth=3)
    refs = dict(four_stems)
    refs["harmony"] = str(tmp_path / "short.wav")

    with pytest.raises(S.StemError, match="G4-9"):
        S.mix_to_master(refs, tmp_path / "out.wav")
    assert not (tmp_path / "out.wav").exists(), "失败时不得留下半成品 master"


# --- 失败路径 ②：缺轨 / 多轨 / 坏文件 ---------------------------------------
@pytest.mark.parametrize("dropped", list(S.REQUIRED_STEMS))
def test_missing_stem_named_in_problems(four_stems, dropped):
    refs = {k: v for k, v in four_stems.items() if k != dropped}
    r = S.check_stems(refs)
    assert not r.ok
    assert any(dropped in p and "缺 stem" in p for p in r.problems), r.problems


def test_extra_unknown_stem_flagged(four_stems):
    refs = dict(four_stems)
    refs["kazoo"] = four_stems["drums"]
    r = S.check_stems(refs)
    assert not r.ok and any("kazoo" in p for p in r.problems)


def test_duplicate_stem_flagged(four_stems):
    refs = [
        S.StemRef("drums", four_stems["drums"]),
        S.StemRef("drums", four_stems["drums"]),
        S.StemRef("bass", four_stems["bass"]),
        S.StemRef("harmony", four_stems["harmony"]),
        S.StemRef("vocal", four_stems["vocal"]),
    ]
    r = S.check_stems(refs)
    assert not r.ok and any("重复" in p for p in r.problems)


def test_missing_file_is_reported_not_skipped(four_stems):
    refs = dict(four_stems)
    refs["bass"] = "/nonexistent/song01_bass_v001.wav"
    r = S.check_stems(refs)
    assert not r.ok
    assert any("不可重开" in p and "MissingFileError" in p for p in r.problems)


def test_corrupt_file_is_reported(four_stems, tmp_path):
    bad = tmp_path / "song01_bass_v001.wav"
    bad.write_bytes(b"RIFFgarbage-not-a-wave")
    refs = dict(four_stems)
    refs["bass"] = str(bad)
    r = S.check_stems(refs)
    assert not r.ok and any("不可重开" in p for p in r.problems)


def test_wrong_spec_stem_fails_g4_check(tmp_path, four_stems):
    """16bit 的 stem 在要求 24bit 时报错（G4-10 规格传导到分轨）。"""
    p16 = tmp_path / "song01_bass_v001.wav"
    W.write_wav(p16, tone(seconds=1.0), sampwidth=2)
    refs = dict(four_stems)
    refs["bass"] = str(p16)

    assert not S.check_stems(refs).ok
    assert S.check_stems(refs, expect=None).ok  # 只查时长时放行


def test_zero_byte_stem_flagged(tmp_path, four_stems):
    empty = tmp_path / "song01_bass_v001.wav"
    empty.write_bytes(b"")
    refs = dict(four_stems)
    refs["bass"] = str(empty)
    r = S.check_stems(refs)
    assert not r.ok


def test_missing_stems_dir_raises(tmp_path):
    with pytest.raises(W.MissingFileError, match="stems 目录不存在"):
        S.check_stems(str(tmp_path / "nope"))


def test_silent_stems_are_flagged(tmp_path, four_stems):
    """全零素材 → 同播后峰值 0 → 报「全静音」，而不是当作通过。"""
    silent = tmp_path / "song01_bass_v001.wav"
    W.write_wav(silent, [0.0] * 44100, sampwidth=3)
    refs = dict(four_stems)
    refs["bass"] = str(silent)
    r = S.check_stems(refs)
    assert any("全静音" in p for p in r.problems)


# --- 混音产出 ---------------------------------------------------------------
def test_mix_to_master_produces_44100_24bit(tmp_path, four_stems):
    out = tmp_path / "song_name_mix_v001.wav"
    report, info = S.mix_to_master(four_stems, out)

    assert report.ok
    assert info["framerate"] == 44100 and info["bit_depth"] == 24
    assert info["duration"] == pytest.approx(1.0, abs=1e-9)
    assert os.path.basename(info["path"]) == "song_name_mix_v001.wav"
    assert info["peak_before_clip"] == pytest.approx(report.mix_peak)


def test_mix_to_master_is_deterministic(tmp_path, four_stems):
    """同样输入 → 同样字节。打包工具必须可复现（否则哈希清单毫无意义）。"""
    a = tmp_path / "a.wav"
    b = tmp_path / "b.wav"
    S.mix_to_master(four_stems, a)
    S.mix_to_master(four_stems, b)
    assert a.read_bytes() == b.read_bytes()


def test_mix_to_master_clips_overflow_but_reports_it(tmp_path, four_stems):
    """四轨各 0.25 振幅、相位撞在一起时可能 >1：写入被夹住，但 peak 如实上报。"""
    loud = {}
    for name in S.REQUIRED_STEMS:
        p = tmp_path / f"song01_{name}_v001.wav"
        W.write_wav(p, [0.9] * 4410, sampwidth=3)
        loud[name] = str(p)

    _, info = S.mix_to_master(loud, tmp_path / "hot.wav")
    assert info["peak_before_clip"] == pytest.approx(3.6, abs=1e-6)
    assert W.peak_of(info["samples"]) == pytest.approx(1.0, abs=1e-5)
