"""g4_checklist.py（#61）回归测试。

策略
    **不读任何真实媒体**：所有 WAV 在 ``tmp_path`` 里用 stdlib 现场合成；
    manifest 直接构造（或经 ``tools/render/manifest`` 生成，验证链路一致）。
    断言点严格对齐 ``00_docs/gates/gate_G4.md``：
      G4-3 削波（峰值 ≥ -0.5dBFS）/ G4-4 静音占比 / G4-5 时长 ±1s /
      G4-9 四轨时长差 ≤5ms / G4-10 44.1k·24bit / G4-12 五类交付物 + 哈希非空。

    被脚本是 stdlib-only，所以这些用例在沙箱（无 numpy/ffmpeg）里必须全绿。
    最后用一个 ``sys.modules`` 探针钉死「不引入第三方」。
"""

from __future__ import annotations

import json
import math
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from music_rebuild_project.tools.checks import g4_checklist as G  # noqa: E402
from music_rebuild_project.tools.render import manifest as M  # noqa: E402
from music_rebuild_project.tools.render import stems as S  # noqa: E402
from music_rebuild_project.tools.render import wavio as W  # noqa: E402

SR = 44100
SCRIPT = Path(__file__).resolve().parents[1] / "g4_checklist.py"


# ---------------------------------------------------------------- WAV 合成
def tone(freq=440.0, seconds=1.0, amplitude=0.4, sr=SR, phase=0.0):
    """确定性正弦（无 RNG）—— 同参数永远同字节。"""
    n = int(round(seconds * sr))
    return [amplitude * math.sin(2.0 * math.pi * freq * (i / sr) + phase) for i in range(n)]


def write_wav(path, samples, *, sampwidth=3, sr=SR, channels=1):
    return W.write_wav(str(path), samples, framerate=sr, sampwidth=sampwidth, channels=channels)


def make_stems(tmp_path, *, seconds=1.0, amplitude=0.25, spread_frames=0):
    """四轨合规 stem → ``(stems_dir, {name: path})``。

    ``spread_frames``：给最后一轨多加若干帧，用于构造 G4-9 时长不一致。
    """
    d = tmp_path / "stems"
    d.mkdir(exist_ok=True)
    freqs = {"drums": 110.0, "bass": 55.0, "harmony": 220.0, "vocal": 330.0}
    paths = {}
    for i, name in enumerate(S.REQUIRED_STEMS):
        extra = spread_frames if name == S.REQUIRED_STEMS[-1] else 0
        sig = tone(freq=freqs[name], seconds=seconds, amplitude=amplitude)
        if extra:
            sig = sig + [0.0] * extra
        paths[name] = write_wav(d / f"song01_{name}_v001.wav", sig)
    return str(d), paths


def make_manifest(tmp_path, *, master=None, vocal=None, records=True):
    """用 #59 的 ``check_deliverables`` 生成 manifest JSON 路径（走真实链路）。"""
    master = master or write_wav(tmp_path / "master.wav", tone(220.0, 1.0, 0.5))
    vocal = vocal or write_wav(tmp_path / "vocal.wav", tone(330.0, 1.0, 0.25))
    files = {
        "source_score_image": master,
        "approved_musicxml": master,
        "vocal_wav": vocal,
        "arrangement_midi": master,
        "master_wav": master,
    }
    recs = {"license": True, "version": True, "settings": True, "approval_log": True} \
        if records else {}
    m = M.check_deliverables(files, records=recs)
    p = tmp_path / "manifest.json"
    p.write_text(m.to_json(), encoding="utf-8")
    return str(p), master


def ctx(**kw):
    return G.build_context(**kw)


def by_id(report, gate_id):
    for it in report.items:
        if it.id == gate_id:
            return it
    raise AssertionError(f"report missing {gate_id}")


# ---------------------------------------------------------------- 结构
def test_checklist_covers_g4_1_through_12():
    """检查表必须覆盖 G4-1~G4-12，一条不漏、顺序对齐 gate 表。"""
    ids = [gid for gid, _ in G.CHECK_ORDER]
    assert ids == [f"G4-{i}" for i in range(1, 13)]


def test_report_ok_is_failure_free():
    r = G.ChecklistReport()
    r.add(G.Item("G4-1", "x", G.SKIP))
    assert r.ok  # 只有 SKIP → 仍算通过
    r.add(G.Item("G4-3", "y", G.FAIL))
    assert not r.ok


