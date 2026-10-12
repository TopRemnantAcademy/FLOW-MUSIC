"""播放/下载链路上的 CDN 选择（GAP-019）。

**一句话规则**：只要这首歌的音频已经在自有桶/CDN 上（`audio_cdn_url` 非空），
所有「把音频地址交给客户端 / 交给下游拉取」的出口都优先用它，而不是上游
`audio_url`（上游链接 30 天过期、且是中国境外直连，APK 上表现为「过几天就播不了」）。

为什么不做「按需回源」（即出口时为 NULL 的曲目现取现搬）：
  出口在**请求路径**上（详情/广场/搜索/播放计数/下载/试听）。在那里拉几十 MB 音频
  会把请求线程占住（用户看到的是响应变慢甚至超时）。按需回填放在**后台任务**里
  （`mirror.mirror_in_background`），下一次请求自然命中 CDN —— 见 songs.py 的
  `_schedule_cdn_backfill`。同步链路（sync-flow）例外：它本来就是「准备」语义，
  允许阻塞等待回源（APK 歌单需要稳定链接）。
"""

from __future__ import annotations


def full_audio_url(song, *, force_upstream: bool = False) -> str:
    """整曲播放地址：优先 CDN，其次上游。

    `force_upstream=True` 用于「必须拿到上游原件」的场景（如分轨/后期处理要传给
    供应商端点），避免把 CDN 域名喂给只认识上游存储的第三方接口。
    """
    upstream = getattr(song, "audio_url", None) or ""
    if force_upstream:
        return upstream
    return getattr(song, "audio_cdn_url", None) or upstream


def has_cdn(song) -> bool:
    return bool(getattr(song, "audio_cdn_url", None))
