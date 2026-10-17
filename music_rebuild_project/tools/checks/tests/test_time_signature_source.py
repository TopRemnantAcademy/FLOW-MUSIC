"""time_signature_source（T7 拍号自动化来源）回归测试。

硬要求（issue §增补 T7）：
  - 不得静默填充：来源与置信必须随结论（declared / ocr / inferred / none）
  - **推断值只可 PASS_WITH_NOTES**，不得当声明值判 PASS
  - 元数据声明优先于 OCR，OCR 优先于推断
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve()
_TOOLS_DIR = _HERE.parents[2]                 # music_rebuild_project/tools
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from checks import time_signature_source as TSS   # noqa: E402


# ------------------------------------------------------------------ 来源 1：元数据
class TestManifest:
    def test_markdown_table_declares(self, tmp_path):
        p = tmp_path / "manifest.md"
        p.write_text("| song_id | song01 |\n| 拍号 | 4/4 |\n", encoding="utf-8")
        ts = TSS.from_manifest(p)
        assert ts is not None and ts.value == "4/4"
        assert ts.source == "declared" and ts.confidence == 1.0
        assert ts.verdict_cap == "PASS"

    def test_markdown_kv_declares(self, tmp_path):
        p = tmp_path / "manifest.md"
        p.write_text("time_signature: 6/8\n", encoding="utf-8")
        assert TSS.from_manifest(p).value == "6/8"

    def test_json_nested_declares(self, tmp_path):
        p = tmp_path / "m.json"
        p.write_text('{"meta": {"meter": "3/4"}}', encoding="utf-8")
        ts = TSS.from_manifest(p)
        assert ts.value == "3/4" and ts.source == "declared"

    def test_absent_returns_none(self, tmp_path):
        p = tmp_path / "manifest.md"
        p.write_text("# 无拍号声明\n", encoding="utf-8")
        assert TSS.from_manifest(p) is None

    def test_missing_file_returns_none(self, tmp_path):
        assert TSS.from_manifest(tmp_path / "nope.md") is None

    def test_common_time_word(self, tmp_path):
        p = tmp_path / "manifest.md"
        p.write_text("meter: common time\n", encoding="utf-8")
        assert TSS.from_manifest(p).value == "4/4"

    def test_invalid_denominator_rejected(self, tmp_path):
        p = tmp_path / "manifest.md"
        p.write_text("拍号 | 4/5 |\n", encoding="utf-8")
        assert TSS.from_manifest(p) is None


# ------------------------------------------------------------------ 来源 2：OCR（注入式）
class TestOcr:
    def test_no_image(self):
        assert TSS.from_ocr(None) is None
        assert TSS.from_ocr(Path("/nonexistent.png")) is None

    def test_ocr_hit_is_used(self, tmp_path, monkeypatch):
        img = tmp_path / "p.png"
        img.write_bytes(b"x")
        monkeypatch.setattr(TSS, "_ocr_region", lambda *a, **k: [("4/4", 92.0)])
        ts = TSS.from_ocr(img)
        assert ts.value == "4/4" and ts.source == "ocr"
        assert ts.confidence == pytest.approx(0.92)
        assert ts.verdict_cap == "PASS"

    def test_ocr_picks_highest_confidence(self, tmp_path, monkeypatch):
        img = tmp_path / "p.png"
        img.write_bytes(b"x")
        monkeypatch.setattr(TSS, "_ocr_region",
                            lambda *a, **k: [("3/4", 70.0), ("4/4", 95.0)])
        assert TSS.from_ocr(img).value == "4/4"

    def test_ocr_noise_ignored(self, tmp_path, monkeypatch):
        img = tmp_path / "p.png"
        img.write_bytes(b"x")
        monkeypatch.setattr(TSS, "_ocr_region", lambda *a, **k: [("小小望楼", 99.0)])
        assert TSS.from_ocr(img) is None


# ------------------------------------------------------------------ 来源 3：结构推断
class TestInference:
    def test_mode_infers_4_4(self):
        ts = TSS.from_structure([4.0, 4.0, 4.0, 3.0])
        assert ts.value == "4/4" and ts.source == "inferred"
        assert ts.confidence == pytest.approx(0.75)

    def test_inferred_is_capped_at_pass_with_notes(self):
        ts = TSS.from_structure([4.0, 4.0])
        assert ts.verdict_cap == "PASS_WITH_NOTES"

    def test_inferred_detail_says_not_declared(self):
        ts = TSS.from_structure([3.0, 3.0, 3.0])
        assert "推断" in ts.detail and "非声明" in ts.detail

    def test_six_eight(self):
        assert TSS.from_structure([6.0, 6.0]).value == "6/8"

    def test_upbeat_ignored_by_mode(self):
        ts = TSS.from_structure([1.0, 4.0, 4.0, 4.0])
        assert ts.value == "4/4"
        assert "first_measure=2" in ts.evidence

    def test_empty_returns_none(self):
        assert TSS.from_structure([]) is None
        assert TSS.from_structure([0.0, 0.0]) is None


# ------------------------------------------------------------------ 组合优先级
class TestResolve:
    def test_declared_wins(self, tmp_path, monkeypatch):
        m = tmp_path / "manifest.md"
        m.write_text("拍号 | 6/8 |\n", encoding="utf-8")
        monkeypatch.setattr(TSS, "_ocr_region", lambda *a, **k: [("4/4", 99.0)])
        ts = TSS.resolve(manifest_path=m, image_path=tmp_path / "x.png",
                         part_highest_times=[4.0] * 5)
        assert ts.value == "6/8" and ts.source == "declared"

    def test_ocr_beats_inference(self, monkeypatch, tmp_path):
        monkeypatch.setattr(TSS, "_ocr_region", lambda *a, **k: [("3/4", 90.0)])
        img = tmp_path / "x.png"
        img.write_bytes(b"x")
        ts = TSS.resolve(image_path=img, part_highest_times=[4.0] * 5)
        assert ts.source == "ocr" and ts.value == "3/4"

    def test_inference_used_only_when_allowed(self, monkeypatch, tmp_path):
        monkeypatch.setattr(TSS, "_ocr_region", lambda *a, **k: [])
        img = tmp_path / "x.png"
        img.write_bytes(b"x")
        # allow_inferred=False → 不采用推断，如实报 none（默认由调用方显式开关）
        ts = TSS.resolve(image_path=img, part_highest_times=[4.0] * 5,
                         allow_inferred=False)
        assert ts.source == "none" and ts.value is None

    def test_inference_used_when_enabled(self, monkeypatch, tmp_path):
        monkeypatch.setattr(TSS, "_ocr_region", lambda *a, **k: [])
        img = tmp_path / "x.png"
        img.write_bytes(b"x")
        ts = TSS.resolve(image_path=img, part_highest_times=[4.0] * 5, allow_inferred=True)
        assert ts.source == "inferred" and ts.value == "4/4"
        assert ts.verdict_cap == "PASS_WITH_NOTES"

    def test_no_source_is_explicit_none(self, monkeypatch, tmp_path):
        monkeypatch.setattr(TSS, "_ocr_region", lambda *a, **k: [])
        ts = TSS.resolve(image_path=tmp_path / "x.png")
        assert ts.source == "none" and ts.value is None
        assert ts.verdict_cap == "PASS_WITH_NOTES"      # 未知 → 不得判 PASS
        assert "无任何可用来源" in ts.detail

    def test_as_dict_exposes_source_and_cap(self):
        d = TSS.from_structure([2.0, 2.0]).as_dict()
        assert d["source"] == "inferred" and d["verdict_cap"] == "PASS_WITH_NOTES"
        assert d["value"] == "2/4"


# ------------------------------------------------------------------ 音长校验
class TestApplyToMeasures:
    def test_all_measures_match(self):
        ts = TSS.TimeSignature(4, 4, source="declared", confidence=1.0)
        rep = TSS.apply_to_measures([4.0, 4.0, 4.0], ts)
        assert rep["checked"] == 3 and not rep["problems"]
        assert rep["expected_quarter_length"] == 4.0

    def test_mismatch_flagged(self):
        ts = TSS.TimeSignature(4, 4, source="declared", confidence=1.0)
        rep = TSS.apply_to_measures([4.0, 3.0, 4.0], ts)
        assert rep["problems"] and "第2小节" in rep["problems"][0]

    def test_last_measure_allow_incomplete(self):
        ts = TSS.TimeSignature(4, 4, source="declared", confidence=1.0)
        assert not TSS.apply_to_measures([4.0, 2.0], ts)["problems"]

    def test_inferred_cap_propagates(self):
        ts = TSS.TimeSignature(4, 4, source="inferred", confidence=0.9)
        rep = TSS.apply_to_measures([4.0], ts)
        assert rep["verdict_cap"] == "PASS_WITH_NOTES"

    def test_no_source_checks_nothing(self):
        rep = TSS.apply_to_measures([4.0, 4.0], TSS.TimeSignature(None, None))
        assert rep["checked"] == 0 and rep["expected_quarter_length"] is None
