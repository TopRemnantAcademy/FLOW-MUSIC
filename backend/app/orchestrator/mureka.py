"""Route A：Mureka 商业 API 封装（昆仑万维 Skywork AI）。

官方协议（2026-09-03 全量核对 platform.mureka.ai/docs：全部 28 个 API 操作 + 5 个指南）：
- 服务器：https://api.mureka.ai  （quickstart 确认；config.mureka_api_base=.../v1 对齐）
- 鉴权：Authorization: Bearer <MUREKA_API_KEY>  （quickstart 确认；错误包体 {"error":{"message":...},"trace_id":...}）
- 本封装实际调用：
  · POST {base}/song/generate  （歌词生曲，USED）
      请求体：lyrics(必填,≤5000), model(auto/mureka-7.6/mureka-o2/mureka-8/mureka-9/mureka-9.5),
              n(默认2最大3,按首计费), prompt(≤1024,自由文本风格), gender(female/male),
              reference_id, vocal_id, melody_id, stream
      响应(200)：{ id, created_at, finished_at, model, status, failed_reason, choices[] }
  · GET {base}/song/query/{task_id}  （轮询，USED）
  · GET {base}/account/billing  （get_billing()，查余额/并发上限，见下）
- ⚠️ choices[] 子字段(audio_url/cover_url/lyrics/title/duration/id)的真实 schema：
  经核对 generate/query/easy-generate/extend/remix/region-edit/vocal-clone/stem/instrumental
  共 9 个文档，全部仅标 "object[]" 而**不展开** → 官方文档处处未公开子字段名，
  只能靠**账号充值后实机成功调用**校准。本文件以多候选键容错解析（见 get()）。
- 风格控制：generate 的 `prompt` 是**自由文本**（≤1024），本封装**原样透传**用户填写的
  style / vocal_gender / prompt，**不做任何曲风注入**（2026-09-10 起）。
  ⚠️ 2026-09-10 이전엔 모든 요청에 "gospel" 을 강제 선두 주입했으나,
     세속 주제까지 복음 곡이 되는 정위 모순으로 **제거**됨(见 _build_prompt 주석).
  ⚠️ 注意 easy-generate(Prompt to song) 才有结构化 styles:["gospel"] 枚举，但它**无 lyrics 字段**；
  本产品是用户歌词生曲 → 用 generate + 自由文本 prompt 是正确的（非遗漏）。
- 相邻能力（均已按官方协议核实并**已实现**，见下方各方法；除 transcribe/describe 外全部接线）：
  easy-generate(prompt→song, L633)、song/extend(L382)、song/remix+cover(L417)、
  song/vocal-clone(vocal_id 源, L550 — 产品暂停, `settings.enable_vocal_clone` 默认 False→501 fail-closed)、
  instrumental/generate+独立 query(L611/L451)、lyrics/generate(L667)、song/stem 分轨(L567)、
  song/region-edit 改词(L582)、files/upload(L514)。
  ⚠️ 仍未实现（官方有端点但本产品未接线）：transcribe、describe；
  超出音乐平台范围（其他产品线）：soundtrack/track/video/tts、files/upload 分块版(uploads-*)。
- 无官方 webhook/CallbackURL → 走轮询（同 Suno）；回调优势仅火山 GenBGM 享有。

实现要点：
- 对齐 MusicProvider 契约：create() 提交并返回 SongResult(external_id=task_id, status="processing")；
  get() 轮询并映射状态/音频/封面/歌词/时长/模型版本。
- 并发：asyncio.Semaphore(mureka_max_concurrent) 约束提交并发，避免入门档(1并发)被 429。
- 容错解析：choices 字段名在不同版本可能微调，做多候选键容错。
- n>1（多首）暂取 choices[0] 映射单条 SongResult；多首落盘见 P1.5（需多 Song 行）。
"""

import asyncio
import json
import logging
from typing import NoReturn

import httpx

from app.config import settings
from app.orchestrator.base import GenerateRequest, MusicProvider, SongResult

log = logging.getLogger(__name__)