def test_to_dict_shape():
    r = G.run_checklist(ctx())
    d = r.to_dict()
    assert d["gate"] == "G4"
    assert set(d["counts"]) == {G.PASS, G.FAIL, G.SKIP}
    assert len(d["items"]) == 12
    assert d["status"] in (G.PASS, G.FAIL)


# ---------------------------------------------------------------- G4-1/2/6/7/8/11 SKIP
@pytest.mark.parametrize("gate_id", ["G4-1", "G4-2", "G4-6", "G4-7", "G4-8", "G4-11"])
def test_unmeasurable_items_are_skip_not_fail(gate_id):
    """不可纯 stdlib 判定的条目显式 SKIP，且带 needs 说明，不报错。"""
    r = G.run_checklist(ctx())
    it = by_id(r, gate_id)
    assert it.state == G.SKIP
    assert it.needs
    assert any("SKIP" in p for p in it.problems)
    assert gate_id not in r.to_dict()["failed"]


# ---------------------------------------------------------------- G4-3 削波
def test_g4_3_normal_signal_passes(tmp_path):
    mp, master = make_manifest(tmp_path)
    r = G.run_checklist(ctx(manifest_path=mp))
    it = by_id(r, "G4-3")
    assert it.state == G.PASS
    assert it.evidence["master"]["peak_dbfs"] < G.G4_CLIP_PEAK_DBFS


def test_g4_3_suspected_clip_above_minus_half_dbfs_fails(tmp_path):
    """峰值 ≥ -0.5dBFS 视为疑似削波（gate_G4 明文比 0dBFS 更严）。"""
    hot = write_wav(tmp_path / "hot.wav", tone(220.0, 0.5, 1.0))  # 满幅正弦 → 0dBFS
    mp, _ = make_manifest(tmp_path, master=hot)
    r = G.run_checklist(ctx(manifest_path=mp))
    it = by_id(r, "G4-3")
    assert it.state == G.FAIL
    assert it.evidence["master"]["clipped"] is True
    assert any("疑似削波" in p for p in it.problems)


def test_g4_3_threshold_boundary_is_inclusive(tmp_path):
    """恰好 -0.5dBFS 应判疑似削波（≥ 语义，不是 >）。"""
    amp = 10.0 ** (G.G4_CLIP_PEAK_DBFS / 20.0)
    sig = [amp if i % 2 == 0 else -amp for i in range(SR)]  # 精确摆到阈值
    hot = write_wav(tmp_path / "bound.wav", sig)
    r = G.run_checklist(ctx(manifest_path=None, stems_dir=None, mp3_path=None))
    # 直接用检查函数，避免 manifest 干扰
    item = G.check_g4_3({"audio_targets": lambda: [("bound", hot)]})
    assert item.state == G.FAIL
    assert item.evidence["bound"]["clipped"] is True


# ---------------------------------------------------------------- G4-4 静音
def test_g4_4_silence_fails(tmp_path):
    silent = write_wav(tmp_path / "silent.wav", [0.0] * SR)
    item = G.check_g4_4({"audio_targets": lambda: [("master", silent)]})
    assert item.state == G.FAIL
    assert item.evidence["master"]["rms_dbfs"] is None
    assert any("全静音" in p for p in item.problems)


def test_g4_4_long_tail_silence_fails(tmp_path):
    """连续静音 ≥1s → 异常静音段，FAIL。"""
    sig = tone(220.0, 1.0, 0.3) + [0.0] * (SR * 2)
    p = write_wav(tmp_path / "tail.wav", sig)
    item = G.check_g4_4({"audio_targets": lambda: [("master", p)]})
    assert item.state == G.FAIL
    assert item.evidence["master"]["longest_silence_sec"] >= G.G4_MIN_SILENCE_SEC


def test_g4_4_short_fade_gap_passes(tmp_path):
    """淡入淡出的零星静音帧不触发（占比与最长段都在限内）。"""
    sig = [0.0] * 100 + tone(220.0, 1.0, 0.3) + [0.0] * 100
    p = write_wav(tmp_path / "fade.wav", sig)
    item = G.check_g4_4({"audio_targets": lambda: [("master", p)]})
    assert item.state == G.PASS


# ---------------------------------------------------------------- G4-5 时长
def test_g4_5_within_tolerance_passes(tmp_path):
    mp, _ = make_manifest(tmp_path)
    r = G.run_checklist(ctx(manifest_path=mp, duration_expected=1.0))
    assert by_id(r, "G4-5").state == G.PASS


