"""Route B 供应商：Meta AudioCraft MusicGen 本地开源生成（F5）。

替代 Suno 商业 API，去限制、降本；需 torch + audiocraft + GPU。
延迟导入重型依赖，缺依赖/无 GPU 时清晰报错，不静默造桩。
切换：settings.music_provider = "musicgen"（默认不启用）。
"""

from __future__ import annotations

import asyncio

from app.orchestrator.base import GenerateRequest, MusicProvider, SongResult


class MusicGenProvider(MusicProvider):
    """本地 MusicGen 生成。create 提交（本地推理），get 返回完成态。"""

    def __init__(self) -> None:
        self._counter = 0

    def _next_id(self) -> str:
        self._counter += 1
        return f"musicgen-{self._counter}"

    async def create(self, req: GenerateRequest) -> SongResult:
        ext_id = self._next_id()
        # 本地同步推理（无 GPU 会在此抛清晰错误），用线程避免阻塞事件循环
        return await asyncio.to_thread(self._generate, req, ext_id)

    def _generate(self, req: GenerateRequest, ext_id: str) -> SongResult:
        try:
            import torch
            from audiocraft.models import MusicGen
            from audiocraft.data.audio import audio_write
        except ImportError as e:
            raise RuntimeError(
                "MusicGen 需安装 torch + audiocraft 且有 GPU："
                "pip install torch audiocraft；并设置 CUDA。当前未启用或环境不满足。"
            ) from e
        if not torch.cuda.is_available():
            raise RuntimeError("MusicGen 推理需要 CUDA GPU，当前不可用。")
        model = MusicGen.get_pretrained("facebook/musicgen-small")
        model.set_generation_params(duration=30)
        descriptions = [req.prompt or req.lyric or "instrumental music"]
        wavs = model.generate_descriptions(descriptions, progress=False)
        out_path = f"/tmp/musicgen_{ext_id}.wav"
        audio_write(out_path, wavs[0], model.sample_rate, strategy="loudness")
        # 本地 /tmp 路径前端/播放器无法直接访问：已配置对象存储则上传返回绝对 URL。
        # 未配置时保留本地路径（已知限制：MusicGen 需 GPU + 对象存储才能对外播放）。
        audio_url = out_path
        from app.storage import s3 as storage

        if storage.is_configured():
            try:
                with open(out_path, "rb") as f:
                    audio_url = storage.upload_bytes(
                        f"songs/musicgen/{ext_id}.wav", f.read(), content_type="audio/wav"
                    )
            except Exception:
                # 上传失败不阻断生成，保留本地路径
                pass
        return SongResult(
            external_id=ext_id,
            status="completed",
            audio_url=audio_url,
            duration=30,
            title=req.title or "MusicGen Song",
        )

    async def get(self, external_id: str) -> SongResult:
        # 本地同步生成，create 已返回 completed
        return SongResult(external_id=external_id, status="completed")
