"""#13 命名规范 + 批准本冻结规则的可执行测试。

三条纪律：

1. **不碰真实媒体、不碰数据目录。** 全部在 `tmp_path` / `tempfile` 里造临时文件；
   测试从不写到 `music_rebuild_project/02_source/` `04_approved/` 等任何数据目录。
   一个会写数据目录的测试，跑一次就把被测对象的输入改了。
2. **逐字段断言。** 哈希记录格式不给「大概对」的余地：四段、两个空格、UTC 结尾 Z ——
   全都要按 `gate_G2.md` 的例子逐字段核。
3. **篡改演示必须真篡改。** 直接改文件字节，然后确认 `verify_frozen` 抓得住。
   如果测试自己"模拟"篡改（比如直接改记录里的哈希），那验的是解析逻辑，
   不是校验逻辑 —— 那种测试全绿也不说明工具能发现改动。

关于时间：所有冻结用例都**注入** `utc_time`。不是为了好看，是因为
「记录里的时间戳格式正确」这件事必须能被钉死；让测试读系统时钟，
就只能断言"大概是现在"，等于没断言。
"""

from __future__ import annotations

import os
import stat
import sys
import tempfile
from pathlib import Path

import pytest

# 被测模块在 tools/naming/ 下，测试在 tools/naming/tests/ 下。
# 不加这个 sys.path，`pytest tools/naming/tests/` 会 ImportError ——
# 仓库里没有任何 conftest 帮我们做这件事（backend 有一份，但那是 backend 的）。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import freeze  # noqa: E402
import naming  # noqa: E402

FIXED_UTC = "2026-10-13T00:00:00Z"
APPROVER = "朴素"


# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------

@pytest.fixture()
def approved_dir(tmp_path):
    """模拟 `04_approved/<song_id>/`：**临时目录**，不是仓库数据目录。"""
    d = tmp_path / "04_approved" / "song01"
    d.mkdir(parents=True)
    return d


def _write(path, content: str = "<score-partwise/>") -> str:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return str(p)


def _freeze(path, *, approver=APPROVER, utc_time=FIXED_UTC) -> dict:
    return freeze.freeze_file(path, approver, utc_time=utc_time, read_only=False)


def _tamper(path, content: str = "<score-partwise><part id='P1'/></score-partwise>") -> None:
    """真的改文件：先恢复写位，再覆写内容。"""
    p = Path(path)
    os.chmod(p, 0o644)
    p.write_text(content, encoding="utf-8")


# ===========================================================================
# 一、命名规范：song_id
# ===========================================================================

@pytest.mark.parametrize("song_id", ["song01", "song02", "song99", "song100"])
def test_song_id_valid_forms(song_id):
    """`glossary.md`：ASCII 定长 `songNN`，超 99 用三位。"""
    assert naming.validate(song_id, kind="song_id") == []


@pytest.mark.parametrize(
    "bad",
    ["song1", "song0001", "Song01", "song-01", "song_01", "곡01", "song 01", "song", "1song"],
)
def test_song_id_invalid_forms(bad):
    violations = naming.validate(bad, kind="song_id")
    assert violations, f"{bad!r} 应被判为非法 song_id"
    assert any(v.code.startswith("E_") for v in violations)


# ===========================================================================
# 二、命名规范：版本后缀
# ===========================================================================

@pytest.mark.parametrize("name", ["song01_review_v001.musicxml", "song01_bass_v312.mid"])
def test_version_suffix_valid(name):
    assert naming.validate(name) == []


@pytest.mark.parametrize("name", ["song01_review_v1.musicxml", "song01_review_v0001.musicxml"])
def test_version_suffix_must_be_vNNN(name):
    codes = {v.code for v in naming.validate(name)}
    assert "E_VERSION_FORM" in codes, f"{name!r} 的版本号不是 vNNN，应被拦下"


def test_missing_version_is_rejected_for_primary_deliverable():
    """deliverables.md：正式交付物必须带版本号 —— `song01_review.musicxml` 不合法。"""
    codes = {v.code for v in naming.validate("song01_review.musicxml")}
    assert "E_NO_VERSION" in codes