def test_g4_5_beyond_tolerance_fails(tmp_path):
    mp, _ = make_manifest(tmp_path)
    r = G.run_checklist(ctx(manifest_path=mp, duration_expected=5.0))
    it = by_id(r, "G4-5")
    assert it.state == G.FAIL
    assert it.evidence["error_sec"] > 1.0


def test_g4_5_without_expected_is_skip(tmp_path):
    mp, _ = make_manifest(tmp_path)
    r = G.run_checklist(ctx(manifest_path=mp))
    assert by_id(r, "G4-5").state == G.SKIP


# ---------------------------------------------------------------- G4-9 stem
def test_g4_9_four_stems_passes(tmp_path):
    sd, _ = make_stems(tmp_path)
    item = G.check_g4_9({"stem_refs": lambda: sd})
    assert item.state == G.PASS
    assert item.evidence["duration_spread_sec"] == 0.0
    assert sorted(item.evidence["stems_present"]) == sorted(S.REQUIRED_STEMS)


def test_g4_9_duration_spread_beyond_5ms_fails(tmp_path):
    """时长极差 >5ms（≈221 帧）→ FAIL。"""
    sd, _ = make_stems(tmp_path, spread_frames=int(SR * 0.02))
    item = G.check_g4_9({"stem_refs": lambda: sd})
    assert item.state == G.FAIL
    assert item.evidence["duration_spread_sec"] > S.TOLERANCE_SECONDS


def test_g4_9_missing_stem_fails(tmp_path):
    sd, paths = make_stems(tmp_path)
    (Path(sd) / "song01_bass_v001.wav").unlink()
    item = G.check_g4_9({"stem_refs": lambda: sd})
    assert item.state == G.FAIL
    assert any("缺 stem" in p for p in item.problems)


# ---------------------------------------------------------------- G4-10 规格
def test_g4_10_correct_spec_passes(tmp_path):
    mp, _ = make_manifest(tmp_path)
    it = by_id(G.run_checklist(ctx(manifest_path=mp)), "G4-10")
    assert it.state == G.PASS
    assert it.evidence["wav"]["sample_rate"] == 44100
    assert it.evidence["wav"]["bit_depth"] == 24
    assert it.evidence["mp3"]["present"] is False  # 无编码器，缺失不判 FAIL


def test_g4_10_16bit_master_fails(tmp_path):
    bad = write_wav(tmp_path / "m16.wav", tone(220.0, 1.0, 0.5), sampwidth=2)
    item = G.check_g4_10({
        "master_path": lambda: bad,
        "mp3_path": lambda: None,
    })
    assert item.state == G.FAIL
    assert any("位深" in p for p in item.problems)


def test_g4_10_wrong_sample_rate_fails(tmp_path):
    bad = write_wav(tmp_path / "m48.wav", tone(220.0, 1.0, 0.5, sr=48000), sr=48000)
    item = G.check_g4_10({"master_path": lambda: bad, "mp3_path": lambda: None})
    assert item.state == G.FAIL
    assert any("采样率" in p for p in item.problems)


def test_g4_10_real_mp3_is_checked_not_encoded(tmp_path):
    """MP3 缺席时报告 unavailable_reason，位率不猜（None）。"""
    mp, _ = make_manifest(tmp_path)
    it = by_id(G.run_checklist(ctx(manifest_path=mp)), "G4-10")
    assert it.evidence["mp3"]["bitrate_kbps"] is None
    assert "unavailable_reason" in it.evidence["mp3"]


# ---------------------------------------------------------------- G4-12 齐全
def test_g4_12_complete_passes(tmp_path):
    mp, _ = make_manifest(tmp_path)
    it = by_id(G.run_checklist(ctx(manifest_path=mp)), "G4-12")
    assert it.state == G.PASS
    for role in ("master_wav", "vocal_wav", "approved_musicxml",
                 "arrangement_midi", "source_score_image"):
        assert role in it.evidence["deliverable_roles"]


def test_g4_12_missing_deliverable_fails(tmp_path):
    master = write_wav(tmp_path / "m.wav", tone(220.0, 0.2, 0.5))
    m = M.check_deliverables({"master_wav": master})  # 只给一类
    p = tmp_path / "m.json"
    p.write_text(m.to_json(), encoding="utf-8")
    it = by_id(G.run_checklist(ctx(manifest_path=str(p))), "G4-12")
    assert it.state == G.FAIL
    assert any("缺交付物" in x for x in it.problems)


