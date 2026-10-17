#!/usr/bin/env python3
"""smoke_omr_model_load.py — OMR 栈 **模型加载/版本告警** 冒烟断言（#171）。

为什么需要它
------------
oemer 0.1.8 把 4 个训练好的符号分类器以 pickle 形式打包（`sklearn_models/*.model`），
pickle 里记着当时的 scikit-learn 版本（**1.2.0**）。sklearn 的
`BaseEstimator.__setstate__` 发现「pickle 版本 ≠ 运行时版本」就会抛
`InconsistentVersionWarning`，措辞是「可能产出破坏代码或无效结果」。

这是最坏的一种缺陷：**不报错**。谱号/休止符/变音记号分类器悄悄偏斜，MusicXML 里
就是错的音符，而既有门禁（许可、CV 冒烟、单测）全绿 —— 因为没人去看 stderr 里
那行 warning。本脚本把「有没有 version 类告警」变成**退出码**。

做什么
------
1. `import oemer` + `import oemer.ete`（触发整条推理链的 import，自带告警也算）；
2. 逐个 load oemer 打包的 sklearn 模型并**真跑一次 predict**（只 load 不 predict
   会漏掉 predict 路径的告警，也测不出模型还能不能用）；
3. 收集 warnings + stderr 里的 version 类告警 → **非零退出**。

fail-closed：import 失败、模型缺失、预测抛错、任何未豁免的 version 告警都算失败，
没有「跳过即通过」的路径。

唯一豁免（写死在 `_ALLOWED`，改它要改注释和 tests/test_smoke_omr_model_load.py）
------------------------------------------------------------------------------
本栈钉的 sklearn **1.3.2** 与 pickle 里的 **1.2.0** 仍会触发同一条告警。这不是漏网：
1.2.0~1.3.2 全都会告警（SVC 状态布局确实变过），而更老的 1.2.x **没有 cp312 wheel**，
在 python:3.12-slim 里装不上。所以这条告警是「已知且被评估过」的，按**版本精确豁免**；
运行时版本一旦离开 1.3.2（漂到 1.9.x、2.x…），告警文本不再匹配 → 红。

用法::

    python deploy/omr-qa/smoke_omr_model_load.py [--strict]

退出码：0=通过；1=发现未豁免的 version 告警 / 模型不可用；2=环境缺 oemer。

供复用：``run()`` 返回 ``(ok, lines)``，不打印、不退出（镜像构建期与单测共用）。
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.metadata as md
import io
import sys
import warnings
from pathlib import Path

# oemer 0.1.8 打包的 sklearn 模型：全都要过一遍（顺序无关）
MODEL_NAMES = ("clef", "rests", "rests_above8", "sfn")

# 已知且被评估过的 (运行时版本, pickle 版本)。命中即豁免，其余一律失败。
_ALLOWED: set[tuple[str, str]] = {("1.3.2", "1.2.0")}

# 版本类告警的文本特征。不用 isinstance 判类：sklearn 老版本没有这个 Warning 类，
# 而 stderr 里是自由文本，两者要一起抓。
_VERSION_MARKERS = (
    "inconsistentversionwarning",
    "trying to unpickle estimator",
    "when using version",
)

EXIT_OK, EXIT_FAIL, EXIT_ENV = 0, 1, 2


def _installed_version(pkg: str) -> str:
    try:
        return md.version(pkg)
    except md.PackageNotFoundError:  # pragma: no cover - 环境相关
        return "(未安装)"


def is_version_warning(text: str) -> bool:
    low = text.lower()
    return any(m in low for m in _VERSION_MARKERS)


def parse_versions(text: str) -> tuple[str | None, str | None]:
    """从告警文本里抠出 (pickle 版本, 运行时版本)；抠不到给 None。"""
    low = text.lower()
    pickle_v = runtime_v = None
    if "from version " in low:
        pickle_v = low.split("from version ", 1)[1].split()[0].rstrip(".,;)")
    if "when using version " in low:
        runtime_v = low.split("when using version ", 1)[1].split()[0].rstrip(".,;)")
    return pickle_v, runtime_v


def classify(text: str) -> str | None:
    """版本类告警 → 处理结论：``None``=不是版本告警；``""``=未豁免（失败）；其它=豁免原因。"""
    if not is_version_warning(text):
        return None
    pickle_v, runtime_v = parse_versions(text)
    if (runtime_v, pickle_v) in _ALLOWED:
        return (f"已知豁免：pickle {pickle_v} → 运行时 {runtime_v}"
                "（#171 决策，理由见 requirements-omr.txt）")
    return ""


def _load_all_models(module_path: str) -> None:
    """真实加载 + 最小推理：oemer 的每个 sklearn 模型。"""
    import pickle

    import numpy as np

    for name in MODEL_NAMES:
        path = Path(module_path) / "sklearn_models" / f"{name}.model"
        if not path.is_file():
            raise FileNotFoundError(f"oemer 模型缺失：{path}")
        with path.open("rb") as f:
            m_info = pickle.load(f)
        model, w, h = m_info["model"], m_info["w"], m_info["h"]
        toy = np.random.default_rng(0).random((1, w * h)) * 255.0
        model.predict(toy)      # 只 load 不 predict 会漏掉 predict 路径的告警


def _exercise(loader) -> None:
    """跑一遍加载流程。默认走真实 oemer；单测可注入 ``loader``。"""
    if loader is not None:
        loader()
        return
    import oemer
    from oemer import MODULE_PATH

    _load_all_models(MODULE_PATH)


def run(strict: bool = False, loader=None) -> tuple[bool, list[str], int]:
    """跑一次冒烟 → ``(是否通过, 输出行, 建议退出码)``。不打印、不退出。"""
    lines = [
        "[info] 版本："
        + " ".join(f"{p}={_installed_version(p)}" for p in
                   ("scikit-learn", "scipy", "scikit-image", "numpy", "oemer"))
    ]
    if strict:
        lines.append("[info] --strict：豁免清单被忽略，任何版本告警都算失败")

    # 1) import 阶段（oemer 的 import 链很重，跑得到才有意义）
    try:
        import oemer  # noqa: F401
        import oemer.ete  # noqa: F401
    except Exception as e:  # noqa: BLE001 - 缺栈/缺依赖都算环境问题
        return False, lines + [f"[FAIL] 无法 import oemer（环境缺栈）：{type(e).__name__}: {e}"], EXIT_ENV

    # 2) 加载 + 推理阶段：warnings 与 stderr 一起收（C 层直接写 stderr 的漏不出来）
    stderr_buf = io.StringIO()
    with warnings.catch_warnings(record=True) as caught, \
            contextlib.redirect_stderr(stderr_buf):
        warnings.simplefilter("always")     # 不管外面设过什么 filter，一律收全
        try:
            _exercise(loader)
        except Exception as e:  # noqa: BLE001 - fail-closed：任何异常都不许静默
            return False, lines + [f"[FAIL] 模型加载/推理抛错：{type(e).__name__}: {e}"], EXIT_FAIL

    # 3) 判定
    sources: list[str] = [str(getattr(w, "message", w)) for w in caught]
    sources += [ln for ln in stderr_buf.getvalue().splitlines() if ln.strip()]

    failures: list[str] = []
    seen: set[str] = set()
    for text in sources:
        verdict = classify(text)
        if verdict is None:
            continue
        if verdict and not strict:
            if text not in seen:
                lines.append(f"[info] {verdict}")
                seen.add(text)
            continue
        if text not in seen:
            failures.append(text.strip())
            seen.add(text)

    if failures:
        for text in failures:
            lines.append(f"[FAIL] 版本类告警（模型可能已偏斜）：{text}")
        lines.append(
            "[hint] 修法：把 requirements-omr.txt 的 scikit-learn/scipy/scikit-image "
            "钉回与 oemer 0.1.8 pickle 兼容的版本（见 #171），或显式评估后更新豁免清单"
        )
        return False, lines, EXIT_FAIL

    lines.append(f"[PASS] {len(MODEL_NAMES)} 个 oemer sklearn 模型加载 + 推理通过，"
                 "无未豁免的版本类告警")
    return True, lines, EXIT_OK


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="smoke_omr_model_load.py",
        description="OMR 栈模型加载/版本告警冒烟断言（失败即非零退出）",
    )
    ap.add_argument("--strict", action="store_true",
                    help="忽略豁免清单：只要出现版本类告警就失败")
    args = ap.parse_args(argv)

    ok, lines, code = run(strict=args.strict)
    for ln in lines:
        print(ln)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
