"""`tests/wild_omr/` 的依赖小工具：缺包 → skip（而不是 error/fail）。

为什么不用 `pytest.importorskip`：它只把 `ImportError` 当成"没装"，
但本包要在**裸容器**里也能跑（不挂 PR/push，但本地 `pytest tests/wild_omr` 不该炸）。
上游包自身导入失败时 `find_spec` 会抛别的异常，importorskip 会把它升级成错误。
这里统一收口。
"""

from __future__ import annotations

import pytest


def require(mod: str, why: str) -> None:
    try:
        __import__(mod)
    except ImportError:
        pytest.skip(why)
