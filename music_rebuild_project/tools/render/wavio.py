"""WAV 读写 / 混音（纯 stdlib：`wave` + `array` + `struct`）。

落点背景：#59「输出格式（master WAV/MP3 + 分轨 stem + manifest）」，
即管道 F 段末尾的打包与校验；G4-10 的规格是 **44.1k / 24bit WAV**，
G4-9 要求各 stem「可重开、可同播、时长一致」。

本模块**不依赖** numpy / scipy / pydub / soundfile，也不用 ffmpeg：
`wave` 原生支持 8/16/32bit，24bit 要自己做位打包（见 `_encode`），
这就是本文件存在的理由 —— 它把「24bit 无库可写」这件事变成几十行确定性代码。

约定（与 `08_qc/qc_file_check.py`(#60) 的口径保持一致，避免两处算法打架）：

- 采样为**归一化 float**，值域 [-1.0, 1.0]。读盘时按位深换算，写盘时再换回整数。
- 单文件只测 16bit / 24bit（G4-10 涉及的两档）；其余位深读得懂就用，写只允许这两档。
- 时长一律 `frames / framerate`，**不信任** WAV 头里的 data 块声明（见 `read_wav` 注释）。

失败一律抛异常（`WavError` 家族），不返回 None、不静默降级：
这是打包工具，静默通过等于把坏产物送去分发。
"""

from __future__ import annotations

import array
import os
import struct
import wave

#: G4-10 的硬规格：44.1kHz。
SAMPLE_RATE = 44100
#: G4-10 的硬规格：24bit 母带（MP3 由外部编码器转，见 `mp3.py`）。
BIT_DEPTH = 24
#: 允许读入的位深（写只允许 16/24 —— gate_G4 只认这两档）。
READABLE_WIDTHS = (1, 2, 3, 4)
#: 允许写入的位深。gate_G4 的输出规格是 24bit；16bit 留给 mock/中间产物。
WRITABLE_WIDTHS = (2, 3)

#: 每种位深对应的满量程（正）与偏移（8bit 是无符号，中心 128）。
_FULL_SCALE = {1: 128, 2: 32768, 3: 8388608, 4: 2147483648}
_BIAS = {1: 128}


class Spec:
    """一份输出规格：采样率 / 字节宽（位深/8）/ 声道数。

    G4-10 说的「44.1k / 24bit」就是 `Spec(44100, 3, 1)`。
    单独成类而不写成元组，是因为它要出现在错误信息里 —— `str(Spec(...))`
    要说人话（"44100Hz/24bit/1ch"），而不是 `(44100, 3, 1)`。
    """

    __slots__ = ("framerate", "sampwidth", "channels")

    def __init__(self, framerate, sampwidth, channels):
        self.framerate = int(framerate)
        self.sampwidth = int(sampwidth)
        self.channels = int(channels)

    @property
    def bit_depth(self):
        return self.sampwidth * 8

    def matches(self, info):
        return (
            int(info.get("framerate", 0)) == self.framerate
            and int(info.get("sampwidth", 0)) == self.sampwidth
            and int(info.get("channels", 0)) == self.channels
        )

    def __str__(self):
        return f"{self.framerate}Hz/{self.bit_depth}bit/{self.channels}ch"

    def __repr__(self):
        return f"Spec({self.framerate}, {self.sampwidth}, {self.channels})"

    def __eq__(self, other):
        if not isinstance(other, Spec):
            return NotImplemented
        return (
            self.framerate == other.framerate
            and self.sampwidth == other.sampwidth
            and self.channels == other.channels
        )


class WavError(Exception):
    """本模块所有失败的基类 —— 便于调用方一把捞。"""


class MissingFileError(WavError):
    """文件不存在。**不静默跳过**：打包环节缺文件就是缺陷。"""


class InvalidWavError(WavError):
    """文件不是合法 WAV，或位深不在支持范围。"""


class SpecMismatchError(WavError):
    """采样率/位深/声道与要求的输出规格不符（对应 G4-10）。"""


