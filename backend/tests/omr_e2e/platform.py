"""平台闭环桥：把 OMR 流水线的成品注册进 FLOW-MUSIC 后端。

**复用既有基础设施，不另起炉灶**（issue #122 铁律 4）：
  - `tests/conftest.py` 的 session 级 `TestClient` + `MockProvider` 注入；
  - 生成走 `app.routers.songs._process_song`（与生产 BackgroundTasks 同一个函数，
    见 `tests/test_api.py::_drive` 的同一手法）；
  - 音频用「手工搭一个已经跑通的生成结果」写回 DB —— 因为 WAV 在临时目录里，
    没有可供 `httpx` 拉取的 URL。

为什么是「播种」而不是直接 POST /generate：
  `POST /generate` 会去拉 `result.audio_url` 做 CDN 回源 / 试听片段（都要网络），
  而沙箱里既没有网络、也不该去网络。所以这里把 **MockProvider 的产物字段
  （external_id='mock-task-0001' / custom_id / model_version / duration）**
  原样写进 Song，再从 `/api/songs/explore` 匿名取回 —— 平台侧那条链
  （写库 → 审核态 → 公开流 → CDN 出口选择）全部真实跑过，**一次网络都不发**。

这条桥的边界写在这里，省得后来者以为「explore 拿到了就是全链路通了」：
  · 真正被验证的是「成品 → Song 行 → 审核态 → 公开流可匿名取到」；
  · 没被验证的是「HTTP 上的生成提交」（那需要真实/可拉取的音频 URL）。
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from fastapi.testclient import TestClient

from app.routers.songs import _process_song  # noqa: F401  （保持与 tests/test_api.py 同一驱动口径）

#: MockProvider 的固定产物值 —— 只引用、不臆造（见 app/orchestrator/mock.py）。
MOCK_TASK_ID = "mock-task-0001"
MOCK_CUSTOM_ID = "mock-custom-0001"
MOCK_MODEL_VERSION = "chirp-v4.5"
#: mock 里的上传替身：内网地址，SSRF 守卫**必然**拒掉 → 证明「外面拉不到也不影响注册」。
MOCK_AUDIO_URL = "http://127.0.0.1:9/mock-master.wav"
#: 上游 CDN 替身（.com 可解析但不连通）→ 给「CDN 优先出口」那条断言一个可用输入。
MOCK_CDN_URL = "https://cdn.mock.example.com/songs/master.wav"


class ClosedLoop:
    """一次「注册 + 取证」的调用封装。返回的东西足够贴进 issue 评论。"""

    def __init__(self, client: TestClient, headers: dict[str, str] | None = None):
        self.client = client
        self.headers = headers

    # ---- 账号 ----
    def register(self, email: str, password: str = "pw123456") -> dict[str, str]:
        r = self.client.post("/api/auth/register", json={"email": email, "password": password})
        assert r.status_code == 200, f"注册失败：{r.status_code} {r.text}"
        self.headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
        return self.headers

    def subscribe(self, tier: str = "standard") -> None:
        """升级到订阅档 —— 让整曲链接能被下发（未订阅只会拿到试听片段）。

        用测试开关 `enable_mock_subscribe`（conftest 已显式打开），
        不走真实支付，也不需要任何密钥（gate 的 fail-closed 依然生效）。
        """
        assert self.headers, "先 register"
        r = self.client.post("/api/auth/subscribe", json={"tier_key": tier}, headers=self.headers)
        assert r.status_code == 200, f"订阅失败：{r.status_code} {r.text}"

    # ---- 生成提交（走平台自己的端点） ----
    def generate(self, *, title: str, lyric: str, style: str, prompt: str, is_public: bool = True) -> int:
        assert self.headers, "先 register"
        r = self.client.post(
            "/api/songs/generate",
            json={
                "title": title,
                "lyric": lyric,
                "style": style,
                "prompt": prompt,
                "is_public": is_public,
                "model_version": MOCK_MODEL_VERSION,
            },
            headers=self.headers,
        )
        assert r.status_code == 200, f"提交生成失败：{r.status_code} {r.text}"
        return r.json()["song_id"]

    # ---- 成品播种（把 OMR 流水线的 master 结果落成 Song 行） ----
    def register_master(
        self,
        result: Any,
        *,
        title: str,
        lyric: str,
        style: str = "OMR pipeline / mock",
        prompt: str = "乐谱→音乐 mock 全环节",
        duration: int | None = None,
        is_public: bool = True,
        audio_url: str = MOCK_AUDIO_URL,
    ) -> dict:
        """把一个 `PipelineResult` 的成品写成一个已完成的 Song，返回取证信息。

        刻意**不使用** `client.post("/generate")`：那条路要拉音频、要网络。
        这里直接构造「生成已完成」的状态，再让平台自己的读路径去取它。
        """
        from app.db import SessionLocal
        from app.models import Song, User

        master = result.artifacts.get("master_wav")
        assert master and os.path.exists(master), "流水线没有产出 master WAV，不能注册"

        db = SessionLocal()
        try:
            user = db.query(User).order_by(User.id.asc()).first()
            assert user is not None, "需要先注册用户"
            song = Song(
                user_id=user.id,
                title=title,
                style=style,
                prompt=prompt,
                lyric=lyric,
                status="completed",
                external_id=MOCK_TASK_ID,
                custom_id=MOCK_CUSTOM_ID,
                audio_url=audio_url,
                cover_url="",
                model_version=MOCK_MODEL_VERSION,
                duration=duration if duration is not None else int(round(_wav_seconds(master))),
                task_type="generate",
                is_public=is_public,
                moderation_status="approved",  # 与 _finalize_song 的 E4 自动流转同语义
                engine_name="mock",
            )
            db.add(song)
            db.commit()
            db.refresh(song)
            return {
                "song_id": song.id,
                "title": song.title,
                "status": song.status,
                "moderation_status": song.moderation_status,
                "is_public": song.is_public,
                "duration": song.duration,
                "external_id": song.external_id,
                "custom_id": song.custom_id,
                "master_wav": master,
                "master_sha256": _sha256(master),
                "master_seconds": round(_wav_seconds(master), 3),
            }
        finally:
            db.close()

    # ---- 取证 ----
    def explore(self, *, q: str | None = None) -> list[dict]:
        params = {"q": q} if q else None
        r = self.client.get("/api/songs/explore", params=params)  # 匿名（不带 headers）
        assert r.status_code == 200, f"explore 失败：{r.status_code} {r.text}"
        return r.json()

    def find_in_explore(self, song_id: int) -> dict | None:
        for row in self.explore():
            if row["id"] == song_id:
                return row
        return None


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------
def _wav_seconds(path: str) -> float:
    import wave

    with wave.open(path, "rb") as w:
        return w.getnframes() / float(w.getframerate())


def _sha256(path: str) -> str:
    import hashlib

    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def drive(song_id: int) -> None:
    """显式驱动后台处理（与生产 BackgroundTasks 同一个函数）。"""
    asyncio.run(_process_song(song_id))
