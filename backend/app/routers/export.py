"""GAP-028 批量导出：我的作品 → 音频 zip（内置 metadata.csv）+ 独立元数据 CSV。

三条边界，每条都对应一个会真出事的场景：

1. **只导出自己的曲目**。导出物里是**完整音频 + 全部元数据**，比单曲下载更敏感：
   传别人的 id 必须 403（不是 404、不是静默跳过）—— 这里显式区分「不存在」与
   「存在但不属于你」，因为单曲详情/批量端点的 404 语义是为**不泄露存在性**设计的，
   而本端点的滥用面是「拿别人的 id 来偷作品」，必须让越权者拿到明确的拒绝。
2. **未配 storage_* 也要能用**。prod 当前就是「0 条 storage_*」（见 GAP-019 的
   config.py 注释），若导出强依赖对象存储，这个功能一上线就是 500 —— 故：
   · 配了 → 打包上传桶，返回 presign 一次性下载链接（`export_url_ttl_seconds`）；
   · 没配 → 本地临时 zip + `/api/songs/export/download/{token}` 一次性领取，同样会过期。
   两条路的返回形状完全一致（url/expires_at/expires_at_iso），前端不需要分支。
3. **打包不是无界的**。zip 同时受三道闸：曲目数（`EXPORT_MAX_SONGS`）、
   总体积（`export_max_total_mb`）、单曲体积（`export_max_song_mb`）——
   没有闸门时一个脚本就能让服务器把 200 首无损音频拉满本地带宽与磁盘（CWE-400）。

⚠ 为什么不复用 `/api/songs/{id}/download` 的实现：那里是**单曲 + 订阅门控 + attachment
流式**，而导出是**多曲 + 打包 + 一次性链接**。共用会把「一次性链接」这套生命周期
硬塞进单曲下载路径，代价大于收益。本文件复用它的**取源**语义（`full_audio_url`）。
"""

from __future__ import annotations

import csv
import datetime
import io
import logging
import os
import re
import secrets
import tempfile
import threading
import time
import zipfile

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import Song, User
from app.schemas import SongExportRow
from app.ratelimit import rate_limit_export
from app.routers.auth import get_current_user
from app.storage import s3 as storage
from app.storage.transport import full_audio_url

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/songs", tags=["export"])

#: 单次导出的曲目数上限（**硬 400**，不静默截断 —— 截断会让用户以为拿全了）。
#: 与库页一页 20 / 批量上限 200 对齐：多选是「一屏到几屏」的操作，不是「把整个库拖走」。
EXPORT_MAX_SONGS = 200

#: 单次导出 zip 的总体积上限（MB）。50 首无损 ≈ 1.5GB，取 512MB 作默认护栏，
#: 运维可用 `export_max_total_mb` 按机器磁盘/带宽调整。
DEFAULT_MAX_TOTAL_MB = 512

#: 单曲体积上限（MB）。超过的曲目**跳过并如实汇报**（不整批失败），
#: 理由同 library 批量端点：一首 4K 母带不该拖垮另外 49 首。
DEFAULT_MAX_SONG_MB = 100

def _max_total_mb() -> int:
    """总体积上限（MB）：<=0 视为关闭该闸门（运维显式决定，而不是意外为 0）。"""
    v = int(settings.export_max_total_mb or 0)
    return v if v > 0 else DEFAULT_MAX_TOTAL_MB


def _max_song_mb() -> int:
    v = int(settings.export_max_song_mb or 0)
    return v if v > 0 else DEFAULT_MAX_SONG_MB


def _url_ttl() -> int:
    v = int(settings.export_url_ttl_seconds or 0)
    return v if v > 0 else 3600


# ═══════════════════════════════════════════════════════════════════════════
# metadata.csv —— 字段对齐 SongOut
# ═══════════════════════════════════════════════════════════════════════════

#: CSV 列顺序。**与 SongExportRow 的字段同名同序**（后者是唯一契约来源，
#: 这里只决定「列怎么排」）。一眼能确定「打开表格第一列就是曲名 + id」，故 id/title 前置。
CSV_COLUMNS = [
    "id",
    "title",
    "style",
    "duration",
    "created_at",
    "play_count",
    "like_count",
    "favorite_count",
    "rating_avg",
    "rating_count",
    "status",
    "task_type",
    "model_version",
    "content_category",
    "license",
    "is_public",
    "moderation_status",
    "audio_format",
    "audio_url",
    "cover_url",
]

#: Excel 打开 UTF-8 CSV 时中文全是乱码 —— 加 BOM 是唯一不需要用户改设置的解法。
CSV_BOM = "\ufeff"

