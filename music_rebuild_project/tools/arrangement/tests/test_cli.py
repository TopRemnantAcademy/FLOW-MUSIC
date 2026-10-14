"""CLI 端到端：产物落盘 + 退出码语义（0 通过 / 1 校验失败 / 2 spec 读不了）。"""

from __future__ import annotations

import json

import pytest

from music_rebuild_project.tools.arrangement import sections as S
from music_rebuild_project.tools.arrangement.tests.conftest import REPO_ROOT


def test_cli_writes_sections_json(full_song_spec, write_spec, run_cli, tmp_path):
    spec_path = write_spec(full_song_spec)
    out = tmp_path / "out" / "sections.json"

    code, stdout, stderr = run_cli("--spec", str(spec_path), "--out", str(out))

    assert code == 0, stderr
    assert stderr.strip() == ""
    document = json.loads(out.read_text(encoding="utf-8"))
    assert document["schema_version"] == S.SCHEMA_VERSION
    assert document["bars"] == 34
    assert document["duration_s"] == 68.0
    assert len(document["sections"]) == 6


def test_cli_writes_markdown(full_song_spec, write_spec, run_cli, tmp_path):
    spec_path = write_spec(full_song_spec)
    md = tmp_path / "sections.md"

    code, _, stderr = run_cli(
        "--spec", str(spec_path), "--out", str(tmp_path / "sections.json"), "--md", str(md)
    )

    assert code == 0, stderr
    text = md.read_text(encoding="utf-8")
    assert "Chorus" in text and "34 小节" in text


def test_cli_stdout_only_when_dash(full_song_spec, write_spec, run_cli):
    spec_path = write_spec(full_song_spec)
    code, stdout, stderr = run_cli("--spec", str(spec_path), "--out", "-")
    assert code == 0, stderr
    assert json.loads(stdout)["bars"] == 34


@pytest.mark.parametrize(
    "spec, code_in_stderr",
    [
        ({"sections": [{"type": "Chrous", "bars": 4}]}, "unknown_type"),
        ({"sections": [{"type": "Intro", "bars": 4},
                       {"type": "Verse", "start_bar": 3, "end_bar": 8}]}, "overlap"),
        ({"duration_s": 90.0, "sections": [{"type": "Verse", "bars": 8}]}, "duration_mismatch"),
    ],
)
def test_cli_exit_code_1_on_validation_failure(spec, code_in_stderr, write_spec, run_cli, tmp_path):
    """校验失败 = 退出码 1，且错误码与字段路径出现在 stderr（不静默退出）。"""
    spec_path = write_spec(spec)
    out = tmp_path / "sections.json"
    code, _, stderr = run_cli("--spec", str(spec_path), "--out", str(out))
    assert code == 1
    assert code_in_stderr in stderr
    assert "$" in stderr
    # 失败也要把「算得出来的部分」落盘，方便人看着结果改 spec
    assert out.exists()


def test_cli_exit_code_2_when_spec_is_missing(run_cli, tmp_path):
    code, _, stderr = run_cli("--spec", str(tmp_path / "nope.json"), "--out", str(tmp_path / "o.json"))
    assert code == 2
    assert "FAIL" in stderr
    assert not (tmp_path / "o.json").exists()


def test_cli_exit_code_2_when_spec_is_not_json(write_spec, run_cli, tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{ not json", encoding="utf-8")
    code, _, stderr = run_cli("--spec", str(bad))
    assert code == 2
    assert "合法 JSON" in stderr


def test_cli_extra_type_flag_allows_custom_type(write_spec, run_cli, tmp_path):
    spec_path = write_spec({"sections": [{"type": "Rap", "bars": 4}]})
    out = tmp_path / "sections.json"
    code, _, stderr = run_cli(
        "--spec", str(spec_path), "--out", str(out), "--extra-type", "Rap"
    )
    assert code == 0, stderr
    assert json.loads(out.read_text(encoding="utf-8"))["sections"][0]["type"] == "Rap"


def test_cli_warning_does_not_fail_but_strict_does(write_spec, run_cli, tmp_path):
    """`Pre-Chorus` 只是提示：默认退出 0；`--strict` 下警告也算失败。"""
    spec_path = write_spec({"sections": [{"type": "Pre-Chorus", "bars": 4}]})
    out = tmp_path / "sections.json"

    code, _, stderr = run_cli("--spec", str(spec_path), "--out", str(out))
    assert code == 0
    assert "WARN" in stderr

    code, _, stderr = run_cli("--spec", str(spec_path), "--out", str(out), "--strict")
    assert code == 1
    assert "WARN" in stderr


def test_cli_json_flag_prints_report(full_song_spec, write_spec, run_cli, tmp_path):
    spec_path = write_spec(full_song_spec)
    code, stdout, stderr = run_cli(
        "--spec", str(spec_path), "--out", str(tmp_path / "sections.json"), "--json"
    )
    assert code == 0, stderr
    document = json.loads(stdout)
    assert document["validation"]["ok"] is True


def test_cli_works_from_music_rebuild_project_dir(full_song_spec, write_spec, tmp_path):
    """两种跑法都要能用：仓库根 `-m music_rebuild_project.tools...`，
    以及 `cd music_rebuild_project` 后 `-m tools.arrangement.sections`
    （与 `tools/midi/render_demo.py` 的用法一致；包里没有 `tools/__init__.py`，
    所以这是两条不同的模块路径）。"""
    import os
    import subprocess
    import sys

    spec_path = write_spec(full_song_spec)
    pkg_dir = tmp_path / "pkg_out.json"
    proc = subprocess.run(
        [
            sys.executable, "-m", "tools.arrangement.sections",
            "--spec", str(spec_path), "--out", str(pkg_dir),
        ],
        cwd=str(REPO_ROOT / "music_rebuild_project"),
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT / "music_rebuild_project")},
    )
    assert proc.returncode == 0, proc.stderr
    assert json.loads(pkg_dir.read_text(encoding="utf-8"))["bars"] == 34


def test_cli_output_is_byte_stable(full_song_spec, write_spec, run_cli, tmp_path):
    """无 RNG：两次运行的 `sections.json` 逐字节相同。"""
    spec_path = write_spec(full_song_spec)
    first, second = tmp_path / "a.json", tmp_path / "b.json"
    assert run_cli("--spec", str(spec_path), "--out", str(first))[0] == 0
    assert run_cli("--spec", str(spec_path), "--out", str(second))[0] == 0
    assert first.read_bytes() == second.read_bytes()