# ---------------------------------------------------------------------------
# 读
# ---------------------------------------------------------------------------
def read_wav(path, *, expect_spec=None):
    """读 WAV 为 `{framerate, sampwidth, channels, frames, duration, samples}`。

    `samples` 是 `array('d')`，值域 [-1.0, 1.0]（归一化，便于混音与算峰值）。

    - 缺文件 → `MissingFileError`（不返回空结果）。
    - 位深不在 `READABLE_WIDTHS` → `InvalidWavError`。
    - 传了 `expect_spec`（如 `Spec(44100, 3, 2)`）且不匹配 → `SpecMismatchError`。

    时长用 `getnframes()` 算，不用 `os.path.getsize()` 推：
    后者在有 `LIST`/`id3` 等额外 chunk 或截断文件上会给出偏大的时长，
    而 G4-5 是「误差 ≤ ±1 秒」的硬门，不能喂给它一个估算值。
    """
    _require_nonempty(path)
    try:
        with wave.open(str(path), "rb") as w:
            channels = w.getnchannels()
            sampwidth = w.getsampwidth()
            framerate = w.getframerate()
            frames = w.getnframes()
            raw = w.readframes(frames)
    except (wave.Error, EOFError) as exc:
        # 空文件/半截文件会让 `wave` 抛裸 `EOFError`（它不继承 wave.Error），
        # 如果放它出去，调用方只会看到 "EOFError" 而不知道是哪个文件坏了。
        raise InvalidWavError(f"不是合法 WAV：{path}（{type(exc).__name__}: {exc}）") from exc

    if sampwidth not in READABLE_WIDTHS:
        raise InvalidWavError(f"不支持位深 {sampwidth * 8}bit：{path}")

    samples = _decode(raw, sampwidth, channels)
    info = {
        "path": str(path),
        "framerate": framerate,
        "sampwidth": sampwidth,
        "bit_depth": sampwidth * 8,
        "channels": channels,
        "frames": frames,
        "duration": frames / float(framerate) if framerate else 0.0,
        "samples": samples,
    }
    if expect_spec is not None and not expect_spec.matches(info):
        raise SpecMismatchError(
            f"规格不符：{path} 实为 {framerate}Hz/{sampwidth * 8}bit/{channels}ch，"
            f"期望 {expect_spec}"
        )
    return info


def duration_of(path):
    """只读头取时长（秒）。不加载 PCM —— 给 G4-5/G4-9 这类「只问多长」的检查用。"""
    _require_nonempty(path)
    try:
        with wave.open(str(path), "rb") as w:
            frames = w.getnframes()
            framerate = w.getframerate()
    except (wave.Error, EOFError) as exc:
        raise InvalidWavError(f"不是合法 WAV：{path}（{type(exc).__name__}: {exc}）") from exc
    if not framerate:
        raise InvalidWavError(f"采样率为 0，头损坏：{path}")
    return frames / float(framerate)


def wave_spec(path):
    """返回 `(framerate, sampwidth, channels)`。G4-10 的编码参数核对用它。"""
    _require_nonempty(path)
    try:
        with wave.open(str(path), "rb") as w:
            return w.getframerate(), w.getsampwidth(), w.getnchannels()
    except (wave.Error, EOFError) as exc:
        raise InvalidWavError(f"不是合法 WAV：{path}（{type(exc).__name__}: {exc}）") from exc


# ---------------------------------------------------------------------------
# 写
# ---------------------------------------------------------------------------
def write_wav(path, samples, *, framerate=SAMPLE_RATE, sampwidth=3, channels=None):
    """写 WAV。`samples` 为归一化 float 序列（或 `(L, R)` 立体声元组序列）。

    - `sampwidth` 只允许 2（16bit）/ 3（24bit），其余抛 `WavError`。
    - 写前**显式限幅**到 [-1.0, 1.0]：否则 `struct.pack` 会静默回绕成噪声，
      把「削波」这种真缺陷伪装成正常波形。
    - 返回 path，方便链式调用。

    24bit 的打包见 `_encode`：`struct.pack('<i', v)` 后取低 3 字节，
    按小端顺序写入 —— 这是 WAV 对 24bit 唯一的合法布局。
    """
    if sampwidth not in WRITABLE_WIDTHS:
        raise WavError(
            f"只支持写 16bit/24bit，收到 {sampwidth * 8}bit"
            f"（gate_G4 输出规格为 44.1k/24bit）"
        )
    if not samples:
        raise WavError("samples 为空：拒绝写 0 帧 WAV（时长会算成 0，下游必炸）")

    stereo = channels is None and isinstance(samples[0], (tuple, list))
    nch = channels if channels is not None else (2 if stereo else 1)
    if nch not in (1, 2):
        raise WavError(f"只支持 1/2 声道，收到 {nch}")

    flat = []
    if nch == 2:
        for frame in samples:
            left, right = (frame if isinstance(frame, (tuple, list)) else (frame, frame))
            flat.append(_clip(left))
            flat.append(_clip(right))
    else:
        for s in samples:
            flat.append(_clip(s[0] if isinstance(s, (tuple, list)) else s))

    payload = _encode(flat, sampwidth)

    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(nch)
        w.setsampwidth(sampwidth)
        w.setframerate(framerate)
        w.writeframes(payload)
    return str(path)