def test_version_of_and_next_version():
    assert naming.version_of("song01_review_v001.musicxml") == 1
    assert naming.version_of("song01_review.musicxml") == 0
    assert naming.next_version("song01_review_v001.musicxml") == "song01_review_v002.musicxml"
    assert naming.next_version("song01_review_v009.mid") == "song01_review_v010.mid"


def test_next_version_of_fixed_frozen_name_starts_at_v002():
    """固定名 `ground_truth.musicxml` 就是 v001，所以下一个是 v002 —— 不是 v001。"""
    assert naming.next_version("ground_truth.musicxml") == "ground_truth_v002.musicxml"
    assert naming.next_version("approved_vocal_pilot.wav") == "approved_vocal_pilot_v002.wav"


def test_next_version_rejects_unknown_name_without_version():
    with pytest.raises(ValueError):
        naming.next_version("something_else.musicxml")


# ===========================================================================
# 三、命名规范：G4-10 master 输出
# ===========================================================================

@pytest.mark.parametrize(
    "name",
    [
        "song01_mix_v001.wav",          # README 实例化形态
        "flow_demo_mix_v001.wav",       # #122 MockDAW 实际产出形态（同一规则）
        "song_name_mix_v001.wav",       # gate_G4 G4-10 字面占位符形态
        "song100_mix_v012.wav",
    ],
)
def test_master_mix_valid(name):
    assert naming.validate(name, kind="master_mix") == []


@pytest.mark.parametrize(
    "name,expect",
    [
        ("song_mix_v002_v001.wav", "E_MASTER_MIX_NAME"),  # 双版本号
        ("song01_mix_v1.wav", "E_MASTER_MIX_NAME"),      # 版本不定长
        ("song01_mix.wav", "E_MASTER_MIX_NAME"),         # 无版本
        ("song01_mix_v001.mp3", "E_MASTER_MIX_NAME"),    # G4-10 钉的是 wav
        ("song01_Mix_v001.wav", "E_UPPER_CASE"),         # 大写混用风险
        ("song01 mix v001.wav", "E_SPACE"),              # 空格
        ("찬양하라_mix_v001.wav", "E_NON_ASCII"),         # 非 ASCII
        ("song01_mix_v001.wav.bak", "E_MASTER_MIX_NAME"),  # 备份后缀
    ],
)
def test_master_mix_invalid(name, expect):
    codes = {v.code for v in naming.validate(name, kind="master_mix")}
    assert expect in codes, f"{name!r} 期望 {expect}，实得 {codes}"


# ===========================================================================
# 四、命名规范：禁用字符（README「Windows 路径风险规避」）
# ===========================================================================

@pytest.mark.parametrize(
    "name,code",
    [
        ("song01_찬양하라_v001.musicxml", "E_NON_ASCII"),
        ("song01_my song_v001.musicxml", "E_SPACE"),
        ("song01_Stems_v001.musicxml", "E_UPPER_CASE"),
        ("song01_notes?.musicxml", "E_ILLEGAL_CHAR"),
        ("con.musicxml", "E_RESERVED_NAME"),
        ("song01_bass_v001.mid.", "E_TRAILING_DOT_OR_SPACE"),  # 尾部点会被 Windows 吞掉
    ],
)
def test_forbidden_chars(name, code):
    codes = {v.code for v in naming.validate(name)}
    assert code in codes, f"{name!r} 期望 {code}，实得 {codes}"


def test_segment_length_limit():
    long = "song01_" + "a" * 45 + "_v001.musicxml"
    codes = {v.code for v in naming.validate(long)}
    assert "E_SEGMENT_TOO_LONG" in codes


def test_directory_segments_are_checked_too():
    """大写目录名同样会在 NTFS 上撞车，不能只看文件名。"""
    codes = {v.code for v in naming.validate_relative_path("07_mix/song01/Stems/song01_drums_v001.wav")}
    assert "E_UPPER_CASE" in codes


# ===========================================================================
# 五、命名规范：目录约定与相对路径
# ===========================================================================

def test_repo_path_conventions_ok():
    assert naming.validate("02_source/song01/originals/song01_source_p001_v001.tif", kind="repo_path") == []
    assert naming.validate("07_mix/song01/masters/song01_mix_v001.wav", kind="repo_path") == []
    assert naming.validate("05_vocals/song01/vocal_wav/approved_vocal_pilot.wav", kind="repo_path") == []


