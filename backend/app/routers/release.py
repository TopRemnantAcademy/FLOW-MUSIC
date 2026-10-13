"""应用内更新日志（GAP-040）：把「当前版本 / 是否有新版本 / 要点」下发给客户端。

设计取舍 —— 这里**故意**不读数据库、不加模型：

- 更新日志是**发布产物**，不是运行时数据。它随代码一起评审、一起上线；
  放进 DB 只会多出一张表、一套迁移、一个「运营改完没人知道」的漂移面。
- 真相只有两处：根 `CHANGELOG.md`（人读）与 `frontend/package.json` 的 `version`
  （版本号）。本模块是把它们投射成机器可读结构的那一层，见 `docs/RELEASE_PROCESS.md`。

⚠ 因此本模块下的 `CURRENT_VERSION` / `LATEST_VERSION` **必须与 `package.json` 同步**,
由 `tests/test_release_notes.py` 守着(不同步即红),不靠自觉。
"""

from __future__ import annotations

import re
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/api/release", tags=["release"])

# 服务端认为的当前发布版本 —— 与 frontend/package.json 的 version 一致。
CURRENT_VERSION = "0.2.0"

# 面向用户的要点（摘要，完整版见根 CHANGELOG.md）。
# 每条都来自真实提交/PR；空 ref 表示「待补」，不允许编。
RELEASE_NOTES: list[dict] = [
    {
        "version": "0.2.0",
        "date": "2026-10-12",
        "entries": [
            {"kind": "新增", "text": "搜索与发现：标签分类浏览、相似作品推荐、趋势榜", "ref": "#82"},
            {"kind": "新增", "text": "下载可选 mp3 / wav / flac，无无损源时如实提示", "ref": "#105"},
            {"kind": "新增", "text": "播放列表、文件夹、批量操作与回收站", "ref": "#108"},
            {"kind": "新增", "text": "邮箱验证、密码重置、两步验证与登录失败锁定", "ref": "#70"},
            {"kind": "新增", "text": "支持 Google / Kakao / Naver 账号登录", "ref": "#99"},
            {"kind": "新增", "text": "作品授权类型标注与内容审核队列", "ref": "#84"},
            {"kind": "新增", "text": "音频优先走 CDN 分发", "ref": "#87"},
            {"kind": "新增", "text": "应用内更新日志", "ref": "#117"},
            {"kind": "变更", "text": "后端支持 PostgreSQL，迁移可在真实 PG 从零建表", "ref": "#106"},
            {"kind": "变更", "text": "续写歌曲可选择从开头或结尾衔接", "ref": "fd463fe"},
            {"kind": "修复", "text": "修复迁移链冲突导致全新环境部署失败", "ref": "#104"},
            {"kind": "安全", "text": "登录凭证改为 HttpOnly Cookie 保存", "ref": "d003c81"},
        ],
    },
]

_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


class ReleaseNotesOut(BaseModel):
    app_version: str
    latest_version: Optional[str] = None
    has_update: bool = False
    channel: str = "stable"
    published_at: Optional[str] = None
    notes: list[str] = []


def _parse(v: Optional[str]) -> Optional[tuple[int, int, int]]:
    """严格解析 `x.y.z`。解析不了返回 None —— **不兜底猜**。

    上游/配置里出现过 `1.0`、`v1.0`、`1.0.0-rc1` 这类写法；任何「宽容解析」
    都会把不兼容的版本判成「有更新」，触发用户白跑一次刷新。
    """
    m = _VERSION_RE.match((v or "").strip())
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)))


def _latest_published() -> Optional[dict]:
    return RELEASE_NOTES[0] if RELEASE_NOTES else None


@router.get("/notes", response_model=ReleaseNotesOut)
def release_notes(app_version: Optional[str] = None) -> ReleaseNotesOut:
    """当前版本要点 + 是否有新版本。

    `app_version` 由调用方传入（客户端已安装的版本）。缺省时用服务端的当前版本，
    此时 `has_update` 恒为 false —— 没有可比对象就不该报「有更新」。
    """
    latest = _latest_published()
    latest_version: Optional[str] = latest["version"] if latest else CURRENT_VERSION

    installed = _parse(app_version) if app_version else None
    target = _parse(latest_version)
    # 两侧都能解析且客户端更旧，才算「有更新」。相等/更新/无法解析 → False。
    has_update = bool(installed and target and target > installed)

    notes = [
        f"{e['kind']}：{e['text']}" + (f"（{e['ref']}）" if e.get("ref") else "")
        for e in (latest["entries"] if latest else [])
    ]

    return ReleaseNotesOut(
        app_version=app_version or CURRENT_VERSION,
        latest_version=latest_version,
        has_update=has_update,
        channel="stable",
        published_at=latest["date"] if latest else None,
        notes=notes,
    )