def test_g4_12_empty_sha256_fails(tmp_path):
    """构造 sha256 为空的 manifest 条目 → FAIL（哈希是分发的唯一凭证）。"""
    master = write_wav(tmp_path / "m.wav", tone(220.0, 0.2, 0.5))
    m = M.check_deliverables(
        {"master_wav": master, "vocal_wav": master, "approved_musicxml": master,
         "arrangement_midi": master, "source_score_image": master},
        records={"license": True, "version": True, "settings": True, "approval_log": True},
    )
    d = m.to_dict()
    d["deliverables"][0]["sha256"] = ""  # 抹掉一个哈希
    p = tmp_path / "m.json"
    p.write_text(json.dumps(d), encoding="utf-8")
    it = by_id(G.run_checklist(ctx(manifest_path=str(p))), "G4-12")
    assert it.state == G.FAIL
    assert it.evidence["empty_sha256"]


def test_g4_12_without_manifest_is_skip():
    it = by_id(G.run_checklist(ctx()), "G4-12")
    assert it.state == G.SKIP


# ---------------------------------------------------------------- 退出码语义
def _run_cli(args):
    return subprocess.run([sys.executable, str(SCRIPT)] + args,
                          capture_output=True, text=True, cwd=str(REPO_ROOT))


def test_cli_exit_0_when_no_fail(tmp_path):
    sd, _ = make_stems(tmp_path)
    mp, _ = make_manifest(tmp_path)
    r = _run_cli(["--manifest", mp, "--stems-dir", sd,
                  "--duration-expected", "1.0", "--json"])
    assert r.returncode == 0, r.stdout + r.stderr
    assert json.loads(r.stdout)["status"] == "PASS"


def test_cli_exit_1_on_fail(tmp_path):
    mp, _ = make_manifest(tmp_path)
    r = _run_cli(["--manifest", mp, "--duration-expected", "9.0"])
    assert r.returncode == 1
    assert "FAIL" in r.stdout


def test_cli_exit_2_on_bad_manifest(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("not json", encoding="utf-8")
    assert _run_cli(["--manifest", str(bad)]).returncode == 2
    assert _run_cli(["--manifest", str(tmp_path / "nope.json")]).returncode == 2


def test_cli_json_machine_readable(tmp_path):
    mp, _ = make_manifest(tmp_path)
    r = _run_cli(["--manifest", mp, "--duration-expected", "1.0", "--json"])
    d = json.loads(r.stdout)
    for item in d["items"]:
        assert item["state"] in (G.PASS, G.FAIL, G.SKIP)
        assert item["id"].startswith("G4-")


def test_cli_writes_report_file(tmp_path):
    mp, _ = make_manifest(tmp_path)
    out = tmp_path / "report.json"
    r = _run_cli(["--manifest", mp, "--json", "--out", str(out)])
    assert r.returncode == 0
    assert json.loads(out.read_text(encoding="utf-8"))["gate"] == "G4"


# ---------------------------------------------------------------- 无第三方依赖
def test_no_third_party_imports():
    """纯 stdlib：numpy / soundfile / scipy / pydub 不得被引入。"""
    forbidden = ("numpy", "soundfile", "scipy", "pydub")
    saved = {k: sys.modules.pop(k, None) for k in forbidden}
    try:
        _reload_module()
        leaked = [k for k in forbidden if k in sys.modules]
        assert not leaked, f"引入了第三方库：{leaked}"
    finally:
        for k, v in saved.items():
            if v is not None:
                sys.modules[k] = v


def test_script_does_not_spawn_external_processes():
    """脚本不 fork 外部进程 —— 「不依赖 ffmpeg」是可验证的：源码里没有进程调用。

    `ffmpeg` 只会作为 ``needs`` 说明字符串出现，绝不作为被执行的命令。
    """
    src = SCRIPT.read_text(encoding="utf-8")
    for forbidden in ("subprocess.", "os.system(", "os.popen(", "shutil.which("):
        assert forbidden not in src, f"脚本不应包含 {forbidden}"
    assert "ffmpeg" in src  # 仅出现在 needs 说明里


def _reload_module():
    import importlib
    importlib.reload(G)
