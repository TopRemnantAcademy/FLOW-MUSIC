"""ACE-Step 1.5 私有引擎 provider（本地 GPU 自托管，开源 MIT 可商用）。

来源与许可（2026-09-25 实证，非推测）：
- 仓库：CNB `chguanw/ace-step-1.5`（star 1，但为 ACE-Step 1.5 的完整本地部署工程）。
- 许可：仓库根 `LICENSE` 实读为 **MIT License, Copyright (c) 2026 ACEStudio**（宽松可商用）。
  ⚠ 注意 selection.DEFAULT_ENGINES 旧注释写的是「Apache-2.0 可商用」，此处以实测文件为准
  并已同步修正（见 selection.py 该条目的 notes）。代码与模型许可不同：模型权重按各自
  仓库许可（Modelscope/HuggingFace 分发），部署前须自行确认模型许可条款。
- 协议基线：`vendor/ace-step-1.5/acestep/api_server.py`（经 `cnb git get-archive` 实取核对）。
  本文件所有字段/状态码均可在上述源码定位，未凭记忆臆造。

对接的三个端点（api_server.py:
  · POST {base}/release_task   (api_server.py:2253) 提交 → {"data":{"task_id","status","queue_position"}}
  · POST {base}/query_result   (api_server.py:2467) 轮询 → {"data":[{"task_id","result","status","progress_text"}]}
  · GET  {base}/v1/audio?path= (api_server.py:2809) 回源音频（result[].file 是**服务端本地绝对路径**）
- 状态码（api_server.py:239 STATUS_MAP）：0=queued|running（进行中）/ 1=succeeded / 2=failed。
- result 字段是 **JSON 字符串**，parse 后为 list：{file, wave, status, prompt, lyrics,
  metas{bpm,duration,genres,keyscale,timesignature}} 等（api_server.py:2536-2563）。
- 鉴权：body 的 `ai_token` 或 `Authorization: Bearer <key>`；服务端 --api-key 留空则免鉴权
  （api_server.py:299 verify_token_from_request）。

与 MurekaProvider 的差异（重要）：
- ACE-Step 无云端 API，音频落在**自托管 GPU 机的本地磁盘**。我们的平台必须把它回源到
  对象存储（回源到 CDN）后才能真正供 APK/Web 播放 —— 故 get() 成功态会主动回源+上传，
  而不是像 Mureka 那样直接透传第三方 URL。
- 回源**不走** app.storage.s3.download_to_temp：该函数对 URL 做 SSRF guard，
  而 ACE-Step 服务与音频通常同机/内网（默认 http://127.0.0.1:8001），必然被 guard 拒绝。
  这里改走 provider 自己的直连下载（目标为我们自己部署的受控服务，非任意外部 URL）。

能力边界（诚实 fail-closed，见 base.MusicProvider.supported_tasks 契约）：
- 仅声明 generate / custom。cover 需要服务端本地音频路径（src_audio_path），而本平台持有的是
  远程 URL，缺少「远程音频上传进 ACE-Step 工作目录」这一步 → 不宣称支持，交由 501 报错。
- make_instrumental 用「空歌词 + 无人声 instruction」表达，而非宣称 vocals2bgm
  （后者对应 ACE-Step task_type="extract"，属音轨分离，本 provider 未接线）。
"""

import asyncio
import contextlib
import json
import logging
import os
import tempfile
from typing import Any

import httpx

from app.config import settings
from app.orchestrator.base import GenerateRequest, MusicProvider, SongResult

log = logging.getLogger(__name__)

# ACE-Step task_type 合法值（api_server.py:72-97 generate_instruction 逐项分支实测枚举）。
# 本 provider 只映射到其中已接线的两个。
_TASK_TEXT2MUSIC = "text2music"
_TASK_COVER = "cover"
_TASK_REPAINT = "repaint"
_TASK_EXTRACT = "extract"
_TASK_LEGO = "lego"
_TASK_COMPLETE = "complete"

# query_result 返回的状态码（api_server.py:239）。
_STATUS_RUNNING = 0
_STATUS_SUCCEEDED = 1
_STATUS_FAILED = 2

