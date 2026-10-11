"""音频后处理管线测试：F1/F2/F3 真实跑通（需 pedalboard/noisereduce），
F4 分离在无 GPU/缺依赖时清晰报错（不造桩）。

pedalboard / noisereduce 为重型可选依赖（requirements.txt 标注「按需启用」），
在 mock 上线门禁中不应成为硬阻塞：依赖缺失时对应用例自动 skip，装好后自动运行。
"""

import math
import os
import struct
import wave

from app.postprocess import (
    PostprocessError,
    denoise,
    loudness_normalize,
    master,
    parse_steps,
    run_postprocess,
    separate_stems,
)

import pytest

# 重型音频依赖为可选：依赖缺失时自动 skip，装好后自动运行。
_HAS_PEDALBOARD = False
try:
    import pedalboard  # noqa: F401

    _HAS_PEDALBOARD = True
except Exception:
    pass

_HAS_NOISEREDUCE = False
try:
    import noisereduce  # noqa: F401

    _HAS_NOISEREDUCE = True
except Exception:
    pass

_SKIP_AUDIO = "可选重型音频依赖未安装（pedalboard/noisereduce），mock 环境跳过；安装后自动运行"


def _make_wav(path: str, freq: int = 440, dur: float = 2, sr: int = 44100, channels: int = 2):
    with wave.open(path, "w") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(sr)
        n = int(sr * dur)
        frames = bytearray()
        for i in range(n):
            val = int(32767 * 0.3 * math.sin(2 * math.pi * freq * i / sr))
            frames += struct.pack("<h", val) * channels
        w.writeframes(bytes(frames))


def _size(path: str) -> int:
    return os.path.getsize(path)


def test_loudness_normalize(tmp_path):
    src = tmp_path / "in.wav"
    _make_wav(str(src))
    out = tmp_path / "out.wav"
    loudness_normalize(str(src), str(out))
    assert out.exists() and _size(str(out)) > 0


@pytest.mark.skipif(not _HAS_PEDALBOARD, reason=_SKIP_AUDIO)
def test_master(tmp_path):
    src = tmp_path / "in.wav"
    _make_wav(str(src))
    out = tmp_path / "out.wav"
    master(str(src), str(out))
    assert out.exists() and _size(str(out)) > 0


@pytest.mark.skipif(not _HAS_NOISEREDUCE, reason=_SKIP_AUDIO)
def test_denoise(tmp_path):
    src = tmp_path / "in.wav"
    _make_wav(str(src))
    out = tmp_path / "out.wav"
    denoise(str(src), str(out))
    assert out.exists() and _size(str(out)) > 0


@pytest.mark.skipif(not (_HAS_PEDALBOARD and _HAS_NOISEREDUCE), reason=_SKIP_AUDIO)
def test_run_pipeline(tmp_path):
    src = tmp_path / "in.wav"
    _make_wav(str(src))
    out = tmp_path / "out.wav"
    final = run_postprocess(str(src), str(out), steps=["loudness", "mastering", "denoise"])
    assert os.path.exists(final) and _size(final) > 0


def test_separation_missing_dep(tmp_path):
    with pytest.raises(PostprocessError):
        separate_stems(str(tmp_path / "x.wav"), str(tmp_path / "stems"))


def test_parse_steps():
    assert parse_steps("loudness, mastering , denoise") == [
        "loudness",
        "mastering",
        "denoise",
    ]
