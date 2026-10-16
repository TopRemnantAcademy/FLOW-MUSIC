"""smoke_cv 回归测试（#165 T2）。

只测**契约**：引擎正常 → 通过；引擎报错 / 未测到线距 → **非零退出**（拒绝静默通过）。
不依赖真实图片与 cv2：全部用**注入的假 collect_metrics** 驱动，跨平台确定性。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve()
_DEPLOY_OMR_QA = _HERE.parents[1]          # music_rebuild_project/deploy/omr-qa
_TOOLS_CHECKS = _HERE.parents[3] / "tools" / "checks"
for p in (str(_DEPLOY_OMR_QA), str(_TOOLS_CHECKS)):
    if p not in sys.path:
        sys.path.insert(0, p)


def _load_smoke():
    """按文件路径加载（目录名含 `-`，不能当包导入）。"""
    spec = importlib.util.spec_from_file_location(
        "smoke_cv_under_test", _DEPLOY_OMR_QA / "smoke_cv.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


S = _load_smoke()


@pytest.fixture()
def img(tmp_path):
    f = tmp_path / "page.png"
    f.write_bytes(b"\x89PNG\r\n\x1a\n")   # 内容无关紧要：假引擎不读图
    return f


def _patch_metrics(monkeypatch, metrics):
    """把 g1_auto.collect_metrics 换成返回固定 metrics 的假函数。"""
    import g1_auto
    monkeypatch.setattr(g1_auto, "collect_metrics", lambda path, engines=(): metrics)


class TestRun:
    def test_pass_when_unit_size_measured(self, img, monkeypatch):
        from g1_auto import AutoMetrics
        _patch_metrics(monkeypatch, AutoMetrics(source="cv", unit_size_px=18.0))
        ok, lines = S.run(img)
        assert ok is True
        assert any("[PASS]" in ln for ln in lines)

    def test_fail_when_engine_error(self, img, monkeypatch):
        """引擎报错 → 失败（这是本脚本存在的理由：errors 不能被忽略）。"""
        from g1_auto import AutoMetrics
        _patch_metrics(monkeypatch, AutoMetrics(source="cv", errors={"cv": "libxcb.so.1 缺失"}))
        ok, lines = S.run(img)
        assert ok is False
        assert any("[FAIL]" in ln for ln in lines)
        assert any("libxcb" in ln for ln in lines)      # 给出 libxcb 专属提示

    def test_fail_when_unit_size_none(self, img, monkeypatch):
        """没报错但也没测到线距 → 仍失败（防"空手通过"）。"""
        from g1_auto import AutoMetrics
        _patch_metrics(monkeypatch, AutoMetrics(source="cv", unit_size_px=None))
        ok, lines = S.run(img)
        assert ok is False
        assert any("unit_size_px 为 None" in ln for ln in lines)

    def test_fail_when_below_min(self, img, monkeypatch):
        from g1_auto import AutoMetrics
        _patch_metrics(monkeypatch, AutoMetrics(source="cv", unit_size_px=3.0))
        ok, lines = S.run(img, min_unit_px=8.0)
        assert ok is False
        assert any("低于 G1-1" in ln for ln in lines)

    def test_fail_when_image_missing(self, tmp_path):
        ok, lines = S.run(tmp_path / "nope.png")
        assert ok is False
        assert any("图片不存在" in ln for ln in lines)


class TestMain:
    def test_exit_zero_on_pass(self, img, monkeypatch):
        from g1_auto import AutoMetrics
        _patch_metrics(monkeypatch, AutoMetrics(source="cv", unit_size_px=18.0))
        assert S.main([str(img)]) == 0

    def test_exit_nonzero_on_failure(self, img, monkeypatch):
        """硬约束：失败必须非零退出 —— CI 靠退出码拦人。"""
        from g1_auto import AutoMetrics
        _patch_metrics(monkeypatch, AutoMetrics(source="cv", errors={"cv": "boom"}))
        assert S.main([str(img)]) == 1
