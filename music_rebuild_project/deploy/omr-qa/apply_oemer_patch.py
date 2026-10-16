#!/usr/bin/env python3
"""apply_oemer_patch.py — 幂等地为已安装的 oemer 施加 `bbox.find_lines` 形状补丁。

问题：oemer 0.1.8 `bbox.py` 假设 `cv2.HoughLinesP` 返回 `(N,1,4)`；部分 OpenCV 版本返回
`(N,4)`，于是 `line = line[0]` 把 `(4,)` 行降成标量 → `IndexError: invalid index to scalar
variable`（经 `staffline_extraction.further_infer_track_nums → find_lines` 触发）。

修复：把行归一化为 `(4,)` 并跳过退化项；两种返回形状都能工作。
权威记录：`00_docs/plan/patches/oemer-0.1.8-bbox-find_lines-shape.patch`

用法：python deploy/omr-qa/apply_oemer_patch.py   （幂等：已打过则跳过）
退出码：0=已打/已是最新；1=补丁位点未找到（oemer 版本变了，需人工核对）；2=找不到 oemer
"""

from __future__ import annotations

import sys
from pathlib import Path

MARKER = "# [patch] cv2.HoughLinesP"
OLD = "            line = line[0]\n"
NEW = (
    "            # [patch] cv2.HoughLinesP 返回 (N,1,4) 或 (N,4)，两种都要能处理；\n"
    "            # 原码把 (4,) 行降成标量 → IndexError: invalid index to scalar variable\n"
    "            line = np.asarray(line).reshape(-1)\n"
    "            if line.size < 4:\n"
    "                continue\n"
)


def main() -> int:
    try:
        import oemer
    except Exception as e:
        sys.stderr.write(f"找不到 oemer：{e}\n")
        return 2

    path = Path(oemer.__file__).parent / "bbox.py"
    if not path.exists():
        sys.stderr.write(f"找不到 {path}\n")
        return 2

    src = path.read_text(encoding="utf-8")
    if MARKER in src:
        print(f"已打过补丁，跳过：{path}")
        return 0
    if OLD not in src:
        sys.stderr.write(
            f"补丁位点未找到（oemer 版本可能已变）：{path}\n"
            "请人工核对 bbox.find_lines，并更新 00_docs/plan/patches/ 记录。\n"
        )
        return 1

    path.write_text(src.replace(OLD, NEW, 1), encoding="utf-8")
    print(f"补丁已应用：{path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