#: Excel 的 CSV 注入（CWE-1236）：以 = + - @ 开头的单元格会被当公式执行。
#: 标题/style 是用户可控的自由文本，必须前缀单引号中和。
_FORMULA_PREFIX = ("=", "+", "-", "@", "\t", "\r")


def _csv_cell(value) -> str:
    """单元格归一化：None → 空串；公式前缀中和。"""
    if value is None:
        return ""
    text = str(value)
    if text[:1] in _FORMULA_PREFIX:
        return "'" + text
    return text


def _row_for(song: Song, likes: int = 0, favorites: int = 0) -> dict:
    """一首歌的一行元数据：按 SongOut 口径取值 → `SongExportRow` 校验归一化 → 返回 dict。

    多这一步（而不是直接拼 dict）是为了让「字段表」只有一处定义：
    新增/改名一个 CSV 列时，拼错键会在构造成导出时就炸出明确错误，
    而不是等用户打开 Excel 才发现某列整列空。
    """
    values = {
        "id": song.id,
        "title": song.title or "",
        "style": song.style or "",
        "duration": int(song.duration or 0),
        "created_at": song.created_at.isoformat() if song.created_at else "",
        "play_count": int(song.play_count or 0),
        "like_count": likes,
        "favorite_count": favorites,
        # 无评分时为 None（不是 0）：0 会被读成「大家都打 0 分」，
        # None 在 CSV 里落成空单元格 = 「尚无评分」，与 SongOut.rating_avg=None 同义。
        "rating_avg": getattr(song, "rating_avg", None),
        "rating_count": 0,
        "status": song.status or "",
        "task_type": song.task_type or "generate",
        "model_version": song.model_version or "",
        "content_category": song.content_category or "",
        "license": song.license or "",
        "is_public": bool(song.is_public),
        "moderation_status": song.moderation_status or "",
        "audio_format": song.audio_format or "mp3",
        "audio_url": full_audio_url(song),
        "cover_url": song.cover_url or "",
    }
    row = SongExportRow(**values)
    return row.model_dump()


def _interaction_counts(db: Session, song_ids: list[int]) -> dict[int, dict[str, int]]:
    """批量聚合互动计数（赞/收藏/评分人数）。

    不复用 `songs._interaction_stats`：它会带上 liked_by_me 等**登录态相关**字段并对
    评分求平均，而导出只需要三个整数计数。共用会把「广场展示」的语义耦合进导出。
    """
    from sqlalchemy import func

    from app.models import SongFavorite, SongLike, SongRating

    out: dict[int, dict[str, int]] = {
        sid: {"likes": 0, "favorites": 0, "ratings": 0} for sid in song_ids
    }
    if not song_ids:
        return out
    for table, key in ((SongLike, "likes"), (SongFavorite, "favorites"), (SongRating, "ratings")):
        rows = (
            db.query(table.song_id, func.count(table.id))
            .filter(table.song_id.in_(song_ids))
            .group_by(table.song_id)
            .all()
        )
        for sid, c in rows:
            out.setdefault(sid, {"likes": 0, "favorites": 0, "ratings": 0})[key] = int(c)
    return out


def build_metadata_csv(songs: list[Song], counts: dict[int, dict[str, int]] | None = None) -> bytes:
    """生成 metadata.csv 字节（UTF-8 + BOM）。

    返回 bytes 而非 str：BOM 必须原样落进 zip 条目/响应体，写 str 会被上层
    再编码一次，BOM 变成 ``\\ufeff`` 六个字符（Excel 又乱码，且这 bug 在
    「肉眼看着有 BOM」的情况下极难发现）。
    """
    counts = counts or {}
    buf = io.StringIO(newline="")
    writer = csv.DictWriter(buf, fieldnames=CSV_COLUMNS, lineterminator="\r\n")
    writer.writeheader()
    for s in songs:
        st = counts.get(s.id, {})
        row = _row_for(s, likes=st.get("likes", 0), favorites=st.get("favorites", 0))
        row["rating_count"] = st.get("ratings", 0)
        writer.writerow({k: _csv_cell(v) for k, v in row.items()})
    return (CSV_BOM + buf.getvalue()).encode("utf-8")


# ═══════════════════════════════════════════════════════════════════════════
# 取歌 / 归属校验
# ═══════════════════════════════════════════════════════════════════════════


