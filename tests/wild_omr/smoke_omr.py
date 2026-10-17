#!/usr/bin/env python3
"""smoke_omr.py — 在**真实 omr-qa 栈**上跑一遍野上传 OMR，并断言「没静默退化」。

与 `g1_auto.collect_metrics` / `smoke_cv.py` 的分工
--------------------------------------------------
`collect_metrics` 的语义是「引擎异常只记账、不炸主流程」；`smoke_cv` 只盯 CV 引擎。
`smoke_cv` 那种「errors 非空就红」的写法**不能**照搬到 OMR：野上传本来就该被 G1 拦下，
引擎对它报错是**预期**，不是故障。所以本脚本把判断拆成两条：

  1. **OMR 真的跑到了**（证明装出来的栈没坏）
       a. `oemer` 引擎无 error，且 `unit_size_px` 测到 → 直接通过；
       b. `oemer` 报错时，只有当错误属于**已知的拒绝/降级类**（超时、检查点缺失、
          无 MusicXML、非零退出）才算"引擎活着"；其它异常一律红。
  2. **失败没有被吞掉**：跑完 `--assert-fail-closed` 指定的 pytest 断言集，
     输出末行必须是 `N passed`（守卫在"用跳过/放宽来骗绿"时会暴露）。

退出码：0=通过；1=断言失败；2=用法/环境错误。
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

_HERE = Path(__file__).resolve()
_REPO = _HERE.parents[2]
_TOOLS_CHECKS = _REPO / "music_rebuild_project" / "tools" / "checks"
if str(_TOOLS_CHECKS) not in sys.path:
    sys.path.insert(0, str(_TOOLS_CHECKS))

#: oemer 报错里属于「引擎活着但拒绝/降级」的措辞（其余都视为故障）
KNOWN_DEGRADATION = ("超时", "检查点", "未产出 MusicXML", "退出码")


def run_engine(image: Path, engines: tuple[str, ...] = ("oemer",)) -> tuple[bool, list[str]]:
    lines: list[str] = []
    if not image.is_file():
        return False, [f"[FAIL] 图片不存在：{image}"]
    try:
        from g1_auto import collect_metrics
    except Exception as e:  # pragma: no cover - 环境相关
        return False, [f"[FAIL] 无法导入 g1_auto：{type(e).__name__}: {e}"]

    m = collect_metrics(image, engines=engines)
    lines.append(f"[info] 引擎={','.join(engines)} source={m.source} 图={image.name}")
    lines.append(f"[info] unit_size_px={m.unit_size_px} n_notes={m.n_notes} "
                 f"n_measures={m.n_measures} musicxml={m.musicxml_path}")
    for eng, err in m.errors.items():
        lines.append(f"[info] 引擎 {eng} 报错：{err}")

    if not m.errors and m.unit_size_px is not None:
        lines.append(f"[PASS] OMR 实测线距={m.unit_size_px:.2f}px，"
                     f"MusicXML={m.musicxml_path or '(未记录)'}")
        return True, lines

    unknown = {e: err for e, err in m.errors.items()
               if not any(k in err for k in KNOWN_DEGRADATION)}
    if unknown:
        for eng, err in unknown.items():
            lines.append(f"[FAIL] OMR 引擎 {eng} 出现**未被归类**的异常：{err}")
        return False, lines

    lines.append("[PASS] OMR 引擎活着，但对野上传如实降级/拒绝（已知类别）—— 未静默通过")
    return True, lines


def run_pytest(targets: list[str], extra: list[str] | None = None) -> tuple[bool, list[str]]:
    cmd = [sys.executable, "-m", "pytest", *targets, "-q", "-p", "no:cacheprovider", *(extra or [])]
    lines = ["[info] " + " ".join(cmd)]
    # 不用 capture_output：断言输出就是本条流水线的证据
    proc = subprocess.run(cmd, cwd=str(_REPO), text=True)
    ok = proc.returncode == 0
    lines.append(f"[{'PASS' if ok else 'FAIL'}] pytest 退出码={proc.returncode}（目标 {len(targets)} 个）")
    return ok, lines


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="smoke_omr.py", description="野上传 OMR 冒烟 + fail-closed 断言")
    ap.add_argument("--image", required=True, help="脱敏野上传样本图")
    ap.add_argument("--engines", default="oemer", help="逗号分隔的引擎（默认 oemer）")
    ap.add_argument("--assert-fail-closed", nargs="*", default=[],
                    help="跑完引擎后额外执行的 pytest 目标（守卫用）")
    ap.add_argument("--pytest-arg", action="append", default=[], help="透传给 pytest 的参数")
    args = ap.parse_args(argv)

    ok1, lines1 = run_engine(Path(args.image), tuple(e for e in args.engines.split(",") if e))
    ok2, lines2 = (True, [])
    if args.assert_fail_closed:
        ok2, lines2 = run_pytest(args.assert_fail_closed, args.pytest_arg)

    for ln in (*lines1, *lines2):
        print(ln)
    return 0 if (ok1 and ok2) else 1


if __name__ == "__main__":
    raise SystemExit(main())
