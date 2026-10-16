"""requirements-omr.txt 的 **headless OpenCV 策略**回归测试（#165 T2/T3）。

为什么值得单独一个测试：`rapidocr-onnxruntime` 硬声明依赖 `opencv-python`（非 headless），
而它与 `opencv-python-headless` **共用同一个 `cv2` 包名** —— 谁后装谁生效。
一旦有人在 requirements 里挪动顺序、或删掉 Dockerfile/CI 里的 uninstall 步骤，
镜像就会悄悄装成 GUI 版，然后在无头容器里 `import cv2` 炸 `libGL.so.1`。
这里把"策略"钉成可读的断言：'opencv-python' 只能以 '-headless' 形式出现。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve()
_DEPLOY_OMR_QA = _HERE.parents[1]        # music_rebuild_project/deploy/omr-qa
_REPO_ROOT = _HERE.parents[4]            # 仓库根（omr-qa → deploy → music_rebuild_project → 仓库根）
_REQ = _DEPLOY_OMR_QA / "requirements-omr.txt"

# 解析"有效的"依赖行：剥掉注释后再看有无包名（`# license:` 标注行不算）
_REQ_LINE = re.compile(r"^\s*([A-Za-z0-9._-]+)\s*(?:[<>=!~].*)?$")


def _package_names() -> list[str]:
    names = []
    for raw in _REQ.read_text(encoding="utf-8").splitlines():
        code = raw.split("#", 1)[0].strip()
        if not code:
            continue
        m = _REQ_LINE.match(code)
        if m:
            names.append(m.group(1))
    return names


class TestRequirementsHeadless:
    def test_headless_is_declared(self):
        assert "opencv-python-headless" in _package_names()

    def test_no_non_headless_opencv(self):
        """硬约束：清单里**不得**出现裸 `opencv-python`（会被 rapidocr 拉进来，
        但绝不能是我们自己声明的）—— 它的存在就意味着策略被破坏。"""
        bad = [n for n in _package_names() if n.lower() == "opencv-python"]
        assert not bad, f"requirements 里出现了非 headless 的 opencv-python：{bad}"

    def test_every_dependency_has_license_annotation(self):
        """清单每一行依赖都必须标许可（check_licenses.py --requirements 的硬要求）。"""
        missing = []
        for raw in _REQ.read_text(encoding="utf-8").splitlines():
            code = raw.split("#", 1)[0].strip()
            if not code or not _REQ_LINE.match(code):
                continue
            if "# license:" not in raw:
                missing.append(code)
        assert not missing, f"这些依赖没标 `# license:`：{missing}"


class TestStrategyPropagatedToBuilders:
    """策略必须同时落到**两处**构建入口，否则一处漂移就白做。"""

    @pytest.mark.parametrize("rel", [
        "music_rebuild_project/deploy/omr-qa/Dockerfile",  # 镜像构建期
        ".cnb.yml",                                        # CI 重活 stage（仓库根）
    ])
    def test_uninstall_non_headless_present(self, rel):
        p = _REPO_ROOT / rel
        assert p.is_file(), f"找不到 {p}"
        text = p.read_text(encoding="utf-8")
        assert "pip uninstall -y opencv-python" in text, \
            f"{rel} 缺少卸载非 headless 的步骤（rapidocr 会覆盖 headless）"
        assert "opencv-python-headless" in text
