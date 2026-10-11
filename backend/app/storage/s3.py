"""对象存储（S3 兼容：腾讯云 COS / Cloudflare R2 / AWS S3 等）。

配置见 config.py 的 storage_* 项。未配置时 is_configured()=False，调用方降级处理
（如 sync-flow 直接返回 Suno 原始链接，仅本地验证用）。
香港服务器建议把音频回源到香港/全球 CDN 桶，保证 APK 稳定播放。
"""

from functools import lru_cache

import contextlib
import os
import tempfile
import time

from app.config import settings
from app.security.ssrf import ssrf_guard


def is_configured() -> bool:
    return bool(
        settings.storage_endpoint
        and settings.storage_bucket
        and settings.storage_access_key
        and settings.storage_secret_key
    )


@lru_cache(maxsize=1)
def _client():
    import boto3

    return boto3.client(
        "s3",
        endpoint_url=settings.storage_endpoint or None,
        region_name=settings.storage_region or None,
        aws_access_key_id=settings.storage_access_key,
        aws_secret_access_key=settings.storage_secret_key,
    )


def _public_url(key: str) -> str:
    base = (settings.storage_public_base or "").rstrip("/")
    if base:
        return f"{base}/{key}"
    return f"{settings.storage_endpoint}/{settings.storage_bucket}/{key}"


def upload_bytes(key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
    """上传字节流并返回公开访问 URL。"""
    if not is_configured():
        raise RuntimeError("对象存储未配置")
    _client().put_object(
        Bucket=settings.storage_bucket,
        Key=key,
        Body=data,
        ContentType=content_type,
    )
    return _public_url(key)


def delete(key: str) -> None:
    if not is_configured():
        return
    _client().delete_object(Bucket=settings.storage_bucket, Key=key)


def upload(path: str, key: str) -> str:
    """上传本地文件到 storage_bucket，返回可访问 URL。"""
    if not is_configured():
        raise RuntimeError("对象存储未配置")
    _client().upload_file(path, settings.storage_bucket, key)
    return _public_url(key)


def download_to_temp(
    url: str,
    *,
    timeout_total: float | None = None,
    max_bytes: int | None = None,
) -> str:
    """下载远程文件到临时文件，返回本地路径。

    [SEC] H4/H5(2026-09-13): 리다이렉트 대상까지 매 홉 재검증 + DNS-rebinding 차단.
    종전에는 ``guard(url)`` 1회 후 ``follow_redirects=True`` 로 넘겼기 때문에,
    공인 URL 이 302 → 169.254.169.254(클라우드 메타데이터) 로 튀면 가드를 그냥 통과했다.
    지금은 ``guarded_get_stream`` 이 홉마다 ``guard`` + IP 핀ning 을 수행한다.

    timeout_total / max_bytes: 둘 중 하나라도 주면「스트리밍 + 전체 상한」경로로 동작한다.
      ⚠ httpx 의 timeout 은 "읽기 간 유휴 시간" 기준이라, 천천히 but 꾸준히 흐르는
        업스트림에는 걸리지 않아 요청이 무한정 붙잡힐 수 있다. 사용자 대면 경로(
        GET /api/songs/{id}/preview) 는 반드시 전체 상한을 함께 건다.
      · timeout_total: 전체 경과 시간 상한(초)
      · max_bytes    : 누적 수신 바이트 상한
    """
    ssrf_guard.guard(url)
    suffix = os.path.splitext(url.split("?")[0])[1] or ".mp3"
    total_timeout = float(timeout_total) if timeout_total else None
    deadline = time.monotonic() + total_timeout if total_timeout else None
    limit = int(max_bytes) if max_bytes else None

    fd, path = tempfile.mkstemp(suffix=suffix)
    written = 0
    try:
        with os.fdopen(fd, "wb") as f:
            with ssrf_guard.guarded_get_stream(url, timeout=30, max_hops=5) as resp:
                resp.raise_for_status()
                for chunk in resp.iter_bytes(65536):
                    if deadline is not None and time.monotonic() > deadline:
                        raise TimeoutError(f"下载超时（超过 {total_timeout}s）: {url}")
                    written += len(chunk)
                    if limit is not None and written > limit:
                        raise ValueError(f"文件过大（超过 {limit} bytes）: {url}")
                    f.write(chunk)
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(path)
        raise
    return path