# 纯音乐 instruction（用于 make_instrumental；对应 ACE-Step 的 DEFAULT_DIT_INSTRUCTION 风格）。
_INSTRUMENTAL_INSTRUCTION = (
    "instrumental only, no vocals, no lyrics, instrumental music, no vocal section"
)

_ACE_AUDIO_SUFFIX = ".mp3"


class AceStepError(RuntimeError):
    """ACE-Step HTTP/网络层错误，带 status_code 便于上层分类。"""

    def __init__(self, status_code: int | None, message: str, remediation: str = ""):
        self.status_code = status_code
        self.remediation = remediation
        super().__init__(message)

    def __str__(self) -> str:
        extra = f" | 处置: {self.remediation}" if self.remediation else ""
        return f"[AceStepError status={self.status_code}] {super().__str__()}{extra}"


def _classify(status_code: int | None, body_text: str) -> tuple[str, str]:
    """把 ACE-Step 的状态码/错误体转成可执行的处置建议（不臆造原因）。"""
    if status_code == 401:
        return (
            "ACE-Step 鉴权失败(401)：请检查 ACE_STEP_API_KEY 是否与自托管服务 --api-key 一致",
            "核对 ACE_STEP_API_KEY 与服务端启动参数",
        )
    if status_code == 403:
        return (
            "ACE-Step 拒绝访问(403)：常见于 --api-key 已设置但请求未带 token",
            "在服务端启动时确认 ACESTEP_API_KEY 后重启，或在请求中带 ai_token",
        )
    if status_code == 429:
        return (
            "ACE-Step 队列已满(429)：本地 GPU 服务拒收新任务",
            "调高引擎并发上限或错峰；ace_step_max_concurrent 不宜超过 GPU 能力",
        )
    if status_code == 422 or status_code == 400:
        return (
            f"ACE-Step 参数错误({status_code})：{body_text[:200]}",
            "核对 model / task_type / 字段约束（见 vendor/ace-step-1.5/acestep/api_server.py:457）",
        )
    if status_code is not None and status_code >= 500:
        return (
            f"ACE-Step 服务端错误({status_code})：{body_text[:200]}",
            "检查 GPU 显存/模型加载；turbo 模型约需 8GB，base 需更多",
        )
    return (
        f"ACE-Step 请求失败：{body_text[:200]}",
        "确认 ace_step_api_base 指向的 ACE-Step 服务已启动（GET /health）",
    )


