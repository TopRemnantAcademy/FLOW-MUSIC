"""测试夹具：把 `tools/` 与仓库根加入 `sys.path`（与 arrangement/tests/conftest 同口径）。

`tools/` 在 path 上才能 `from omr import classify_input`；仓库根在 path 上才能
`from music_rebuild_project.tools.omr import classify_input`。两种方式都用（见 test）。
"""

from __future__ import annotations

import sys
from pathlib import Path

_TOOLS_DIR = Path(__file__).resolve().parents[2]      # music_rebuild_project/tools
REPO_ROOT = _TOOLS_DIR.parents[1]                     # 仓库根

for _p in (str(_TOOLS_DIR), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)
