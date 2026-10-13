"""无状态分享链接（GAP-013）。

设计约束（**必须无状态**，本批仅 #91 允许迁移）：
  · 不新增数据表、不新增迁移 —— 分享凭据是**自包含的签名 token**，服务端不存任何状态；
  · token = base64url(payload) + "." + base64url(HMAC-SHA256(secret, payload))，
    载荷只有 `{sid, exp}` 两个字段：看完即弃，无需吊销列表；
  · 密钥只从 env 读（`SHARE_SECRET`；未配置时回落 `JWT_SECRET`），**绝不进 git**
    （仓库 Public）。

为什么不是「把 song_id 直接放 URL」：
  裸 song_id 无法表达「何时失效」，也无从防篡改 —— 拿到链接的人可以把 id 改成任意值，
  等于把**未公开**的作品（`is_public=False`）也暴露出去。签名把「作者授权的这一首 +
  这个有效期」绑成一个不可伪造的整体。

访问闸门（**两道，缺一不可**）：
  1. token 闸 —— 签名有效 + 未过期（`exp`），否则 403；
  2. 内容闸 —— 曲目仍满足「未软删 + is_public + 非 rejected + （开审核时）approved」，
     否则 404（**不泄露存在性**：撤回公开后的作品连「存在」都不该被探知）。
  第 2 道是**在访问时刻实时判定**的 —— 作者撤回公开 / 审核驳回后，已发出的旧链接
  立刻失效，这正是不落地表也成立的地方（对比：落库的 share 记录需要额外失效逻辑）。

分享访问**不需要登录**（未登录浏览器也能播）：
  解析出的音频地址仍走既有的权益门控 —— 未订阅者拿到的是试听片段（`preview_audio_url`），
  订阅者/作者拿到整曲 CDN 链接。分享不会绕开 freemium 门，它只是换了个入口。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import subprocess
import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.entitlements import can_download, can_play_full
from app.models import Song, User
from app.ratelimit import rate_limit_preview
from app.routers.auth import get_current_user, get_optional_user
from app.storage.transport import full_audio_url

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/share", tags=["share"])

# 默认有效期 7 天。上限 30 天：分享链接是「给朋友听一下」的临时凭据，
# 不是永久公开的第二套发布体系（要永久公开走 is_public + 广场）。
DEFAULT_TTL_SECONDS = 7 * 24 * 3600
MAX_TTL_SECONDS = 30 * 24 * 3600

# token 版本前缀。将来若载荷结构变更（例如加 `v2` 用途字段），老 token 可据此拒绝，
# 而不必猜测载荷形状 —— 省掉一次「新旧混读导致误判」的线上事故。
_TOKEN_V1 = "v1"


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64d(text: str) -> bytes:
    """base64url 解码（容忍缺失的 padding）。非法输入抛 ValueError。"""
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def share_secret() -> str:
    """签名密钥。

    优先 `SHARE_SECRET`（独立密钥，便于单独轮换而不影响登录态）；未配置时回落
    `JWT_SECRET` —— 复用现有密钥是本 issue 明确允许的选项，也避免「忘了配新 env
    导致分享功能整体不可用」。
    ⚠ 两者都只从 env 读；代码里没有任何字面量密钥。
    """
    return (settings.share_secret or "").strip() or settings.jwt_secret


def sign_share_token(song_id: int, ttl_seconds: int = DEFAULT_TTL_SECONDS, now: Optional[int] = None) -> str:
    """签发分享 token。`now` 可注入以便测试构造已过期 token。"""
    ttl = max(60, min(int(ttl_seconds), MAX_TTL_SECONDS))
    issued = int(now if now is not None else time.time())
    payload = {"v": _TOKEN_V1, "sid": int(song_id), "exp": issued + ttl, "iat": issued}
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    sig = hmac.new(share_secret().encode("utf-8"), body, hashlib.sha256).digest()
    return f"{_b64e(body)}.{_b64e(sig)}"


class ShareClaims(BaseModel):
    song_id: int
    exp: int


def verify_share_token(token: str, now: Optional[int] = None) -> ShareClaims:
    """校验 token：签名（恒定时间比较）+ 结构 + 版本 + 未过期。任一不满足抛 403。

    用 403 而非 401：**这不是认证失败**，是「这张凭据无效或已过期」，
    401 会诱导客户端去走登录流程（登录了也救不回一张过期的分享票）。
    """
    bad = HTTPException(status_code=403, detail="分享链接无效或已过期")
    if not token or token.count(".") != 1:
        raise bad
    body_part, sig_part = token.split(".", 1)
    try:
        body = _b64d(body_part)
        sig = _b64d(sig_part)
    except Exception:
        raise bad
    expected = hmac.new(share_secret().encode("utf-8"), body, hashlib.sha256).digest()
    # 恒定时间比较：逐字节比较的耗时差可被用来逐位爆破签名（时序侧信道）。
    if not hmac.compare_digest(sig, expected):
        raise bad
    try:
        payload = json.loads(body.decode("utf-8"))
        sid = int(payload["sid"])
        exp = int(payload["exp"])
        ver = str(payload.get("v", ""))
    except Exception:
        raise bad
    if ver != _TOKEN_V1 or sid <= 0:
        raise bad
    ts = int(now if now is not None else time.time())
    if exp <= ts:
        raise bad
    return ShareClaims(song_id=sid, exp=exp)


def _share_visible_song(song_id: int, db: Session) -> Song:
    """分享访问的内容闸：与广场（plaza._visible_song）同一套可见性语义。

    与 plaza 的差异：本函数**多要求 is_public**。分享是「公开作品的传播」，
    作者私下分享自己未公开发布的作品不在本端点范围内 —— 那属于「作者本人访问」，
    应走已登录的详情端点（`GET /api/songs/{id}`，自己可见）。
    这样切分的好处：分享端点**没有**任何「凭 token 即可读私密内容」的路径，
    即使密钥泄露也无法把它当成越权读取器用。
    """
    song = db.get(Song, song_id)
    if not song or song.deleted_at is not None:
        raise HTTPException(status_code=404, detail="作品不存在或已下架")
    if not song.is_public or song.status != "completed":
        raise HTTPException(status_code=404, detail="作品不存在或已下架")
    if song.moderation_status == "rejected":
        raise HTTPException(status_code=404, detail="作品不存在或已下架")
    # 与广场一致：机审开启时显式 approved 才可见（防未审内容经分享外流）。
    if settings.enable_moderation and song.moderation_status != "approved":
        raise HTTPException(status_code=404, detail="作品不存在或已下架")
    return song


class ShareCreateReq(BaseModel):
    ttl_seconds: int = DEFAULT_TTL_SECONDS


class ShareLinkResp(BaseModel):
    """签发回执。

    `token` 是**敏感凭据**（拿到即可访问），这里只回给作者本人（端点需登录 + 所有权校验）。
    `path` 由后端拼好，前端不必知道 token 该塞进哪个参数（少一处拼错的地方）。
    """

    token: str
    path: str
    expires_at: int


@router.post("/songs/{song_id}", response_model=ShareLinkResp)
def create_share_link(
    song_id: int,
    body: Optional[ShareCreateReq] = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """作者为自己的**已公开发布**作品签发分享链接。

    要求「已公开」而非只要求「自己的」：未公开作品本就不可对外传播，给它发分享票
    会让「未公开」这个状态形同虚设（作者可以自己点详情页看，不需要分享票）。
    """
    song = db.get(Song, song_id)
    # 所有权失败与不存在返回同一个 404：不给出「这首存在但不是你的」这种存在性信号。
    if not song or song.user_id != user.id or song.deleted_at is not None:
        raise HTTPException(status_code=404, detail="歌曲不存在")
    if not song.is_public or song.status != "completed":
        raise HTTPException(status_code=400, detail="仅已公开发布且生成完成的作品可生成分享链接")
    if song.moderation_status == "rejected":
        raise HTTPException(status_code=403, detail="该作品未通过内容审核，无法分享")

    ttl = body.ttl_seconds if body is not None else DEFAULT_TTL_SECONDS
    token = sign_share_token(song_id, ttl)
    claims = verify_share_token(token)  # 回读一次，保证回执里的 expires_at 与 token 真实一致
    return ShareLinkResp(
        token=token, path=f"/share/{token}", expires_at=claims.exp
    )


@router.get("/songs/{token}")
def resolve_share_link(
    token: str,
    request: Request,
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_optional_user),
):
    """分享落地解析：**匿名可访问**（未登录浏览器也能播）。

    返回一个**精简视图**而非完整 SongOut：
      · 分享页只需要「能播 + 能显示谁的作品 + 授权类型」；
      · 不下发 play_count / 互动统计 / cost_cny / is_owner 等 —— 那些是登录态与
        互动场景的字段，匿名分享页拿到只会平白多出信息面（CWE-200 收敛）。
    """
    rate_limit_preview(f"ip:{request.client.host if request.client else 'unknown'}")
    claims = verify_share_token(token)
    song = _share_visible_song(claims.song_id, db)

    # 权益门控不放水：未订阅者仍是试听片段（fail-closed，片段缺失就给 None）。
    full = can_play_full(user)
    audio = (song.audio_cdn_url or song.audio_url or None) if full else (song.preview_audio_url or None)
    return {
        "song_id": song.id,
        "title": song.title or "未命名",
        "cover_url": song.cover_url or None,
        "audio_url": audio,
        "duration": song.duration or 0,
        # 试听秒数仅用于「试听前 N 秒」文案；完整版为 None。
        "preview_seconds": None if full else song.preview_seconds,
        "can_play_full": full,
        "can_download": can_download(user),
        "license": song.license or "non_commercial",
        "moderation_status": song.moderation_status or "pending",
        "expires_at": claims.exp,
    }


# ═══════════════════════════════════════════════════════════════════════════
# 波形峰值（GAP-013）
# ═══════════════════════════════════════════════════════════════════════════
# 为什么峰值由**后端**给，而不是前端 fetch 音频自己 decode —— 这是浏览器实测
# 逼出来的结论，不是偏好：
#
#   1. 前端 `fetch(audio_url)` 受 **页面 CSP 的 `connect-src`** 管，
#      而 security-headers.js 里写死的是 `connect-src 'self' {API_BASE}`。
#      生产音频在 `STORAGE_PUBLIC_BASE`（CDN，**与 API 不同源**）→ fetch 直接被 CSP 拦。
#      实测（chromium --headless + CDP Network.loadingFailed）：`blockedReason: "csp"`。
#   2. 前端若改用 `credentials: "include"` 去绕，还会撞 CORS
#      `PreflightInvalidAllowCredentials`（`ACAO: *` + include 是非法组合）。
#   3. 连 wavesurfer 自己的 `load(url)` 也一样死在 CSP（实测 `Failed to fetch`、canvas=0）。
#      它只是「把 fetch 包在库里」，不改变 CSP 事实。
#   4. 唯一 CSP 放行的是 `media-src 'self' blob: https:` —— 即 `<audio src>` 能播，
#      但 `fetch()` 不能取字节。而 `<audio>` 没有任何「读原始 PCM」的 API。
#
# 所以：**把峰值抽取挪到服务端**。后端本来就能拿到音频（试听片段生成已经在做
# `download_to_temp` + ffmpeg），多抽一条峰值曲线是同一份数据的第二次读，边际成本极低；
# 前端则退化成「拿一个 JSON 数组 → 画 canvas」，完全无网络/CSP 顾虑，也不白拉一遍整曲。
#
# 复用与边界：本端点与 `/songs/{id}/preview` 共用「下载 → ffmpeg」这套基础设施
# （app.storage.s3.download_to_temp、settings.audio_postprocess_ffmpeg_bin、preview_* 上限）。
# 归属放在 share.py 而非 songs.py，是因为本批次 songs.py 被划为**不可改**（并行任务冲突面）。

# 峰值桶数默认 800（约每 1-2px 一个桶，600px 宽的波形足够细腻，JSON 也只有几 KB）。
PEAKS_DEFAULT_BUCKETS = 800
PEAKS_MAX_BUCKETS = 4000

# 峰值缓存目录（与 _preview_cache_dir 同风格，独立子目录避免与试听 mp3 混放）。
def _peaks_cache_dir() -> str:
    d = os.path.join(settings.preview_cache_dir or ".cache/preview", "peaks")
    os.makedirs(d, exist_ok=True)
    return d


def _peaks_cache_path(song_id: int, buckets: int) -> str:
    return os.path.join(_peaks_cache_dir(), f"{int(song_id)}.{int(buckets)}.json")


def _extract_peaks(local_audio: str, buckets: int) -> list[float]:
    """用 ffmpeg 把音频降采样成单声道 8kHz s16le，再按桶取绝对峰值。

    为什么先降采样：整曲 44.1kHz 立体声的原始 PCM 是几十 MB，读进 Python 再分桶
    既慢又费内存。降到 8kHz 单声道后（一首 4 分钟约 4MB），峰值包络完全够用 ——
    波形的可视化精度本来就只有几百个像素。
    `-f s16le -` 让 ffmpeg 把裸 PCM 吐到 stdout，不落中间文件。
    """
    cmd = [
        settings.audio_postprocess_ffmpeg_bin or "ffmpeg",
        "-v", "error",
        "-i", local_audio,
        "-ac", "1",
        "-ar", "8000",
        "-f", "s16le",
        "-",
    ]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0 or not proc.stdout:
        raise RuntimeError(f"ffmpeg 峰值抽取失败: {proc.stderr[-300:].decode('utf-8', 'replace')}")

    raw = proc.stdout
    # s16le：每 2 字节一个小端有符号样本。
    total = len(raw) // 2
    if total <= 0:
        raise RuntimeError("ffmpeg 未产出任何音频样本")
    step = max(1, total // buckets)
    peaks: list[float] = []
    import struct

    for i in range(buckets):
        start = i * step
        if start >= total:
            break
        end = min(start + step, total)
        # struct.unpack_from 一批读，比逐样本 struct.unpack 快一个量级。
        chunk = raw[start * 2 : end * 2]
        vals = struct.unpack(f"<{end - start}h", chunk)
        peak = 0
        for v in vals:
            av = v if v >= 0 else -v
            if av > peak:
                peak = av
        peaks.append(round(peak / 32768.0, 4))
    return peaks


@router.get("/songs/{token}/peaks")
def share_song_peaks(
    token: str,
    request: Request,
    buckets: int = Query(PEAKS_DEFAULT_BUCKETS, ge=64, le=PEAKS_MAX_BUCKETS),
    db: Session = Depends(get_db),
):
    """分享曲目的波形峰值（匿名可访问，与分享 token 同权限）。

    与 `/api/share/songs/{token}` 走**完全相同**的两道闸（签名/过期 + 公开/审核），
    所以它是一个「同权限的子资源」而不是新开的读取面 —— 不存在「拿 token 换 peaks
    去探测未公开作品」的路径。

    fail-closed：抽不出峰值就 404，前端据此退回「只用进度条」而不显示假波形
    （本项目禁止假功能：宁可没有波形，也不要一根随机跳动的柱子）。
    """
    import json as _json

    from fastapi.responses import JSONResponse

    from app.storage.s3 import download_to_temp

    rate_limit_preview(f"ip:{request.client.host if request.client else 'unknown'}")
    claims = verify_share_token(token)
    song = _share_visible_song(claims.song_id, db)

    cached = _peaks_cache_path(song.id, buckets)
    if os.path.exists(cached):
        try:
            with open(cached, "r", encoding="utf-8") as fh:
                return JSONResponse(_json.load(fh))
        except Exception:
            pass  # 缓存损坏 → 重算（不因为一个坏缓存把功能打挂）

    source = full_audio_url(song)
    if not source:
        raise HTTPException(status_code=404, detail="波形不可用")
    try:
        local = download_to_temp(
            source,
            timeout_total=settings.preview_fetch_timeout,
            max_bytes=int(settings.preview_fetch_max_mb * 1024 * 1024),
        )
        peaks = _extract_peaks(local, buckets)
    except Exception as e:
        log.warning("波形峰值抽取失败（song_id=%s）：%s", song.id, e)
        raise HTTPException(status_code=404, detail="波形不可用") from e

    if not peaks:
        raise HTTPException(status_code=404, detail="波形不可用")

    payload = {
        "song_id": song.id,
        "buckets": len(peaks),
        "duration": song.duration or 0,
        "peaks": peaks,
    }
    try:
        with open(cached, "w", encoding="utf-8") as fh:
            _json.dump(payload, fh)
    except Exception:
        pass  # 写缓存失败不影响本次响应
    return JSONResponse(payload)
