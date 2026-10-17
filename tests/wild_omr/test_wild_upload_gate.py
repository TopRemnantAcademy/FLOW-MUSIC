"""野上传 · 断言 ①：G1 门禁对「低分辨率野图」必须**诚实 FAIL**。

被钉住的事实（issue #169 / #165）
--------------------------------
野上传是手机随手拍/随手截的图。`g1_image_quality.py` 对**测不到 dpi** 的图会
如实 FAIL；对**dpi 达标但短边不足**的图也必须 FAIL，不得因为「dpi 300 看着还行」
就放行。反例（静默通过）正是本文件要消灭的形态。

fixture 是脱敏副本（半分辨率 + 模糊，音符/歌词不可读）：只保留"尺寸/密度"这类
结构信号，够判定 G1-1，又不含任何可识别的乐谱内容。
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest


_HERE = Path(__file__).resolve()
_REPO = _HERE.parents[2]                              # tests/wild_omr → tests → 仓库根
_TOOLS = _REPO / "music_rebuild_project" / "tools"
for _p in (str(_TOOLS), str(_HERE.parent)):      # tools/ 与本目录（_deps.py）
    if _p not in sys.path:
        sys.path.insert(0, _p)

from _deps import require  # noqa: E402 - 同目录 helper：裸环境缺包 → skip

from checks.g1_image_quality import Config, evaluate_dir, gate_verdict  # noqa: E402

FIXTURES = _HERE.parent / "fixtures"
WILD_IMAGE = FIXTURES / "song01_preprocessed_v001_blur300.jpeg"


@pytest.fixture()
def wild_page(tmp_path: Path) -> Path:
    """把脱敏野图放进临时「上传目录」——G1-9 需要 source_manifest.txt 才算得只读。"""
    d = tmp_path / "wild_upload"
    d.mkdir()
    dst = d / WILD_IMAGE.name
    shutil.copyfile(WILD_IMAGE, dst)
    digest = __import__("hashlib").sha256(dst.read_bytes()).hexdigest()
    (d / "source_manifest.txt").write_text(f"{digest}  {dst.name}\n", encoding="utf-8")
    return d


def test_fixture_is_anonymized_and_lowres():
    """fixture 自身的契约：脱敏（半分辨率）且短边不足 —— 否则后面的断言没有意义。

    `classify_input` 读图像头，需要 Pillow；没装就跳过（别把"环境缺依赖"报成"断言失败"）。
    """
    require("PIL", "classify_input 需要 Pillow；本环境没装则跳过")
    from omr.classify_input import classify_file

    c = classify_file(WILD_IMAGE)
    assert c["container"] == "jpeg" and c["subtype"] == "raster"
    assert c["dpi"] == 300.0, "刻意保留 300 dpi 元数据：让失败只因短边，而非 dpi 不可测"
    assert min(c["width"], c["height"]) < 2400, "野图短边必须落在硬线之下（这是被测事实）"


def test_gate_is_fail_not_pass(wild_page: Path):
    """门禁结论必须是 FAIL；一旦有人把「短边不足」放宽成 NOTE/PASS，这里立刻红。"""
    rep = evaluate_dir(wild_page, Config())
    assert gate_verdict(rep) == "FAIL", f"野上传不得通过 G1：{rep}"

    g1_1 = rep["files"][0]["checks"]["G1-1"]
    assert g1_1["status"] == "FAIL", f"G1-1 必须 FAIL，实际 {g1_1}"
    assert "短边" in g1_1["detail"], "失败原因须写清是短边不足（不是含糊的『不达标』）"

    # 对照：G1-8（分类准入）与 G1-9（原始只读）不背锅 —— 失败必须来自分辨率实测
    assert rep["files"][0]["checks"]["G1-8"]["status"] == "PASS"
    assert rep["G1-9"]["status"] == "PASS"


def test_fails_closed_when_dpi_unmeasurable(wild_page: Path, monkeypatch):
    """dpi 测不到的野图同样 FAIL（「不能证明达标」= 不放行），不是 SKIP。"""
    from omr import classify_input

    real = classify_input.classify_file

    def fake(path):
        c = dict(real(path))
        c["dpi"] = None
        return c

    monkeypatch.setattr("checks.g1_image_quality.classify_file", fake)
    rep = evaluate_dir(wild_page, Config())
    g1_1 = rep["files"][0]["checks"]["G1-1"]
    assert g1_1["status"] == "FAIL" and "无法测得 dpi" in g1_1["detail"]
    assert gate_verdict(rep) == "FAIL"