def _normalize_ids(raw: list[int]) -> list[int]:
    """去重 + 保序 + 上限。重复 id 会让 zip 里的曲目数与用户看到的勾选数对不上。"""
    seen: set[int] = set()
    out: list[int] = []
    for sid in raw or []:
        try:
            sid = int(sid)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="song_ids 必须是整数") from None
        if sid not in seen:
            seen.add(sid)
            out.append(sid)
    if not out:
        raise HTTPException(status_code=400, detail="请至少选择一首歌曲")
    if len(out) > EXPORT_MAX_SONGS:
        raise HTTPException(
            status_code=400, detail=f"单次导出最多 {EXPORT_MAX_SONGS} 首，请分批导出"
        )
    return out


def _resolve_owned(ids: list[int], db: Session, user: User) -> list[Song]:
    """按请求顺序取回自己的曲目；**任一不满足即 403**。

    为何是 403 而不是「跳过」：
      · 不存在 → 403（本端点只服务自己的库，「不存在」与「不是你的」对调用方
        是同一个事实：你无权导出它。分开报会泄露 id 是否存在）；
      · 存在但 user_id != 我 → 403（越权导出完整音频 + 元数据，是本功能最直接的滥用面）；
      · 在回收站 → 403（回收站里的歌「已删除但可恢复」，导出会把「删除」变成无意义的动作，
        用户想导出应先恢复）；
      · 未生成完成 → 400（没有音频可以打包，这是**状态**问题不是权限问题）。

    整批拒绝而不是部分成功：导出物是一个 zip，缺了几首的 zip 会让人以为
    「全都导出了」，比直接失败更糟。
    """
    rows = db.query(Song).filter(Song.id.in_(ids)).all()
    by_id = {s.id: s for s in rows}
    for sid in ids:
        song = by_id.get(sid)
        if song is None or song.user_id != user.id or song.deleted_at is not None:
            raise HTTPException(status_code=403, detail="只能导出你自己的作品")
    songs = [by_id[sid] for sid in ids]
    unfinished = [s.id for s in songs if s.status != "completed"]
    if unfinished:
        raise HTTPException(
            status_code=400,
            detail=f"以下作品尚未生成完成，无法导出：{', '.join(str(i) for i in unfinished[:10])}",
        )
    return songs


# ═══════════════════════════════════════════════════════════════════════════
# 打包
# ═══════════════════════════════════════════════════════════════════════════

_BAD_FILENAME = re.compile(r'[\\/:*?"<>|\r\n\t]')
#: 连续点（`..` / `...`）—— zip-slip 的关键片段，条目名里一律折叠掉。
_DOT_RUN = re.compile(r"\.{2,}")


def _entry_name(song: Song, index: int, ext: str) -> str:
    r"""zip 内条目名：`001_歌名.mp3`。

    zip 条目名是**用户可控字符串进入解压端文件系统**的位置（zip-slip，CWE-22）：
    仅去掉路径分隔符还不够 —— 标题 `../../etc/passwd` 里剩下的 `..` 在**宽松的解压器**
    上仍能穿越（Windows 更会把它当上一级目录）。故这里把连续的点整体折叠成 `_`，
    从根上不产出 `..` 片段。
    """
    raw = (song.title or "未命名").strip()
    safe = _BAD_FILENAME.sub("_", raw)
    safe = _DOT_RUN.sub("_", safe)  # 任何 `..`/`...` 片段都不允许出现在条目名里
    safe = safe.strip("_ .")[:60] or "未命名"
    return f"{index:03d}_{safe}{ext}"


def _ext_for(song: Song, url: str) -> str:
    """音频扩展名：优先按真实 URL 推断（URL 可能指向 wav/flac 而无损列没落库）。"""
    ext = os.path.splitext((url or "").split("?")[0])[1].lower()
    if ext in (".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg"):
        return ext
    fmt = (song.audio_format or "mp3").lower()
    return f".{fmt}" if fmt in ("mp3", "wav", "flac") else ".mp3"


class ExportResult(BaseModel):
    """两个导出端点的统一返回形状（前端不需要区分 storage 配没配）。"""

    url: str
    song_count: int
    skipped: list[str] = []
    total_bytes: int = 0
    expires_at: int | None = None
    expires_at_iso: str | None = None
    storage: str = "s3"