def mix_down(tracks, *, headroom=1.0):
    """把多轨**等长** PCM 相加（`tracks` = 归一化 float 序列的可迭代）。

    这是 G4-9「各 stem 可同播」的算术版本：同播 = 逐采样相加。

    - 长度必须完全一致，不一致直接 `WavError`（**不补齐、不截断** ——
      静默补齐会让「时长不一致」的缺陷蒙混过关，而那正是 G4-9 要拦的）。
    - 结果按 `headroom` 缩放（默认 1.0 = 只做限幅；DAW 真混音会有 EQ/压缩，
      本函数刻意保持线性，别把它当混音器）。
    - 混音后**不削波的保证**由调用方负责：`mix_down` 只报峰值，不改策略。

    返回 `(samples, peak)`：peak 是混音后（限幅前）的绝对值峰值，供调用方
    判断「是不是加爆了」并决定要不要降增益重来。
    """
    tracks = [list(t) for t in tracks]
    if not tracks:
        raise WavError("tracks 为空：没有可混的轨")
    length = len(tracks[0])
    for i, t in enumerate(tracks):
        if len(t) != length:
            raise WavError(
                f"stem 时长不一致，无法混音：第 0 轨 {length} 帧，第 {i} 轨 {len(t)} 帧"
                f"（G4-9 要求各 stem 时长一致）"
            )

    out = [0.0] * length
    for t in tracks:
        for j, s in enumerate(t):
            out[j] += s
    if headroom != 1.0:
        out = [s * headroom for s in out]

    peak = max((abs(s) for s in out), default=0.0)
    return out, peak


def peak_of(samples):
    """归一化峰值（0.0 ~ ∞，混音后可 >1.0）。"""
    return max((abs(s) for s in samples), default=0.0)


def dbfs(amplitude):
    """线性幅度 → dBFS。0 幅值返回 -inf（JSON 里写成 None 更清楚，看调用方）。"""
    import math

    if amplitude <= 0:
        return float("-inf")
    return 20.0 * math.log10(amplitude)


# ---------------------------------------------------------------------------
# 内部：位深换算
# ---------------------------------------------------------------------------
def _clip(value):
    """限幅到 [-1.0, 1.0]。写盘前的最后一道闸。"""
    if value > 1.0:
        return 1.0
    if value < -1.0:
        return -1.0
    return float(value)


def _require_nonempty(path):
    """存在 + 非空。0 字节文件是打包事故的典型现场，给它一句人话。"""
    if not os.path.isfile(path):
        raise MissingFileError(f"WAV 不存在：{path}")
    if os.path.getsize(path) == 0:
        raise InvalidWavError(f"0 字节空文件：{path}")
    return True


def _truncate(raw, width):
    """把字节流截成 width 的整数倍 —— `array.frombytes` 拒绝半截样本。"""
    extra = len(raw) % width
    return raw[: len(raw) - extra] if extra else raw


def _decode(raw, sampwidth, channels):
    """字节 → 归一化 float 序列（保留声道交织顺序）。"""
    fs = _FULL_SCALE[sampwidth]
    bias = _BIAS.get(sampwidth, 0)

    if sampwidth == 1:
        ints = array.array("B")
        ints.frombytes(_truncate(raw, 1))
        return array.array("d", ((v - bias) / fs for v in ints))

    if sampwidth == 2:
        ints = array.array("h")
        ints.frombytes(_truncate(raw, 2))
        return array.array("d", (v / fs for v in ints))

    if sampwidth == 4:
        ints = array.array("i")
        ints.frombytes(_truncate(raw, 4))
        return array.array("d", (v / fs for v in ints))

    # 24bit 无原生容器：手拆 3 字节 → 有符号 32bit（符号扩展靠补足高位）。
    n = len(raw) // 3
    out = array.array("d", [0.0]) * n
    for i in range(n):
        b0, b1, b2 = raw[i * 3], raw[i * 3 + 1], raw[i * 3 + 2]
        v = b0 | (b1 << 8) | (b2 << 16)
        if v & 0x800000:  # 24bit 补码负数 → 减 2^24
            v -= 1 << 24
        out[i] = v / fs
    return out


def _encode(floats, sampwidth):
    """归一化 float → 字节。24bit 路径是本函数的重点。"""
    if sampwidth == 2:
        ints = array.array("h", (int(round(v * 32767.0)) for v in floats))
        ints = array.array("h", (max(-32768, min(32767, v)) for v in ints))
        if struct.pack("<h", 1) != b"\x01\x00":  # 大端机器兜底
            ints.byteswap()
        return ints.tobytes()

    if sampwidth == 3:
        buf = bytearray()
        for v in floats:
            i = int(round(v * 8388607.0))
            i = max(-8388608, min(8388607, i))
            if i < 0:
                i += 1 << 24  # 补码
            buf += bytes((i & 0xFF, (i >> 8) & 0xFF, (i >> 16) & 0xFF))
        return bytes(buf)

    raise WavError(f"不支持写 {sampwidth * 8}bit")