def test_repo_path_flags_bad_song_id_in_tree():
    codes = {v.code for v in naming.validate("02_source/song1/originals/song1_source_p001_v001.tif", kind="repo_path")}
    assert "E_SONG_ID_FORM" in codes


def test_repo_path_flags_bad_master_name():
    codes = {v.code for v in naming.validate("07_mix/song01/masters/song01_mix.wav", kind="repo_path")}
    assert "E_MASTER_MIX_NAME" in codes


@pytest.mark.parametrize("bad", ["/abs/path.wav", "C:/x/y.wav", "\\\\server\\share\\x.wav"])
def test_absolute_paths_raise(bad):
    """绝对路径在搬迁后必然断链（07_mix/README「禁绝对路径」）—— 直接拒绝，不静默放过。"""
    with pytest.raises(ValueError):
        naming.validate_relative_path(bad)


def test_path_escape_is_a_violation_not_an_exception():
    codes = {v.code for v in naming.validate_relative_path("07_mix/../02_source/song01/x_v001.wav")}
    assert "E_PATH_ESCAPE" in codes


def test_unknown_kind_raises():
    with pytest.raises(ValueError):
        naming.validate("song01", kind="not-a-kind")


# ===========================================================================
# 六、冻结件命名（04_approved/）
# ===========================================================================

@pytest.mark.parametrize(
    "name",
    [
        "ground_truth.musicxml",                     # gate_G2 固定名
        "ground_truth.musicxml.sha256",              # 哈希记录
        "ground_truth_v002.musicxml",                # #13 版本派生
        "ground_truth_v002.musicxml.sha256",
        "approved_vocal_pilot.wav",                  # gate_G3 G3-10 固定名
        "approved_vocal_pilot.wav.sha256",
        "approved_vocal_pilot_v002.wav",
        "song01_approved_v001.mscz",
    ],
)
def test_frozen_names_ok(name):
    assert naming.validate_frozen_name(name) == [], f"{name!r} 应被认作合规冻结件"


@pytest.mark.parametrize("name", ["ground_truth_hash.txt", "ground_truth_sha.txt", "random.musicxml"])
def test_frozen_names_reject_unregistered_forms(name):
    codes = {v.code for v in naming.validate_frozen_name(name)}
    assert "E_FROZEN_NAME" in codes, f"{name!r} 不是 gate_G2 认可的冻结件名"


# ===========================================================================
# 七、哈希记录格式（与 gate_G2 示例逐字段对照）
# ===========================================================================

def test_hash_record_is_exactly_four_fields():
    """gate_G2 原文：`<sha256>  <file>  <utc_time>  <approver>`。四段，不多不少。"""
    sha = "0123456789abcdef" * 4
    line = freeze.format_hash_record(sha, "ground_truth.musicxml", FIXED_UTC, "mock-approver")
    assert line.endswith("\n")
    body = line.rstrip("\n")
    assert body.count("  ") == 3, "必须是两个空格分隔的四段"
    sha_field, file_field, time_field, approver_field = body.split("  ")
    assert sha_field == sha and len(sha_field) == 64
    assert file_field == "ground_truth.musicxml"
    assert time_field == "2026-10-13T00:00:00Z"
    assert approver_field == "mock-approver"


def test_hash_record_matches_gate_g2_documented_example():
    """gate_G2.md / orchestrator.py 里的实际写法，逐字段核。"""
    sha = "b9aae72f4ba52452c177f5528bbce127dedb90d8165d5ec237eeca7f2ad47c80"
    line = freeze.format_hash_record(sha, "ground_truth.musicxml", FIXED_UTC, "mock-approver")
    assert line == f"{sha}  ground_truth.musicxml  2026-10-13T00:00:00Z  mock-approver\n"


def test_utc_now_shape():
    """自有时间戳也必须落在 gate_G2 的形态里（秒精度 + 字面 Z，不是 +00:00）。"""
    now = freeze.utc_now()
    assert len(now) == 20 and now.endswith("Z") and now[10] == "T"
    freeze.parse_hash_record(f"{'a' * 64}  x.musicxml  {now}  me")  # 时间格式由解析器背书


