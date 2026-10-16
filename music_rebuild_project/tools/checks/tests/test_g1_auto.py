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


# ---------------------------------------------------- T2 共识原语（全部四分支）
class TestConsensusStatus:
    def test_empty_is_skip(self):
        st, d = A.consensus_status([])
        assert st == "SKIP" and "无引擎结果" in d

    def test_all_blank_strings_is_skip(self):
        assert A.consensus_status(["", ""])[0] == "SKIP"

    def test_all_skip_is_skip(self):
        st, d = A.consensus_status(["SKIP", "SKIP"])
        assert st == "SKIP" and "弃权" in d

    def test_unanimous_pass(self):
        st, d = A.consensus_status(["PASS", "PASS"])
        assert st == "PASS" and "一致" in d

    def test_unanimous_fail(self):
        assert A.consensus_status(["FAIL", "FAIL"])[0] == "FAIL"

    def test_unanimous_note(self):
        assert A.consensus_status(["NOTE", "NOTE"])[0] == "NOTE"

    def test_any_fail_wins(self):
        st, d = A.consensus_status(["FAIL", "PASS"])
        assert st == "FAIL" and "含 FAIL" in d

    def test_fail_beats_note(self):
        assert A.consensus_status(["NOTE", "FAIL", "PASS"])[0] == "FAIL"

    def test_pass_note_split_does_not_release_pass(self):
        """引擎分歧（PASS vs NOTE）→ NOTE，绝不放行 PASS。"""
        st, d = A.consensus_status(["PASS", "NOTE"])
        assert st == "NOTE" and "不放行" in d

    def test_skip_is_ignored_when_live_engines_exist(self):
        assert A.consensus_status(["SKIP", "PASS"])[0] == "PASS"

    def test_skip_plus_split_is_note(self):
        assert A.consensus_status(["SKIP", "PASS", "NOTE"])[0] == "NOTE"

    def test_single_engine_result_passes_through(self):
        assert A.consensus_status(["PASS"])[0] == "PASS"

    def test_returns_detail_string(self):
        for statuses in ([], ["PASS"], ["FAIL"], ["SKIP"], ["PASS", "NOTE"]):
            st, d = A.consensus_status(statuses)
            assert st in ("PASS", "NOTE", "FAIL", "SKIP") and isinstance(d, str) and d


# ---------------------------------------------------- T1 CV 无模型引擎（真跑）
class TestCvEngine:
    """`_cv_collect` 用合成五线谱真跑；需要 opencv/numpy（不可用则如实 skip）。"""

    @pytest.fixture(scope="class")
    @classmethod
    def synth(cls, tmp_path_factory):
        pytest.importorskip("cv2")
        np = pytest.importorskip("numpy")
        from PIL import Image
        d = tmp_path_factory.mktemp("cv")
        path = d / "synth.png"
        img = np.full((600, 800), 255, dtype=np.uint8)
        for i in range(3):                       # 3 组五线谱，线距 12px
            y0 = 80 + i * 160
            for j in range(5):
                img[y0 + j * 12, 60:740] = 0
        Image.fromarray(img).save(path)
        return path

    def test_registered_in_engines(self):
        assert "cv" in A._ENGINES

    def test_measures_unit_size(self, synth):
        m = A._cv_collect(synth)
        assert m.source == "cv"
        assert m.unit_size_px == pytest.approx(12.0, abs=1.0)
        assert m.unit_size_min_px == pytest.approx(12.0, abs=1.0)

    def test_measures_skew_and_margin(self, synth):
        m = A._cv_collect(synth)
        assert m.skew_deg_max is not None and m.skew_deg_max < 1.0
        assert m.content_margin_ratio is not None and m.content_margin_ratio > 0

    def test_failure_raises_engine_unavailable(self, tmp_path):
        """不可读图 → EngineUnavailable（由 collect_metrics 捕获，不炸主流程）。"""
        bad = tmp_path / "nope.png"
        bad.write_bytes(b"not an image")
        with pytest.raises(A.EngineUnavailable):
            A._cv_collect(bad)

    def test_collect_metrics_degrades_without_raising(self, tmp_path):
        bad = tmp_path / "nope.png"
        bad.write_bytes(b"not an image")
        m = A.collect_metrics(bad, engines=("cv",))
        assert m.source == "none"
        assert m.errors.get("cv")

    def test_unknown_engine_name_ignored(self, synth):
        m = A.collect_metrics(synth, engines=("does-not-exist",))
        assert m.source == "none"