def _pack(songs: list[Song], counts: dict[int, dict[str, int]]) -> tuple[str, list[str], int]:
    """下载音频 → 打 zip（含 metadata.csv）。返回 (本地 zip 路径, 跳过的原因, 字节数)。

    ⚠ 同步阻塞（网络 + 磁盘）：调用方必须 `run_in_threadpool`，否则会占住事件循环，
    让同机的其他请求（含播放/生成）一起卡住。
    """
    from app.storage.s3 import download_to_temp

    max_total = _max_total_mb() * 1024 * 1024
    max_song = _max_song_mb() * 1024 * 1024
    skipped: list[str] = []
    total = 0
    fd, zip_path = tempfile.mkstemp(suffix=".zip", prefix="export_")
    os.close(fd)
    try:
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
            zf.writestr("metadata.csv", build_metadata_csv(songs, counts))
            tmp_files: list[str] = []
            try:
                for i, song in enumerate(songs, start=1):
                    url = full_audio_url(song)
                    if not url:
                        skipped.append(f"{song.title or song.id}：无音频")
                        continue
                    # 单曲上界 = min(单曲上限, 总量剩余)，使「慢速大文件」在超量前就断掉
                    remaining = max_total - total
                    if remaining <= 0:
                        skipped.append(f"{song.title or song.id}：已达本次导出体积上限")
                        continue
                    limit = min(max_song, remaining)
                    try:
                        local = download_to_temp(url, timeout_total=120, max_bytes=limit)
                    except Exception as e:  # 单曲失败不整批失败
                        skipped.append(f"{song.title or song.id}：下载失败（{type(e).__name__}）")
                        continue
                    tmp_files.append(local)
                    size = os.path.getsize(local)
                    ext = _ext_for(song, url)
                    zf.write(local, _entry_name(song, i, ext))
                    total += size
            finally:
                for p in tmp_files:
                    try:
                        os.unlink(p)
                    except OSError:
                        pass
            # 压缩包自述：没有它，用户拿到 zip 只看到一堆编号文件名，无法知道跳过了谁
            if skipped:
                zf.writestr(
                    "export-notes.txt",
                    ("以下曲目未打包：\n" + "\n".join(f"- {s}" for s in skipped) + "\n").encode(
                        "utf-8"
                    ),
                )
        return zip_path, skipped, os.path.getsize(zip_path)
    except Exception:
        try:
            os.unlink(zip_path)
        except OSError:
            pass
        raise


# ═══════════════════════════════════════════════════════════════════════════
# 本地降级：临时 zip + 一次性领取
# ═══════════════════════════════════════════════════════════════════════════


