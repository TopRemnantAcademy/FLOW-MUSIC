#!/usr/bin/env python3
"""编曲自动化守卫：目录写保护的轻量可执行校验（#57）。

只读检查，不写任何数据目录。用法：

    python3 00_docs/check_arrangement_guard.py            # 默认扫描本仓库
    python3 00_docs/check_arrangement_guard.py --root .   # 指定仓库根

检查项（与 00_docs/automation_safety_rules.md 的 F-1/F-2/F-4/F-6 对应）：
  1. 冻结区必须存在且**不可写**（02_source/、04_approved/）；
  2. 冻结区内的已跟踪产物若带可写位，列为 WARN（该由 #27 的 check_ro.sh 判定）；
  3. 编曲区 06_arrangement/ 允许可写（不作为失败）。

退出码：0 = 无 FAIL；1 = 至少一项 FAIL；2 = 用法错误。
只用 Python 标准库。

注意：以 root 运行时 os.access(W_OK) 恒为 True，冻结区会被误报；
请以普通用户执行，或仅把本脚本当作提醒（权威只读判定归 #27 的 check_ro.sh）。
"""

from __future__ import annotations

import argparse
import os
import stat
import sys
from pathlib import Path

# 与 automation_safety_rules.md 一致的冻结根目录
FROZEN_DIRS = ("02_source", "04_approved")
WRITABLE_OK_DIRS = ("06_arrangement",)

FAIL: list[str] = []
WARN: list[str] = []


def _is_writable(path: Path) -> bool:
    """路径对当前进程是否可写（不实际写入）。"""
    return os.access(path, os.W_OK)


def check_frozen_dirs(root: Path) -> None:
    for name in FROZEN_DIRS:
        target = root / name
        if not target.is_dir():
            # #27 的只读加固未落地时目录可能不存在，不判 FAIL，只提示。
            WARN.append(f"MISSING {name}: 冻结区不存在（#27 只读加固未落地？）")
            continue
        if _is_writable(target):
            FAIL.append(
                f"F-1/F-2 违规风险: {name}/ 对当前进程可写，"
                f"应设为只读（#27 / check_ro.sh）"
            )
        else:
            print(f"OK  {name}/ 只读")


def check_frozen_artifacts(root: Path) -> None:
    """冻结区内的文件若带 group/other 可写位，提示复查。"""
    for name in FROZEN_DIRS:
        target = root / name
        if not target.is_dir():
            continue
        for path in target.rglob("*"):
            if not path.is_file():
                continue
            mode = path.stat().st_mode
            if mode & (stat.S_IWGRP | stat.S_IWOTH):
                WARN.append(
                    f"F-3 复查: {path.relative_to(root)} 带 group/other 可写位"
                )


def check_writable_ok(root: Path) -> None:
    for name in WRITABLE_OK_DIRS:
        target = root / name
        if target.is_dir() and not _is_writable(target):
            WARN.append(f"A-4: {name}/ 不可写，正常编曲动作会被阻塞（L1 应为可迭代）")
        elif target.is_dir():
            print(f"OK  {name}/ 可写（L1 可迭代）")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="编曲自动化守卫（只读检查）")
    parser.add_argument("--root", default=".", help="仓库根目录（默认当前目录）")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"用法错误: --root {args.root} 不是目录", file=sys.stderr)
        return 2

    print(f"扫描根目录: {root}")
    check_frozen_dirs(root)
    check_frozen_artifacts(root)
    check_writable_ok(root)

    for msg in WARN:
        print(f"WARN {msg}")
    for msg in FAIL:
        print(f"FAIL {msg}")

    print(f"结论: FAIL={len(FAIL)} WARN={len(WARN)}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
