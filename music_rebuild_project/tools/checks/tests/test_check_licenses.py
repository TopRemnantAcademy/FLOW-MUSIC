"""check_licenses（许可合规门禁）回归测试 —— 纯函数层，零外部依赖。

关键断言：
  - AGPL/GPL → deny；**LGPL → allow**（分类顺序必须先判 LGPL 再判 GPL，否则被误伤）
  - 依赖行缺 `# license:` 标注 → 视为违规（强制把许可写清楚）
  - 包名命中禁用清单（audiveris/homr）→ 违规（纵深防御）
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve()
_TOOLS_DIR = _HERE.parents[2]              # music_rebuild_project/tools
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from checks import check_licenses as CL    # noqa: E402


class TestClassify:
    @pytest.mark.parametrize("lic", ["MIT", "MIT-CMU", "BSD-3-Clause", "Apache-2.0",
                                     "ISC", "MPL-2.0", "PSF-2.0", "Unlicense", "CC0-1.0"])
    def test_allow(self, lic):
        assert CL.classify(lic) == "allow"

    @pytest.mark.parametrize("lic", ["AGPL-3.0", "AGPL-3.0-only", "GPL-3.0", "GPL-2.0",
                                     "SSPL-1.0", "EUPL-1.2", "proprietary"])
    def test_deny(self, lic):
        assert CL.classify(lic) == "deny"

    @pytest.mark.parametrize("lic", ["LGPL-3.0", "LGPL-2.1", "GNU Lesser General Public License v3.0"])
    def test_lgpl_is_allowed_not_confused_with_gpl(self, lic):
        """分类顺序守卫：LGPL 含子串 'GPL'，必须先判 LGPL。"""
        assert CL.classify(lic) == "allow"

    @pytest.mark.parametrize("lic", ["", "OSI Approved", "谁知道呢", "Custom"])
    def test_unknown(self, lic):
        assert CL.classify(lic) == "unknown"


class TestPackageName:
    @pytest.mark.parametrize("line,expected", [
        ("oemer==0.1.8  # license: MIT", "oemer"),
        ("passlib[bcrypt]==1.7.4", "passlib"),
        ("fastapi>=0.115.0", "fastapi"),
        ("python-jose[cryptography]==3.4.0", "python-jose"),
        ("numpy", "numpy"),
    ])
    def test_parse(self, line, expected):
        assert CL.package_name(line) == expected


class TestRequirementsGate:
    def _write(self, tmp_path: Path, text: str) -> Path:
        p = tmp_path / "req.txt"
        p.write_text(text, encoding="utf-8")
        return p

    def test_clean_pass(self, tmp_path):
        p = self._write(tmp_path, "oemer==0.1.8  # license: MIT\nnumpy  # license: BSD-3-Clause\n")
        v, u, o = CL.check_requirements(p)
        assert not v and not u and len(o) == 2

    def test_agpl_is_violation(self, tmp_path):
        p = self._write(tmp_path, "homr==1.0  # license: AGPL-3.0\n")
        v, _, _ = CL.check_requirements(p)
        # homr 同时命中禁用包名清单 → 必然违规
        assert v

    def test_forbidden_package_name(self, tmp_path):
        p = self._write(tmp_path, "audiveris==5.4  # license: MIT\n")
        v, _, _ = CL.check_requirements(p)
        assert any("禁用清单" in x for x in v)

    def test_missing_annotation_is_violation(self, tmp_path):
        p = self._write(tmp_path, "somepkg==1.0\n")
        v, _, _ = CL.check_requirements(p)
        assert any("未标注许可" in x for x in v)

    def test_comments_and_blanks_ignored(self, tmp_path):
        p = self._write(tmp_path, "# 说明\n\n   \noemer==0.1.8  # license: MIT\n")
        v, u, o = CL.check_requirements(p)
        assert not v and len(o) == 1

    def test_unknown_license_fails_by_default(self, tmp_path):
        p = self._write(tmp_path, "weirdpkg==1.0  # license: Custom-Thing\n")
        v, _, _ = CL.check_requirements(p)
        assert any("无法分级" in x for x in v)

    def test_unknown_license_can_be_soft(self, tmp_path):
        p = self._write(tmp_path, "weirdpkg==1.0  # license: Custom-Thing\n")
        v, u, _ = CL.check_requirements(p, allow_unknown=True)
        assert not v and u
