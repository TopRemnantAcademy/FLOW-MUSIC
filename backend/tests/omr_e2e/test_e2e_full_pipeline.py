"""A→F + G1-G4 全过 + 注册 Song：端到端主链路（issue #122 验收标准第一条）。"""

from . import fixtures as fx
from .orchestrator import run_score_to_music
from .platform import ClosedLoop


def _run(tmp_path, **kw):
    return run_score_to_music(
        fx.synth_score_png(),
        fx.SAMPLE_LYRIC,
        {"song_id": fx.SONG_ID, "bpm": fx.BPM, "measures": fx.MEASURES},
        work_dir=str(tmp_path),
        **kw,
    )


def test_e2e_full_pipeline(tmp_path):
    """乐谱图 → MusicXML → ground_truth 冻结 → 보컬 WAV → 编曲 → master/stem 全绿。"""
    result = _run(tmp_path)

    assert result.ok is True, f"halted at {result.halted_at}: {result.halt_reason}"
    # 四门必须都有结论，且都是 PASS（本 fixture 下不应出现降级）
    assert sorted(result.gate_reports) == ["G1", "G2", "G3", "G4"]
    for gate, report in result.gate_reports.items():
        assert report.status == "PASS", f"{gate} = {report.status}，失败项 {report.failed_ids()}"
    # 六个阶段的中间产物都在（缺一个都说明流水线在偷工）
    for key in (
        "source_png",
        "ground_truth_musicxml",
        "approved_vocal_wav",
        "arrangement_json",
        "master_wav",
        "stem_vocal",
    ):
        assert key in result.artifacts, f"缺产物 {key}"


def test_e2e_pipeline_is_deterministic(tmp_path):
    """两次运行产物逐字节一致（CI 可复现 —— issue #122 铁律 5）。"""
    a = _run(tmp_path / "a")
    b = _run(tmp_path / "b")
    from .mocks import sha256_file

    for key in ("ground_truth_musicxml", "approved_vocal_wav", "master_wav", "arrangement_json"):
        assert sha256_file(a.artifacts[key]) == sha256_file(b.artifacts[key]), f"{key} 两次不一致"
    assert a.gate_reports["G4"].to_dict() == b.gate_reports["G4"].to_dict()


def test_gate_reports_carry_every_item(tmp_path):
    """各门报告必须逐项映射 gate_*.md 的编号（否则「全绿」不可审计）。"""
    result = _run(tmp_path)
    expected = {
        "G1": [f"G1-{i}" for i in range(1, 10)],
        "G2": [f"G2-{i}" for i in range(1, 10)],
        "G3": [f"G3-{i}" for i in range(1, 11)],
        "G4": [f"G4-{i}" for i in range(1, 13)],
    }
    for gate, ids in expected.items():
        got = [it.id for it in result.gate_reports[gate].items]
        assert got == ids, f"{gate} 检查项不齐：{got}"


def test_platform_registration_closes_the_loop(tmp_path, client):
    """成品 → Song 注册 → /api/songs/explore 匿名命中（平台闭环）。"""
    loop = ClosedLoop(client)
    loop.register("omr-e2e-main@test.com")
    loop.subscribe()

    result = run_score_to_music(
        fx.synth_score_png(),
        fx.SAMPLE_LYRIC,
        {"song_id": fx.SONG_ID, "bpm": fx.BPM, "measures": fx.MEASURES},
        work_dir=str(tmp_path),
        register_song=lambda r: loop.register_master(
            r, title=fx.TITLE, lyric=fx.SAMPLE_LYRIC, is_public=True
        )["song_id"],
    )
    assert result.ok is True
    assert isinstance(result.registered_song_id, int)

    hit = loop.find_in_explore(result.registered_song_id)
    assert hit is not None, "注册的 Song 没有出现在 explore 公开流里"
    assert hit["title"] == fx.TITLE
    assert hit["status"] == "completed"
    # explore 是匿名接口：作者名可用，但**邮箱绝不外泄**
    assert "@" not in (hit.get("author_name") or "")
