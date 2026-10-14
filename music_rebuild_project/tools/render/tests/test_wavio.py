"""`wavio`：16/24bit 写读往返、时长、限幅、混音等长。"""

from __future__ import annotations

import math
import os
import struct

import pytest

from music_rebuild_project.tools.render import wavio as W


# --- 写读往返 ---------------------------------------------------------------
@pytest.mark.parametrize("sampwidth,bits", [(2, 16), (3, 24)])
def test_roundtrip_preserves_spec_and_length(tmp_path, sampwidth, bits):
    from .conftest import tone

    p = tmp_path / f"rt{bits}.wav"
    W.write_wav(p, tone(seconds=0.5, amplitude=0.5), framerate=44100, sampwidth=sampwidth)
    info = W.read_wav(p)

    assert (info["framerate"], info["bit_depth"], info["channels"]) == (44100, bits, 1)
    assert info["frames"] == 22050
    assert info["duration"] == pytest.approx(0.5, abs=1e-9)


def test_24bit_quantization_error_within_one_lsb(tmp_path):
    """24bit 的量化误差必须 < 1 LSB —— 这是「24bit 真的写对了」的唯一硬证据。

    若打包字节序错了，误差会是满量程量级，这条立刻红。
    """
    from .conftest import tone

    sig = tone(seconds=0.2, amplitude=0.7)
    p = tmp_path / "q24.wav"
    W.write_wav(p, sig, sampwidth=3)
    got = W.read_wav(p)["samples"]

    err = max(abs(a - b) for a, b in zip(sig, got))
    assert err < 1.0 / 8388607.0 * 1.5, f"24bit 误差 {err} 过大，疑似位序错误"


def test_16bit_quantization_error_within_one_lsb(tmp_path):
    from .conftest import tone

    sig = tone(seconds=0.2, amplitude=0.7)
    p = tmp_path / "q16.wav"
    W.write_wav(p, sig, sampwidth=2)
    got = W.read_wav(p)["samples"]
    assert max(abs(a - b) for a, b in zip(sig, got)) < 1.0 / 32767.0 * 1.5


def test_24bit_bytes_are_little_endian_3byte(tmp_path):
    """直接验字节布局：样本 +1.0 应写成 FF FF 7F（小端 24bit 补码）。"""
    p = tmp_path / "one.wav"
    W.write_wav(p, [1.0], sampwidth=3)
    with open(p, "rb") as f:
        blob = f.read()
    assert blob.endswith(b"\xff\xff\x7f")
    assert (len(blob) - 44) % 3 == 0 or blob.count(b"data") == 1
    with open(p, "rb") as f:
        raw = f.read()
    idx = raw.index(b"data")
    payload = raw[idx + 8 :]
    assert payload == b"\xff\xff\x7f"


def test_negative_sample_two_complement(tmp_path):
    """-1.0 → 24bit 补码。满量程取对称值 ±8388607（不是 -8388608），
    所以 -1.0 的字节是 01 00 80，而不是理论最负的 00 00 80 ——
    这条断言就是在钉死「我们用的是对称标度」这个选择。
    """
    p = tmp_path / "neg.wav"
    W.write_wav(p, [-1.0], sampwidth=3)
    raw = p.read_bytes()
    payload = raw[raw.index(b"data") + 8 :]
    assert payload == b"\x01\x00\x80"
    assert -1.0 == pytest.approx(-8388607 / 8388607.0)


def test_write_rejects_unsupported_bit_depth(tmp_path):
    with pytest.raises(W.WavError, match="16bit/24bit"):
        W.write_wav(tmp_path / "x.wav", [0.1, 0.2], sampwidth=4)


def test_write_rejects_empty_samples(tmp_path):
    with pytest.raises(W.WavError, match="0 帧"):
        W.write_wav(tmp_path / "x.wav", [], sampwidth=3)


def test_write_clips_instead_of_wrapping(tmp_path):
    """越界值必须被夹住，不能回绕成噪声（否则削波会被伪装成正常波形）。"""
    p = tmp_path / "clip.wav"
    W.write_wav(p, [3.0, -3.0], sampwidth=3)
    got = list(W.read_wav(p)["samples"])
    assert got == [pytest.approx(1.0), pytest.approx(-1.0)]