class AceStepPrivateProvider(MusicProvider):
    """ACE-Step 1.5 私有引擎。

    create() 提交任务返回 task_id；get() 轮询并把音频回源+上传对象存储。
    """

    # 只声明真正会分流的 task；其余交由 songs.py 的 _require_capability 返回 501。
    supported_tasks: frozenset[str] = frozenset({"generate", "custom"})

    def __init__(self):
        self.base = (settings.ace_step_api_base or "http://127.0.0.1:8001").rstrip("/")
        self.api_key = settings.ace_step_api_key
        self.model = settings.ace_step_model or "acestep-v15-turbo"
        self._sem = asyncio.Semaphore(max(1, int(settings.ace_step_max_concurrent or 1)))
        self._timeout = httpx.Timeout(connect=10, read=600, write=10, pool=10)

    # ---- 底层 HTTP ----
    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    @staticmethod
    def _describe(resp: httpx.Response) -> tuple[str, str]:
        try:
            data = resp.json()
        except Exception:
            data = None
        if isinstance(data, dict):
            for k in ("detail", "message", "error", "msg"):
                v = data.get(k)
                if isinstance(v, str) and v.strip():
                    return v.strip(), ""
                if isinstance(v, dict):
                    inner = v.get("message") or v.get("detail")
                    if isinstance(inner, str) and inner.strip():
                        return inner.strip(), ""
            err = data.get("error")
            if isinstance(err, str) and err.strip():
                return err.strip(), ""
        return (resp.text or "")[:300], ""

    def _raise_for_status(self, resp: httpx.Response, action: str) -> None:
        if resp.status_code < 400:
            return
        message, remediation = _classify(resp.status_code, self._describe(resp)[0])
        raise AceStepError(resp.status_code, f"{action} 失败：{message}", remediation)

    async def _post(self, path: str, payload: dict) -> dict:
        last: httpx.Response | None = None
        for attempt in range(3):
            async with self._sem, httpx.AsyncClient(timeout=self._timeout) as c:
                last = await c.post(f"{self.base}{path}", headers=self._headers(), json=payload)
            if last.status_code < 400:
                body = last.json()
                # 统一剥掉 _wrap_response 之外层，返回 data（兼容未来剥离包裹的变更）。
                if isinstance(body, dict) and "data" in body:
                    d = body["data"]
                    if isinstance(d, (dict, list)):
                        return d
                return body if isinstance(body, dict) else {}
            # 仅 429/5xx 退避重试；401/403/4xx 参数类失败重试无意义。
            if last.status_code not in (429, 500, 502, 503, 504) or attempt == 2:
                self._raise_for_status(last, f"POST {path}")
            await asyncio.sleep(min(2**attempt, 10))
        # 循环耗尽（仅可能发生在全部为可重试状态码时）：last 必非 None。
        assert last is not None
        self._raise_for_status(last, f"POST {path}")

    # ---- 请求体构造 ----
    @staticmethod
    def _build_payload(req: GenerateRequest) -> dict:
        """把平台统一的 GenerateRequest 映射到 ACE-Step GenerateMusicRequest 字段。

        字段语义对齐 api_server.py:457-535（GenerateMusicRequest）：
        prompt=自由文本描述，lyrics=歌词，task_type，vocal_language，model，bpm/key_scale/
        time_signature/audio_duration 等元数据（README 明确支持 50+ 语言与结构化控制）。
        """
        payload: dict[str, Any] = {
            "prompt": (req.prompt or "").strip(),
            "lyrics": (req.lyric or "").strip(),
            "task_type": _TASK_TEXT2MUSIC,
            "vocal_language": "en",
            "model": req.model_version or "",
            "audio_format": "mp3",
            "inference_steps": 8,
            "guidance_scale": 7.0,
        }
        # 让服务端自己选模型（走 settings 默认），避免把 settings 值漏成空串。
        if not payload["model"]:
            payload.pop("model")

        if req.make_instrumental:
            # 纯音乐：清空歌词并改用无人声 instruction（不宣称 vocals2bgm，见文件头）。
            payload["lyrics"] = ""
            payload["instruction"] = _INSTRUMENTAL_INSTRUCTION

        # 结构化控制：仅在用户显式给了值时下发，避免把 None 变成 "None" 字符串。
        if req.style:
            style_hint = str(req.style).strip()
            if style_hint:
                payload["prompt"] = f"{style_hint} {payload['prompt']}".strip()
        if req.title:
            payload["track_name"] = str(req.title).strip()
        if req.vocal_gender:
            payload["vocal_language"] = "en"  # ACE-Step 无 gender 字段；声线由 prompt 描述承载
        return payload

    # ---- MusicProvider 契约 ----
    async def create(self, req: GenerateRequest) -> SongResult:
        payload = self._build_payload(req)
        data = await self._post("/release_task", payload)
        task_id = str(data.get("task_id") or "").strip()
        if not task_id:
            raise AceStepError(None, f"ACE-Step /release_task 未返回 task_id，响应: {data}")
        return SongResult(
            external_id=task_id,
            custom_id=task_id,
            status="processing",
            title=req.title,
            lyric=req.lyric,
            model_version=payload.get("model") or self.model,
        )

    async def get(self, external_id: str) -> SongResult:
        data = await self._post("/query_result", {"task_id_list": [external_id]})
        if not isinstance(data, list) or not data:
            raise AceStepError(None, f"ACE-Step /query_result 返回异常: {data}")
        item = data[0] if isinstance(data[0], dict) else {}
        status = int(item.get("status", _STATUS_RUNNING))
        if status == _STATUS_RUNNING:
            return SongResult(external_id=external_id, status="processing")
        if status == _STATUS_FAILED:
            raw = item.get("result")
            note = ""
            try:
                parsed = json.loads(raw) if isinstance(raw, str) else raw
                if isinstance(parsed, list) and parsed and isinstance(parsed[0], dict):
                    note = str(parsed[0].get("error") or "")
                    err = parsed[0].get("status_message")
                    if err:
                        note = note or str(err)
            except Exception:
                # 解析失败不掩盖 ACE-Step 的原始 failed 语义，降级为通用提示。
                log.debug("ACE-Step failed task 的 result 无法解析: task=%s", external_id)
            return SongResult(
                external_id=external_id,
                status="failed",
                moderation_note=note or f"ACE-Step 生成失败（task={external_id}）",
            )

        # succeeded → 回源第一个音频文件
        return await self._resolve_completed(external_id, item)

    async def _resolve_completed(self, external_id: str, item: dict) -> SongResult:
        raw = item.get("result")
        try:
            parsed = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            parsed = None
        files: list[str] = []
        metas: dict = {}
        if isinstance(parsed, list) and parsed:
            first = parsed[0] if isinstance(parsed[0], dict) else {}
            f = first.get("file")
            if isinstance(f, str) and f.strip():
                files.append(f.strip())
            metas = first.get("metas") if isinstance(first.get("metas"), dict) else {}
        elif isinstance(parsed, dict):
            f = parsed.get("file")
            if isinstance(f, str) and f.strip():
                files.append(f.strip())
            metas = parsed.get("metas") if isinstance(parsed.get("metas"), dict) else {}

        lyric_out = None
        title_out = None
        if isinstance(parsed, list) and parsed and isinstance(parsed[0], dict):
            lyric_out = parsed[0].get("lyrics") or None
            title_out = parsed[0].get("track_name") or None

        if not files:
            return SongResult(
                external_id=external_id,
                status="failed",
                moderation_note=f"ACE-Step 已完成但未提供音频文件（task={external_id}）",
            )

        audio_url = await self._fetch_and_publish(files[0], external_id)
        duration = metas.get("duration")
        try:
            duration = int(float(duration)) if duration else None
        except (TypeError, ValueError):
            duration = None
        return SongResult(
            external_id=external_id,
            custom_id=external_id,
            status="completed",
            audio_url=audio_url,
            lyric=lyric_out,
            title=title_out,
            model_version=self.model,
            duration=duration,
        )

    # ---- 音频回源与发布 ----
    async def _fetch_and_publish(self, server_path: str, task_id: str) -> str:
        """把 ACE-Step 本地音频经 GET /v1/audio 拉回，上传对象存储并返回可播放 URL。

        [SEC] 不复用 app.storage.s3.download_to_temp：它对 URL 做 SSRF guard，而本服务
        默认 http://127.0.0.1:8001（同机/内网）会被直接拒绝。目标是我们自托管的受控
        服务，非外部任意 URL，风险面明确。若将来改为公网部署，须在此重新接入 guard。
        """
        url = f"{self.base}/v1/audio"
        async with httpx.AsyncClient(timeout=self._timeout) as c:
            resp = await c.get(url, params={"path": server_path}, headers=self._headers())
        if resp.status_code >= 400:
            message, remediation = _classify(resp.status_code, self._describe(resp)[0])
            raise AceStepError(resp.status_code, f"音频回源失败：{message}", remediation)
        data = resp.content
        if not data:
            raise AceStepError(None, f"ACE-Step 音频回源为空（task={task_id}, path={server_path}）")

        from app.storage.s3 import is_configured, upload_bytes

        if not is_configured():
            raise AceStepError(
                None,
                "ACE-Step 引擎产出音频完成，但对象存储未配置，无法生成可播放 URL",
                "配置 storage_endpoint/storage_bucket/storage_access_key/storage_secret_key "
                "（音频必须在回源到 CDN 后才能被 APK 播放）",
            )
        suffix = os.path.splitext(server_path)[1] or _ACE_AUDIO_SUFFIX
        fd, tmp = tempfile.mkstemp(suffix=suffix)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            key = f"ace_step/{task_id}{suffix}"
            return upload_bytes(key, data, content_type="audio/mpeg")
        finally:
            # 无论上传成败都清理临时文件（上传成功时对象存储已持有副本）。
            with contextlib.suppress(OSError):
                os.unlink(tmp)
