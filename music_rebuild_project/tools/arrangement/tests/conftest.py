"""测试夹具：把 `tools/` 与仓库根加入 `sys.path`。

本包测试既要用 `arrangement.chords`（`tools/` 在 path 上），
也要在 parity 用例里 import `midi.bass`（同样在 `tools/` 下），
还要能 `-m music_rebuild_project.tools.arrangement.chords` 跑 CLI。
"""

from __future__ import annotations

import sys
from pathlib import Path

_TOOLS_DIR = Path(__file__).resolve().parents[2]      # music_rebuild_project/tools
_REPO_ROOT = _TOOLS_DIR.parents[1]                    # 仓库根

for _p in (str(_TOOLS_DIR), str(_REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)