class _LocalExportRegistry:
    """进程内「临时导出包」登记表（未配对象存储时的降级通道）。

    · 一次性：领取即摘除（token 泄露的窗口只到第一次成功下载）；
    · 有 TTL：过期由 `sweep()` 删文件，防 /tmp 无限膨胀；
    · 进程内 dict 而非 Redis：与 storage 未配置的部署形态（单机 docker-compose）一致；
      多实例下 token 落到别的实例就是 404，用户重试即好 —— 属可接受的降级，不是静默错误。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: dict[str, dict] = {}

    def add(self, user_id: int, path: str, song_count: int, ttl: int) -> str:
        token = secrets.token_urlsafe(32)
        now = time.time()
        with self._lock:
            self.sweep(now)
            self._items[token] = {
                "user_id": user_id,
                "path": path,
                "song_count": song_count,
                "expires_at": now + ttl,
            }
        return token

    def pop(self, token: str, user_id: int) -> dict | None:
        """取出并摘除。**归属不符直接返回 None**（不区分「不存在」与「不是你的」）。"""
        now = time.time()
        with self._lock:
            item = self._items.get(token)
            if item is None or item["user_id"] != user_id or item["expires_at"] <= now:
                return None
            self._items.pop(token, None)
        return item

    def sweep(self, now: float | None = None) -> int:
        now = now or time.time()
        dead = [t for t, it in self._items.items() if it["expires_at"] <= now]
        for t in dead:
            item = self._items.pop(t, None)
            if item:
                try:
                    os.unlink(item["path"])
                except OSError:
                    pass
        return len(dead)

    def reset(self) -> None:
        """清空（测试用）。"""
        with self._lock:
            for item in self._items.values():
                try:
                    os.unlink(item["path"])
                except OSError:
                    pass
            self._items.clear()


local_exports = _LocalExportRegistry()


def _expires_fields(ttl: int) -> dict:
    exp = int(time.time()) + ttl
    return {
        "expires_at": exp,
        "expires_at_iso": datetime.datetime.fromtimestamp(
            exp, tz=datetime.timezone.utc
        ).isoformat(),
    }


# ═══════════════════════════════════════════════════════════════════════════
# 端点
# ═══════════════════════════════════════════════════════════════════════════


class ExportReq(BaseModel):
    song_ids: list[int]


@router.post("/export", response_model=ExportResult)
async def export_songs(
    body: ExportReq,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """POST /api/songs/export — 批量导出（音频 + metadata.csv）为一个 zip。

    **同步**返回一次性下载链接（与 `/songs/{id}/stem` 同形状）：打包在
    `run_in_threadpool` 里做，请求期间用户看到的是「进度反馈」而不是 202 + 轮询。
    上限护栏见文件头注；未配 storage_* 时降级为本地临时包 + 一次性 token。
    """
    rate_limit_export(str(user.id))
    ids = _normalize_ids(body.song_ids)
    songs = _resolve_owned(ids, db, user)
    counts = _interaction_counts(db, ids)

    try:
        zip_path, skipped, size = await run_in_threadpool(_pack, songs, counts)
    except Exception as e:  # 打包失败 = 我们的问题，不是用户的；如实 502 而非 500 堆栈
        raise HTTPException(status_code=502, detail="批量导出失败，请稍后重试") from e

    ttl = _url_ttl()
    exp = _expires_fields(ttl)
    if storage.is_configured():
        try:
            # 键名带随机串而非 user_id-only：同一用户的两次导出不能互相覆盖
            # （覆盖会让「上一条链接还活着，内容却已换成新包」成为可复现的诡异现象）。
            key = f"exports/{user.id}/{secrets.token_urlsafe(16)}.zip"
            await run_in_threadpool(storage.upload_file, zip_path, key, "application/zip")
            url = await run_in_threadpool(storage.presigned_url, key, ttl)
            return ExportResult(
                url=url,
                song_count=len(songs),
                skipped=skipped,
                total_bytes=size,
                storage="s3",
                **exp,
            )
        except Exception as e:
            # 上传/签名失败 → **不 500**，落到本地降级通道（导出仍可用）
            log.warning("导出上传对象存储失败，降级为本地包：%s", e)

    token = local_exports.add(user.id, zip_path, len(songs), ttl)
    return ExportResult(
        url=f"/api/songs/export/download/{token}",
        song_count=len(songs),
        skipped=skipped,
        total_bytes=size,
        storage="local",
        **exp,
    )


@router.get("/export/download/{token}")
def download_export(
    token: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """一次性领取本地降级包（未配对象存储时）。登录 + 归属校验，领取后即刻失效。"""
    from fastapi.responses import FileResponse

    from starlette.background import BackgroundTask

    item = local_exports.pop(token, user.id)
    if item is None or not os.path.exists(item["path"]):
        raise HTTPException(status_code=404, detail="下载链接已失效，请重新导出")
    path = item["path"]

    def _cleanup() -> None:
        """响应发完即删：一次性包留在磁盘上没有意义，只会撑大 /tmp。"""
        try:
            os.unlink(path)
        except OSError:
            pass

    return FileResponse(
        path,
        media_type="application/zip",
        filename="my-songs-export.zip",
        background=BackgroundTask(_cleanup),
    )


@router.get("/export.csv")
def export_metadata_csv(
    ids: str | None = Query(
        None, description="逗号分隔的曲目 id；留空 = 导出全部（上限 200 首）"
    ),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """GET /api/songs/export.csv — 我的作品元数据 CSV（UTF-8 + BOM）。

    为什么单独一个端点而不是「打包里的那份够用」：最常见的用法是把元数据丢进
    Excel 做对账/发行清单，为此下载 512MB 音频是荒唐的。字段集与 zip 内的
    metadata.csv **完全同源**（同一个 `build_metadata_csv`），不会漂移。
    """
    if ids is not None and ids.strip() != "":
        raw = [p for p in (x.strip() for x in ids.split(",")) if p]
        id_list = _normalize_ids(raw)
    else:
        id_list = _normalize_ids(
            [
                r[0]
                for r in db.query(Song.id)
                .filter(Song.user_id == user.id, Song.deleted_at.is_(None))
                .order_by(Song.id.desc())
                .limit(EXPORT_MAX_SONGS)
                .all()
            ]
        )
    # CSV 不过 _resolve_owned 的「必须 completed」：元数据对未完成作品同样有意义
    # （进度表里也得有它们）。归属仍是硬门 —— 越权一律 403。
    rows = db.query(Song).filter(Song.id.in_(id_list)).all()
    by_id = {s.id: s for s in rows}
    for sid in id_list:
        song = by_id.get(sid)
        if song is None or song.user_id != user.id or song.deleted_at is not None:
            raise HTTPException(status_code=403, detail="只能导出你自己的作品")
    songs = [by_id[sid] for sid in id_list]
    payload = build_metadata_csv(songs, _interaction_counts(db, id_list))
    return Response(
        content=payload,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="my-songs.csv"'},
    )
