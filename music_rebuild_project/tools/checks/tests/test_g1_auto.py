"""g1_auto（G1 自动化判定核心）回归测试 —— **纯裁决层**，零重依赖。

策略
  裁决逻辑是纯函数：直接构造 ``AutoMetrics`` 断言三态（PASS/NOTE/FAIL/SKIP）。
  编排层用**注入的假 provider** 验证，不触发 oemer / Tesseract。
  硬约束：任何"测不到"的字段 → 该项 **SKIP**，**绝不冒充 PASS**（对齐 P5 与
  ``g1_image_quality.py`` 的「SKIP 不计入通过项」契约）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve()
_TOOLS_DIR = _HERE.parents[2]               # music_rebuild_project/tools
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from checks import g1_auto as A             # noqa: E402


def M(**kw) -> A.AutoMetrics:
    return A.AutoMetrics(**kw)


# ------------------------------------------------------------------ G1-1 线距
class TestLineSpace:
    def test_pass_above_8(self):
        st, d = A.verdict_line_space(M(unit_size_px=18.3))
        assert st == "PASS" and "18.3" in d

    def test_pass_exactly_8_is_inclusive(self):
        assert A.verdict_line_space(M(unit_size_px=8.0))[0] == "PASS"

    def test_note_band_6_to_8(self):
        assert A.verdict_line_space(M(unit_size_px=6.5))[0] == "NOTE"

    def test_fail_below_6(self):
        assert A.verdict_line_space(M(unit_size_px=5.9))[0] == "FAIL"

    def test_skip_when_unmeasured(self):
        assert A.verdict_line_space(M())[0] == "SKIP"

    def test_falls_back_to_min(self):
        assert A.verdict_line_space(M(unit_size_min_px=7.0))[0] == "NOTE"


# ------------------------------------------------------------------ G1-2 歪斜
class TestSkew:
    def test_pass(self):
        assert A.verdict_skew(M(skew_deg_max=0.169))[0] == "PASS"

    def test_note_band(self):
        assert A.verdict_skew(M(skew_deg_max=1.5))[0] == "NOTE"

    def test_fail_at_2(self):
        assert A.verdict_skew(M(skew_deg_max=2.0))[0] == "FAIL"

    def test_skip(self):
        assert A.verdict_skew(M())[0] == "SKIP"


# ------------------------------------------------------------------ G1-3 裁切
class TestCrop:
    def test_pass_with_margin(self):
        assert A.verdict_crop(M(content_margin_ratio=0.03))[0] == "PASS"

    def test_note_tight_margin(self):
        assert A.verdict_crop(M(content_margin_ratio=0.005))[0] == "NOTE"

    def test_fail_touching_edge(self):
        assert A.verdict_crop(M(content_margin_ratio=0.0))[0] == "FAIL"

    def test_skip(self):
        assert A.verdict_crop(M())[0] == "SKIP"


# ------------------------------------------------------------------ G1-5 文字
class TestText:
    def test_skip_when_no_ocr(self):
        assert A.verdict_text(M())[0] == "SKIP"

    def test_note_when_no_text_found(self):
        assert A.verdict_text(M(text_total=0))[0] == "NOTE"

    def test_pass(self):
        st, _ = A.verdict_text(M(text_total=100, text_confident=95, text_conf_median=88.0,
                                 text_height_px=20.0))
        assert st == "PASS"

    def test_fail_low_conf_ratio(self):
        assert A.verdict_text(M(text_total=100, text_confident=50,
                                text_conf_median=40.0, text_height_px=20.0))[0] == "FAIL"

    def test_fail_small_height(self):
        assert A.verdict_text(M(text_total=100, text_confident=99,
                                text_conf_median=95.0, text_height_px=12.0))[0] == "FAIL"


# ------------------------------------------------------------------ G1-6 音符
class TestSymbols:
    def test_skip(self):
        assert A.verdict_symbols(M())[0] == "SKIP"

    def test_fail_zero_notes(self):
        assert A.verdict_symbols(M(n_notes=0, n_barlines=3, n_clefs=1))[0] == "FAIL"

    def test_note_when_structure_missing(self):
        assert A.verdict_symbols(M(n_notes=10, n_barlines=0, n_clefs=0))[0] == "NOTE"

    def test_pass(self):
        st, _ = A.verdict_symbols(M(n_notes=179, n_barlines=29, n_clefs=8,
                                    n_sfns=26, n_rests=14))
        assert st == "PASS"


# ------------------------------------------------------------------ G1-7 密度
class TestDensity:
    def test_skip(self):
        assert A.verdict_density(M())[0] == "SKIP"

    def test_pass(self):
        assert A.verdict_density(M(n_systems=6, n_measures=24))[0] == "PASS"

    def test_fail_systems_over_6(self):
        st, d = A.verdict_density(M(n_systems=8, n_measures=23))
        assert st == "FAIL" and "系统数" in d

    def test_fail_measures_over_24(self):
        assert A.verdict_density(M(n_systems=4, n_measures=25))[0] == "FAIL"


# ------------------------------------------------------------------ G1-4 页序
class TestOrder:
    def test_skip_empty(self):
        assert A.verdict_order([])[0] == "SKIP"

    def test_skip_all_unreadable(self):
        assert A.verdict_order([("a.png", None), ("b.png", None)])[0] == "SKIP"

    def test_pass_contiguous(self):
        assert A.verdict_order([("a.png", 1), ("b.png", 2), ("c.png", 3)])[0] == "PASS"

    def test_fail_duplicate(self):
        assert A.verdict_order([("a.png", 1), ("b.png", 1)])[0] == "FAIL"

    def test_fail_not_monotonic(self):
        assert A.verdict_order([("a.png", 2), ("b.png", 1)])[0] == "FAIL"

    def test_fail_gap(self):
        assert A.verdict_order([("a.png", 1), ("b.png", 3)])[0] == "FAIL"

    def test_note_when_partially_unreadable(self):
        assert A.verdict_order([("a.png", 1), ("b.png", None), ("c.png", 2)])[0] == "NOTE"

    # ---- 诚实性：单页不能证明「缺页=0」----
    def test_note_single_page_without_expected(self):
        st, d = A.verdict_order([("a.png", 25)])
        assert st == "NOTE" and "无法证明" in d

    def test_pass_single_page_with_expected_declared(self):
        st, _ = A.verdict_order([("a.png", 25)], expected_pages=1)
        assert st == "PASS"

    def test_fail_page_count_mismatch(self):
        assert A.verdict_order([("a.png", 1), ("b.png", 2)], expected_pages=3)[0] == "FAIL"


# ------------------------------------------------------------ 编排 + fail-closed
class TestOrchestration:
    def test_auto_checks_with_fake_provider(self, tmp_path):
        img = tmp_path / "p.png"
        img.write_bytes(b"x")

        def fake(_img):
            return M(source="fake", unit_size_px=9.0, skew_deg_max=0.5,
                     n_notes=5, n_barlines=2, n_clefs=1,
                     n_systems=3, n_measures=10)

        out = A.auto_checks(img, provider=fake)
        assert set(A.VERDICTS).issubset(out.keys())
        assert out["G1-1L"]["status"] == "PASS"
        assert out["G1-2"]["status"] == "PASS"
        assert out["G1-6"]["status"] == "PASS"
        assert out["G1-7"]["status"] == "PASS"
        # 未提供裁剪/文字指标 → 必须 SKIP，绝不 PASS
        assert out["G1-3"]["status"] == "SKIP"
        assert out["G1-5"]["status"] == "SKIP"
        assert out["_metrics"]["source"] == "fake"

    def test_empty_engine_list_yields_all_skip(self, tmp_path):
        img = tmp_path / "p.png"
        img.write_bytes(b"x")
        m = A.collect_metrics(img, engines=())
        assert m.source == "none"
        for key, fn in A.VERDICTS.items():
            assert fn(m)[0] == "SKIP", f"{key} 无测量时必须 SKIP"

    def test_auto_checks_dir_adds_g1_4(self, tmp_path):
        f1 = tmp_path / "a.png"
        f2 = tmp_path / "b.png"
        f1.write_bytes(b"x")
        f2.write_bytes(b"x")

        pages = {"a.png": 1, "b.png": 2}

        def fake(img: Path):
            return M(source="fake", page_no=pages[img.name])

        rep = A.auto_checks_dir([f1, f2], provider=fake)
        assert rep["G1-4"]["status"] == "PASS"
        assert len(rep["files"]) == 2


# ------------------------------------------------------------- 阈值快照一致性
def test_thresholds_match_gate_config():
    """阈值必须与 08_qc/gate_config.json 的 auto 块一致（防漂移）。"""
    import json

    cfg_path = _HERE.parents[2].parent / "08_qc" / "gate_config.json"
    if not cfg_path.exists():
        pytest.skip("gate_config.json 不存在")
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    auto = cfg.get("G1", {}).get("auto")
    if not auto:
        pytest.skip("gate_config.json 尚无 auto 块（本地未回填）")
    # gate_config.json 的阈值按项目纪律存为对象：{"value": x, "adjustable": ..., ...}
    assert auto["line_space_pass_px"]["value"] == A.LINE_SPACE_PASS_PX
    assert auto["skew_pass_deg"]["value"] == A.SKEW_PASS_DEG
    assert auto["text_conf_min"]["value"] == A.TEXT_CONF_MIN
    assert auto["systems_max"]["value"] == A.SYSTEMS_MAX
    assert auto["measures_max"]["value"] == A.MEASURES_MAX
    assert auto["crop_margin_min_ratio"]["value"] == A.CROP_MARGIN_MIN_RATIO
