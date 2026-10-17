"""smoke_omr_model_load 回归测试（#171）。

只测**契约**，全部用注入的 ``loader`` 驱动，不需要装 oemer/sklearn：

- 版本漂移 / 未知版本告警 → **非零退出**（这是本脚本存在的唯一理由）
- 已评估的 (0.1.x → 1.3.2) 告警 → 通过，但必须把豁免理由打印出来（不许静默）
- 告警走 stderr 也要抓到（C 层直接写 stderr 的收不进 warnings）
- 模型加载/推理抛错 → 失败（fail-closed）
- 缺栈 → 退出码 2（与「断言失败」区分，便于 CI 排错）
"""

from __future__ import annotations

import importlib.util
import sys
import warnings
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve()
_DEPLOY_OMR_QA = _HERE.parents[1]          # music_rebuild_project/deploy/omr-qa

_MARKER = "Trying to unpickle estimator SVC from version {old} when using version {new}. " \
          "This might lead to breaking code or invalid results."


def _load_smoke():
    """按文件路径加载（目录名含 `-`，不能当包导入）。"""
    spec = importlib.util.spec_from_file_location(
        "smoke_omr_model_load_under_test", _DEPLOY_OMR_QA / "smoke_omr_model_load.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


S = _load_smoke()


def _fake_oemer(monkeypatch):
    """让 `import oemer` / `import oemer.ete` 在没有真 oemer 的环境里也能过。"""
    import types

    pkg = types.ModuleType("oemer")
    pkg.MODULE_PATH = "/nonexistent"          # 真加载路径不会被走到（loader 已注入）
    ete = types.ModuleType("oemer.ete")
    pkg.ete = ete
    monkeypatch.setitem(sys.modules, "oemer", pkg)
    monkeypatch.setitem(sys.modules, "oemer.ete", ete)


def _warn_via_warnings(text: str):
    def loader():
        warnings.warn(UserWarning(text))
    return loader


def _warn_via_stderr(text: str):
    def loader():
        sys.stderr.write(text + "\n")
    return loader


class TestClassify:
    @pytest.mark.parametrize("text,allowed", [
        (_MARKER.format(old="1.2.0", new="1.3.2"), True),    # 本栈钉的组合：豁免
        (_MARKER.format(old="1.2.0", new="1.9.1"), False),   # 版本漂移：不豁免
        (_MARKER.format(old="1.0.0", new="1.3.2"), False),   # pickle 版本变了也不豁免
    ])
    def test_version_warning_verdicts(self, text, allowed):
        verdict = S.classify(text)
        assert verdict is not None, "版本类告警必须被识别"
        assert bool(verdict) is allowed

    @pytest.mark.parametrize("text", [
        "ResourceWarning: unclosed file <_io.BufferedReader name='x'>",
        "UserWarning: some unrelated deprecation",
        "",
    ])
    def test_non_version_warnings_ignored(self, text):
        assert S.classify(text) is None

    def test_parse_versions(self):
        old, new = S.parse_versions(_MARKER.format(old="1.2.0", new="1.9.1"))
        assert (old, new) == ("1.2.0", "1.9.1")


class TestRun:
    def test_pass_when_only_allowed_warning(self, monkeypatch):
        _fake_oemer(monkeypatch)
        ok, lines, code = S.run(loader=_warn_via_warnings(_MARKER.format(old="1.2.0", new="1.3.2")))
        assert ok is True and code == S.EXIT_OK
        # 豁免必须留下痕迹，不能静默通过
        assert any("已知豁免" in ln for ln in lines)

    def test_fail_when_version_drifted(self, monkeypatch):
        """核心断言：sklearn 漂到 1.9.x → 必须红（#171 的原始缺陷）。"""
        _fake_oemer(monkeypatch)
        ok, lines, code = S.run(loader=_warn_via_warnings(_MARKER.format(old="1.2.0", new="1.9.1")))
        assert ok is False and code == S.EXIT_FAIL
        assert any("[FAIL]" in ln and "1.9.1" in ln for ln in lines)

    def test_fail_when_warning_only_on_stderr(self, monkeypatch):
        """C 层直接写 stderr 的告警不会进 warnings 列表，也必须被抓到。"""
        _fake_oemer(monkeypatch)
        ok, _, code = S.run(loader=_warn_via_stderr(_MARKER.format(old="1.2.0", new="1.9.1")))
        assert ok is False and code == S.EXIT_FAIL

    def test_fail_when_loader_raises(self, monkeypatch):
        """fail-closed：模型加载/推理抛错不许当成通过。"""
        _fake_oemer(monkeypatch)

        def boom():
            raise RuntimeError("SVC 状态布局不兼容")

        ok, lines, code = S.run(loader=boom)
        assert ok is False and code == S.EXIT_FAIL
        assert any("SVC 状态布局不兼容" in ln for ln in lines)

    def test_strict_ignores_exemption(self, monkeypatch):
        _fake_oemer(monkeypatch)
        ok, _, code = S.run(strict=True,
                            loader=_warn_via_warnings(_MARKER.format(old="1.2.0", new="1.3.2")))
        assert ok is False and code == S.EXIT_FAIL

    def test_exit_2_when_oemer_missing(self, monkeypatch):
        """缺栈 → 退出码 2（与断言失败的 1 区分开）。"""
        for name in ("oemer", "oemer.ete"):
            monkeypatch.setitem(sys.modules, name, None)   # import 会抛 ImportError
        ok, lines, code = S.run(loader=lambda: None)
        assert ok is False and code == S.EXIT_ENV
        assert any("无法 import oemer" in ln for ln in lines)


class TestMain:
    def test_exit_nonzero_on_failure(self, monkeypatch):
        """硬约束：失败必须非零退出 —— CI 靠退出码拦人。"""
        _fake_oemer(monkeypatch)
        monkeypatch.setattr(S, "run", lambda strict=False, loader=None: (
            False, ["[FAIL] boom"], S.EXIT_FAIL))
        assert S.main([]) != 0

    def test_exit_zero_on_pass(self, monkeypatch):
        _fake_oemer(monkeypatch)
        monkeypatch.setattr(S, "run", lambda strict=False, loader=None: (
            True, ["[PASS] ok"], S.EXIT_OK))
        assert S.main([]) == 0


class TestRequirementsPinned:
    """需求文件必须真的钉住 ML 依赖（否则 smoke 只是「恰好这天绿」）。"""

    _REQ = _DEPLOY_OMR_QA / "requirements-omr.txt"

    @pytest.mark.parametrize("pkg", ["scikit-learn", "scipy", "scikit-image", "numpy"])
    def test_ml_dependency_is_pinned(self, pkg):
        lines = [ln.split("#", 1)[0].strip() for ln in self._REQ.read_text(encoding="utf-8").splitlines()]
        hit = [ln for ln in lines if ln.lower().startswith(pkg.lower())]
        assert hit, f"requirements-omr.txt 没有钉 {pkg}（#171 的核心修复）"
        # 钉 = 有上界（== 或 <）；只写裸包名等于交给解析器漂
        assert any("==" in ln or "<" in ln for ln in hit), f"{pkg} 未钉上界：{hit}"
