import sys
import os

# 确保 backend/ 在 sys.path 最前, 使 `import app` 解析到本项目的 backend/app 包
# (与 FLOW 主仓库的 backend.app 包名不同, 互不干预; [2026-09-10] 本仓库已迁入 FLOW/ai-music-platform).
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
