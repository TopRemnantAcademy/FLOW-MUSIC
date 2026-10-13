"""GAP-012 歌词编辑：LRC 导出 / 导入 / 保存（改词后重对齐）。

为什么单独开一个 router 而不是塞进 songs.py：
  songs.py 已 2300+ 行、且承载生成/后期/互动/权利四类关注点。歌词编辑是**独立闭环**
  （读 lyrics_sync → 编 → 写回 lyrics_sync + lyric），挂 `/api/songs/{id}/lyrics*` 下
  与既有 songs 路由同命名空间，不需要前端多记前缀。

边界（本批并行任务隔离）：
  · 不新增迁移、不改 models.py —— `Song.lyrics_sync` 列已存在（models.py:88），直接复用；
  · 重对齐结果序列化为行级 lyrics_sections，与 mureka.py:514 落库的**同一种形状**
    （段 → lines → start/end/text），自家前端的 flattenSync 与播放器高亮零改动即可消费；
  · 引擎能力（aligned_lyrics）不在这里碰：Mureka 无该端点，重对齐走**本地纯算法**
    （app/lyrics/lrc.py），不依赖任何供应商。
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_db
from app.entitlements import can_download
from app.lyrics import lrc as lrcx
from app.models import Song, User
from app.quota import rate_limit_generate
from app.ratelimit import rate_limit_assist
from app.routers.auth import get_current_user, get_optional_user

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/songs", tags=["lyrics"])

# 歌词文本上限：与 CreateForm 生成路径同级（songs.py:948 的 5000 字）。
# 定在同一个数上，避免「能生成但编辑不了」或反之。
MAX_LYRIC_CHARS = 5000
# 行级时间轴最多接受这么多行：按 5000 字 / 平均 4 字一行 ≈ 1250 行，留 2 倍余量。
# 无上限时一个脚本就能塞进 10 万行 JSON（~10MB）把库和响应撑爆。
MAX_SYNC_LINES = 3000


# ══════════════════════════════════════════════════════════════════════════════
# 响应/请求模型（就地定义 —— 不往 schemas.py 加东西，减少与并行任务的文件交叠）
# ══════════════════════════════════════════════════════════════════════════════
class LyricLineIn(BaseModel):
    start: int = 0
    end: Optional[int] = None
    text: str = ""
    section: str = "verse"


class LyricLineOut(BaseModel):
    start: int
    end: Optional[int] = None
    text: str
    section: str = "verse"


class LyricsDocResp(BaseModel):
    song_id: int
    title: str = ""
    duration: Optional[float] = None
    lyric: str = ""
    # 解析后的行级时间轴（已升序/去重叠）。空数组 = 本曲无逐字时间轴（Suno / prompt-only）
    lines: list[LyricLineOut] = Field(default_factory=list)
    # 原始 lyrics_sync 是否非空（前端据此决定「按时间轴编辑」还是「纯文本编辑」）
    has_timeline: bool = False
    # 逐字（词级）数据点数量 —— 只用于展示「本曲有 N 个逐字时点」，编辑本身是行级的
    word_count: int = 0
    is_owner: bool = False
    can_edit: bool = False


class LyricsSaveReq(BaseModel):
    """保存歌词。

    · lines 非空 → 行级时间轴（编辑器/导入的 LRC 结果），**原样尊重用户的时点**；
    · lines 为空 + lyric → 纯文本歌词（无时间轴）；若库中已有时间轴且
      realign=True，则按行做重对齐（改词后保持播放器高亮不失配）。
    """

    lyric: str = ""
    lines: Optional[list[LyricLineIn]] = None
    # 是否在「已有时间轴 + 只给了纯文本」时做本地重对齐（默认开 —— 这正是改词后
    # 高亮不失配的关键动作；关掉 = 明确要丢弃时间轴）
    realign: bool = True


class LrcImportReq(BaseModel):
    lrc: str = ""
    # 导入即保存（默认）。False = 只回显解析结果，不落库（前端「预览后再存」）
    save: bool = True


class LyricsImportResp(BaseModel):
    song_id: int
    parsed_lines: int = 0
    imported_lines: list[LyricLineOut] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    lyric: str = ""
    saved: bool = False
    # 诊断提示（如「文件无时标，已作为未定时行导入」）
    notes: list[str] = Field(default_factory=list)


# ══════════════════════════════════════════════════════════════════════════════
# 公共取数 / 组装
# ══════════════════════════════════════════════════════════════════════════════
def _load_song(song_id: int, db: Session, user: Optional[User]) -> Song:
    """按**公开可见性**取歌（同 songs.get_song 口径）：匿名只能看公开曲目。

    导出端点放开给匿名是刻意的：`.lrc` 是作品的附属字幕文件，公开曲目不该因为
    “要登录”而让第三方播放器/脚本拿不到歌词。
    """
    song = db.get(Song, song_id)
    if not song or song.deleted_at is not None:
        raise HTTPException(status_code=404, detail="歌曲不存在")
    owner_id = user.id if user is not None else None
    if song.user_id != owner_id and not song.is_public:
        raise HTTPException(status_code=404, detail="歌曲不存在")
    return song


def _load_owned(song_id: int, db: Session, user: User) -> Song:
    """编辑类端点：必须是本人作品。

    返回 404 而非 403 —— 不泄露「这首歌存在」。**未认证走 401**（由 get_current_user
    依赖在进入本函数前抛出）：401 与 404 是两件事，不能都压成 404，否则用户拿到
    「歌曲不存在」会去检查自己的作品列表，而真正的原因是登录态过期。
    """
    song = db.get(Song, song_id)
    if not song or song.deleted_at is not None or song.user_id != user.id:
        raise HTTPException(status_code=404, detail="歌曲不存在")
    return song


def _current_lines(song: Song) -> list[dict]:
    """库内当前时间轴（dict 形态，见 lrcx.normalize_timeline 的输出契约）。

    `lyrics_sync` 存的是 mureka 原样 JSON —— 段落类型（verse/chorus）是**段级**
    字段，`sections_to_lines` 已把它摊到每行上，故编辑器能从行直接读出所属段落。
    """
    lines = _as_dicts(lrcx.sections_to_lines(song.lyrics_sync))
    if not lines:
        # 无时间轴 → 用纯文本歌词充行（start=0，与前端降级渲染同一口径）
        lines = [
            {"start": 0, "end": None, "text": t, "section": ""}
            for t in lrcx.extract_plain_lines(song.lyric or "")
        ]
    return lines


def _as_dicts(lines: list[lrcx.LrcLine]) -> list[dict]:
    return [
        {"start": l.start, "end": l.end, "text": l.text, "section": l.section} for l in lines
    ]


def _count_words(sync) -> int:
    total = 0
    for sec in lrcx._sections_of(sync):
        rows = sec.get("lines") if isinstance(sec.get("lines"), list) else sec.get("rows")
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            words = row.get("words") if isinstance(row.get("words"), list) else row.get("word")
            if isinstance(words, list):
                total += sum(1 for w in words if isinstance(w, dict))
    return total


def _pack_sync(lines: list[dict]) -> str | None:
    """行级时间轴 → `lyrics_sync` 落库字符串。

    **保持 mureka 的段结构**（`[{section_type, lines:[...]}]`）而不是存平铺行数组：
    · 前端 `lib/syncedLyrics.ts` 的 flattenSync 按段→行→词解析，平铺数组虽也兼容，
      但段结构能保留 verse/chorus 分组（编辑器复读时不用重新推断）；
    · 与 mureka.py:514 `json.dumps(choice["lyrics_sections"])` 同一形状 —— 库内
      `lyrics_sync` 只有一种结构，消费侧只需一套解析。
    """
    if not lines:
        return None
    sections: list[dict] = []
    for ln in lines:
        sec_type = ln.get("section") or "verse"
        if not sections or sections[-1]["section_type"] != sec_type:
            sections.append({"section_type": sec_type, "lines": []})
        line: dict = {"start": int(ln["start"]), "text": ln.get("text") or ""}
        if ln.get("end") is not None:
            line["end"] = int(ln["end"])
        # words 必须一起落库：行级 LRC 虽然只用到行时标，但自家播放器是**逐字**高亮的，
        # 只存行时标等于每次编辑都把逐字体验降级成整行点亮。
        # 其余键（如 realign 的 aligned 诊断位）不是存储契约，不落库。
        words = ln.get("words")
        if isinstance(words, list) and words:
            line["words"] = words
        sections[-1]["lines"].append(line)
    return json.dumps(sections, ensure_ascii=False)


def _plain_from_lines(lines: list[dict]) -> str:
    return lrcx.lyric_from_lines([ln.get("text") or "" for ln in lines])


def _tokenize_lyric(text: str) -> list[str]:
    """把一行歌词切成**词**（中文按字、拉丁按词、数字按段）。

    新导入的 LRC 只有行级时标，没有词级数据。给每行补上均匀分布的词级时点，
    播放器的逐字高亮才有东西可跟（否则整行一次性点亮 = 丢掉了逐字体验）。
    这是**插值**而非伪造：词级时刻由该行确定的起止内插得到，误差不超过一行时长。
    """
    out: list[str] = []
    buf = ""
    for ch in text:
        if ch.isspace():
            if buf:
                out.append(buf)
                buf = ""
            continue
        if "\u4e00" <= ch <= "\u9fff":  # CJK：一字一词
            if buf:
                out.append(buf)
                buf = ""
            out.append(ch)
        else:
            buf += ch
    if buf:
        out.append(buf)
    return out or ([text] if text else [])


def _with_word_timings(lines: list[dict]) -> list[dict]:
    """给行级时间轴补词级 `words[]`（行内均匀分布，末词吃满行尾）。"""
    out: list[dict] = []
    for ln in lines:
        text = ln.get("text") or ""
        start, end = int(ln["start"]), int(ln.get("end") or 0)
        words = _tokenize_lyric(text)
        if not text or not words or end <= start:
            out.append({**ln, "words": []})
            continue
        span = end - start
        per = max(1, span // len(words))
        ws = []
        for i, w in enumerate(words):
            w_start = start + i * per
            w_end = (start + (i + 1) * per) if i + 1 < len(words) else end
            ws.append({"start": w_start, "end": max(w_start + 1, w_end), "text": w})
        out.append({**ln, "words": ws})
    return out


def _lines_json(lines: list[dict]) -> list[LyricLineOut]:
    return [
        LyricLineOut(
            start=int(ln.get("start") or 0),
            end=int(ln["end"]) if ln.get("end") is not None else None,
            text=ln.get("text") or "",
            section=ln.get("section") or "verse",
        )
        for ln in lines
    ]


def _serialize_for_store(lines: list[dict], *, with_words: bool = True) -> str | None:
    packed = _with_word_timings(lines) if with_words else lines
    return _pack_sync(packed)


# ══════════════════════════════════════════════════════════════════════════════
# ① 读取：编辑器数据源
# ══════════════════════════════════════════════════════════════════════════════
@router.get("/{song_id}/lyrics/doc", response_model=LyricsDocResp)
def get_lyrics_doc(
    song_id: int,
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_optional_user),
):
    """歌词编辑器数据源：纯文本 + 行级时间轴 + 段落分组。

    与 `GET /api/songs/{id}`（SongOut.lyrics_sync 原始 JSON）的分工：
    详情接口给的是**原始供应商结构**，本端点给的是**已归一的行级结构** ——
    编辑器不该在浏览器里重复实现一遍容错解析（前端那次容错是为了渲染，
    编辑是双向的，解析口径必须与后端导出一致，否则「显示的行」≠「导出的行」）。
    """
    song = _load_song(song_id, db, user)
    lines = _current_lines(song)
    is_owner = bool(user is not None and song.user_id == user.id)
    return LyricsDocResp(
        song_id=song.id,
        title=song.title or "",
        duration=float(song.duration) if song.duration else None,
        lyric=song.lyric or "",
        lines=_lines_json(lines),
        has_timeline=not lrcx.is_sync_empty(song.lyrics_sync),
        word_count=_count_words(song.lyrics_sync),
        is_owner=is_owner,
        can_edit=is_owner,
    )


# ══════════════════════════════════════════════════════════════════════════════
# ② 导出：/lyrics.lrc（第三方播放器可直接吃）
# ══════════════════════════════════════════════════════════════════════════════
_LRC_MEDIA_TYPE = "text/plain; charset=utf-8"


def _ascii_fallback(name: str) -> str:
    """Content-Disposition 的 ASCII 兜底文件名（非 ASCII 一律替换为 _）。

    为什么要兜底：`filename*=UTF-8''…` 是 RFC 5987 扩展，老播放器/下载器只读
    `filename=`。若那里塞的是裸中文，文件名会变成乱码甚至丢弃整个头。
    """
    safe = "".join(ch if (ch.isascii() and (ch.isalnum() or ch in "-_ .")) else "_" for ch in name)
    return (safe.strip() or "lyrics")[:60]


@router.get("/{song_id}/lyrics.lrc")
def download_lrc(
    song_id: int,
    db: Session = Depends(get_db),
    user: Optional[User] = Depends(get_optional_user),
    offset_ms: int = 0,
):
    """导出标准 `.lrc`（行级 `[mm:ss.xx]`）。

    可解析性优先：只写行首时标 + 文件头元信息，**不写**增强型逐字标记（第三方支持面极窄）。
    无时间轴（Suno / prompt-only 生成）时**降级为无时标纯文本歌词文件**并附 `[re:]`
    说明 —— 而不是返回 404/空文件：用户要的是「把歌词带走」，播放器拿不到时标是
    次要损失，拿不到文件才是。
    """
    song = _load_song(song_id, db, user)
    lines = _current_lines(song)
    has_sync = not lrcx.is_sync_empty(song.lyrics_sync)

    notes: list[str] = []
    if not has_sync:
        notes.append("本曲无时间轴，已导出无时标的纯文本歌词")
        # 无时标 ≠ 塞一堆 [00:00.00]（那会让播放器把所有行挤在开头闪烁）：
        # 直接给纯文本行 + 文件头说明，任何「歌词」读取器都能用。
        text = "\n".join(x["text"] for x in lines)
        head = [f"[ti:{song.title or ''}]"] if song.title else []
        if notes:
            head.append(f"[re:{notes[0]}]")
        text = "\n".join(head + ([text] if text else [])) + "\n"
    else:
        text = lrcx.build_lrc(
            lines,
            title=song.title or "",
            artist="AI Music Platform",
            by="AI Music Platform",
            offset_ms=offset_ms,
            duration_s=float(song.duration) if song.duration else None,
        )
        if not can_download(user) and user is not None:
            # 订阅门槛只针对**音频**下载；歌词导出不做付费墙（导出成本为零，
            # 且歌词是用户自己写的文本）。这里显式留痕，避免后人误以为漏了门控。
            log.debug("lrc export by non-subscriber user=%s song=%s", user.id, song_id)

    # 非 ASCII 标题必须走 RFC 5987 双写法（见 _ascii_fallback 注释）
    from urllib.parse import quote

    pretty = f"{song.title or 'lyrics'}.lrc"
    disp = f"attachment; filename=\"{_ascii_fallback(pretty)}\"; filename*=UTF-8''{quote(pretty)}"
    headers = {
        "Content-Disposition": disp,
        # 歌词是「用户已拥有的资源」的派生物，不必 CDN 缓存；但允许浏览器短缓存
        "Cache-Control": "private, max-age=60",
        "X-Lyrics-Source": "lyrics_sync" if has_sync else "plain",
    }
    return Response(content=text.encode("utf-8"), media_type=_LRC_MEDIA_TYPE, headers=headers)


# ══════════════════════════════════════════════════════════════════════════════
# ③ 导入：.lrc → 时间轴（可顺带保存）
# ══════════════════════════════════════════════════════════════════════════════
@router.post("/{song_id}/lyrics/import", response_model=LyricsImportResp)
def import_lrc(
    song_id: int,
    body: LrcImportReq,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """导入 LRC：解析 → （可选）落库。

    解析容错见 `app/lyrics/lrc.py:parse_lrc`；本次导入的行**原样采用文件里的时点**
    （不做重对齐 —— 用户可能就是从别处拿到的、正确的时轴）。
    文件行数与我们已有的行数不同也照收：以文件为准，用户按了导入就是授权。
    """
    rate_limit_generate(str(user.id))
    rate_limit_assist(request.client.host if request.client else "unknown")
    song = _load_owned(song_id, db, user)

    text = body.lrc or ""
    if len(text) > MAX_LYRIC_CHARS * 4:  # 含时标/元信息，宽放 4 倍
        raise HTTPException(status_code=400, detail="LRC 文件过大，请精简后重试")

    doc = lrcx.parse_lrc(text)
    if not doc.lines:
        raise HTTPException(status_code=400, detail="未能从文件中解析出任何歌词行")

    lines = lrcx.normalize_timeline(
        doc.lines, duration_s=float(song.duration) if song.duration else None
    )
    if len(lines) > MAX_SYNC_LINES:
        raise HTTPException(status_code=400, detail=f"歌词行数超过 {MAX_SYNC_LINES} 行上限")

    plain = _plain_from_lines(lines)
    if len(plain) > MAX_LYRIC_CHARS:
        raise HTTPException(
            status_code=400,
            detail=f"歌词超过 {MAX_LYRIC_CHARS} 字上限（当前 {len(plain)} 字），请精简后重试",
        )

    notes = list(doc.warnings)
    if doc.offset_ms:
        notes.append(f"已应用文件内 [offset:{doc.offset_ms:+d}] 偏移")
    # 段落标签：LRC 的裸 [Verse] 行 → 段落类型（parse_lrc 已归一到行上）
    if any(ln.get("section") for ln in lines if ln.get("section") != "verse"):
        notes.append("已识别段落标签（verse/chorus 等）")

    saved = False
    if body.save:
        song.lyric = plain
        song.lyrics_sync = _serialize_for_store(lines, with_words=True)
        db.commit()
        db.refresh(song)
        saved = True

    return LyricsImportResp(
        song_id=song.id,
        parsed_lines=len(doc.lines),
        imported_lines=_lines_json(lines),
        warnings=doc.warnings,
        lyric=plain,
        saved=saved,
        notes=notes,
    )


# ══════════════════════════════════════════════════════════════════════════════
# ④ 保存：编辑器的唯一写入口（含改词后重对齐）
# ══════════════════════════════════════════════════════════════════════════════
@router.put("/{song_id}/lyrics", response_model=LyricsDocResp)
def save_lyrics(
    song_id: int,
    body: LyricsSaveReq,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """保存歌词（行级时间轴 or 纯文本 + 重对齐）。

    三种情形，语义各不相同、都必须显式区分：
      A. `lines` 非空 → 用户直接编辑了时间轴（改了时点/拆了行）→ 原样落库；
      B. `lines` 为空 + `lyric` 非空 + 已有时间轴 + `realign` → **重对齐**：
         新歌词按原节奏重采样，播放器高亮仍在原曲时轴上（改词不失配）；
      C. `lines` 为空 + `lyric` 非空 + `realign=False` → 明确丢弃时间轴（清 lyrics_sync）。
         不提供这条路径的话，用户「只改文字、保留时间轴」和「要重新定时」就无法区分。

    还有一条不变式：无论走哪条路径，**行级时轴落库前一律经 normalize_timeline**。
    否则「有 end 的行」和「没有 end 行」会同时存在于库里，导出与重对齐的口径立刻分叉。
    """
    rate_limit_generate(str(user.id))
    rate_limit_assist(request.client.host if request.client else "unknown")
    song = _load_owned(song_id, db, user)

    edited = body.lines or None
    lyric_text = (body.lyric or "").strip()
    if not edited and not lyric_text:
        raise HTTPException(status_code=400, detail="歌词内容为空，未做修改")

    model_lines: list[dict] = []
    # 库内是否已有可用时间轴（两条 else 分支都要用；在 if 之前算，避免各自算一遍）
    old_has_sync = not lrcx.is_sync_empty(song.lyrics_sync)

    if edited:
        if len(edited) > MAX_SYNC_LINES:
            raise HTTPException(status_code=400, detail=f"歌词行数超过 {MAX_SYNC_LINES} 行上限")
        model_lines = lrcx.normalize_timeline(
            [ln.model_dump() for ln in edited],
            duration_s=float(song.duration) if song.duration else None,
        )
        plain = _plain_from_lines(model_lines)
        if not plain:
            raise HTTPException(status_code=400, detail="歌词内容为空，未做修改")
        if len(plain) > MAX_LYRIC_CHARS:
            raise HTTPException(
                status_code=400,
                detail=f"歌词超过 {MAX_LYRIC_CHARS} 字上限（当前 {len(plain)} 字），请精简后重试",
            )
    else:
        new_lines = lrcx.extract_plain_lines(lyric_text)
        if not new_lines:
            raise HTTPException(status_code=400, detail="歌词内容为空，未做修改")
        if len(lyric_text) > MAX_LYRIC_CHARS:
            raise HTTPException(
                status_code=400,
                detail=f"歌词超过 {MAX_LYRIC_CHARS} 字上限（当前 {len(lyric_text)} 字），请精简后重试",
            )
        if old_has_sync and body.realign:
            # 重对齐：以**库内当前时间轴**为骨架，套上新歌词（见 lrcx.realign_sync 策略表）
            model_lines = lrcx.realign_sync(
                song.lyrics_sync, new_lines, duration=float(song.duration) if song.duration else None
            )
            # aligned=False = 旧时轴不可用（无轴 / 多行同时点）→ 不能假装重排成功
            if not any(ln.get("aligned") for ln in model_lines):
                raise HTTPException(
                    status_code=409,
                    detail="本曲时间轴无法用于重对齐，请按行手动编辑或先导入 LRC",
                )
        else:
            # realign=False（明确丢弃时间轴）或无旧轴可对齐 → 纯文本行。
            # 这两条路径上**没有可复用的旧时轴**，却仍需走 normalize_timeline：
            # 它给每行补上 `end` 兜底（末行用曲长），否则落库的行既不完整、
            # 也会让导出侧一路退化成「全部 [00:00.00]」。无旧轴 ≠ 不用补 end。
            model_lines = lrcx.normalize_timeline(
                [{"start": 0, "end": None, "text": t, "section": "verse"} for t in new_lines],
                duration_s=float(song.duration) if song.duration else None,
            )
        plain = lrcx.lyric_from_lines(new_lines)

    # 改动歌词后必须重新过内容审核：歌词是公开展示面，改词能绕过生成期的那次检查
    # （songs.py 在生成前 check_lyric）。这里保持同一口径：只判定、不改写用户内容。
    from app import moderation

    if getattr(song, "moderation_status", None) == "rejected" and not moderation.check_lyric(plain):
        raise HTTPException(status_code=409, detail="当前歌词未通过内容检查，请修改后再保存")

    song.lyric = plain
    # 「时间轴归零」与「行有 start=0 但没时轴」是两件事，必须用显式分支表达 ——
    # 否则路径 C（realign=False 明确丢弃时轴）产出的行带着 start=0，
    # `if model_lines:` 一判就又把时轴写了回去，用户的「丢弃」变成静默无效。
    if (edited is not None) or (old_has_sync and body.realign):
        song.lyrics_sync = _serialize_for_store(model_lines, with_words=True)
    else:
        # 无旧轴可保 / 用户明确要丢弃 → 清空（不落「全是 0 的假时轴」）
        song.lyrics_sync = None
    db.commit()
    db.refresh(song)

    lines_out = _as_dicts(lrcx.sections_to_lines(song.lyrics_sync))
    return LyricsDocResp(
        song_id=song.id,
        title=song.title or "",
        duration=float(song.duration) if song.duration else None,
        lyric=song.lyric or "",
        lines=_lines_json(lines_out),
        has_timeline=not lrcx.is_sync_empty(song.lyrics_sync),
        word_count=_count_words(song.lyrics_sync),
        is_owner=True,
        can_edit=True,
    )


# ══════════════════════════════════════════════════════════════════════════════
# ⑤ 重对齐（独立端点：编辑器「改词后一键重排」按钮）
# ══════════════════════════════════════════════════════════════════════════════
@router.post("/{song_id}/lyrics/realign", response_model=LyricsDocResp)
def realign_lyrics(
    song_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """把**当前库内纯文本歌词**重新对齐到**当前库内时间轴**上并落库。

    与 `mode=text&realign=true` 的保存路径是同一条算法，单独开端点是为了让编辑器把
    「输入框还在改、但我只想重排时轴」与「保存我改的这版文字」分成两个动作 ——
    前者不要求用户先把输入框同步回 state。
    """
    rate_limit_generate(str(user.id))
    rate_limit_assist(request.client.host if request.client else "unknown")
    song = _load_owned(song_id, db, user)

    if lrcx.is_sync_empty(song.lyrics_sync):
        raise HTTPException(status_code=409, detail="本曲没有时间轴，无法重对齐（请先导入 LRC）")
    new_lines = lrcx.extract_plain_lines(song.lyric or "")
    if not new_lines:
        raise HTTPException(status_code=400, detail="歌词内容为空")

    model_lines = lrcx.realign_sync(
        song.lyrics_sync, new_lines, duration=float(song.duration) if song.duration else None
    )
    song.lyrics_sync = _serialize_for_store(model_lines, with_words=True)
    db.commit()
    db.refresh(song)

    lines = _as_dicts(lrcx.sections_to_lines(song.lyrics_sync))
    return LyricsDocResp(
        song_id=song.id,
        title=song.title or "",
        duration=float(song.duration) if song.duration else None,
        lyric=song.lyric or "",
        lines=_lines_json(lines),
        has_timeline=True,
        word_count=_count_words(song.lyrics_sync),
        is_owner=True,
        can_edit=True,
    )
