#!/usr/bin/env python3
"""smoke_cv.py — 对外栈 **CV 引擎冒烟断言**（CI 重活 stage 与镜像构建期共用）。

为什么需要它
------------
`collect_metrics(..., engines=("cv",))` 的语义是**降级不抛出**：引擎挂了只记
`m.errors`，然后照常返回一个几乎全空的对象。判定层面对空字段会给出 `SKIP`
（"不冒充 PASS"），但如果没人检查 `errors`，一次"引擎坏了"就会静默滑过去 ——
本 issue（#165）要根治的正是这种**静默通过**。

因此本脚本只做一件事：**跑一次 cv 引擎，然后断言真的测到了东西**。
`unit_size_px is None` 或 `errors` 非空 → 打印诊断并以**非零退出**。

背景缺陷（#163 T6+ / 2026-10-16 实测）
-------------------------------------
无头镜像里装 `opencv-python`（非 headless）会在 `import cv2` 时炸
`libxcb.so.1: cannot open shared object file` —— 因为没有 X11 运行时。
本栈策略是钉 `opencv-python-headless`（见 `requirements-omr.txt`），
本脚本就是那个策略的**可执行断言**：换成非 headless 版本，这里立刻红。

用法::

    python deploy/omr-qa/smoke_cv.py <图片路径> [--min-unit-px 8.0]

退出码：0=断言通过；1=断言失败（引擎坏/没测到）；2=用法或读图错误。

供复用：``run(path, min_unit_px=...)`` 返回 ``(ok, lines)``，便于镜像构建期或测试调用。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

# 自举：脚本在 deploy/omr-qa/ 下，被测模块在 tools/checks/ 下（镜像里则是 /app/tools/checks）
_HERE = Path(__file__).resolve()
_TOOLS_CHECKS = _HERE.parents[2] / "tools" / "checks"
if _TOOLS_CHECKS.is_dir() and str(_TOOLS_CHECKS) not in sys.path:
    sys.path.insert(0, str(_TOOLS_CHECKS))

# 线距下限：与 gate_G1.md G1-1 子项一致（≥8px 才算合格）；这里只断言"测到了且不算退化"
MIN_UNIT_PX_DEFAULT = 8.0


def run(path: Path, min_unit_px: float = MIN_UNIT_PX_DEFAULT,
        engines: Sequence[str] = ("cv",)) -> tuple[bool, list[str]]:
    """跑一次冒烟，返回 ``(是否通过, 输出行)``。不打印、不退出（便于单测复用）。"""
    lines: list[str] = []
    if not path.is_file():
        return False, [f"[FAIL] 图片不存在：{path}"]

    try:
        from g1_auto import collect_metrics  # noqa: PLC0415 - 延迟导入，import cv2 在引擎内部
    except Exception as e:  # pragma: no cover - 环境相关
        return False, [f"[FAIL] 无法导入 g1_auto：{type(e).__name__}: {e}"]

    m = collect_metrics(path, engines=engines)
    lines.append(f"[info] 引擎={','.join(engines)} source={m.source} 图={path.name}")
    if m.errors:
        for eng, err in m.errors.items():
            lines.append(f"[FAIL] 引擎 {eng} 报错：{err}")
    lines.append(
        f"[info] unit_size_px={m.unit_size_px} unit_size_min_px={m.unit_size_min_px} "
        f"skew_deg_max={m.skew_deg_max} content_margin_ratio={m.content_margin_ratio}"
    )

    if m.errors:
        # 最常见的两个坑，直接点出来省排查时间
        joined = " ".join(m.errors.values())
        if "libxcb" in joined:
            lines.append("[hint] 缺 libxcb.so.1 → opencv 装了非 headless 版；"
                         "本栈要求 opencv-python-headless（见 requirements-omr.txt）")
        lines.append("[FAIL] CV 冒烟失败：引擎报错（见上）—— 拒绝静默通过")
        return False, lines

    if m.unit_size_px is None:
        lines.append("[FAIL] CV 冒烟失败：unit_size_px 为 None（未测到五线谱线距）"
                     "→ 引擎退化，拒绝静默通过")
        return False, lines

    if m.unit_size_px < min_unit_px:
        lines.append(f"[FAIL] CV 冒烟失败：线距={m.unit_size_px:.2f}px < {min_unit_px:g}px"
                     "（低于 G1-1 子项下限）")
        return False, lines

    lines.append(f"[PASS] CV 冒烟断言通过：线距={m.unit_size_px:.2f}px ≥ {min_unit_px:g}px，"
                 f"cv2 在无头容器内可用")
    return True, lines


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="smoke_cv.py",
                                 description="对外栈 CV 引擎冒烟断言（失败即非零退出）")
    ap.add_argument("image", help="用于冒烟的乐谱图片路径")
    ap.add_argument("--min-unit-px", type=float, default=MIN_UNIT_PX_DEFAULT,
                    help=f"线距下限（默认 {MIN_UNIT_PX_DEFAULT:g}px，与 G1-1 子项一致）")
    args = ap.parse_args(argv)

    ok, lines = run(Path(args.image), min_unit_px=args.min_unit_px)
    for ln in lines:
        print(ln)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
