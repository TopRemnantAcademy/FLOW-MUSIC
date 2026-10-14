"""`manifest`：G4-12 交付物齐全性 + SHA-256 指纹 + MP3 占位。

两条主线：
  - 哈希**必须**是可独立复算的（用 `hashlib` 直接对同一文件再算一遍对答案）；
  - 缺失**必须**被报出（`missing_required` / `missing_records`），不静默通过。
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib

import pytest

from music_rebuild_project.tools.render import manifest as M
from music_rebuild_project.tools.render import mp3 as MP3
from music_rebuild_project.tools.render import wavio as W


# --- 夹具：一个「齐全」的分发文件夹 ----------------------------------------
@pytest.fixture
def dist(tmp_path, four_stems):
    """五类交付物齐全的分发文件夹 + 四类记录。返回构造好的入参字典。"""
    d = tmp_path / "dist"
    d.mkdir()

    def blob(name, content):
        p = d / name
        p.write_bytes(content)
        return str(p)

    files = {
        "source_score_image": blob("song01_source_p001_v001.tif", b"II*\x00tif-bytes" * 7),
        "approved_musicxml": blob("ground_truth.musicxml", b"<?xml version='1.0'?><score-partwise/>"),
        "vocal_wav": four_stems["vocal"],
        "arrangement_midi": [
            blob("song01_drums_main_v001.mid", b"MThd\x00\x00\x00\x06" + b"\x00" * 12),
            blob("song01_bass_v001.mid", b"MThd\x00\x00\x00\x06" + b"\x01" * 12),
        ],
        "master_wav": blob("song_name_mix_v001.wav", pathlib.Path(four_stems["drums"]).read_bytes()),
    }
    records = {
        "license": blob("LICENSE.txt", b"CC-BY-4.0 (demo)"),
        "version": blob("VERSION.txt", b"song01_song_name_mix_v001\n"),
        "settings": blob("vocal_settings.json", b'{"engine":"demo"}'),
        "approval_log": blob("approval_log.md", b"# approval log\n- G4 pending\n"),
    }
    return {"files": files, "records": records, "dir": str(d)}


# --- 及格线 ----------------------------------------------------------------
def test_complete_deliverables_pass(dist):
    m = M.check_deliverables(dist["files"], records=dist["records"])
    assert m.ok, (m.missing_required, m.missing_records, m.problems)
    assert not m.missing_required and not m.missing_records
    assert m.to_dict()["status"] == "PASS"
    assert m.to_dict()["gate"] == "G4-12"


def test_all_five_roles_are_covered(dist):
    m = M.check_deliverables(dist["files"], records=dist["records"])
    roles = {e.role for e in m.entries}
    for role in M.REQUIRED_DELIVERABLES:
        assert any(r == role or r.startswith(role + "[") for r in roles), role


# --- 哈希正确性 -------------------------------------------------------------
def test_sha256_matches_independent_computation(dist):
    """用 `hashlib` 直接再算一遍对答案 —— 哈希算错了 manifest 就毫无意义。"""
    m = M.check_deliverables(dist["files"], records=dist["records"])
    for e in m.entries:
        plain = hashlib.sha256(open(e.path, "rb").read()).hexdigest()
        assert e.sha256 == plain, f"{e.role} 哈希不符"
        assert e.bytes == os.path.getsize(e.path)
        assert len(e.sha256) == 64


def test_known_hash_vector(tmp_path):
    """空串的 SHA-256 是公开常量 —— 用它防「把路径当内容哈希」这类低级错。"""
    p = tmp_path / "empty.bin"
    p.write_bytes(b"")
    assert M.sha256_of(p) == hashlib.sha256(b"").hexdigest()
    assert M.sha256_of(p) == (
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    )


def test_sha256_hashes_content_not_path(tmp_path):
    a = tmp_path / "a.bin"
    b = tmp_path / "b.bin"
    a.write_bytes(b"same")
    b.write_bytes(b"same")
    assert M.sha256_of(a) == M.sha256_of(b)


def test_sha256_changes_with_content(tmp_path):
    p = tmp_path / "c.bin"
    p.write_bytes(b"one")
    first = M.sha256_of(p)
    p.write_bytes(b"two")
    assert M.sha256_of(p) != first


def test_sha256_missing_file_raises(tmp_path):
    with pytest.raises(W.MissingFileError):
        M.sha256_of(tmp_path / "nope.bin")


def test_duration_recorded_for_wav_but_not_for_midi(dist):
    m = M.check_deliverables(dist["files"], records=dist["records"])
    by_role = {e.role: e for e in m.entries}
    assert by_role["vocal_wav"].duration == pytest.approx(1.0, abs=1e-9)
    assert by_role["arrangement_midi[0]"].duration is None


def test_verify_manifest_detects_drift(dist):
    m = M.check_deliverables(dist["files"], records=dist["records"])
    assert M.verify_manifest(m) == []

    with open(dist["files"]["approved_musicxml"], "ab") as f:
        f.write(b"<!-- tampered -->")

    drift = M.verify_manifest(m)
    assert len(drift) == 1
    assert drift[0]["role"] == "approved_musicxml"
    assert drift[0]["expected"] != drift[0]["actual"]


def test_verify_manifest_detects_deletion(dist):
    m = M.check_deliverables(dist["files"], records=dist["records"])
    os.remove(dist["files"]["source_score_image"])
    drift = M.verify_manifest(m)
    assert drift and drift[0]["actual"] == ""


# --- 失败路径 ①：缺交付物 ---------------------------------------------------
@pytest.mark.parametrize("dropped", list(M.REQUIRED_DELIVERABLES))
def test_missing_deliverable_is_reported(dist, dropped):
    files = dict(dist["files"])
    files.pop(dropped)
    m = M.check_deliverables(files, records=dist["records"])

    assert not m.ok
    assert any(dropped in x for x in m.missing_required), m.missing_required
    assert m.to_dict()["status"] == "FAIL"


def test_nonexistent_deliverable_path_is_reported(dist, tmp_path):
    files = dict(dist["files"])
    files["master_wav"] = str(tmp_path / "ghost_mix_v001.wav")
    m = M.check_deliverables(files, records=dist["records"])
    assert not m.ok
    assert any("master_wav" in x and "不存在" in x for x in m.missing_required)


def test_empty_midi_list_is_reported(dist):
    files = dict(dist["files"])
    files["arrangement_midi"] = []
    m = M.check_deliverables(files, records=dist["records"])
    assert any("arrangement_midi" in x for x in m.missing_required)


def test_multi_file_role_reports_the_missing_one(dist, tmp_path):
    files = dict(dist["files"])
    files["arrangement_midi"] = dist["files"]["arrangement_midi"] + [
        str(tmp_path / "song01_guitar_v001.mid")
    ]
    m = M.check_deliverables(files, records=dist["records"])
    assert any("song01_guitar_v001.mid" in x for x in m.missing_required)
    assert any(e.role.startswith("arrangement_midi[") for e in m.entries)


# --- 失败路径 ②：缺记录 -----------------------------------------------------
@pytest.mark.parametrize("dropped", list(M.REQUIRED_RECORDS))
def test_missing_record_is_reported(dist, dropped):
    records = dict(dist["records"])
    records.pop(dropped)
    m = M.check_deliverables(dist["files"], records=records)

    assert not m.ok
    assert any(dropped in x for x in m.missing_records), m.missing_records
    # 交付物是齐的 —— 失败必须精确地归因到「记录」，不能糊成一片
    assert not m.missing_required


def test_record_path_must_exist(dist, tmp_path):
    records = dict(dist["records"])
    records["approval_log"] = str(tmp_path / "not_here.md")
    m = M.check_deliverables(dist["files"], records=records)
    assert any("approval_log" in x and "不存在" in x for x in m.missing_records)


def test_falsy_record_value_is_missing(dist):
    records = dict(dist["records"])
    records["license"] = None
    m = M.check_deliverables(dist["files"], records=records)
    assert any("license" in x for x in m.missing_records)


# --- MP3：占位与诚实边界 ----------------------------------------------------
def test_mp3_absent_reports_reason_not_silence(dist):
    m = M.check_deliverables(dist["files"], records=dist["records"], mp3_path=None)
    mp3 = m.mp3
    assert mp3["present"] is False and mp3["available"] is False
    assert "无 MP3 编码器" in mp3["unavailable_reason"]
    assert mp3["expected_spec"]["bitrate_kbps"] == 320
    # MP3 缺失**不**污染交付物清单：G4-12 的五类是 WAV/MIDI/XML/图
    assert m.ok, (m.missing_required, m.missing_records)
    assert mp3["counted_as_missing"] is False


def test_renamed_wav_as_mp3_is_rejected(dist, tmp_path):
    """把 WAV 改名成 .mp3 必须被识破 —— 伪装比缺失更坏。

    这条是**回归测试**：早期版本的探测只看 MPEG frame sync，PCM 里
    偶然出现的 `FF Ex` 字节对被判成合法帧头，改名文件因此蒙混过关。
    现在先查容器的魔数（RIFF），命中即 `foreign:wave/riff`。
    """
    fake = tmp_path / "song_name_mix_v001.mp3"
    fake.write_bytes(pathlib.Path(dist["files"]["master_wav"]).read_bytes())

    status = M.check_mp3(str(fake))
    assert status["present"] is True
    assert status["available"] is False
    assert "伪装" in status["unavailable_reason"]
    assert status["probe"] == "foreign:wave/riff"


@pytest.mark.parametrize(
    "magic,kind",
    [
        (b"RIFF", "wave/riff"),
        (b"FORM", "aiff"),
        (b"fLaC", "flac"),
        (b"OggS", "ogg"),
        (b"\x00\x00\x00\x18ftyp", "mp4"),
    ],
)
def test_other_containers_rejected_by_magic(tmp_path, magic, kind):
    p = tmp_path / "x.mp3"
    p.write_bytes(magic + b"\x00" * 512)
    status = M.check_mp3(str(p))
    assert status["available"] is False
    assert status["probe"] == f"foreign:{kind}"


def test_truncated_file_probe():
    """短于 4 字节的文件不能崩，也不要假装读懂了。"""
    import tempfile

    p = pathlib.Path(tempfile.mkdtemp()) / "t.mp3"
    p.write_bytes(b"AB")
    assert M._probe_mp3(str(p)) == "too-short"


def test_real_mp3_header_is_accepted(dist, tmp_path):
    """一段带 ID3 tag 的伪 MP3：探测层应放行（位率仍标 N/A，不猜）。"""
    p = tmp_path / "song_name_mix_v001.mp3"
    p.write_bytes(b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\xff\xfb\x90\x64" + b"\x00" * 200)

    status = M.check_mp3(str(p))
    assert status["present"] and status["available"]
    assert status["probe"] == "id3"
    spec = MP3.validate_spec(str(p))
    assert spec["ok"] is True and spec["bitrate_kbps"] is None
    assert "ffprobe" in spec["note"]


def test_no_encoder_state_is_reported():
    """沙箱断言：找不到也没关系，但必须**说出来**而不是空手返回。"""
    found = MP3.find_encoder()
    if found is None:
        assert MP3.NO_ENCODER_REASON in MP3.NO_ENCODER_REASON
        result = MP3.transcode_placeholder("in.wav", "out.mp3")
        assert result["ok"] is False
        assert "编码器" in result["reason"] or "不存在" in result["reason"]


def test_transcode_placeholder_never_runs_by_default(dist, tmp_path):
    """默认不代跑编码：即使有 ffmpeg，`run=False` 也只给命令。"""
    result = MP3.transcode_placeholder(dist["files"]["master_wav"], str(tmp_path / "o.mp3"))
    assert result["ok"] is False
    assert result["command"].startswith("ffmpeg") or result["command"].startswith("lame")
    assert not (tmp_path / "o.mp3").exists()


def test_transcode_placeholder_reports_missing_source(tmp_path):
    result = MP3.transcode_placeholder(str(tmp_path / "gone.wav"), str(tmp_path / "o.mp3"))
    assert result["ok"] is False and "不存在" in result["reason"]


def test_planned_command_carries_320k():
    assert "320" in MP3.planned_command("a.wav", "b.mp3")


# --- 落盘 -------------------------------------------------------------------
def test_write_manifest_emits_txt_and_json(dist, tmp_path):
    m = M.check_deliverables(dist["files"], records=dist["records"])
    out = tmp_path / "masters"
    paths = M.write_manifest(m, str(out), stem="song_name_mix_v001")

    assert [os.path.basename(p) for p in paths] == [
        "song_name_mix_v001_manifest.txt",
        "song_name_mix_v001_manifest.json",
    ]
    txt = open(paths[0], encoding="utf-8").read()
    assert "gate G4-12" in txt and "sha256" in txt
    assert "song_name_mix_v001.wav" in txt

    js = json.loads(open(paths[1], encoding="utf-8").read())
    assert js["hash_algo"] == "sha256"
    assert js["status"] == "PASS"
    assert len(js["deliverables"]) == len(m.entries)


def test_manifest_text_lists_missing_for_fail_case(dist, tmp_path):
    files = dict(dist["files"])
    files.pop("vocal_wav")
    m = M.check_deliverables(files, records=dist["records"])
    paths = M.write_manifest(m, str(tmp_path / "out"))
    txt = open(paths[0], encoding="utf-8").read()
    assert "status: FAIL" in txt
    assert "缺交付物" in txt and "vocal_wav" in txt


def test_manifest_json_is_utf8_not_escaped(dist, tmp_path):
    """中文描述不能被转义成 \\uXXXX —— 报告是给人看的。"""
    m = M.check_deliverables(dist["files"], records={})
    paths = M.write_manifest(m, str(tmp_path / "out"))
    raw = open(paths[1], encoding="utf-8").read()
    assert "许可信息" in raw and "\\u8bb8" not in raw