# ---------------------------------------------------- 与 T3 标定链路的一致性契约
class TestCalibrationContract:
    """标定依赖的接口契约：阈值常量、systems 字段、CV 引擎可用性。"""

    def test_cv_in_default_engine_order(self):
        import inspect
        sig = inspect.signature(A.collect_metrics)
        default = sig.parameters["engines"].default
        assert "cv" in tuple(default)
        assert tuple(default)[-1] == "cv", "cv 只补空缺，排在末位"

    def test_density_verdict_uses_systems_threshold(self):
        assert A.verdict_density(A.AutoMetrics(n_systems=A.SYSTEMS_MAX))[0] == "PASS"
        assert A.verdict_density(A.AutoMetrics(n_systems=A.SYSTEMS_MAX + 1))[0] == "FAIL"


# ------------------------------------------- _cv_collect 的两个**实测回归**（#163 标定集抓到）
class TestCvRegression:
    """标定集（T3）当场抓到的真实缺陷 → 固化为回归测试，防复发。

    ① 倾斜谱页：全页水平投影会把 5 条 1px 细线糊成一条厚墨带 → 线距测不到（假 SKIP）。
    ② 整行连续的细谱线：`0.45×proj.max()` 阈值会把**全部**谱线列排除 → 同上。
    """

    @pytest.fixture(scope="class")
    @classmethod
    def slanted(cls, tmp_path_factory):
        pytest.importorskip("cv2")
        np = pytest.importorskip("numpy")
        from PIL import Image
        d = tmp_path_factory.mktemp("cvreg")
        out = {}
        for angle in (0.3, 0.8, 2.5):
            path = d / f"slant_{angle}.png"
            img = np.full((1400, 900), 255, dtype=np.uint8)
            x0, x1 = 80, 820
            xs = np.arange(x0, x1)
            for sysy in range(4):
                for j in range(5):
                    ys = (150 + sysy * 300 + j * 12) + np.tan(np.radians(angle)) * (xs - x0)
                    yi = np.round(ys).astype(int)
                    img[yi, xs] = 0
            Image.fromarray(img).save(path)
            out[angle] = path
        return out

    def test_slanted_staff_still_measures_spacing(self, slanted):
        """缺陷①：倾斜 ≠ 测不到线距（此前返回 None → G1-1L 假 SKIP）。"""
        for angle, path in slanted.items():
            m = A._cv_collect(path)
            assert m.unit_size_px == pytest.approx(12.0, abs=0.5), f"angle={angle} 线距未测到"

    def test_slanted_staff_verdict_not_skip(self, slanted):
        for path in slanted.values():
            assert A.verdict_line_space(A._cv_collect(path))[0] == "PASS"

    def test_uniform_line_density_is_not_skipped(self, slanted):
        """缺陷②：列投影分布均匀（max==p90）时不得整体排除。"""
        np = pytest.importorskip("numpy")
        from checks import g1_auto  # noqa: F401
        col = np.zeros(40, dtype=np.uint8)
        for y in (5, 11, 17, 23, 29):     # 5 条 1px 线，间距 6px
            col[y] = 255
        assert A._line_diffs(col) == [6.0] * 4   # 5 条线 → 4 个相邻间距
        # 末段游程不得被吞（曾用 -99 哨兵导致最后一个间距丢失）
        assert A._line_diffs(col)[-1] == 6.0

    def test_col_samples_bounded(self):
        cols = A._col_samples(3508, max_cols=64)
        assert len(cols) <= 65 and cols[-1] == 3507 and cols[0] == 0

    def test_song01_spacing_unchanged_by_refactor(self):
        """真实页（2480×3508）的线距仍为 ~18px（与 #164 基线一致，未回归）。"""
        img = Path(__file__).resolve().parents[3] / "02_source" / "song01" / \
            "preprocessed" / "song01_preprocessed_v001.jpeg"
        if not img.exists():
            pytest.skip("song01 预处理页不在工作区")
        m = A._cv_collect(img)
        assert m.unit_size_px == pytest.approx(18.0, abs=0.6)
        assert A.verdict_line_space(m)[0] == "PASS"