@pytest.mark.parametrize(
    "text",
    [
        "",                                               # 空
        "a" * 64,                                         # 只有 1 段
        f"{'a' * 64}  f.musicxml  2026-10-13T00:00:00Z",   # 3 段
        f"{'a' * 64}  f  t  who  extra",                   # 5 段
        f"{'A' * 64}  f.musicxml  2026-10-13T00:00:00Z  who",   # 大写哈希
        f"short  f.musicxml  2026-10-13T00:00:00Z  who",         # 哈希长度不对
        f"{'a' * 64}  f.musicxml  2026-10-13 00:00:00  who",     # 时间格式不对
        f"{'a' * 64}  f.musicxml  2026-10-13T00:00:00+00:00  who",  # 不是字面 Z
        f"{'a' * 64}    f.musicxml  2026-10-13T00:00:00Z  who",     # 4 空格
    ],
)
def test_malformed_hash_records_are_rejected(text):
    with pytest.raises(ValueError):
        freeze.parse_hash_record(text)


def test_parse_hash_record_roundtrip():
    sha = freeze.sha256_file.__module__ and "a" * 64
    rec = freeze.parse_hash_record(freeze.format_hash_record(sha, "approved_vocal_pilot.wav", FIXED_UTC, APPROVER))
    assert rec == {"sha256": sha, "file": "approved_vocal_pilot.wav",
                   "utc_time": FIXED_UTC, "approver": APPROVER}


def test_format_rejects_empty_fields():
    """空字段会在 join 时静默塌列，必须直接抛，不能写出四段变三段的记录。"""
    for kwargs in (
        {"sha256": "", "file_name": "f", "utc_time": FIXED_UTC, "approver": "who"},
        {"sha256": "a" * 64, "file_name": "", "utc_time": FIXED_UTC, "approver": "who"},
        {"sha256": "a" * 64, "file_name": "f", "utc_time": "", "approver": "who"},
        {"sha256": "a" * 64, "file_name": "f", "utc_time": FIXED_UTC, "approver": ""},
    ):
        with pytest.raises(ValueError):
            freeze.format_hash_record(**kwargs)


# ===========================================================================
# 八、冻结 → 校验 → 篡改
# ===========================================================================

def test_freeze_writes_record_beside_file(approved_dir):
    path = _write(approved_dir / "ground_truth.musicxml")
    result = freeze.freeze_file(path, APPROVER, utc_time=FIXED_UTC, read_only=False)

    record = Path(path + ".sha256")
    assert record.exists(), "哈希记录必须与被冻结件同目录同前缀"
    assert "  " in record.read_text(encoding="utf-8")
    # 记录里的 <file> 段是**文件名**，不是路径（gate_G2 示例就是文件名的样子）
    assert freeze.read_hash_record(path)["file"] == "ground_truth.musicxml"
    assert result["sha256"] == freeze.sha256_file(path)


def test_freeze_then_verify_ok(approved_dir):
    path = _write(approved_dir / "ground_truth.musicxml")
    _freeze(path)
    assert freeze.verify_frozen(path).ok is True
    assert freeze.verify_frozen(path).status == "ok"


def test_verify_fails_after_tamper(approved_dir):
    """核心演示：冻结 → 原地篡改 → verify 必须失败（不是"可能"失败）。"""
    path = _write(approved_dir / "ground_truth.musicxml")
    _freeze(path)
    good = freeze.verify_frozen(path)
    assert good.ok

    _tamper(path, "<score-partwise><part id='P1'><note/></part></score-partwise>")

    bad = freeze.verify_frozen(path)
    assert bad.ok is False
    assert bad.status == "hash_mismatch"
    assert bad.expected == good.expected
    assert bad.actual != bad.expected
    assert freeze.sha256_file(path) == bad.actual


def test_tamper_detected_even_if_only_one_byte_changes(approved_dir):
    """一个字节也要抓住 —— 哈希校验的意义就是没有"差不多"。"""
    path = _write(approved_dir / "ground_truth.musicxml", "<score/>")
    _freeze(path)
    os.chmod(path, 0o644)
    Path(path).write_text("<score >", encoding="utf-8")  # 只多一个空格
    assert freeze.verify_frozen(path).status == "hash_mismatch"


