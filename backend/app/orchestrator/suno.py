"""Route A：Suno 商业 API 封装。

⚠️ 重要取舍：商业 API 有内容政策（宗教/仇恨/敏感题材会被拦截），
这是选择 Route A 时接受的约束。要彻底无限制，请实现 Route B（见 base.py 与 PROMPT.md）。

协议基线（2026-08-27 调研）：以 **open.suno.cn（SUNO API 平台）** 为准
（文档最完整、明确支持 extend/cover/remix，目标用户为中国客户）。
详见 https://open.suno.cn/api-guide
- 鉴权：Authorization: Bearer <access_key>
- 生成：POST {base}/music/generate  → { data: { task_ids: [int, ...] } }
- 轮询：GET  {base}/music/task?id=<task_id> → { data: { status, result: {...} } }
- 延长：task="extend" + continue_clip_id（=父歌 custom_id）
- 翻唱：task="cover"  + cover_clip_id（remix 暂用 cover 近似）
- 任务状态：pending / processing / completed / failed
- 音频：result.fileInfo.mp3Url；封面：result.fileInfo.cosUrl；时长：result.fileInfo.duration
- 后续操作（延长/翻唱）需用 result.custom_id（UUID），而非轮询用的 task_id（数字）

其余商业服务（sunoapi.org / gcui-art/suno-api）端点与字段不同：
sunoapi.org 用 POST /generate + model=V4_5ALL + customMode 布尔、响应 {data:{taskId}}；
gcui-art/suno-api 用 POST /api/generate|/api/custom_generate、响应 AudioInfo[]。
下方 create/get 已对常见响应字段做容错解析，但接入它们仍需按文档调整端点与字段。
"""

import asyncio

import httpx

from app.config import settings
from app.orchestrator.base import GenerateRequest, MusicProvider, SongResult


