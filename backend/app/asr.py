"""ASR（自动语音识别）客户端 —— GAP-016：把音频转写成文本，供 check_lyric 复核。

## 为什么必须有这一层
`moderation.moderate_audio` 原实现恒返回 `True`（占位），意味着
`enable_audio_moderation=True` 时**任何音频都自动通过**——审核形同虚设。
本模块提供真实的「音频 → 文本」能力，让音频审核退化成已验证的文本审核（check_lyric），
而不是再次造一个假通过的开关。

## 供应商与成本/时延（实证口径）
| provider | 端点 | 计价（公开定价，2026-10 核对） | 时延（1 分钟音频） |
|---|---|---|---|
| `openai`（whisper-1 / gpt-4o-transcribe） | `POST {base}/audio/transcriptions` | whisper-1 $0.006/分钟；gpt-4o-transcribe 约 $0.006/分钟（按音频时长计） | 网络往返，约 3~8s |
| `whisper`（自建 faster-whisper / whisper.cpp HTTP 服务） | `POST {base}/transcribe` | **零边际 API 费**；需 CPU/GPU + 内存常驻 | 1g1cpu 共置机跑 base 模型约 5~15x 实时（1 分钟 ≈ 4~12s） |
> ⚠ 上表为**公开定价/实测区间**，非本站实测账单；接入前须用自己的密钥跑一次真实音频核对。
> 本仓库不声称「已接入某云」，只提供可配置的 OpenAI 兼容 + 自建两档。

## 设计要点
- **未配置 = 不可用**：`asr_provider` 为空 → `transcribe()` 抛 ASRUnavailable。
  调用方（moderation.moderate_audio）据此 fail-closed，而不是静默放行。
- **不把音频 URL 直传**：先经 storage.download_to_temp 取回本地（带 SSRF 守卫），
  再以 multipart 上传。原因：伪造 Content-Type 的上游 URL 直传等于替供应商 SSRF。
- **同步实现**：调用方在 threadpool 里跑（见 songs.py 的 run_in_threadpool 约定），
  故本模块用同步 httpx，不引入 async 泄漏。
"""

from __future__ import annotations

import logging
import os

import httpx

from app.config import settings

log = logging.getLogger(__name__)

DEFAULT_OPENAI_BASE = "https://api.openai.com/v1"


class ASRError(Exception):
    """ASR 上游调用失败（网络/超时/非 2xx/响应结构异常）。"""


class ASRUnavailable(ASRError):
    """ASR 未配置或依赖缺失 —— 调用方必须 fail-closed（不得视为「无违规」）。"""


def provider_name() -> str:
    return (settings.asr_provider or "").strip().lower()


def is_configured() -> bool:
    """是否具备可用的 ASR 供应商（用于健康检查/审核状态暴露）。"""
    return provider_name() in ("whisper", "openai") and bool(
        settings.asr_api_base or provider_name() == "openai"
    )


def describe_state() -> dict:
    """运营可见性：ASR 配置状态（不含密钥）。"""
    return {
        "provider": provider_name() or None,
        "configured": is_configured(),
        "api_base_set": bool(settings.asr_api_base),
        "language": settings.asr_language,
    }


def _openai_url() -> str:
    base = (settings.asr_api_base or "").strip().rstrip("/") or DEFAULT_OPENAI_BASE
    return f"{base}/audio/transcriptions"


def _whisper_url() -> str:
    base = (settings.asr_api_base or "").strip().rstrip("/")
    if not base:
        raise ASRUnavailable("asr_api_base 未配置（自建 whisper 服务地址必填）")
    return f"{base}/transcribe"


def _transcribe_openai(local_path: str, filename: str) -> str:
    """OpenAI 兼容 /audio/transcriptions（multipart, response_format=text）。"""
    if not settings.asr_api_key:
        raise ASRUnavailable("asr_api_key 未配置（openai 兼容端点必填）")
    headers = {"Authorization": f"Bearer {settings.asr_api_key}"}
    data = {"model": settings.asr_model, "response_format": "text"}
    if settings.asr_language:
        data["language"] = settings.asr_language
    try:
        with open(local_path, "rb") as f:
            files = {"file": (filename, f, "application/octet-stream")}
            resp = httpx.post(
                _openai_url(),
                headers=headers,
                data=data,
                files=files,
                timeout=settings.asr_timeout_seconds,
            )
        resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        raise ASRError(f"ASR HTTP {e.response.status_code}: {e.response.text[:200]}") from e
    except (httpx.HTTPError, OSError) as e:
        raise ASRError(f"ASR 调用失败: {e}") from e
    # response_format=text → 纯文本裸体；网关若无视 format 返回 JSON 则兼容取 text 字段
    text = resp.text or ""
    stripped = text.strip()
    if stripped.startswith("{"):
        try:
            import json

            stripped = (json.loads(stripped).get("text") or "").strip()
        except Exception:  # noqa: BLE001 — 非 JSON 就当纯文本
            pass
    return stripped


def _transcribe_whisper(local_path: str, filename: str) -> str:
    """自建 whisper HTTP 服务：POST /transcribe multipart → {text: ...} 或裸文本。"""
    headers = {}
    if settings.asr_api_key:
        headers["Authorization"] = f"Bearer {settings.asr_api_key}"
    data = {}
    if settings.asr_language:
        data["language"] = settings.asr_language
    try:
        with open(local_path, "rb") as f:
            files = {"file": (filename, f, "audio/mpeg")}
            resp = httpx.post(
                _whisper_url(),
                headers=headers,
                data=data,
                files=files,
                timeout=settings.asr_timeout_seconds,
            )
        resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        raise ASRError(f"ASR HTTP {e.response.status_code}: {e.response.text[:200]}") from e
    except (httpx.HTTPError, OSError) as e:
        raise ASRError(f"ASR 调用失败: {e}") from e
    body = resp.text or ""
    stripped = body.strip()
    if stripped.startswith("{"):
        try:
            import json

            obj = json.loads(stripped)
            if isinstance(obj, dict):
                # 兼容 {text} / {transcription} / {segments:[{text}]}
                if obj.get("text"):
                    return str(obj["text"]).strip()
                if obj.get("transcription"):
                    return str(obj["transcription"]).strip()
                segs = obj.get("segments")
                if isinstance(segs, list):
                    return " ".join(str(s.get("text", "")) for s in segs).strip()
        except Exception:  # noqa: BLE001
            pass
    return stripped


def transcribe(audio_url: str) -> str:
    """音频 URL → 转写文本。失败一律抛 ASRError 子类（**绝不返回空串冒充成功**）。

    ⚠ 空转写（音频无人声、静音）是**合法结果**，返回空串；调用方 check_lyric("")→True。
       与「调用失败」必须区分：失败抛异常，静音返回空串。
    """
    provider = provider_name()
    if provider not in ("whisper", "openai"):
        raise ASRUnavailable(
            f"ASR 未配置或 provider 不支持（asr_provider={provider!r}）；"
            "请在 .env 配置 asr_provider=whisper|openai"
        )

    # 先落地本地（SSRF 守卫 + 大小上限），再上传。
    from app.storage.s3 import download_to_temp

    local_path = None
    try:
        local_path = download_to_temp(
            audio_url,
            timeout_total=settings.asr_timeout_seconds,
            max_bytes=settings.asr_max_bytes,
        )
        filename = os.path.basename(local_path) or "audio.mp3"
        if provider == "openai":
            return _transcribe_openai(local_path, filename)
        return _transcribe_whisper(local_path, filename)
    finally:
        if local_path:
            try:
                os.unlink(local_path)
            except OSError:
                pass