class MurekaError(RuntimeError):
    """Mureka HTTP/网络层错误，携带 status_code 与 kind 便于上层分类处理。

    继承 RuntimeError：既保持与既有 `except RuntimeError` 分支的向后兼容
    （如 admin 账单健康检查、billing 路由），又可被 `isinstance(e, MurekaError)`
    精确识别，取出 .kind 区分「地域封锁 / 认证失败 / 参数错误 / 限流 / 服务端」。

    kind 取值：auth | region | param | rate_limit | server | network | unknown
    """

    def __init__(
        self,
        status_code: int | None,
        kind: str,
        message: str,
        remediation: str = "",
    ):
        self.status_code = status_code
        self.kind = kind
        #: 可直接展示给运维/用户的处置建议（如 403 → 「中国香港部署或出口代理」）。
        self.remediation = remediation
        super().__init__(message)

    def __str__(self) -> str:
        return f"[MurekaError kind={self.kind} status={self.status_code}] {super().__str__()}"


# —— 403 地域封锁等关键错误的诊断分类 ——
# Mureka 仅允许特定区域（如中国香港）访问，非允许区域出口会收到 403。
# 该错误与 401（密钥无效）性质完全不同：重试无效，须改为合规区域部署或出口代理。
# 分类后错误信息直接给出处置建议，避免运维把「地域封锁」误判为「key 错了 / 参数错了」。
_MUREKA_REGION_KEYWORDS = (
    "region",
    "geo",
    "country",
    "forbidden",
    "不允许",
    "地区",
    "区域",
    "地域",
)
_MUREKA_AUTH_KEYWORDS = (
    "authentication",
    "unauthorized",
    "invalid key",
    "api key",
    "token",
    "认证",
    "密钥",
)


def _classify_mureka_error(status_code: int | None, error_msg: str) -> tuple[str, str]:
    em = (error_msg or "").lower()
    if status_code == 401:
        return "auth", ("API Key 无效/过期或缺少 Authorization 头，请检查 MUREKA_API_KEY 配置")
    if status_code == 403:
        # 地域封锁优先：命中地域关键词，或非明确认证错误时，按地域封锁提示（403 不可重试）。
        if any(k in em for k in _MUREKA_REGION_KEYWORDS) or not any(
            k in em for k in _MUREKA_AUTH_KEYWORDS
        ):
            return "region", (
                "该部署出口疑似被 Mureka 地域封锁(403)。Mureka 仅允许特定区域访问，"
                "请在中国香港等允许区域部署，或通过已备案出口代理访问；不要反复重试（403 不可重试）"
            )
        return "auth", ("API Key 无访问权限(403)，请确认该 key 是否已开通 Mureka API 访问")
    if status_code == 400:
        return "param", (
            "请求参数错误(400)，请检查 model 合法值与字段约束（见 mureka.py 端点 model 表）"
        )
    if status_code == 429:
        if any(k in em for k in ("quota", "billing", "credit", "余额", "额度")):
            return "rate_limit", ("额度/余额耗尽(429)，需充值后重试")
        return "rate_limit", ("触发限流(429)，可退避重试")
    if status_code is not None and 500 <= status_code < 600:
        return "server", ("Mureka 服务端错误(5xx)，可重试")
    if status_code is None:
        return "network", ("网络层错误（未收到 HTTP 响应），检查出口连通性")
    return "unknown", ("未知错误")


def _raise_mureka_error(
    method: str, path: str, resp: "httpx.Response", *, exhausted: bool = False
) -> "NoReturn":
    msg = MurekaProvider._extract_error(resp)
    kind, remediation = _classify_mureka_error(resp.status_code, msg)
    tag = "重试耗尽" if exhausted else "失败"
    raise MurekaError(
        resp.status_code,
        kind,
        f"Mureka {method} {path} {tag} {resp.status_code} [{kind}]: {msg} —— {remediation}",
        remediation=remediation,
    )


_STATUS_PROCESSING = {"preparing", "queued", "running", "streaming"}
_STATUS_FAILED = {"failed", "timeouted", "cancelled"}


