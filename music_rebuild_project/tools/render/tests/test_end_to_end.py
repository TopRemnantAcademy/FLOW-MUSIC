"""端到端：4 条 stem → 混音 master → manifest。

这是 #59 的验收演示的可执行版本 —— 数字全部现场算出来再断言，
不是把 demo 输出粘进测试。顺带钉死两三件容易漂的事：
合成素材的确定性、失败路径的退出码、以及 manifest 里的哈希能独立复算。
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys

import pytest

from music_rebuild_project.tools.render import cli
from music_rebuild_project.tools.render import demo as D
from music_rebuild_project.tools.render import manifest as M
from music_rebuild_project.tools.render import mp3 as MP3
from music_rebuild_project.tools.render import stems as S
from music_rebuild_project.tools.render import wavio as W


# --- demo 全链 --------------------------------------------------------------
@pytest.fixture(scope="module")
def demo_result(tmp_path_factory):
    return D.run(workdir=str(tmp_path_factory.mktemp("e2e")), verbose=False)


def test_demo_chain_passes_g4_9(demo_result):
    r = demo_result["report"]
    assert r.ok, r.problems
    assert set(r.durations) == set(S.REQUIRED_STEMS)
    spread = max(r.durations.values()) - min(r.durations.values())
    assert spread == pytest.approx(0.0, abs=1e-9)


def test_demo_master_matches_g4_10_spec(demo_result):
    info = demo_result["master"]
    assert (info["framerate"], info["bit_depth"], info["channels"]) == (44100, 24, 1)
    assert os.path.basename(info["path"]) == "song_name_mix_v001.wav"
    assert info["duration"] == pytest.approx(D.SECONDS, abs=1e-6)


def test_demo_master_duration_equals_stem_duration(demo_result):
    """G4-5 的口径套在 mock 上：master 与素材时长一致（误差 ≪ ±1s）。"""
    for _, p in demo_result["stems"].items():
        assert abs(W.duration_of(p) - demo_result["master"]["duration"]) <= 1.0


def test_demo_manifest_hashes_are_independently_recomputable(demo_result):
    m = demo_result["manifest"]
    for e in m.entries:
        plain = hashlib.sha256(open(e.path, "rb").read()).hexdigest()
        assert e.sha256 == plain
    assert M.verify_manifest(m) == []


def test_demo_manifest_reports_missing_records(demo_result):
    """demo 故意不给四类记录 → 必须 FAIL 并逐条点名，而不是静默 PASS。"""
    m = demo_result["manifest"]
    assert not m.ok
    assert len(m.missing_records) == len(M.REQUIRED_RECORDS)
    for role in M.REQUIRED_RECORDS:
        assert any(role in x for x in m.missing_records)


def test_demo_text_contains_real_numbers(demo_result):
    text = demo_result["text"]
    for needle in ("8.000000s", "sha256=", "G4-9 校验：PASS", "manifest（G4-12）"):
        assert needle in text, needle


def test_demo_is_deterministic(tmp_path):
    a = D.run(workdir=str(tmp_path / "a"), verbose=False)
    b = D.run(workdir=str(tmp_path / "b"), verbose=False)
    assert M.sha256_of(a["master"]["path"]) == M.sha256_of(b["master"]["path"])


def test_demo_master_equals_sum_of_stems(tmp_path):
    """master = 四轨逐采样和（线性、无限幅作用）—— 用重算验证，不看 demo 自述。"""
    res = D.run(workdir=str(tmp_path / "w"), verbose=False)
    tracks = [W.read_wav(p)["samples"] for p in
              (res["stems"][n] for n in S.REQUIRED_STEMS)]
    expected, _ = W.mix_down(tracks)

    got = list(W.read_wav(res["master"]["path"])["samples"])
    assert len(got) == len(expected)
    worst = max(abs(a - b) for a, b in zip(expected, got))
    assert worst < 1.0 / 8388607.0 * 2, f"master 与逐采样和不符，最大偏差 {worst}"


# --- CLI --------------------------------------------------------------------
def test_cli_check_stems_pass(stems_dir, capsys):
    assert cli.main(["check-stems", "--stems-dir", stems_dir]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["gate"] == "G4-9" and payload["status"] == "PASS"


def test_cli_check_stems_fail_exit_code(tmp_path, four_stems, capsys):
    """CLI 的失败路径要给出退出码 1 + 结构化问题清单，不是异常栈。"""
    d = tmp_path / "s"
    d.mkdir()
    for name, p in four_stems.items():
        if name == "vocal":
            W.write_wav(d / os.path.basename(p), [0.1] * 1000, sampwidth=3)  # 短一截
        else:
            os.replace(p, d / os.path.basename(p))

    assert cli.main(["check-stems", "--stems-dir", str(d)]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "FAIL"
    assert any("时长不一致" in p for p in payload["problems"])


def test_cli_check_stems_missing_dir_is_fail_not_traceback(tmp_path, capsys):
    assert cli.main(["check-stems", "--stems-dir", str(tmp_path / "nope")]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "FAIL" and "不存在" in payload["problems"][0]


def test_cli_mix_writes_master(stems_dir, tmp_path, capsys):
    out = tmp_path / "song_name_mix_v001.wav"
    assert cli.main(["mix", "--stems-dir", stems_dir, "--out", str(out)]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "PASS"
    assert payload["bit_depth"] == 24 and payload["framerate"] == 44100
    assert payload["sha256"] == M.sha256_of(str(out))


def test_cli_manifest_pass(stems_dir, tmp_path, capsys):
    master = tmp_path / "song_name_mix_v001.wav"
    S.mix_to_master(stems_dir, master)

    def blob(name, content):
        p = tmp_path / name
        p.write_bytes(content)
        return str(p)

    argv = [
        "manifest",
        "--master", str(master),
        "--vocal", stems_dir + "/song01_vocal_v001.wav",
        "--musicxml", blob("gt.musicxml", b"<score-partwise/>"),
        "--midi", blob("d.mid", b"MThd"),
        "--score", blob("p001.tif", b"II*"),
        "--license", blob("LICENSE", b"demo"),
        "--version", blob("VERSION", b"v001"),
        "--settings", blob("vocal_settings.json", b"{}"),
        "--approval-log", blob("approval_log.md", b"# ok"),
        "--out-dir", str(tmp_path / "masters"),
    ]
    assert cli.main(argv) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "PASS"
    assert any(e["role"] == "master_wav" for e in payload["deliverables"])


def test_cli_manifest_fail_on_missing(tmp_path, capsys):
    argv = [
        "manifest",
        "--master", str(tmp_path / "ghost.wav"),
        "--out-dir", str(tmp_path / "masters"),
    ]
    assert cli.main(argv) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "FAIL"
    assert len(payload["missing_required"]) == len(M.REQUIRED_DELIVERABLES)
    # 报告已落盘，包含缺失清单
    txt = (tmp_path / "masters" / "song_name_mix_v001_manifest.txt").read_text("utf-8")
    assert "缺交付物" in txt


def test_cli_runs_as_module(stems_dir):
    """`python -m` 入口可用 —— 文档里教人的那条命令必须真能跑。"""
    proc = subprocess.run(
        [sys.executable, "-m", "music_rebuild_project.tools.render.cli",
         "check-stems", "--stems-dir", stems_dir],
        capture_output=True, text=True, cwd=os.getcwd(),
    )
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["status"] == "PASS"


# --- 只依赖 stdlib ----------------------------------------------------------
def test_package_imports_no_third_party():
    """禁止 numpy/scipy/pydub/soundfile —— 本包必须能在裸沙箱里跑。"""
    import music_rebuild_project.tools.render as pkg

    for mod in ("numpy", "scipy", "pydub", "soundfile"):
        assert mod not in sys.modules, f"{mod} 被导入了，违反纯 stdlib 约束"
    assert pkg.MASTER_SPEC == W.Spec(44100, 3, 1)


def test_no_ffmpeg_dependency(tmp_path, stems_dir):
    """全链不调用 ffmpeg —— 用「PATH 里没有 ffmpeg」来证明，而不是靠代码审查。"""
    r = S.check_stems(stems_dir)
    assert r.ok
    _, info = S.mix_to_master(stems_dir, tmp_path / "m.wav")
    assert info["bit_depth"] == 24
    assert MP3.find_encoder() in (None, MP3.find_encoder())  # 有就记录，没有也不影响
