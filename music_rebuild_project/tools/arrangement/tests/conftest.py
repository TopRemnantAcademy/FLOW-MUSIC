"""测试夹具：把 `tools/` 与仓库根加入 `sys.path`，并提供 #51 的 CLI 子进程夹具。

`tools/` 在 path 上才能 `from arrangement import chords/preconfirm`；仓库根在 path 上
才能 `from music_rebuild_project.tools.arrangement import sections`。CLI 用 `subprocess`
直接跑，退出码语义是交付的一部分（0 = 通过，1 = 校验失败，2 = 读不到/不是合法 JSON）。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

_TOOLS_DIR = Path(__file__).resolve().parents[2]      # music_rebuild_project/tools
REPO_ROOT = _TOOLS_DIR.parents[1]                     # 仓库根

for _p in (str(_TOOLS_DIR), str(REPO_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest

from music_rebuild_project.tools.arrangement import sections as S  # noqa: E402


@pytest.fixture
def write_spec(tmp_path):
    """把 spec dict 写成文件，返回路径。"""
    def _write(spec, name="sections.spec.json"):
        path = tmp_path / name
        path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
        return path

    return _write


@pytest.fixture
def run_cli(tmp_path):
    """跑 CLI，返回 `(returncode, stdout, stderr)`。"""
    def _run(*argv):
        env = dict(os.environ)
        env["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
        proc = subprocess.run(
            [sys.executable, "-m", "music_rebuild_project.tools.arrangement.sections", *argv],
            cwd=str(tmp_path),
            capture_output=True,
            text=True,
            env=env,
        )
        return proc.returncode, proc.stdout, proc.stderr

    return _run


#: 与 `tools/midi/tests/conftest.py` 的 `full_song` 同构：34 小节 / 68.0 秒的完整曲式。
FULL_SONG_SPEC = {
    "bpm": 120,
    "beats_per_bar": 4,
    "fps": 30,
    "duration_s": 68.0,
    "sections": [
        {"type": "Intro", "bars": 4},
        {"type": "Verse", "bars": 8, "label": "Verse 1"},
        {"type": "Pre-chorus", "bars": 4},
        {"type": "Chorus", "bars": 8, "label": "Chorus 1"},
        {"type": "Bridge", "bars": 4},
        {"type": "Outro", "bars": 6},
    ],
}


@pytest.fixture
def full_song_spec() -> dict:
    """完整曲式 spec（深拷贝一份，防止用例互相污染）。"""
    return json.loads(json.dumps(FULL_SONG_SPEC))