def test_verify_reports_missing_file_and_missing_record(approved_dir):
    path = _write(approved_dir / "ground_truth.musicxml")
    assert freeze.verify_frozen(path).status == "missing_record"

    _freeze(path)
    os.chmod(path, 0o644)
    os.remove(path)
    assert freeze.verify_frozen(path).status == "missing_file"


def test_verify_reports_bad_record(approved_dir):
    path = _write(approved_dir / "ground_truth.musicxml")
    _freeze(path)
    Path(path + ".sha256").write_text("garbage\n", encoding="utf-8")
    assert freeze.verify_frozen(path).status == "bad_record"


def test_freeze_refuses_to_overwrite_existing_record(approved_dir):
    """#13：已冻结件**不得原地覆盖** —— 第二次 freeze 必须被拒。"""
    path = _write(approved_dir / "ground_truth.musicxml")
    _freeze(path, utc_time="2026-10-13T00:00:00Z")

    with pytest.raises(freeze.AlreadyFrozenError) as exc:
        freeze.freeze_file(path, "另一个人", utc_time="2026-10-14T00:00:00Z", read_only=False)

    err = exc.value
    assert err.next_path.endswith("ground_truth_v002.musicxml")
    assert err.record is not None and err.record["approver"] == APPROVER
    # 原记录**一个字节都没被动**（拒绝覆盖 = 真拒绝，不是"先删再写"）
    assert freeze.read_hash_record(path)["utc_time"] == "2026-10-13T00:00:00Z"


def test_freeze_refuses_even_when_record_is_unparseable(approved_dir):
    """记录读不动也一样拒绝：宁可不冻，也不覆盖一个不知道来历的冻结件。"""
    path = _write(approved_dir / "ground_truth.musicxml")
    Path(path + ".sha256").write_text("broken\n", encoding="utf-8")
    with pytest.raises(freeze.AlreadyFrozenError):
        freeze.freeze_file(path, APPROVER, read_only=False)


def test_freeze_requires_existing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        freeze.freeze_file(str(tmp_path / "not_there.musicxml"), APPROVER)


def test_freeze_rejects_non_canonical_frozen_name(tmp_path):
    """冻结一个叫 `whatever.musicxml` 的文件不合法：名字要能让人一眼看出这是哪一版。"""
    path = _write(tmp_path / "04_approved" / "song01" / "whatever.musicxml")
    with pytest.raises(ValueError) as exc:
        freeze.freeze_file(path, APPROVER, read_only=False)
    assert "E_FROZEN_NAME" in str(exc.value)


def test_freeze_does_not_create_record_when_name_is_bad(tmp_path):
    """被拒时**不得留下半个记录** —— 否则下次冻结会以为"已经冻过了"。"""
    path = _write(tmp_path / "bad_name.musicxml")
    with pytest.raises(ValueError):
        freeze.freeze_file(path, APPROVER, read_only=False)
    assert not os.path.exists(path + ".sha256")


# ===========================================================================
# 九、v002 版本派生（#13「此后任何修改生成 v002」）
# ===========================================================================

def test_derive_next_path_for_fixed_names(approved_dir):
    assert freeze.derive_next_path(approved_dir / "ground_truth.musicxml").endswith(
        os.path.join("song01", "ground_truth_v002.musicxml"))
    assert freeze.derive_next_path(approved_dir / "approved_vocal_pilot.wav").endswith(
        os.path.join("song01", "approved_vocal_pilot_v002.wav"))


def test_derive_next_path_increments_existing_version(approved_dir):
    assert freeze.derive_next_path(approved_dir / "ground_truth_v002.musicxml").endswith("ground_truth_v003.musicxml")
    assert freeze.derive_next_path(approved_dir / "song01_review_v009.mscz").endswith("song01_review_v010.mscz")


