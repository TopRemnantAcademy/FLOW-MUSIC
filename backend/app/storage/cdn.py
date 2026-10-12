"""CDN 对象键 / 存储类别：主播放链路（GAP-019）与回源任务的公共底座。

为什么单独一个模块而不是塞进 s3.py：
  · s3.py 只管「和桶说话」（put/get/delete + URL 拼装）；
  · 「哪首歌的对象键叫什么」「下载过来该是什么媒体类型」「这只桶该用哪个存储类别」
    是**业务约定**，sync-flow、播放链路按需回填、回填脚本、归档脚本四处都要用，
    各写一份必然漂移（旧路径 key 与新路径 key 不一致 = 同一首歌两份对象，回填脚本
    永远认为它没回填过）。

存储类别（归档/保留策略，见 docs/CDN_ARCHIVE_RUNBOOK.md）：
  · 默认档   settings.storage_class（留空 = 桶默认）
  · 归档档   settings.storage_class_archive（默认 STANDARD_IA 低频）
  ⚠ 归档档**不可**用 GLACIER / DEEP_ARCHIVE 类：它们不支持直接 GET，点播会 4xx/长时间
    解冻，等于把老歌变成「点了播不了」。需要深冷归档时走桶生命周期规则 + 回热流程，
    不要直接改单对象的 StorageClass（见 runbook「深冷归档」一节）。
"""

from __future__ import annotations

import os

from app.config import settings

# 扩展名 → 媒体类型。回源上传时 ContentType 写错会让 CDN 边缘缓存出错的响应头，
# 客户端 <audio> 可能直接拒绝播放，故按实际格式给足。
_MEDIA_TYPES: dict[str, str] = {
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".flac": "audio/flac",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".ogg": "audio/ogg",
}
_DEFAULT_MEDIA_TYPE = "audio/mpeg"


def media_type_for(url: str) -> str:
    """按 URL 扩展名推断音频媒体类型（未知扩展名按 mp3 处理）。"""
    ext = os.path.splitext((url or "").split("?")[0])[1].lower()
    return _MEDIA_TYPES.get(ext, _DEFAULT_MEDIA_TYPE)


def song_key(user_id: int | None, song_id: int, source_url: str = "") -> str:
    """曲目音频的对象键。**全链路唯一约定**（sync-flow / 按需回填 / 回填脚本共用）。

    键里不带原始扩展名而是按来源 URL 推断：同一个 song 的 mp3 只有一份对象，
    重复回源必须**落到同一个键**才幂等（换键 = 每次回填新增一份对象，桶无限膨胀）。
    """
    ext = os.path.splitext((source_url or "").split("?")[0])[1].lower()
    if ext not in _MEDIA_TYPES:
        ext = ".mp3"
    return f"songs/{user_id or 0}/{song_id}{ext}"


def default_storage_class() -> str:
    """常规（热）存储类别；空字符串 = 不显式指定，用桶默认。"""
    return (settings.storage_class or "").strip()


def archive_storage_class() -> str:
    """归档目标存储类别（低频）。"""
    return (settings.storage_class_archive or "").strip() or "STANDARD_IA"


def archive_after_days() -> int:
    """归档阈值（天）。<=0 = 关闭归档（脚本据此早退）。"""
    try:
        return int(settings.storage_archive_after_days)
    except (TypeError, ValueError):
        return 0
