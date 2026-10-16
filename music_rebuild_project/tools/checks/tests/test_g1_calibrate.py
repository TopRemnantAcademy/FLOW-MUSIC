"""g1_calibrate（L1 合成标定集）回归测试。

覆盖：
  - 真值函数（线距/歪斜/密度三态边界）
  - 合成页生成（尺寸/注入 K 组系统数/线距）
  - **端到端**：合成样本 → `_cv_collect` → 三态一致率与 `false_pass == 0`
  - systems 维度（K∈{4,6,8,10}）确实被覆盖（issue 增补要求）
  - `--fail-under` 未达标 → 非零退出；false_pass → 非零退出
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve()
_TOOLS_DIR = _HERE.parents[2]                 # music_rebuild_project/tools
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from checks import g1_auto as A               # noqa: E402
from checks import g1_calibrate as C          # noqa: E402


# ------------------------------------------------------------------ 真值函数
class TestTruth:
    @pytest.mark.parametrize("s,exp", [(20.0, "PASS"), (8.0, "PASS"), (7.9, "NOTE"),
                                       (6.0, "NOTE"), (5.9, "FAIL")])
    def test_line_space(self, s, exp):
        assert C.truth_line_space(s) == exp

    @pytest.mark.parametrize("a,exp", [(0.0, "PASS"), (0.9, "PASS"), (1.0, "NOTE"),
                                       (1.9, "NOTE"), (2.0, "FAIL"), (-2.5, "FAIL")])
    def test_skew(self, a, exp):
        assert C.truth_skew(a) == exp

    def test_crop(self):
        assert C.truth_crop(0) == "FAIL"
        assert C.truth_crop(16) == "PASS"

    @pytest.mark.parametrize("k,exp", [(4, "PASS"), (6, "PASS"), (8, "FAIL"), (10, "FAIL")])
    def test_systems(self, k, exp):
        assert C.truth_systems(k) == exp

    def test_truths_agree_with_gate_thresholds(self):
        """真值边界必须与 g1_auto 的阈值常量同源（防漂移）。"""
        assert C.truth_line_space(A.LINE_SPACE_PASS_PX) == "PASS"
        assert C.truth_line_space(A.LINE_SPACE_RELAXED_PX) == "NOTE"
        assert C.truth_skew(A.SKEW_PASS_DEG) == "NOTE"
        assert C.truth_skew(A.SKEW_RELAXED_DEG) == "FAIL"
        assert C.truth_systems(A.SYSTEMS_MAX) == "PASS"
        assert C.truth_systems(A.SYSTEMS_MAX + 1) == "FAIL"


# ------------------------------------------------------------------ 生成器
class TestGenerate:
    def test_grid_size_and_naming(self, tmp_path):
        samples = C.generate(tmp_path, spacings=[8.0], angles=[0.0],
                             systems=[4, 6], bboxes=[0, 16], repeat=2)
        assert len(samples) == 1 * 1 * 2 * 2 * 2
        assert all(s.path.exists() for s in samples)
        assert len({s.path.name for s in samples}) == len(samples)

    def test_systems_dimension_covered(self, tmp_path):
        """issue 增补：标定集**必须**覆盖 systems 维度 K∈{4,6,8,10}。"""
        samples = C.generate(tmp_path, spacings=[8.0], angles=[0.0],
                             systems=C.DEFAULT_SYSTEMS, bboxes=[48])
        assert sorted({s.n_systems for s in samples}) == [4, 6, 8, 10]

    def test_default_grid_over_30_samples(self, tmp_path):
        assert len(C.generate(tmp_path)) >= 30

    def test_injected_spacing_is_recovered_by_cv(self, tmp_path):
        samples = C.generate(tmp_path, spacings=[12.0], angles=[0.0],
                             systems=[4], bboxes=[48])
        m = C.measure(samples[0])
        assert m.unit_size_px == pytest.approx(12.0, abs=1.0)

    def test_injected_skew_roughly_recovered(self, tmp_path):
        samples = C.generate(tmp_path, spacings=[12.0], angles=[2.5],
                             systems=[4], bboxes=[48])
        m = C.measure(samples[0])
        assert m.skew_deg_max == pytest.approx(2.5, abs=0.3)
        assert A.verdict_skew(m)[0] == "FAIL"

    def test_zero_margin_sample_fails_geometry_truth(self, tmp_path):
        samples = C.generate(tmp_path, spacings=[12.0], angles=[0.0],
                             systems=[4], bboxes=[0])
        assert samples[0].truth["G1-3"] == "FAIL"


# ------------------------------------------------------------------ 端到端一致率
class TestEndToEnd:
    @pytest.fixture(scope="class")
    @classmethod
    def report(cls, tmp_path_factory):
        out = tmp_path_factory.mktemp("cal")
        samples = C.generate(out)
        measured = {s.index: C.measure(s) for s in samples}
        return C.evaluate(samples, measured)

    def test_false_pass_is_zero(self, report):
        assert report["overall"]["false_pass_count"] == 0
        assert all(not v["false_pass"] for v in report["per_check"].values())

    def test_agreement_rate_meets_target(self, report):
        rate = report["overall"]["agreement_rate"]
        assert rate is not None and rate >= 0.99

    def test_no_skips_from_engine_failure(self, report):
        """CV 引擎在本测试环境必须可用（否则一致率会被静默抽空）。"""
        assert report["overall"]["comparisons"] > 0

    def test_scale_error_is_small(self, report):
        assert report["scale_error"]["unit_size_px"]["rel_median"] < 0.05

    def test_systems_blocks_present_for_all_K(self, report):
        assert sorted(int(k) for k in report["systems"]["injected_K"]) == [4, 6, 8, 10]
        for k, row in report["systems"]["per_k"].items():
            assert row["samples"] > 0
            assert row["injected_truth"] == C.truth_systems(int(k))

    def test_report_is_json_serialisable(self, report):
        json.dumps(report, ensure_ascii=False)


# ------------------------------------------------------------------ CLI / 退出码
class TestCli:
    def test_json_and_report_file(self, tmp_path, capsys):
        out = tmp_path / "cal"
        rep_file = tmp_path / "rep.json"
        rc = C.main(["--out-dir", str(out), "--report", str(rep_file),
                     "--spacings", "5,12", "--angles", "0,2.5",
                     "--systems", "4,8", "--bboxes", "0,48", "--json"])
        assert rc == 0
        rep = json.loads(capsys.readouterr().out)
        assert rep["overall"]["false_pass_count"] == 0
        assert json.loads(rep_file.read_text(encoding="utf-8"))["n_samples"] == rep["n_samples"]

    def test_fail_under_unreachable_exits_nonzero(self, tmp_path, capsys):
        rc = C.main(["--out-dir", str(tmp_path / "c"), "--json", "--fail-under", "1.01"])
        assert rc == 1
        capsys.readouterr()

    def test_fail_under_met_exits_zero(self, tmp_path, capsys):
        rc = C.main(["--out-dir", str(tmp_path / "c"), "--json", "--fail-under", "0.99"])
        assert rc == 0
        capsys.readouterr()

    def test_omr_engine_unavailable_reports_skip_not_fake_data(self, tmp_path):
        """oemer 不可用时必须如实报 SKIP，不得编造 systems 一致率。"""
        samples = C.generate(tmp_path, spacings=[12.0], angles=[0.0],
                             systems=[4, 8], bboxes=[48])
        res = C.systems_by_k(samples, engines=("oemer",))
        assert res["status"] in ("PASS", "SKIP")
        assert set(res["per_k"]) == {"4", "8"}
        if res["status"] == "SKIP":
            for row in res["per_k"].values():
                assert row["status"] == "SKIP" and row.get("reason")
            assert res["systems_exact_rate"] is None      # 绝不编数字

    def test_probe_reports_engine_error_as_skip(self, tmp_path):
        samples = C.generate(tmp_path, spacings=[12.0], angles=[0.0],
                             systems=[4], bboxes=[48])
        row = C.probe_omr_systems(samples[0], engines=("not-an-engine",))
        assert row["status"] == "SKIP"


def _oemer_available() -> bool:
    try:
        import oemer  # noqa: F401
        return True
    except Exception:
        return False
