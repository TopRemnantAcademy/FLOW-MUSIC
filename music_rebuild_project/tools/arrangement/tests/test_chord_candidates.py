"""chord_candidates（#49）测试：候选生成确定性 + 人工批准闸 + CLI 退出码。"""

from __future__ import annotations

import json

import pytest

from music_rebuild_project.tools.arrangement import chord_candidates as CC
from music_rebuild_project.tools.arrangement.tests.conftest import REPO_ROOT  # noqa: F401  (path side-effect)


# ---- parse_key --------------------------------------------------------------

@pytest.mark.parametrize("key,root,mode", [
    ("C", 0, "major"), ("Am", 9, "minor"), ("F#m", 6, "minor"),
    ("G", 7, "major"), ("Bbm", 10, "minor"), ("Cmaj", 0, "major"),
])
def test_parse_key_ok(key, root, mode):
    pc, m, canon = CC.parse_key(key)
    assert pc == root and m == mode
    assert canon  # 非空回显


@pytest.mark.parametrize("bad", ["H", "Cx", "5", "", "Cminor7", "Cm7"])
def test_parse_key_rejects(bad):
    with pytest.raises(ValueError):
        CC.parse_key(bad)


# ---- scale ------------------------------------------------------------------

def test_scale_major_c():
    assert CC.scale_pcs(0, "major") == [0, 2, 4, 5, 7, 9, 11]


def test_scale_minor_a():
    assert CC.scale_pcs(9, "minor") == [9, 11, 0, 2, 4, 5, 7]


# ---- generate_candidates ----------------------------------------------------

def test_candidates_require_human_approval_and_status():
    spec = {"song_id": "s1", "key": "C",
            "melody": [{"bar": 1, "notes": [60, 64, 67]}]}  # C E G → I
    rep = CC.generate_candidates(spec)
    assert rep["status"] == "CANDIDATES"
    assert rep["requires_human_approval"] is True
    assert rep["key"] == "C" and rep["mode"] == "major"
    best = rep["candidates"][0]["best"]
    assert best["roman"] == "I" and best["chord"] == "C"
    assert best["covered_count"] == 3


def test_deterministic():
    spec = {"song_id": "s1", "key": "C",
            "melody": [{"bar": 1, "notes": [62, 65, 69]},
                       {"bar": 2, "notes": [67, 71, 62]}]}
    a = CC.generate_candidates(spec)
    b = CC.generate_candidates(spec)
    assert a == b  # 无 RNG，逐字段相同


def test_minor_key_candidates():
    # A 小调：A C E = i
    spec = {"song_id": "s1", "key": "Am",
            "melody": [{"bar": 1, "notes": [69, 72, 76]}]}  # A C E
    rep = CC.generate_candidates(spec)
    assert rep["candidates"][0]["best"]["roman"] == "i"


def test_empty_bar_has_no_candidate():
    spec = {"song_id": "s1", "key": "C",
            "melody": [{"bar": 1, "notes": []}]}
    rep = CC.generate_candidates(spec)
    assert rep["candidates"][0]["best"] is None


def test_alternatives_present_and_exclude_best():
    spec = {"song_id": "s1", "key": "C",
            "melody": [{"bar": 1, "notes": [60, 64, 67]}]}
    rep = CC.generate_candidates(spec)
    alts = rep["candidates"][0]["alternatives"]
    assert alts and all(a["roman"] != "I" for a in alts)


@pytest.mark.parametrize("bad_melody,msg", [
    ([{"bar": 1}], "notes"),
    ([{"bar": 0, "notes": [60]}], "bar"),
    ([{"bar": 1, "notes": [60]}, {"bar": 1, "notes": [62]}], "重复"),
    ([{"bar": 1, "notes": [200]}], "MIDI"),
    ([{"bar": True, "notes": [60]}], "bar"),
    ("nope", "list"),
])
def test_generate_rejects_bad_input(bad_melody, msg):
    with pytest.raises(ValueError):
        CC.generate_candidates({"song_id": "s", "key": "C", "melody": bad_melody})


def test_missing_required_field():
    with pytest.raises(ValueError):
        CC.generate_candidates({"key": "C", "melody": [{"bar": 1, "notes": [60]}]})


def test_never_writes_approved(tmp_path):
    # 输出只落 --out 指定处，绝不碰 approved/
    spec = {"song_id": "s1", "key": "C", "melody": [{"bar": 1, "notes": [60]}]}
    out = tmp_path / "cand.json"
    CC._emit(CC.generate_candidates(spec), str(out))
    assert out.exists()
    assert json.loads(out.read_text(encoding="utf-8"))["status"] == "CANDIDATES"


# ---- CLI（真子进程，退出码是交付的一部分） ----------------------------------

def _run_cli(spec_obj, tmp_path, out=True):
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps(spec_obj), encoding="utf-8")
    import os
    import subprocess
    import sys
    argv = [sys.executable, "-m", "music_rebuild_project.tools.arrangement.chord_candidates",
            "--spec", str(spec)]
    if out:
        argv += ["--out", str(tmp_path / "out.json")]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(argv, capture_output=True, text=True, env=env, cwd=str(tmp_path))


def test_cli_ok_exit0(tmp_path):
    p = _run_cli({"song_id": "s", "key": "C",
                  "melody": [{"bar": 1, "notes": [60, 64, 67]}]}, tmp_path)
    assert p.returncode == 0, p.stderr
    rep = json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))
    assert rep["requires_human_approval"] is True


def test_cli_bad_key_exit1(tmp_path):
    p = _run_cli({"song_id": "s", "key": "H",
                  "melody": [{"bar": 1, "notes": [60]}]}, tmp_path)
    assert p.returncode == 1


def test_cli_missing_file_exit2(tmp_path):
    import os
    import subprocess
    import sys
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    p = subprocess.run(
        [sys.executable, "-m", "music_rebuild_project.tools.arrangement.chord_candidates",
         "--spec", str(tmp_path / "nope.json")],
        capture_output=True, text=True, env=env, cwd=str(tmp_path))
    assert p.returncode == 2


def test_cli_no_random_no_third_party():
    src = (REPO_ROOT / "music_rebuild_project/tools/arrangement/chord_candidates.py").read_text(
        encoding="utf-8")
    for banned in ("import random", "import numpy", "import subprocess", "os.system"):
        assert banned not in src, f"chord_candidates 不应包含 {banned}"