def test_full_cycle_v001_frozen_then_v002_is_a_separate_file(approved_dir):
    """完整流程：v001 冻结 → 想改 → 被拒 → 生成 v002 → v001 记录依旧有效。"""
    v001 = _write(approved_dir / "ground_truth.musicxml", "<score>v1</score>")
    freeze.freeze_file(v001, APPROVER, utc_time=FIXED_UTC, read_only=False)

    with pytest.raises(freeze.AlreadyFrozenError) as exc:
        freeze.freeze_file(v001, APPROVER, utc_time="2026-10-20T00:00:00Z", read_only=False)

    v002 = exc.value.next_path
    assert os.path.basename(v002) == "ground_truth_v002.musicxml"
    _write(v002, "<score>v1 + fix</score>")
    freeze.freeze_file(v002, APPROVER, utc_time="2026-10-20T00:00:00Z", read_only=False)

    assert freeze.sha256_file(v001) != freeze.sha256_file(v002)
    assert freeze.verify_frozen(v001).ok, "v001 必须仍然可校验（老版本不能因为出了 v002 就失效）"
    assert freeze.verify_frozen(v002).ok
    # 两份记录各自记自己的名字 —— 记录指向错文件是这套机制最阴的失效方式
    assert freeze.read_hash_record(v001)["file"] == "ground_truth.musicxml"
    assert freeze.read_hash_record(v002)["file"] == "ground_truth_v002.musicxml"
    assert freeze.frozen_names_are_consistent(v001) and freeze.frozen_names_are_consistent(v002)


def test_v002_path_is_naming_compliant(approved_dir):
    """派生出来的名字本身也要过 #13 命名校验 —— 不然冻结规则和命名规则会打架。"""
    assert naming.validate_frozen_name("ground_truth_v002.musicxml") == []
    assert naming.validate_frozen_name("ground_truth_v002.musicxml.sha256") == []


def test_check_no_overwrite_guard(approved_dir):
    path = _write(approved_dir / "ground_truth.musicxml")
    _freeze(path)
    frozen_path = path
    with pytest.raises(freeze.AlreadyFrozenError):
        freeze.check_no_overwrite(frozen_path, frozen_path)  # 典型的"原地覆盖"
    freeze.check_no_overwrite(frozen_path, freeze.derive_next_path(frozen_path))  # 新版本：放行


# ===========================================================================
# 十、只读位与批量校验
# ===========================================================================

def test_read_only_bit_toggle(approved_dir):
    path = _write(approved_dir / "ground_truth.musicxml")
    assert freeze.set_read_only(path, True).startswith("-r--")
    assert freeze.is_read_only(path) is True
    assert freeze.set_read_only(path, False).startswith("-rw-")
    assert freeze.is_read_only(path) is False


def test_freeze_sets_read_only_by_default(approved_dir):
    path = _write(approved_dir / "ground_truth.musicxml")
    freeze.freeze_file(path, APPROVER, utc_time=FIXED_UTC, read_only=True)
    assert freeze.is_read_only(path), "默认冻结应当同时置只读位"
    # 只读不影响校验（读权限还在 —— 置 0o000 就会连自己都验不了）
    assert freeze.verify_frozen(path).ok


def test_verify_all_does_not_short_circuit(approved_dir):
    a = _write(approved_dir / "ground_truth.musicxml")
    _freeze(a)
    b = _write(approved_dir / "approved_vocal_pilot.wav", "RIFF....")
    _freeze(b)
    _tamper(b, "RIFF....tampered")

    results = freeze.verify_all([a, b])
    assert len(results) == 2
    assert [r.ok for r in results] == [True, False]
    assert results[1].status == "hash_mismatch"


# ===========================================================================
# 十一、不碰数据目录（工具的铁律）
# ===========================================================================

