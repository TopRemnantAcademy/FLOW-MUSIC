"""音频后处理管线（F1 响度 / F2 母带 / F3 降噪 / F4 分离）。

全部基于 GitHub 现成库，非自研算法：
- F1 响度归一化：    ffmpeg `loudnorm` 滤镜（系统 ffmpeg 二进制，已确证存在）
- F2 母带/效果链：   Spotify `pedalboard`（pip 已装，0.9.24）
- F3 降噪：          `noisereduce`（pip 已装）
- F4 人声/伴奏分离： Meta `demucs`（重型，需 torch+GPU，延迟导入，缺依赖清晰报错）

设计：管线接收「本地文件 -> 本地文件」，纯函数式、可单测。
主流程接入（songs._process_song）负责「下载 -> run_postprocess -> 上传」，默认关。
"""

from __future__ import annotations

import subprocess
import threading

from app.config import settings


class PostprocessError(RuntimeError):
    """后处理失败（含重型库缺失）。"""


# [SEC] 2026-09-13 审计（CWE-400 资源耗尽，纵深防御）：
# 实测 .env 的 STORAGE_BUCKET / STORAGE_ENDPOINT / STORAGE_ACCESS_KEY **均为空** → 对象存储未配置，
# 试听片段走本地回退：/preview 每遇一次缓存未命中就派生一个 ffmpeg 进程。
# 路由层限流（rate_limit_preview）是主控制；此处再加一道**全局并发上限**，
# 避免多用户叠加把本机 CPU 打满（媒体服务的标准资源护栏）。
_FFMPEG_MAX_CONCURRENT = 4
_ffmpeg_sem = threading.BoundedSemaphore(_FFMPEG_MAX_CONCURRENT)


def _ffmpeg_bin() -> str:
    return settings.audio_postprocess_ffmpeg_bin or "ffmpeg"


def _write(out_path: str, data, sr: int) -> None:
    """用 pedalboard.io.WriteableAudioFile 写文件（显式通道数，float32）。"""
    import numpy as np
    from pedalboard.io import WriteableAudioFile

    if data.dtype != np.float32:
        data = data.astype(np.float32)
    ch = data.shape[0] if data.ndim == 2 else 1
    with WriteableAudioFile(out_path, samplerate=sr, num_channels=ch) as w:
        w.write(data)


# —— F1 响度归一化（ffmpeg loudnorm，系统二进制）——
def loudness_normalize(input_path: str, output_path: str) -> None:
    """将音频统一到流媒体常见响度 (I=-14 LUFS)，音量一致、不忽大忽小。"""
    cmd = [
        _ffmpeg_bin(),
        "-y",
        "-i",
        input_path,
        "-af",
        "loudnorm=I=-14:TP=-1.5:LRA=11",
        "-ar",
        "44100",
        "-ac",
        "2",
        output_path,
    ]
    with _ffmpeg_sem:
        proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise PostprocessError(f"ffmpeg loudnorm 失败: {proc.stderr[-500:]}")


# —— 试听片段裁剪（프리미엄 게이트용：완곡은 그대로 두고 앞 N초만 잘라 별도 자산으로）——
def trim(input_path: str, output_path: str, seconds: int) -> None:
    """앞 `seconds` 초만 잘라 별도 파일로 저장（미구독자 프리뷰 자산 생성용）.

    -t 는 출력 길이 제한. 스트림 재인코딩 없이 자르기 위해 -c copy 를 쓰지 않는 이유는
    컨테이너 포맷(mp3) 에서 키프레임/헤더 정합성이 깨질 수 있어 안정성을 우선했기 때문.
    """
    sec = max(1, int(seconds))
    cmd = [
        _ffmpeg_bin(),
        "-y",
        "-i",
        input_path,
        "-t",
        str(sec),
        "-ar",
        "44100",
        "-ac",
        "2",
        output_path,
    ]
    with _ffmpeg_sem:
        proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise PostprocessError(f"ffmpeg trim 失败: {proc.stderr[-500:]}")


# —— F2 母带/效果链（pedalboard，读写为 pedalboard.io）——
def master(input_path: str, output_path: str) -> None:
    """用 pedalboard 加高通/低通 + 压缩 + 限制，提升成品专业度。"""
    from pedalboard import Pedalboard, Compressor, Limiter, HighpassFilter, LowpassFilter
    from pedalboard.io import AudioFile

    af = AudioFile(input_path)
    data = af.read(af.frames)
    sr = af.samplerate
    board = Pedalboard(
        [
            HighpassFilter(30.0),
            LowpassFilter(16000.0),
            Compressor(),
            Limiter(threshold_db=-1.5),
        ]
    )
    processed = board.process(data, sr)
    _write(output_path, processed, sr)


# —— F3 降噪（noisereduce）——
def denoise(input_path: str, output_path: str) -> None:
    """用 noisereduce 清掉底噪（频谱门）。"""
    import noisereduce as nr
    from pedalboard.io import AudioFile

    af = AudioFile(input_path)
    data = af.read(af.frames)
    sr = af.samplerate
    reduced = nr.reduce_noise(y=data, sr=sr)
    _write(output_path, reduced, sr)


# —— F4 人声/伴奏分离（重型，需 GPU；延迟导入，缺依赖清晰报错）——
def separate_stems(input_path: str, output_dir: str) -> dict[str, str]:
    """用 demucs 分离人声/伴奏。需 torch+GPU；缺失时清晰报错，不静默。"""
    try:
        from demucs.apply import apply_model  # noqa: F401
        from demucs.pretrained import get_model  # noqa: F401
    except ImportError as e:
        raise PostprocessError(
            "人声分离需安装 demucs + torch 且有 GPU：pip install demucs torch；"
            "当前未启用或环境不满足，原音频保持不变。"
        ) from e
    # 真实推理需在 GPU 环境运行 demucs CLI/API；此处仅占位入口（避免无 GPU 造桩）。
    raise PostprocessError("separate_stems 需 GPU 运行环境，当前骨架未执行真实推理")


_STEP_FUNCS = {
    "loudness": loudness_normalize,
    "mastering": master,
    "denoise": denoise,
    "separation": separate_stems,
}


def parse_steps(raw: str | None = None) -> list[str]:
    raw = raw if raw is not None else settings.audio_postprocess_steps
    return [s.strip() for s in raw.split(",") if s.strip()]


def run_postprocess(input_path: str, output_path: str, steps: list[str] | None = None) -> str:
    """按 steps 顺序对本地音频做后处理，返回最终输出路径。

    separation 步骤产出多 stem 到独立目录，不串接单文件管线。
    """
    steps = steps or parse_steps()
    current = input_path
    for idx, step in enumerate(steps):
        func = _STEP_FUNCS.get(step)
        if func is None:
            raise PostprocessError(f"未知后处理步骤: {step}")
        if step == "separation":
            sep_dir = output_path + ".stems"
            func(current, sep_dir)
            continue
        nxt = output_path if idx == len(steps) - 1 else f"{current}.{step}.wav"
        func(current, nxt)
        current = nxt
    return current
