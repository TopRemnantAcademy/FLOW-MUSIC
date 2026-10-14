"""与 backend 的 `omr_e2e` 替身**交叉验证**：两套独立实现必须给出同样的结论。

为什么值得单独一个文件：`backend/tests/omr_e2e/`（#122）已经用 stdlib 实现了
一整套「阶段 A-F + G1-G4」的替身，其中 `MockDAW` 会导出四条 stem 与一条 master。
本包的 `stems.check_stems` 是对 G4-9 的**第二套独立实现**。如果两边对同一批
文件给出不同判定，那至少一边读错了 gate_G4.md —— 这种分歧必须在 CI 里暴露，
而不是等到真去打包时才在真人面前吵。

有意思的是两边**故意采了不同口径**，这里把它们都钉下来：
  - mock 产出 16bit（它自述「stdlib 不产 24bit」）；本包支持并默认要求 24bit。
    所以交叉验证时用 `expect=None` 关掉位深核对 —— 这正是 `expect` 参数存在的理由。
  - mock 的时长容差是 `> 0.01s` 才算不一致；本包收紧到 5ms。
    两者在「完全一致」的产物上都判 PASS，没有矛盾。

`omr_e2e` 需要 backend 的依赖树，导不进来就 skip（**不**假装通过）。
"""

from __future__ import annotations

import os
import sys

import pytest

_THIS = os.path.abspath(__file__)
# tests/ -> render/ -> tools/ -> music_rebuild_project/ -> 仓库根
_REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(_THIS))))
)
BACKEND = os.path.join(_REPO_ROOT, "backend")
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

mocks = pytest.importorskip(
    "tests.omr_e2e.mocks",
    reason="backend/tests/omr_e2e 不可导入（缺依赖树），交叉验证跳过",
)

from music_rebuild_project.tools.render import manifest as M  # noqa: E402
from music_rebuild_project.tools.render import stems as S  # noqa: E402
from music_rebuild_project.tools.render import wavio as W  # noqa: E402


@pytest.fixture(scope="module")
def mock_output(tmp_path_factory):
    """跑一遍上游替身：MockVocalEngine → MockDAW，拿到 stem 与 master。"""
    d = tmp_path_factory.mktemp("xref")
    render = mocks.MockVocalEngine().render(
        "<score/>", "가사", {}, out_path=str(d / "approved_vocal_pilot.wav")
    )
    out = mocks.MockDAW().mix({}, {"approved_vocal_wav": render.wav_path}, out_dir=str(d))
    return out


def test_mock_daw_stems_pass_our_g4_9(mock_output):
    """同一批文件：上游替身判 PASS，本包也判 PASS。"""
    rep = S.check_stems(mock_output["stems"], expect=None)
    assert rep.ok, rep.problems
    assert set(rep.durations) == set(S.REQUIRED_STEMS)
    assert max(rep.durations.values()) - min(rep.durations.values()) <= S.TOLERANCE_SECONDS


def test_durations_agree_with_upstream_reader(mock_output):
    """逐条对上上游自己的读法（`read_wav`），差值为 0 —— 不是「差不多」。"""
    for name, path in mock_output["stems"].items():
        canon = S.canonical_name(name)
        assert S.check_stems(mock_output["stems"], expect=None).durations[canon] == (
            pytest.approx(mocks.read_wav(path)["duration"], abs=1e-12)
        )


def test_upstream_master_matches_our_spec_reader(mock_output):
    master = mock_output["master"]
    sr, width, ch = W.wave_spec(master)
    assert (sr, ch) == (mocks.SAMPLE_RATE, 1)
    assert width == mocks.SAMPLE_WIDTH  # 16bit：mock 的口径，与本包 24bit 默认不同
    assert W.duration_of(master) == pytest.approx(20.0, abs=1e-9)


def test_our_manifest_can_index_upstream_artifacts(mock_output):
    m = M.check_deliverables(
        {
            "source_score_image": None,
            "approved_musicxml": None,
            "vocal_wav": mock_output["stems"]["vocal"],
            "arrangement_midi": None,
            "master_wav": mock_output["master"],
        },
        records={},
    )
    assert not m.ok  # 三类交付物 + 全部记录都给不出 → 必须 FAIL
    assert len(m.missing_required) == 3
    roles = {e.role for e in m.entries}
    assert roles == {"vocal_wav", "master_wav"}
    for e in m.entries:
        assert e.sha256 == M.sha256_of(e.path)
        assert e.duration is not None


def test_upstream_16bit_stems_fail_our_24bit_default(mock_output):
    """口径差异的**显式**记录：上游 16bit 的 stem 在默认规格下判 FAIL。

    这不是 bug —— gate_G4 要的就是 24bit。写下来是为了让未来的人看到
    「mock 与真产物在这一项上本来就不该一致」，而不是以为本包读错了。
    """
    rep = S.check_stems(mock_output["stems"])
    assert not rep.ok
    assert any("不可重开" in p for p in rep.problems)