# --- 时长 / 规格 ------------------------------------------------------------
def test_duration_of_matches_frames_over_framerate(four_stems):
    p = four_stems["drums"]
    assert W.duration_of(p) == pytest.approx(1.0, abs=1e-9)
    assert W.wave_spec(p) == (44100, 3, 1)


def test_duration_of_ignores_trailing_garbage(tmp_path):
    """data 块之后追加垃圾（如 id3），时长**不应**变 —— 证明不靠文件大小推。"""
    from .conftest import tone

    p = tmp_path / "g.wav"
    W.write_wav(p, tone(seconds=0.25), sampwidth=3)
    before = W.duration_of(p)
    with open(p, "ab") as f:
        f.write(b"\x00" * 4096)
    assert W.duration_of(p) == before == pytest.approx(0.25, abs=1e-9)


def test_read_wav_raises_on_missing_file(tmp_path):
    with pytest.raises(W.MissingFileError):
        W.read_wav(tmp_path / "nope.wav")


def test_read_wav_raises_on_non_wav(tmp_path):
    p = tmp_path / "fake.wav"
    p.write_bytes(b"not a wave file at all")
    with pytest.raises(W.InvalidWavError):
        W.read_wav(p)


def test_read_wav_expect_spec_mismatch(tmp_path):
    from .conftest import tone

    p = tmp_path / "16.wav"
    W.write_wav(p, tone(seconds=0.1), sampwidth=2)
    with pytest.raises(W.SpecMismatchError, match="24bit"):
        W.read_wav(p, expect_spec=W.Spec(44100, 3, 1))
    W.read_wav(p, expect_spec=W.Spec(44100, 2, 1))  # 对上了不抛


def test_spec_str_is_human_readable():
    assert str(W.Spec(44100, 3, 1)) == "44100Hz/24bit/1ch"
    assert W.Spec(44100, 3, 1) == W.Spec(44100, 3, 1)
    assert W.Spec(44100, 3, 1) != W.Spec(48000, 3, 1)


# --- 混音 -------------------------------------------------------------------
def test_mix_down_sums_samplewise_and_reports_peak():
    out, peak = W.mix_down([[0.1] * 4, [0.2] * 4, [-0.05] * 4])
    assert len(out) == 4
    assert out[0] == pytest.approx(0.25)
    assert peak == pytest.approx(0.25)


def test_mix_down_rejects_unequal_lengths():
    """G4-9 的算术版：长度不等直接报错，**不补齐**。"""
    with pytest.raises(W.WavError, match="时长不一致"):
        W.mix_down([[0.1] * 10, [0.1] * 9])


def test_mix_down_rejects_empty():
    with pytest.raises(W.WavError, match="没有可混的轨"):
        W.mix_down([])


def test_mix_down_headroom_scales_result():
    out, peak = W.mix_down([[0.5] * 3, [0.5] * 3], headroom=0.5)
    assert out[0] == pytest.approx(0.5)
    assert peak == pytest.approx(0.5)


def test_mix_down_peak_reflects_preclip_overflow():
    """三轨各 0.5 相加 = 1.5：peak 必须如实报 1.5（调用方才有机会降增益）。"""
    _, peak = W.mix_down([[0.5] * 3] * 3)
    assert peak == pytest.approx(1.5)


def test_dbfs():
    assert W.dbfs(1.0) == pytest.approx(0.0)
    assert W.dbfs(0.5) == pytest.approx(-6.0206, abs=1e-3)
    assert W.dbfs(0.0) == float("-inf")


def test_stereo_write_and_read(tmp_path):
    p = tmp_path / "st.wav"
    W.write_wav(p, [(0.5, -0.5), (0.25, -0.25)], sampwidth=3)
    info = W.read_wav(p)
    assert info["channels"] == 2
    assert info["frames"] == 2
    assert list(info["samples"]) == [pytest.approx(0.5), pytest.approx(-0.5),
                                     pytest.approx(0.25), pytest.approx(-0.25)]