class MurekaProvider(MusicProvider):
    # 阶段 1（2026-09-12 已实施）：create() 已按 req.task **真实分流**到 Mureka 端点，
    # 父子歌真实关联，extend/cover/remix 不再是「另起一首新歌」的假功能。
    #   extend → POST /v1/song/extend {song_id, lyrics≤3000, extend_at ms, extend_type, model∈7.6/8}
    #   remix  → POST /v1/song/remix  {song_id, prompt≤1024, lyrics≤5000, n}
    #   cover  → Mureka 无 cover 端点 → 映射为 remix（换 prompt、沿用歌词）
    # crop / speed / aligned_lyrics / sound 在 Mureka **无对应能力** → 保持 501（诚实 fail-closed）。
    supported_tasks: frozenset[str] = frozenset({"generate", "custom", "extend", "cover", "remix"})

    # —— 各端点 model 合法值集合不同（官方逐端点核实，用错即 400）——
    # song/generate 与 song/easy-generate：auto / 7.6 / o2 / 8 / 9 / 9.5
    # instrumental/generate：auto / 7.6 / 8 / 9 / 9.5（**无 o2**）
    # song/extend：**仅** mureka-7.6 / mureka-8（传 mureka-9 会 400！）
    # song/remix：请求体**无 model 字段**
    # song/stem：audio-separation-1 / 2 / 3
    _EXTEND_MODELS: frozenset[str] = frozenset({"mureka-7.6", "mureka-8"})
    _INSTRUMENTAL_MODELS: frozenset[str] = frozenset(
        {"auto", "mureka-7.6", "mureka-8", "mureka-9", "mureka-9.5"}
    )
    # /v1/song/easy-generate 的 styles 官方枚举（字段大小写官方用连字符/缩写，统一小写比较）。
    # ⚠️ 官方文档仅列出这些；若日后扩充，本集合需同步——不要「接受任意字符串」，
    # 否则用户填了官方不认识的风格会被静默忽略（等于没生效，且难排查）。
    EASY_STYLES: frozenset[str] = frozenset(
        {
            "pop",
            "rock",
            "jazz",
            "r&b",
            "edm",
            "ambient",
            "folk",
            "latin",
            "k-pop",
            "j-pop",
            "house",
            "gospel",
            "lo-fi",
        }
    )

    def __init__(self):
        self.base = settings.mureka_api_base.rstrip("/")
        self.api_key = settings.mureka_api_key
        self.model = settings.mureka_model or "mureka-9"
        self.n = max(1, min(int(settings.mureka_n or 1), 3))
        self._sem = asyncio.Semaphore(max(1, int(settings.mureka_max_concurrent or 1)))
        self._timeout = httpx.Timeout(connect=10, read=180, write=10, pool=10)

    def _auth_header(self) -> dict:
        if self.api_key:
            return {"Authorization": f"Bearer {self.api_key}"}
        return {}

    @staticmethod
    def _extract_error(resp: "httpx.Response") -> str:
        """Mureka 错误包体为 {"error":{"message":...},"trace_id":...}（实测 429 验证）。

        统一抽取服务端真实原因，避免 raise_for_status 把 "You exceeded your current
        quota" 之类关键信息吞掉，导致歌曲静默失败却看不到原因。
        """
        try:
            data = resp.json()
        except Exception:
            return (resp.text or "")[:300]
        if isinstance(data, dict):
            err = data.get("error")
            if isinstance(err, dict) and err.get("message"):
                return str(err["message"])
            if data.get("message"):
                return str(data["message"])
            if data.get("detail"):
                return str(data["detail"])
        return (resp.text or "")[:300]

    @staticmethod
    def _should_retry(resp: "httpx.Response") -> bool:
        """429 有两种（错误码文档 platform.mureka.ai/docs/en/error-codes.html）：

        - 限流(Rate limit reached) → 可退避重试；
        - 额度耗尽(You exceeded your current quota / billing) → **不可重试**，等也白等。
        5xx(服务器/引擎过载) → 可重试。其余(401/403/400) → 直接失败。
        """
        if resp.status_code in (500, 502, 503, 504):
            return True
        if resp.status_code == 429:
            msg = MurekaProvider._extract_error(resp).lower()
            if any(k in msg for k in ("quota", "billing", "credit", "余额", "额度")):
                return False
            return True
        return False

    async def _post(self, path: str, json: dict) -> dict:
        headers = self._auth_header()
        headers["Content-Type"] = "application/json"
        last = None
        for attempt in range(4):
            async with self._sem:  # 约束提交并发（避免入门档 429）
                async with httpx.AsyncClient(timeout=self._timeout) as c:
                    last = await c.post(f"{self.base}{path}", headers=headers, json=json)
            if last.status_code < 400:
                return last.json()
            if self._should_retry(last):
                await asyncio.sleep(min(2**attempt, 30))
                continue
            _raise_mureka_error("POST", path, last)
        _raise_mureka_error("POST", path, last, exhausted=True)

    async def _get(self, path: str) -> dict:
        headers = self._auth_header()
        last = None
        for attempt in range(4):
            async with httpx.AsyncClient(timeout=self._timeout) as c:
                last = await c.get(f"{self.base}{path}", headers=headers)
            if last.status_code < 400:
                return last.json()
            if self._should_retry(last):
                await asyncio.sleep(min(2**attempt, 30))
                continue
            _raise_mureka_error("GET", path, last)
        _raise_mureka_error("GET", path, last, exhausted=True)

    async def _post_multipart(
        self, path: str, data: dict | None = None, files: dict | None = None
    ) -> dict:
        """multipart/form-data POST（files/upload 与 vocal/clone 专用）。

        官方明确要求「do not use JSON format」。注意：**不要**手动设置 Content-Type，
        httpx 会在有 files 时自动附带 multipart boundary；手动设置会导致 boundary 缺失而 400。
        """
        headers = self._auth_header()
        last = None
        for attempt in range(4):
            async with self._sem:
                async with httpx.AsyncClient(timeout=self._timeout) as c:
                    last = await c.post(
                        f"{self.base}{path}", headers=headers, data=data, files=files
                    )
            if last.status_code < 400:
                return last.json()
            if self._should_retry(last):
                await asyncio.sleep(min(2**attempt, 30))
                continue
            _raise_mureka_error("POST(multipart)", path, last)
        _raise_mureka_error("POST(multipart)", path, last, exhausted=True)

    def _resolve_extend_model(self, requested: str | None) -> str:
        """/v1/song/extend **仅接受 mureka-7.6 / mureka-8**。

        我们的默认 `settings.mureka_model="mureka-9"` 对 generate 合法、对 extend 会 400，
        故必须降级：显式指定 7.6 时用 7.6（仅 tail），否则一律用 8（tail+head 均支持）。
        """
        if requested and str(requested).lower() in ("mureka-7.6", "7.6"):
            return "mureka-7.6"
        if requested and str(requested) not in self._EXTEND_MODELS:
            log.warning(
                "Mureka extend 不支持 model=%s（仅 mureka-7.6/8），自动降级为 mureka-8",
                requested,
            )
        return "mureka-8"

    def _build_prompt(self, req: GenerateRequest) -> str:
        """generate 端点用自由文本 prompt 控制风格。

        [2026-09-10 변경] **gospel 강제 주입 제거**.
          종전엔 `parts = ["gospel"]` 로 시작해 **모든 곡**(세속 주제 포함)에 gospel 이 선두 주입됐다.
          그 결과 "失恋的雨夜" 같은 보편 주제도 복음 곡으로 나오는 **제품 정위 모순**이 발생
          (프런트 placeholder 는 세속 예시였음).
          이제 스타일은 **사용자가 입력한 것만** 반영된다. 복음 곡이 필요하면 사용자가
          style 에 `gospel` 을 넣거나(프런트 기본값이 "gospel, slow, piano") AI 최적화의
          `force_gospel` 옵션을 쓰면 된다.
        """
        parts: list[str] = []
        if req.style:
            parts.append(req.style)
        if req.vocal_gender:
            parts.append(str(req.vocal_gender).lower())  # female/male
        if req.prompt and req.prompt != req.style:
            parts.append(req.prompt)
        return ", ".join(p for p in parts if p)

    async def create(self, req: GenerateRequest) -> SongResult:
        # 阶段 1：按 task **真实分流**到 Mureka 对应端点。
        # 原实现一律 POST /song/generate，导致 extend/cover/remix 退化成
        # 「与父歌毫无关联的新歌」假功能（规划 B 阶段 0 已用 501 止血，此处接通）。
        task = str(req.task or "generate").lower()
        if task in ("extend", "cover", "remix"):
            if not req.continue_song_id:
                log.warning(
                    "Mureka %s 缺少 continue_song_id，无法关联父歌 → 退化为普通 generate", task
                )
            elif task == "extend":
                return await self._create_extend(req)
            else:
                return await self._create_remix(req, is_cover=(task == "cover"))

        prompt = self._build_prompt(req)
        # 官方约束 prompt ≤1024 字符（post-v1-song-generate.html），超长截断避免 400。
        if len(prompt) > 1024:
            prompt = prompt[:1021] + "..."
        payload: dict = {
            "lyrics": (req.lyric or req.prompt or "一首歌"),
            # 요청에 모델이 지정되면 **그 모델을 쓴다**(저가 티어 lite → mureka-7.6 원가절감의 핵심).
            # 종전엔 self.model 로 고정해 req.model_version 을 무시했고, 그 결과
            # "티어별 모델 차등" 이 코드상 존재해도 실제로는 발화하지 않았다(no-op).
            "model": (req.model_version or self.model),
            "n": self.n,
        }
        # gospel 주입 제거 후엔 "스타일 미입력" 시 prompt 가 빈 문자열이 될 수 있다.
        # 빈 문자열을 그대로 보내면 의미 없는 필드가 전송되므로, 값이 있을 때만 싣는다.
        if prompt:
            payload["prompt"] = prompt
        if req.vocal_gender:
            vmap = {"male": "male", "female": "female"}
            payload["gender"] = vmap.get(str(req.vocal_gender).lower(), req.vocal_gender)
        # 音色克隆 ID：**仅非 mureka-o2 模型支持**。o2 时官方会 400，必须剔除并留痕，
        # 不能「带上但被忽略」（实际上会直接报错），也不能静默误用。
        if req.vocal_id and str(req.model_version or self.model).lower() != "mureka-o2":
            payload["vocal_id"] = req.vocal_id
        # 注意：Mureka 生成请求体无 title 字段（官方文档确认），歌名由 choices[].title 回传，
        # 故不在此上传 title（上传会被忽略，且易误导）。
        body = await self._post("/song/generate", json=payload)
        task_id = body.get("id")
        if not task_id:
            raise RuntimeError(f"Mureka create 未返回 id，响应: {body}")
        used_model = body.get("model") or self.model
        return SongResult(
            external_id=str(task_id),
            status="processing",
            model_version=str(used_model),
        )

    # ---- 阶段 1：真实 Mureka 端点形状（规格见 PLAN_H5_AND_MUREKA_GAPS.md B.5.3）----
    async def _create_extend(self, req: GenerateRequest) -> SongResult:
        """POST /v1/song/extend — 真正基于父歌续写。

        官方字段：song_id（或 upload_audio_id，互斥）+ lyrics(必填,≤3000)
                  + extend_at(必填, ms) + extend_type(tail|head) + model∈{mureka-7.6,mureka-8}
        轮询复用 /v1/song/query/{id}（与 generate 一致，无需独立轮询）。
        """
        model = self._resolve_extend_model(req.model_version)
        extend_type = "head" if str(req.extend_type or "").lower() == "head" else "tail"
        # extend_at(ms)：clip_seconds 单位为秒。
        # 未指定时 tail 取上限 420000（官方：大于歌曲时长则按歌曲时长计），head 取 8000；
        # 再统一 clamp 到 [8000, 420000]（mureka-7.6 的合法区间，对 8 同样安全）。
        raw_at = (
            int(req.clip_seconds * 1000)
            if req.clip_seconds
            else (420000 if extend_type == "tail" else 8000)
        )
        extend_at = max(8000, min(raw_at, 420000))
        payload = {
            "song_id": str(req.continue_song_id),
            "lyrics": (req.lyric or "")[:3000],
            "extend_at": extend_at,
            "extend_type": extend_type,
            "model": model,
        }
        body = await self._post("/song/extend", json=payload)
        task_id = body.get("id")
        if not task_id:
            raise RuntimeError(f"Mureka extend 未返回 id，响应: {body}")
        return SongResult(
            external_id=str(task_id),
            status="processing",
            model_version=str(body.get("model") or model),
        )

    async def _create_remix(self, req: GenerateRequest, is_cover: bool = False) -> SongResult:
        """POST /v1/song/remix — 真正基于父歌重混 / 翻唱。

        Mureka **没有独立的 cover 端点**；cover 语义（换风格、沿用歌词）正是 remix 的能力，
        故 cover 一并走本方法，仅 prompt 兜底文案不同。
        官方字段：song_id（或 upload_audio_id，互斥）+ prompt(必填,≤1024)
                  + lyrics(必填,≤5000) + n。请求体**无 model 字段**（官方未列）。
        """
        prompt = self._build_prompt(req)
        if not prompt:
            prompt = f"cover version, {req.style or ''}".strip(", ") if is_cover else "remix"
        if len(prompt) > 1024:
            prompt = prompt[:1021] + "..."
        payload = {
            "song_id": str(req.continue_song_id),
            "prompt": prompt,
            "lyrics": (req.lyric or "")[:5000],
            "n": self.n,
        }
        body = await self._post("/song/remix", json=payload)
        task_id = body.get("id")
        if not task_id:
            raise RuntimeError(f"Mureka remix 未返回 id，响应: {body}")
        return SongResult(
            external_id=str(task_id),
            status="processing",
            model_version=str(body.get("model") or self.model),
        )

    async def get(self, external_id: str) -> SongResult:
        """轮询歌曲任务（generate / extend / remix / region-edit **共用** /v1/song/query）。"""
        body = await self._get(f"/song/query/{external_id}")
        return self._parse_task_body(body, external_id)

    async def get_instrumental(self, external_id: str) -> SongResult:
        """轮询器乐任务 —— **独立端点** /v1/instrumental/query/{id}。

        官方明示 instrumental 不能复用 /song/query，混用会查不到任务。
        """
        body = await self._get(f"/instrumental/query/{external_id}")
        return self._parse_task_body(body, external_id)

    def _parse_task_body(self, body: dict, external_id: str) -> SongResult:
        """解析任务响应体（原 get() 逻辑抽出，供 song 与 instrumental 两条轮询复用）。"""
        status_raw = str(body.get("status", "preparing")).lower()
        if status_raw in _STATUS_FAILED:
            # failed_reason 为 Mureka 返回的失败原因（内容拦截/额度/超时等），作为审核提示落库
            return SongResult(
                external_id=external_id,
                status="failed",
                moderation_note=body.get("failed_reason"),
            )
        if status_raw != "succeeded":
            return SongResult(external_id=external_id, status="processing")
        # 成功：取 choices[0]（n>1 的其余首见 P1.5）
        choices = body.get("choices") or []
        choice = choices[0] if choices else {}
        # 字段名实证校准（2026-09-10 真机成功任务 160155853586434）：
        #   choices[0] 实际仅有 url / flac_url / wav_url / id / lyrics_sections / duration
        #   **没有** audio_url、mp3_url、cover_url、image_url、cover、lyrics、lyric、title。
        # · url 是 mp3 链接（实测 960,723 字节），后两位容错键是历史兜底。
        # · flac_url/wav_url 是无损格式；当前播放链路只用 url，但保留后向兼容。
        audio_url = choice.get("audio_url") or choice.get("url") or choice.get("mp3_url")
        cover_url = choice.get("cover_url") or choice.get("image_url") or choice.get("cover")
        # lyrics_sections 是逐字时间轴（section→line→word 三级，每级 start/end ms + text），
        # 是 synced lyrics 功能的现成数据源。优先用 choices[].lyrics 兜底（无时间轴纯文本）。
        lyric = choice.get("lyrics") or choice.get("lyric")
        # title：2026-09-10 实测 generate 的 choices[0] **无 title 字段**（返回 None），
        # 但保留透传读取——未来版本若回传则自动生效；_process_song 以 result.title or song.title 保留用户值。
        title = choice.get("title")
        # duration 单位：毫秒（实测 156710ms ≈ 2:37），SongResult 契约=秒 → /1000；
        # <10000 的值按秒处理（兼容 mock/异常小值，正常歌曲不会短于 10 秒）。
        raw_duration = choice.get("duration")
        duration_s = None
        if isinstance(raw_duration, (int, float)):
            duration_s = (
                int(round(raw_duration / 1000)) if raw_duration >= 10000 else int(raw_duration)
            )
        custom_id = choice.get("id") or choice.get("custom_id") or choice.get("clip_id")
        model_version = body.get("model") or choice.get("model") or self.model
        # 逐字时间轴 JSON 字符串化落库（按需曝光：详情接口_optional_同步歌词数据源）
        raw_sync = choice.get("lyrics_sections")
        lyrics_sync = json.dumps(raw_sync, ensure_ascii=False) if raw_sync else None
        return SongResult(
            external_id=external_id,
            custom_id=str(custom_id) if custom_id else None,
            status="completed",
            audio_url=audio_url,
            cover_url=cover_url,
            lyric=lyric,
            lyrics_sync=lyrics_sync,
            title=title,
            model_version=str(model_version),
            duration=duration_s,
        )

    # ---- 阶段 1 新增能力（Mureka 有、原实现完全没有；规格见 B.5.3）----
    async def files_upload(
        self,
        *,
        purpose: str,
        file_bytes: bytes | None = None,
        filename: str | None = None,
        url: str | None = None,
    ) -> str:
        """POST /v1/files/upload — **multipart/form-data**（官方：do not use JSON）。

        purpose(必填) 枚举：reference / melody / instrumental / voice / audio / remix /
        soundtrack / lyrics-video。`file` 与 `url` 互斥。同步返回 id。
        各 purpose 时长约束（调用方须校验）：reference[30,30]s、melody[5,60]s、
        instrumental[30,30]s、voice[5,15]s、audio(通用,≤10MB)、remix[10,350]s。
        """
        if not file_bytes and not url:
            raise ValueError("files_upload 需要 file_bytes 或 url 之一（二者互斥）")
        data = {"purpose": purpose}
        if url:
            data["url"] = url
        files = {"file": (filename or "upload.bin", file_bytes)} if file_bytes else None
        body = await self._post_multipart("/files/upload", data=data, files=files)
        file_id = body.get("id")
        if not file_id:
            raise RuntimeError(f"Mureka files/upload 未返回 id，响应: {body}")
        return str(file_id)

    async def upload(self, audio_url: str) -> str:
        """对齐 SunoProvider.upload：上传参考音频，返回 custom_id。

        Mureka 语义不同（multipart + purpose）。这里 purpose 取 **audio**
        （官方：General-purpose audio for song extension and other features），
        返回的 id 可直接作为 /song/extend 的 upload_audio_id。
        """
        return await self.files_upload(purpose="audio", url=audio_url)

    async def vocal_clone(self, file_bytes: bytes, filename: str = "vocal.m4a") -> str:
        """POST /v1/vocal/clone — multipart，**同步**返回 vocal_id。

        官方：file 必填（mp3/m4a, <10MB）；响应 {id, filename, bytes, created_at}。
        ⚠️ 真实路径是 /v1/vocal/clone（**不是** /v1/song/vocal-clone）。
        vocal_id 可用于 song/generate 与 song/easy-generate；注意 **mureka-o2 不支持 vocal_id**。

        ⏸ **产品暂停（2026-09-13 决策）**：入口 `clone_vocal` 默认 501（开关
        `settings.enable_vocal_clone`）。⚠️ **本方法保留、禁止删除**——供应商侧实现已
        逐项核实（multipart 路径/约束），重新上线无需重新调研。测试环境仍覆盖本方法。
        """
        body = await self._post_multipart("/vocal/clone", files={"file": (filename, file_bytes)})
        vocal_id = body.get("id")
        if not vocal_id:
            raise RuntimeError(f"Mureka vocal/clone 未返回 id，响应: {body}")
        return str(vocal_id)

    async def stem(self, url: str, model: str = "audio-separation-2") -> dict:
        """POST /v1/song/stem — **同步**接口（非异步任务，无 task_id、无需轮询）。

        请求 url（mp3/m4a 直链或 base64 data URL，≤10MB）+ model=audio-separation-1/2/3。
        同步返回 {zip_url, **expires_at**(秒级 Unix，URL 过期时间), midi_zip_url}。
        语义是「导出分轨压缩包」而非生成新歌 → 前端应按下载/导出处理，并留意 expires_at
        （到期需重新生成，或生成后立即转存到自有存储）。
        """
        body = await self._post("/song/stem", json={"url": url, "model": model})
        return {
            "zip_url": body.get("zip_url"),
            "expires_at": body.get("expires_at"),
            "midi_zip_url": body.get("midi_zip_url"),
        }

    async def region_edit(
        self, song_id: str, lyrics: str, edit_start: int, edit_end: int
    ) -> SongResult:
        """POST /v1/song/region-edit — 局部改词。

        官方：song_id（或 upload_audio_id，互斥）+ lyrics(必填,≤3000)
              + edit_start/edit_end(ms)。**改写区间必须 ≥3000ms**，否则官方拒绝。
        轮询复用 /v1/song/query/{id}。
        """
        if int(edit_end) - int(edit_start) < 3000:
            raise ValueError(
                f"region-edit 改写区间须 ≥3000ms（当前 {int(edit_end) - int(edit_start)}ms）"
            )
        payload = {
            "song_id": str(song_id),
            "lyrics": (lyrics or "")[:3000],
            "edit_start": int(edit_start),
            "edit_end": int(edit_end),
        }
        body = await self._post("/song/region-edit", json=payload)
        task_id = body.get("id")
        if not task_id:
            raise RuntimeError(f"Mureka region-edit 未返回 id，响应: {body}")
        return SongResult(
            external_id=str(task_id),
            status="processing",
            model_version=str(body.get("model") or self.model),
        )

    async def instrumental(
        self, prompt: str, model: str | None = None, n: int | None = None
    ) -> SongResult:
        """POST /v1/instrumental/generate — 纯器乐（prompt-only，无 lyrics 字段）。

        model 合法值 **不含 mureka-o2**；轮询须用 get_instrumental()（**独立端点**）。
        """
        m = model or self.model
        if m not in self._INSTRUMENTAL_MODELS:
            log.warning("Mureka instrumental 不支持 model=%s，降级为 auto", m)
            m = "auto"
        payload = {"prompt": (prompt or "")[:1024], "model": m, "n": n or self.n}
        body = await self._post("/instrumental/generate", json=payload)
        task_id = body.get("id")
        if not task_id:
            raise RuntimeError(f"Mureka instrumental 未返回 id，响应: {body}")
        return SongResult(
            external_id=str(task_id),
            status="processing",
            model_version=str(body.get("model") or m),
        )

    async def easy_generate(
        self,
        prompt: str,
        styles: list[str] | None = None,
        model: str | None = None,
        n: int | None = None,
        vocal_id: str | None = None,
    ) -> SongResult:
        """POST /v1/song/easy-generate — Prompt to song（**无 lyrics 字段**）。

        styles 枚举（官方）：pop / rock / jazz / r&b / edm / ambient / folk / latin /
        k-pop / j-pop / house / **gospel** / lo-fi。
        prompt ≤2000（注意与 /song/generate 的 1024 不同）。轮询 /v1/song/query/{id}。
        """
        payload: dict = {
            "prompt": (prompt or "")[:2000],
            "model": model or self.model,
            "n": n or self.n,
        }
        if styles:
            payload["styles"] = list(styles)
        # 音色克隆 ID：同样仅非 mureka-o2 支持（官方约束，见 create() 注释）。
        if vocal_id and str(model or self.model).lower() != "mureka-o2":
            payload["vocal_id"] = vocal_id
        body = await self._post("/song/easy-generate", json=payload)
        task_id = body.get("id")
        if not task_id:
            raise RuntimeError(f"Mureka easy-generate 未返回 id，响应: {body}")
        return SongResult(
            external_id=str(task_id),
            status="processing",
            model_version=str(body.get("model") or self.model),
        )

    async def generate_lyrics(self, prompt: str) -> dict:
        """POST /v1/lyrics/generate — 官方 AI 歌词生成（前端「AI 生成」按钮的背后能力）。

        官方契约（2026-09-10 核对 platform.mureka.cn/docs/api/operations/post-v1-lyrics-generate.html）:
          请求体：{ prompt: string(必填) }  —— “生成歌词的提示词”
          响应 200：{ title: string, lyrics: string }
        这是 Mureka 官方暴露的**唯一** AI 辅助生成端点；官方**没有**风格/提示词优化端点，
        故风格规范化由本地规则实现（见 app/services/prompt_assist.py）。
        """
        body = await self._post("/lyrics/generate", json={"prompt": (prompt or "").strip()})
        return {
            "title": (body.get("title") or "").strip(),
            "lyrics": (body.get("lyrics") or "").strip(),
        }

    async def get_billing(self) -> dict:
        """查询账号账单/额度（GET /v1/account/billing）。

        返回字段（官方文档，2026-09-03 核对）：
          account_id, balance(分), total_recharge(分), total_spending(分),
          concurrent_request_limit(账号最大并发请求数)
        用途：生产环境应在生成前或启动健康检查时调用，用 balance<=0 给出清晰
        “余额不足”错误，而非等到生成时收到 429 "You exceeded your current quota"
        （当时我们正是因此困惑——429 双义：限流可重试 vs 额度耗尽不可重试）。
        concurrent_request_limit 即权威并发上限，可取代手填 MUREKA_MAX_CONCURRENT。
        """
        body = await self._get("/account/billing")
        return {
            "account_id": body.get("account_id"),
            "balance": body.get("balance"),
            "total_recharge": body.get("total_recharge"),
            "total_spending": body.get("total_spending"),
            "concurrent_request_limit": body.get("concurrent_request_limit"),
        }
