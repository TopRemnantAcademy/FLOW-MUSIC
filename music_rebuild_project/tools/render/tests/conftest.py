"""测试夹具：全部素材**现场合成**（stdlib），不读任何真实媒体、不碰 ffmpeg。

`sys.path` 兜底：pytest 从仓库根跑时 `music_rebuild_project.tools.render`
是可导入的（有 `__init__.py` 链），但从别的工作目录跑就不一定，
所以这里显式把仓库根塞进 `sys.path` —— 让「怎么跑都能跑」这件事不靠运气。
"""

from __future__ import annotations

import math
import os
import sys

import pytest

REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
)
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from music_rebuild_project.tools.render import stems as S  # noqa: E402
from music_rebuild_project.tools.render import wavio as W  # noqa: E402

SR = 44100


def tone(freq=440.0, seconds=1.0, amplitude=0.4, sr=SR, phase=0.0):
    """确定性正弦（无噪声、无 RNG）—— 同一参数永远同一字节。"""
    n = int(round(seconds * sr))
    return [
        amplitude * math.sin(2.0 * math.pi * freq * (i / sr) + phase) for i in range(n)
    ]


def write_tone(path, **kw):
    """写一条 24bit 单声道 WAV，返回 path。"""
    seconds = kw.pop("seconds", 1.0)
    sr = kw.pop("sr", SR)
    return W.write_wav(
        str(path),
        tone(seconds=seconds, sr=sr, **kw),
        framerate=sr,
        sampwidth=3,
    )


@pytest.fixture
def tone_factory(tmp_path):
    """`factory(name, **kw) -> path`，写在 tmp_path 下。"""
    def _make(name, **kw):
        return write_tone(tmp_path / name, **kw)

    return _make


@pytest.fixture
def four_stems(tmp_path, tone_factory):
    """G4-9 的四条合规 stem，各 1.0s。返回 `{name: path}`（顺序 = REQUIRED_STEMS）。"""
    freqs = {"drums": 110.0, "bass": 55.0, "harmony": 220.0, "vocal": 330.0}
    return {
        name: tone_factory(f"song01_{name}_v001.wav", freq=freqs[name], amplitude=0.25)
        for name in S.REQUIRED_STEMS
    }


@pytest.fixture
def stems_dir(tmp_path, four_stems):
    """把 `four_stems` 放进一个 `stems/` 目录，返回该目录。"""
    d = tmp_path / "stems"
    d.mkdir()
    for name, p in four_stems.items():
        os.replace(p, d / os.path.basename(p))
    return str(d)


def pytest_collection_modifyitems(config, items):
    """给全部用例打上 `render` 标记，便于 `-m render` 单独跑。"""
    for item in items:
        item.add_marker(pytest.mark.render)