def test_tools_never_write_repo_data_dirs():
    """两个模块里**不得出现任何写文件系统的调用** —— 用 AST 把它钉死。

    为什么是扫调用而不是扫字符串：docstring 里提到 `04_approved/` 是在讲规则，
    不是要写它。上一版就是这么误报的。用 AST 只看真正会被执行的代码：
    `open(..., "w"/"a"/"x")`、`os.chmod`、`os.remove`…… 冻结模块里必然有
    `open("w")`（写 `.sha256`），所以再缩一层：**写入目标不得是数据目录**。
    """
    import ast

    tools_dir = Path(__file__).resolve().parent.parent
    banned = ("02_source", "03_omr", "04_approved", "05_vocals", "06_arrangement", "07_mix", "08_qc")
    for module in ("naming.py", "freeze.py"):
        tree = ast.parse((tools_dir / module).read_text(encoding="utf-8"))
        # 字面量里出现的目录名：只要不是写操作的目标就不算违规。
        literals = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
        write_modes = {"w", "a", "x", "wb", "ab", "xb"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                fn = node.func
                name = getattr(fn, "id", None) or getattr(fn, "attr", None)
                if name == "open":
                    modes = [a.value for a in node.args[1:2] if isinstance(a, ast.Constant)]
                    modes += [kw.value.value for kw in node.keywords
                              if kw.arg == "mode" and isinstance(kw.value, ast.Constant)]
                    if any(m in write_modes for m in modes):
                        target = ast.unparse(node.args[0])
                        for token in banned:
                            assert token not in target, f"{module} 往数据目录 {token} 写文件：{target}"
        # 数据目录名只能出现在 docstring/正则里由调用方提供 —— 这里确认模块**没有**
        # 把任何数据目录当作常量导出（否则调用方会顺手用它当默认落点）。
        bad_consts = [s for s in literals if s.strip("/") in banned and s.startswith("/")]
        assert not bad_consts, f"{module} 硬编码了数据目录常量：{bad_consts}"


def test_no_third_party_imports():
    """纯 stdlib：不引新依赖（#13 硬性约束）。"""
    tools_dir = Path(__file__).resolve().parent.parent
    allowed = {
        "__future__", "datetime", "hashlib", "os", "pathlib", "re", "stat", "dataclasses", "typing",
        "naming", "freeze",
    }
    for module in ("naming.py", "freeze.py"):
        for line in (tools_dir / module).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("import "):
                root = line.split()[1].split(".")[0].rstrip(",")
            elif line.startswith("from "):
                root = line.split()[1].split(".")[0]
            else:
                continue
            assert root in allowed, f"{module} 引入了非 stdlib 依赖：{line}"


def test_tests_use_tempdir_not_repo():
    """测试自身也只写临时目录：这条断言的是"测试的输入是它自己造的"。"""
    with tempfile.TemporaryDirectory() as d:
        assert os.path.isdir(d)
    # 仓库数据目录必须仍然只有 README（本测试运行前后都不该多出文件）
    repo_data = Path(__file__).resolve().parents[3]
    for sub in ("04_approved", "02_source"):
        d = repo_data / sub
        if d.exists():
            entries = {p.name for p in d.iterdir()}
            assert entries <= {"README.md", ".gitkeep"}, f"{sub}/ 被写入物污染：{entries}"


# ===========================================================================
# 十二、工具做不到的边界（诚实记录，别假装它做到了）
# ===========================================================================

def test_master_rule_cannot_reject_short_song_id_like_song():
    """`song_mix_v001.wav` 目前**是**合规的 —— 这是已知边界，不是漏测。

    G4-10 的占位符是 `<song_id>`，而 song_id 由 `glossary.md` 定义为 `songNN`，
    #122 的 e2e 又实际使用了 `flow-demo-001`。工具只看**文件名**，
    没有足够信息区分「合法 song_id `flow-demo-001`」与「少写了序号的 `song`」。
    想收紧这条，得让调用方把 song_id 传进来（`validate_master_mix(name, song_id=...)`），
    那是另一件事 —— 这里如实标注，不写一条假装能拦的测试。
    """
    assert naming.validate("song_mix_v001.wav", kind="master_mix") == []
    assert naming.validate("flow_demo_mix_v001.wav", kind="master_mix") == []
    assert naming.validate("song_mix_v002_v001.wav", kind="master_mix") != []


def test_freeze_chmod_is_not_a_security_boundary(approved_dir):
    """只读位挡得住手滑，挡不住 `os.chmod` + 覆写 —— 哈希校验才是那道检测线。

    这条测试**故意演示绕过**：如果哪天有人说"文件已经 0444 了所以不会被改"，
    跑这个用例给他看。冻结的可信来源是「记录 ↔ 内容」的比对，不是权限位。
    """
    path = _write(approved_dir / "ground_truth.musicxml")
    freeze.freeze_file(path, APPROVER, utc_time=FIXED_UTC, read_only=True)
    assert freeze.is_read_only(path)

    os.chmod(path, 0o644)                 # 绕过 1：权限位可以被改回去
    Path(path).write_text("<score>绕过</score>", encoding="utf-8")
    assert freeze.verify_frozen(path).status == "hash_mismatch", "绕过之后必须被哈希校验抓住"
