#!/usr/bin/env python3
"""check_licenses.py — 许可合规门禁（对外服务栈**禁止 AGPL/GPL**）。

背景：本能力将「上传服务器供他人使用」（网络服务）。AGPL-3.0 §13 网络条款会带来
源码披露义务风险，GPL 有传染性；故对外服务栈只允许 **MIT / BSD / Apache / LGPL / ISC /
MPL / PSF** 等宽松或弱 copyleft 许可（LGPL 允许，因其可动态链接/独立进程使用）。
依据：`00_docs/plan/10_oss_best_stack_plan.md` §2、§5。

两种模式
--------
1. **静态**（``--requirements <file>``）：逐行解析依赖文件，要求**每个依赖都显式标注**
   ``# license: <SPDX>``（未标注即失败——强制把许可写清楚，避免"忘了查"），再分级判定。
2. **动态**（``--env``）：扫描**当前 Python 环境**已安装发行版（importlib.metadata）的
   许可字段/分类器，批量分级判定。

退出码：0 = 通过；1 = 发现禁用许可；2 = 用法/读取错误。

用法::

    python tools/checks/check_licenses.py --requirements deploy/omr-qa/requirements-omr.txt
    python tools/checks/check_licenses.py --env
    python tools/checks/check_licenses.py --env --strict     # unknown 也算失败
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# 判定常量（与 10_oss_best_stack_plan.md §5 一致）
ALLOW_TOKENS = (
    "MIT", "BSD", "APACHE", "ISC", "MPL", "PSF", "PYTHON", "UNLICENSE",
    "CC0", "ZLIB", "BSL-1.0", "WTFPL", "0BSD",
)
DENY_TOKENS = ("SSPL", "EUPL", "CPAL", "OSL", "RPL", "PROPRIETARY", "COMMERCIAL", "RESEARCH")

# 纵深防御：即使许可字段写着宽松，这些**包名**也一律拒绝（本项目明确不引入）
FORBIDDEN_PACKAGES = ("audiveris", "homr", "agpl", "gpl")

_LICENSE_ANNOT = re.compile(r"#\s*license\s*:\s*([A-Za-z0-9.\-+ ]+)")


def classify(license_str: str) -> str:
    """许可字符串 → 'allow' | 'deny' | 'unknown'。

    顺序关键：先判 AGPL、再判 LGPL，**最后**才判 GPL —— 否则 "LGPL" 会被 "GPL" 误伤。
    """
    s = (license_str or "").upper().replace(" ", "").replace("_", "-")
    if not s:
        return "unknown"
    # 拼写全称（元数据里很常见，如 "GNU Lesser General Public License v3.0"）先归一化
    # 顺序：AFFERO(AGPL) → LESSER(LGPL) → GENERAL(GPL) —— 否则 LGPL 会被 GPL 误伤
    if "AFFERO" in s or "AGPL" in s:
        return "deny"
    if "LESSERGENERALPUBLIC" in s or "LESSERGPL" in s or "LGPL" in s:
        return "allow"
    if "GENERALPUBLICLICENSE" in s or "GPL" in s:
        return "deny"
    if any(t in s for t in DENY_TOKENS):
        return "deny"
    if any(t in s for t in ALLOW_TOKENS):
        return "allow"
    return "unknown"


def package_name(req_line: str) -> str:
    """'oemer==0.1.8  # license: MIT' → 'oemer'（去掉 extras / 版本约束）。"""
    head = req_line.split("#", 1)[0].strip()
    head = re.split(r"[<>=!~\[]", head, maxsplit=1)[0].strip()
    return head


def check_requirements(path: Path, allow_unknown: bool = False) -> tuple[list[str], list[str], list[str]]:
    """→ (violations, unknowns, oks)。未标注许可 = violation（强制写清）。"""
    violations: list[str] = []
    unknowns: list[str] = []
    oks: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        name = package_name(line)
        if not name:
            continue
        low = name.lower().replace("-", "").replace("_", "")
        if any(f in low for f in FORBIDDEN_PACKAGES):
            violations.append(f"{name}: 包名命中禁用清单（本项目不引入 AGPL/GPL 组件）")
            continue
        m = _LICENSE_ANNOT.search(raw)
        if not m:
            violations.append(f"{name}: **未标注许可**（须写 `# license: <SPDX>`）")
            continue
        lic = m.group(1).strip()
        verdict = classify(lic)
        if verdict == "deny":
            violations.append(f"{name}: 许可 `{lic}` 被禁用（AGPL/GPL 类）")
        elif verdict == "unknown":
            (unknowns if allow_unknown else violations).append(
                f"{name}: 许可 `{lic}` 无法分级（请确认后补进 ALLOW_TOKENS）")
        else:
            oks.append(f"{name}: {lic}")
    return violations, unknowns, oks


def check_env(strict: bool = False) -> tuple[list[str], list[str], list[str]]:
    """扫描当前环境已安装发行版的许可。"""
    import importlib.metadata as md

    violations: list[str] = []
    unknowns: list[str] = []
    oks: list[str] = []
    for dist in md.distributions():
        name = (dist.metadata.get("Name") or "?").strip()
        low = name.lower().replace("-", "").replace("_", "")
        if any(f in low for f in FORBIDDEN_PACKAGES):
            violations.append(f"{name}: 包名命中禁用清单")
            continue
        meta = dist.metadata
        lic = (meta.get("License-Expression") or "").strip()
        if not lic:
            for c in meta.get_all("Classifier") or []:
                if c.startswith("License ::"):
                    lic = c.split("::")[-1].strip()
                    break
        if not lic:
            lic = (meta.get("License") or "").strip().splitlines()[0] if meta.get("License") else ""
        verdict = classify(lic)
        if verdict == "deny":
            violations.append(f"{name}: 许可 `{lic}` 被禁用")
        elif verdict == "unknown":
            tag = f"{name}: 许可 `{lic or '(空)'}` 无法分级"
            (violations if strict else unknowns).append(tag)
        else:
            oks.append(f"{name}: {lic}")
    return violations, unknowns, oks


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="check_licenses.py",
                                 description="许可合规门禁（对外服务栈禁止 AGPL/GPL）")
    ap.add_argument("--requirements", help="依赖文件（逐行需带 `# license: <SPDX>` 标注）")
    ap.add_argument("--env", action="store_true", help="扫描当前 Python 环境")
    ap.add_argument("--strict", action="store_true", help="unknown 也视为失败")
    args = ap.parse_args(argv)

    if not args.requirements and not args.env:
        sys.stderr.write("用法：--requirements <file> 和/或 --env\n")
        return 2

    violations: list[str] = []
    unknowns: list[str] = []
    oks: list[str] = []

    if args.requirements:
        p = Path(args.requirements)
        if not p.exists():
            sys.stderr.write(f"文件不存在: {p}\n")
            return 2
        v, u, o = check_requirements(p)
        violations += v
        unknowns += u
        oks += o
        print(f"[静态] {p} → {len(o)} OK / {len(u)} unknown / {len(v)} 违规")

    if args.env:
        v, u, o = check_env(strict=args.strict)
        violations += v
        unknowns += u
        oks += o
        print(f"[动态] 当前环境 → {len(o)} OK / {len(u)} unknown / {len(v)} 违规")

    for tag, rows in (("违规", violations), ("待确认", unknowns)):
        for r in rows:
            print(f"  [{tag}] {r}")

    if violations:
        print("license gate: FAIL")
        return 1
    print("license gate: PASS" + (f"（{len(unknowns)} 个待确认许可 · 建议 --strict 前先确认）"
                                  if unknowns else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