class SunoProvider(MusicProvider):
    # create()(L71) 按 req.task 真实分流：generate/custom/extend/cover/remix，
    # 且 extend/cover 会带上 continue_clip_id / cover_clip_id → 父子歌真实关联。
    # 故这些 task 均为「真支持」，songs.py 的 /extend /cover /remix 正常放行。
    supported_tasks: frozenset[str] = frozenset({"generate", "custom", "extend", "cover", "remix"})

    def __init__(self):
        self.base = settings.suno_api_base.rstrip("/")
        self.api_key = settings.suno_api_key
        self.email = settings.suno_email
        self.password = settings.suno_password
        self._token: str | None = None
        self._timeout = httpx.Timeout(connect=10, read=180, write=10, pool=10)

    # —— 鉴权：优先 Bearer key；无 key 时尝试 email/password 换 token（gcui-art 风格）——
    async def _auth_header(self) -> dict:
        if self.api_key:
            return {"Authorization": f"Bearer {self.api_key}"}
        if not self._token and self.email and self.password:
            async with httpx.AsyncClient(timeout=self._timeout) as c:
                r = await c.post(
                    f"{self.base}/auth/token",
                    json={"email": self.email, "password": self.password},
                )
                r.raise_for_status()
                body = r.json()
            self._token = body.get("accessToken") or body.get("token") or body.get("cookie")
        return {"Authorization": f"Bearer {self._token}"} if self._token else {}

    async def _request(self, method: str, path: str, **kw) -> dict:
        """带超时、限流(429)/5xx 重试与 401 token 刷新的请求封装。"""
        for attempt in range(4):
            headers = await self._auth_header()
            async with httpx.AsyncClient(timeout=self._timeout) as c:
                r = await c.request(method, f"{self.base}{path}", headers=headers, **kw)
            if r.status_code == 401 and self.email and self.password and not self.api_key:
                self._token = None  # 凭证失效，刷新后重试
                continue
            if r.status_code == 429 or r.status_code >= 500:
                await asyncio.sleep(min(2**attempt, 30))
                continue
            r.raise_for_status()
            return r.json()
        raise RuntimeError(f"Suno request failed after retries: {method} {path}")

    async def create(self, req: GenerateRequest) -> SongResult:
        payload: dict = {
            "make_instrumental": req.make_instrumental,
            "mv": req.model_version or "chirp-v4",
        }
        if req.persona_id:
            payload["personaId"] = req.persona_id
        if req.task == "generate":
            if req.lyric:
                payload["prompt"] = req.lyric
                payload["tags"] = req.style or ""
                payload["title"] = req.title or "未命名"
            else:
                payload["gpt_description_prompt"] = req.prompt or req.title or "一首歌"
                payload["title"] = req.title or "未命名"
        elif req.task == "custom":
            payload["prompt"] = req.lyric or req.prompt or ""
            payload["tags"] = req.style or ""
            payload["title"] = req.title or "未命名"
        elif req.task == "extend":
            payload["task"] = "extend"
            payload["continue_clip_id"] = req.continue_song_id
            payload["title"] = req.title or "续写"
            if req.clip_seconds:
                payload["continueAt"] = req.clip_seconds
        elif req.task in ("cover", "remix"):
            # open.suno.cn 无独立 remix 端点，用 cover（新风格翻唱）近似
            payload["task"] = "cover"
            payload["cover_clip_id"] = req.continue_song_id
            payload["tags"] = req.style or ""
            payload["title"] = req.title or ("Remix" if req.task == "remix" else "翻唱")

        # vocalGender 仅生成类模式生效（cover/remix 为 audio-to-audio 翻唱，保留原声线，不传）。
        # 注：基线 open.suno.cn 的 generate/extend 文档未列出 vocalGender/continueAt；这两字段为
        # sunoapi.org 风格参数，接 open.suno.cn 时可能被忽略（无害），接 sunoapi.org 时生效。不臆测基线行为。
        if req.vocal_gender and req.task in ("generate", "custom", "extend"):
            vmap = {"male": "m", "female": "f"}
            payload["vocalGender"] = vmap.get(str(req.vocal_gender).lower(), req.vocal_gender)

        body = await self._request("POST", "/music/generate", json=payload)
        # 响应：open.suno.cn { data: { task_ids: [int, ...] } }（Suno 每次生成 2 首）
        task_ids = (body.get("data") or {}).get("task_ids") or []
        if not task_ids:
            # 兼容 sunoapi.org: { data: { taskId } }
            tid = (body.get("data") or {}).get("taskId") or body.get("taskId")
            task_ids = [tid] if tid else []
        if not task_ids:
            raise RuntimeError(f"Suno create 未返回 task_id，响应: {body}")
        # 仅轮询第一首（MVP）；其 custom_id 将作为后续 extend/cover 的基准
        return SongResult(external_id=str(task_ids[0]), status="processing")

    async def get(self, external_id: str) -> SongResult:
        body = await self._request("GET", f"/music/task?id={external_id}")
        data = body.get("data") or {}
        status_raw = str(data.get("status", "processing")).lower()
        if status_raw == "completed":
            status = "completed"
        elif status_raw == "failed":
            status = "failed"
        else:
            status = "processing"

        result = data.get("result") or {}
        file_info = result.get("fileInfo") or {}
        audio_url = file_info.get("mp3Url") or result.get("audio_url") or result.get("audioUrl")
        cover_url = (
            file_info.get("cosUrl")
            or result.get("image_url")
            or result.get("imageUrl")
            or result.get("cover_url")
        )
        lyric = result.get("lyric") or result.get("lyrics")
        title = result.get("title")
        model_version = result.get("mv") or result.get("model_name") or result.get("modelVersion")
        duration = (
            file_info.get("duration") or result.get("duration") or result.get("durationSeconds")
        )
        custom_id = result.get("custom_id") or result.get("id")
        return SongResult(
            external_id=external_id,
            custom_id=custom_id,
            status=status,
            audio_url=audio_url,
            cover_url=cover_url,
            lyric=lyric,
            title=title,
            model_version=model_version,
            duration=duration,
        )

    # —— 后期处理 / 衍生（对齐 open.suno.cn 真实端点，非生成主链路）——
    # 这些方法不是 MusicProvider 抽象契约（create/get 才是），由各供应商自行实现；
    # MockProvider 已加对应桩，避免测试时 AttributeError。
    async def _submit_task(self, path: str, payload: dict) -> str:
        """提交后期处理任务，返回 task_id（数字字符串）；复用 get() 轮询结果。"""
        body = await self._request("POST", path, json=payload)
        task_ids = (body.get("data") or {}).get("task_ids") or []
        if not task_ids:
            tid = (body.get("data") or {}).get("taskId") or body.get("taskId")
            task_ids = [tid] if tid else []
        if not task_ids:
            raise RuntimeError(f"Suno {path} 未返回 task_id，响应: {body}")
        return str(task_ids[0])

    async def crop(self, clip_id: str, start_time: int, end_time: int) -> "SongResult":
        task_id = await self._submit_task(
            "/music/crop", {"clip_id": clip_id, "start_time": start_time, "end_time": end_time}
        )
        return await self.get(task_id)

    async def speed(self, clip_id: str, speed: float) -> "SongResult":
        task_id = await self._submit_task("/music/speed", {"clip_id": clip_id, "speed": speed})
        return await self.get(task_id)

    async def whole_song(self, clip_id: str) -> "SongResult":
        task_id = await self._submit_task("/music/whole-song", {"clip_id": clip_id})
        return await self.get(task_id)

    async def aligned_lyrics(self, lyrics: str, suno_id: str) -> "SongResult":
        task_id = await self._submit_task(
            "/music/aligned-lyrics", {"lyrics": lyrics, "suno_id": suno_id}
        )
        return await self.get(task_id)

    async def upload(self, audio_url: str) -> str:
        """上传参考音频，返回 custom_id（供 extend/cover 使用）。"""
        body = await self._request("POST", "/music/upload", json={"audio_url": audio_url})
        custom_id = (body.get("data") or {}).get("custom_id") or body.get("custom_id")
        if not custom_id:
            raise RuntimeError(f"Suno upload 未返回 custom_id，响应: {body}")
        return custom_id

    async def sound(
        self,
        title: str,
        tags: str,
        mv: str = "chirp-crow",
        tempo: int | None = None,
        key: str | None = None,
        loop: bool = False,
    ) -> "SongResult":
        payload: dict = {"title": title, "tags": tags, "mv": mv, "loop": loop}
        if tempo is not None:
            payload["tempo"] = tempo
        if key is not None:
            payload["key"] = key
        task_id = await self._submit_task("/music/sound", payload)
        return await self.get(task_id)
