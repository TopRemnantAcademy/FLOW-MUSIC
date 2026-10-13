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
    """上传字节流并返回公开访问 URL。

    `ContentType` 必须如实填写（音频写 audio/*）：CDN 边缘按上传时的 ContentType
    缓存响应头，写错会让 <audio> 直接拒绝播放，且错误响应头会被缓存很久。
    """
    if not is_configured():
        raise RuntimeError("对象存储未配置")
    _client().put_object(
        Bucket=settings.storage_bucket,
        Key=key,
        Body=data,
        ContentType=content_type,
    )
    return _public_url(key)


def upload_file(
    path: str,
    key: str,
    content_type: str = "application/octet-stream",
    storage_class: str | None = None,
) -> str:
    """上传本地文件到 storage_bucket，返回可访问 URL。

    `storage_class` 留空 = 不显式指定（用桶默认）。归档作业（scripts/archive_cdn.py）
    用它把老歌搬进低频档。
    """
    if not is_configured():
        raise RuntimeError("对象存储未配置")
    extra: dict = {"ContentType": content_type}
    if storage_class:
        extra["StorageClass"] = storage_class
    _client().upload_file(path, settings.storage_bucket, key, ExtraArgs=extra)
    return _public_url(key)


def upload(path: str, key: str) -> str:
    """兼容别名：上传本地文件（无显式 ContentType / 存储类别）。

    [GAP-019] 保留该名字是因为既有调用点（`_apply_postprocess` / `_make_preview`）
    传的是已定型的 mp3 中间产物；新代码请用 `upload_file(path, key, content_type=...,
    storage_class=...)`，别让「上传」这件事有两种缺省语义。
    """
    return upload_file(path, key, content_type="audio/mpeg")


def presigned_url(key: str, expires_in: int = 3600) -> str:
    """生成**一次性**（带签名、会过期）的下载链接。

    GAP-028 批量导出的产物是「我的全部作品音频」——它比单曲公开链接敏感得多，
    不能走 `_public_url`（那是给播放链路用的长期公开地址）。签名 URL 的授权
    写在 query 里（SigV4），过期即失效，故导出物不需要额外的鉴权网关。

    ⚠ 未配置存储时抛 RuntimeError（调用方负责降级），不返回空串：
    空串会被前端当成「链接打不开」，而真正的问题是「这条通道不可用」。
    """
    if not is_configured():
        raise RuntimeError("对象存储未配置")
    return _client().generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.storage_bucket, "Key": key},
        ExpiresIn=int(expires_in),
    )


def object_exists(key: str) -> bool:
    """对象是否已在桶里。未配置存储 / 无凭证 / 网络异常一律返回 False（不抛）。

    调用方（回填作业）拿到 False 只会「多上传一次」，是同键幂等覆盖；
    若在这里抛异常，运维脚本会因为一次瞬时 503 整批中断。
    """
    if not is_configured():
        return False
    try:
        _client().head_object(Bucket=settings.storage_bucket, Key=key)
        return True
    except Exception:
        return False


def set_storage_class(key: str, storage_class: str) -> None:
    """就地改写对象存储类别（归档迁移）。

    S3 兼容实现里改 StorageClass = 「用同一份数据重写一次对象」（原地 copy_Object），
    不是元数据打标：腾讯云 COS / R2 / MinIO 都支持，但**不可用 GLACIER 类**（见 cdn.py 头注）。
    """
    if not is_configured():
        raise RuntimeError("对象存储未配置")
    _client().copy_object(
        Bucket=settings.storage_bucket,
        Key=key,
        CopySource={"Bucket": settings.storage_bucket, "Key": key},
        StorageClass=storage_class,
        MetadataDirective="COPY",
    )


def delete(key: str) -> None:
    if not is_configured():
        return
    _client().delete_object(Bucket=settings.storage_bucket, Key=key)


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
