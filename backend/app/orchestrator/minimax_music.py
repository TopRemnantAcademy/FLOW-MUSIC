"""MiniMax Music 3 客户端（**备选供应商评估用**，非生产默认引擎）。

协议基线（2026-09-26 中/英文档逐字段核对，非二手转述）：
  - 英文文档 https://platform.minimax.io/docs/api-reference/music-generation
  - 中文文档 https://platform.minimax.cn/docs/api-reference/music-generation
  - 端点：POST https://api.minimax.io/v1/music_generation（官方两页均为此 host；
    未找到官方文档明示的国内独立域名，故不臆造第二套基址）
  - 鉴权：Authorization: Bearer <MINIMAX_API_KEY>，Content-Type: application/json
  - Body 字段与约束（以官方文档中/英两页为准）：
        model            必填枚举：music-3.0 | music-2.6 | music-cover |
                         music-3.0-free | music-2.6-free | music-cover-free
        prompt           纯音乐 1-2000；非纯音乐 0-2000；cover 必填 10-300
        lyrics           非纯音乐必填 1-3500；cover 10-1000（缺省则 ASR 抽取）
                         支持结构标签 [Intro]/[Verse]/[Pre Chorus]/[Chorus]/
                         [Interlude]/[Bridge]/[Outro]/[Post Chorus]/
                         [Transition]/[Break]/[Hook]/[Build Up]/[Inst]/[Solo]
        stream           布尔，默认 false
        output_format    hex | url，默认 hex（stream=true 时仅 hex；url 24h 过期）
        audio_setting    {sample_rate, bitrate, format}
        aigc_watermark   布尔，默认 false，**仅在 stream=false 时生效**（中文页独有条目）
        lyrics_optimizer/is_instrumental  布尔，默认 false；
                         均仅 music-3.0/-free、music-2.6/-free 支持，music-cover 不支持
        audio_url / audio_base64 / cover_feature_id  仅 cover 模型，三者互互斥
  - 200 响应：
        { base_resp{status_code, status_msg}, trace_id, analysis_info,
          extra_info{music_duration, music_sample_rate, music_channel,
                     bitrate, music_size}, data{audio, status} }
        data.status 官方示例为 2（同步接口按「1 进行中 / 2 完成」语义解读，
        见 build_payload/parse_response 处的处理说明）

⚠️ audio_setting 的子属性在两处官方文档都是**折叠项**（"Show child attributes"），
   页面未直接展开取值。本文件采用的枚举（44100/256000/mp3 取自官方 curl 示例，
   其余候选值取自 MiniMax Music FAQ 与主流 API 聚合文档）= 与官方示例一致，
   但**未经官方页面直接确认**，故在合规文档里标注为「待折叠区确认」。

设计取舍（为什么**不继承 MusicProvider**）：
  MiniMax music_generation 是**同步接口**——没有 task_id、没有轮询端点、
  没有 webhook，一次调用直接回音频。MurekaProvider 的 create() + get() 轮询
  契约是为其异步任务模型设计的；硬套会让 get() 变成「假装轮询」（比不实现更糟）。
  故本类为独立客户端，明确标注为评估用途；真要进生产引擎链需重做异步适配。

⚠️ 2026-08-20 起官方公告（中英文文档页同一条）：
   免费音乐生成接口 music-3.0-free / music-2.6-free / music-cover-free
   **停止服务**；付费「音乐生成 / 歌词生成」接口自同日起不再面向**新用户**
   （历史付费用户可继续用 music-3.0）。故：
   - 默认模型 id 仍取 settings.minimax_music_model（= "music-3.0-free"），
     以兼容「历史账号仍可用」的情形；
   - **缺 key / 被调用方拒绝时一律响亮失败**，不做静默跳过、不造桩、
     不把失败写进报告当成功（与项目「不许静默通过」铁律一致）。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

import httpx

from app.config import settings

# —— 官方文档给出的合法结构标签（lyrics 分段用）——
OFFICIAL_STRUCTURE_TAGS = (
    "[Intro]",
    "[Verse]",
    "[Pre Chorus]",
    "[Chorus]",
    "[Interlude]",
    "[Bridge]",
    "[Outro]",
    "[Post Chorus]",
    "[Transition]",
    "[Break]",
    "[Hook]",
    "[Build Up]",
    "[Inst]",
    "[Solo]",
)

MAX_PROMPT_CHARS = 2000
MAX_LYRICS_CHARS = 3500

# 官方文档 curl 示例里的 audio_setting；44.1kHz/256kbps/mp3 是示例同款参数。
DEFAULT_AUDIO_SETTING: dict[str, object] = {
    "sample_rate": 44100,
    "bitrate": 256000,
    "format": "mp3",
}

#: output_format 固定用 hex：官方明示 url 链接 **24 小时过期**，
#: hex 直接拿二进制落盘/上传，避免评估侧拿到一半发现链接失效。
OUTPUT_FORMAT = "hex"

#: 接口停服公告要点，调用失败时原样回显给运维（避免反复翻文档）。
DISCONTINUATION_NOTICE = (
    "MiniMax 官方文档公告：自 2026-08-20 起免费音乐生成接口 "
    "(music-3.0-free / music-2.6-free / music-cover-free) 停止服务；"
    "付费音乐生成接口自同日起不再面向新用户（历史付费用户可继续用 music-3.0）。"
)

#: data.status = 2 表示生成完成（官方文档 200 示例即为此值）。
#: 同步接口理论上不会返回「进行中」，但若服务端回别的码值，宁可响亮失败。
DATA_STATUS_COMPLETE = 2

#: 官方文档未直接展开 audio_setting 的候选值，此处按「官方 curl 示例 +
#: MiniMax Music FAQ（MP3/WAV/PCM；16/24/32/44.1kHz；32/64/128/256kbps）」固定，
#: 传给服务端前做白名单校验，避免把非法值丢过去换来一个 2013。
AUDIO_SETTING_ALLOWED: dict[str, tuple[object, ...]] = {
    "sample_rate": (16000, 24000, 32000, 44100),
    "bitrate": (32000, 64000, 128000, 256000),
    "format": ("mp3", "wav", "pcm"),
}

OUTPUT_FORMATS = ("hex", "url")


@dataclass(frozen=True)
class MiniMaxModelSpec:
    """单个 model 的官方规格。UI 能力开关与 build_payload 校验共用同一事实源。"""

    rpm: int
    cover: bool
    supports_instrumental: bool
    supports_lyrics_optimizer: bool
    prompt_range: tuple[int, int] | None
    lyrics_range: tuple[int, int] | None
    discontinued: bool = False
    note: str = ""


#: 官方 model 枚举的规格表（逐条对应文档，无推测值）。
OFFICIAL_MODEL_SPECS: dict[str, MiniMaxModelSpec] = {
    "music-3.0": MiniMaxModelSpec(
        rpm=120,
        cover=False,
        supports_instrumental=True,
        supports_lyrics_optimizer=True,
        prompt_range=(0, 2000),
        lyrics_range=(1, 3500),
        note="官方推荐；Token Plan / 付费用户，RPM 120",
    ),
    "music-2.6": MiniMaxModelSpec(
        rpm=120,
        cover=False,
        supports_instrumental=True,
        supports_lyrics_optimizer=True,
        prompt_range=(0, 2000),
        lyrics_range=(1, 3500),
        note="上一代模型；Token Plan / 付费用户，RPM 120",
    ),
    "music-cover": MiniMaxModelSpec(
        rpm=120,
        cover=True,
        supports_instrumental=False,
        supports_lyrics_optimizer=False,
        prompt_range=(10, 300),
        lyrics_range=(10, 1000),
        note="参考音频翻唱；需要 audio_url/audio_base64 或 cover_feature_id",
    ),
    "music-3.0-free": MiniMaxModelSpec(
        rpm=3,
        cover=False,
        supports_instrumental=True,
        supports_lyrics_optimizer=True,
        prompt_range=(0, 2000),
        lyrics_range=(1, 3500),
        discontinued=True,
        note="限免版本，RPM 3 —— 官方文档明示 2026-08-20 起停止服务",
    ),
    "music-2.6-free": MiniMaxModelSpec(
        rpm=3,
        cover=False,
        supports_instrumental=True,
        supports_lyrics_optimizer=True,
        prompt_range=(0, 2000),
        lyrics_range=(1, 3500),
        discontinued=True,
        note="限免版本，RPM 3 —— 官方文档明示 2026-08-20 起停止服务",
    ),
    "music-cover-free": MiniMaxModelSpec(
        rpm=3,
        cover=True,
        supports_instrumental=False,
        supports_lyrics_optimizer=False,
        prompt_range=(10, 300),
        lyrics_range=(10, 1000),
        discontinued=True,
        note="限免版本，RPM 3 —— 官方文档明示 2026-08-20 起停止服务",
    ),
}

#: 官方文档只给出 base_resp.status_code/msg，未列错误码表；下列映射来自
#: MiniMax 公开技术资料的整理，**非官方错误码表**，仅用于把数字翻成人话；
#: 判定是否失败始终以 status_code != 0 为准，不依赖这张表。
BUSINESS_CODE_HINTS: dict[int, str] = {
    1002: "触发限流（rate limit）",
    1004: "鉴权失败（authentication failure）",
    1008: "余额不足（insufficient balance）",
    1026: "内容命中敏感词（sensitive content）",
    2013: "参数不合法（invalid parameters）",
    2049: "API Key 无效（invalid API key）",
}


def get_model_spec(model: str) -> MiniMaxModelSpec:
    """取 model 的官方规格；不在官方枚举内直接报错（不猜、不兜底）。"""
    spec = OFFICIAL_MODEL_SPECS.get(model)
    if spec is None:
        raise ValueError(
            f"model {model!r} 不在官方枚举内；合法值：{', '.join(OFFICIAL_MODEL_SPECS)}"
        )
    return spec


def describe_specs() -> list[dict]:
    """把官方规格摊平成 UI 可以直接渲染的表（能力开关由文档推导，不臆造）。"""
    out = []
    for name, s in OFFICIAL_MODEL_SPECS.items():
        out.append(
            {
                "model": name,
                "rpm": s.rpm,
                "cover": s.cover,
                "is_instrumental": s.supports_instrumental,
                "lyrics_optimizer": s.supports_lyrics_optimizer,
                "prompt_range": list(s.prompt_range) if s.prompt_range else None,
                "lyrics_range": list(s.lyrics_range) if s.lyrics_range else None,
                "discontinued": s.discontinued,
                "note": s.note,
            }
        )
    return out


class MiniMaxMusicError(RuntimeError):
    """MiniMax HTTP/业务码错误。

    kind: auth | param | rate_limit | server | network | business | unknown
    """

    def __init__(
        self,
        status_code: int | None,
        kind: str,
        message: str,
        *,
        status_msg: str = "",
        trace_id: str | None = None,
    ):
        self.status_code = status_code
        self.kind = kind
        self.status_msg = status_msg
        self.trace_id = trace_id
        super().__init__(message)

    def __str__(self) -> str:
        return f"[MiniMaxMusicError kind={self.kind} status={self.status_code}] {super().__str__()}"


@dataclass(frozen=True)
class MiniMaxResult:
    """一次同步生成的结果。audio_bytes 与 audio_url 二者必有其一。

    data_status 直接取自响应 data.status（官方示例为 2），原样透出不做猜测，
    便于评估阶段核对「HTTP 200 + base_resp=0」之外的第三层状态。
    """

    audio_bytes: bytes | None = None
    audio_url: str | None = None
    duration_ms: int | None = None
    sample_rate: int | None = None
    channels: int | None = None
    bitrate: int | None = None
    size_bytes: int | None = None
    trace_id: str | None = None
    data_status: int | None = None


class _RpmPacer:
    """最小 RPM 限速器：相邻两次调用至少间隔 60/rpm 秒。

    官方对 free 档明示 RPM=3（付费档 120）。限速在**客户端**做，
    否则并发一高就会被服务端按 429 打回。
    """

    def __init__(self, rpm: int) -> None:
        self._interval = 60.0 / max(int(rpm), 1)
        self._last = 0.0
        self._lock = asyncio.Lock()

    @property
    def interval(self) -> float:
        return self._interval

    async def acquire(self) -> None:
        async with self._lock:
            wait = self._interval - (time.monotonic() - self._last)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = time.monotonic()


class MiniMaxMusicClient:
    """MiniMax Music 3 同步生成客户端。"""

    def __init__(
        self,
        api_key: str | None = None,
        base: str | None = None,
        model: str | None = None,
        rpm: int | None = None,
        timeout: float | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.api_key = api_key if api_key is not None else settings.minimax_api_key
        self.base = (base or settings.minimax_api_base).rstrip("/")
        self.model = model or settings.minimax_music_model
        self.rpm = rpm if rpm is not None else settings.minimax_music_rpm
        self.timeout = timeout if timeout is not None else settings.minimax_music_timeout
        # 允许注入 AsyncClient（测试用 MockTransport）；生产不传 → 每次调用自建。
        self.client = client
        self._pacer = _RpmPacer(self.rpm)

    # —— 请求体 ——
    def build_payload(
        self,
        *,
        prompt: str,
        lyrics: str | None = None,
        is_instrumental: bool = False,
        lyrics_optimizer: bool = False,
        output_format: str = OUTPUT_FORMAT,
        audio_setting: dict | None = None,
        model: str | None = None,
        aigc_watermark: bool = False,
        stream: bool = False,
        audio_url: str | None = None,
        audio_base64: str | None = None,
        cover_feature_id: str | None = None,
    ) -> dict:
        """按官方约束组装请求体；越界字段**直接报错**，不做静默截断。

        校验值全部来自 OFFICIAL_MODEL_SPECS，与 UI 能力开关同源：
        同一份文档，改动只需改一处。

        （与 MurekaProvider 截断到 1024 的处理不同：截断会让「我以为传了 3000 字歌词」
          变成「实际只传了 3000 字」，这里的失败必须可见。）
        """
        model_id = model or self.model
        spec = get_model_spec(model_id)

        if output_format not in OUTPUT_FORMATS:
            raise ValueError(
                f"output_format 只能是 {' | '.join(OUTPUT_FORMATS)}，收到 {output_format!r}"
            )
        if stream and output_format != "hex":
            raise ValueError("官方约束：stream=true 时 output_format 只能是 hex")

        prompt_s = (prompt or "").strip()
        lyrics_s = (lyrics or "").strip()

        # —— prompt：官方按「是否纯音乐 / 是否 cover」三档给长度区间 ——
        if spec.prompt_range is None:
            raise ValueError(f"model {model_id} 无官方 prompt 约束可用，拒绝组装请求体")
        p_min, p_max = spec.prompt_range
        if is_instrumental and not spec.supports_instrumental:
            raise ValueError(f"model {model_id} 不支持 is_instrumental（官方约束）")
        if not is_instrumental and lyrics_optimizer and not spec.supports_lyrics_optimizer:
            raise ValueError(f"model {model_id} 不支持 lyrics_optimizer（官方约束）")
        # 官方规则：cover 的 prompt 恒必填；非 cover 时「纯音乐」或
        # 「无歌词且不开 optimizer」这两种情形才要求 prompt 非空；
        # 其余情形（有自填歌词 / optimizer 自动写词）prompt 允许 0 字符。
        if spec.cover:
            need_prompt = True
            p_min_eff = p_min
        else:
            need_prompt = is_instrumental or (not lyrics_s and not lyrics_optimizer)
            # 官方：纯音乐 prompt 下限为 1；非 cover 且「无歌词 + 不开 optimizer」
            # 时 prompt 是唯一描述来源，同样要求非空。
            p_min_eff = 1 if need_prompt else 0
        if not (p_min_eff <= len(prompt_s) <= p_max):
            raise ValueError(
                f"prompt 长度 {len(prompt_s)} 不满足官方区间 [{p_min_eff}, {p_max}]"
                f"（model={model_id}, need_prompt={need_prompt}）"
            )

        # —— lyrics：纯音乐与非必填；cover 有独立区间 ——
        payload: dict = {"model": model_id, "prompt": prompt_s}
        if is_instrumental:
            # 官方：纯音乐时 lyrics 非必填；传了等于自相矛盾，报错而不是悄悄丢掉。
            if lyrics_s:
                raise ValueError("is_instrumental=true 时不应再传 lyrics（官方：纯音乐无歌词）")
        else:
            if spec.lyrics_range is None:
                raise ValueError(f"model {model_id} 无官方 lyrics 约束可用，拒绝组装请求体")
            l_min, l_max = spec.lyrics_range
            if not lyrics_s:
                if not lyrics_optimizer and not spec.cover:
                    raise ValueError(
                        f"非纯音乐且 lyrics_optimizer=false 时 lyrics 必填"
                        f"（官方区间 [{l_min}, {l_max}]）"
                    )
                # cover：lyrics 本就可选（不传则 ASR 从参考音频抽取），
                # 空串不进请求体 —— 传 "," 空字段会被服务端当无效值。
            elif not (l_min <= len(lyrics_s) <= l_max):
                raise ValueError(
                    f"lyrics 长度 {len(lyrics_s)} 不在官方区间 [{l_min}, {l_max}]"
                    f"（model={model_id}）"
                )
        if lyrics_s:
            payload["lyrics"] = lyrics_s

        # —— cover 三字段：官方明示「必须且只能提供其中一个，且三者互斥」——
        provided = [
            n
            for n, v in (
                ("audio_url", audio_url),
                ("audio_base64", audio_base64),
                ("cover_feature_id", cover_feature_id),
            )
            if v
        ]
        if provided and not spec.cover:
            raise ValueError(f"{provided} 仅用于 music-cover / music-cover-free（官方约束）")
        if len(provided) > 1:
            raise ValueError(
                f"audio_url / audio_base64 / cover_feature_id 只能提供一个，收到 {provided}"
            )
        if spec.cover:
            if audio_url:
                payload["audio_url"] = audio_url
            if audio_base64:
                payload["audio_base64"] = audio_base64
            if cover_feature_id:
                payload["cover_feature_id"] = cover_feature_id

        # —— 官方全站同构的布尔量 ——
        payload["is_instrumental"] = bool(is_instrumental)
        payload["lyrics_optimizer"] = bool(lyrics_optimizer)
        payload["stream"] = bool(stream)
        payload["output_format"] = output_format

        # aigc_watermark：官方文档中文页条目，仅在 stream=false 时生效。
        # 默认 False —— 评估用的音乐不该带水印，这里**显式下发 False**，
        # 避免服务端默认开或后续改默认值时悄悄影响 A/B 样本一致性。
        payload["aigc_watermark"] = False if stream else bool(aigc_watermark)

        setting = dict(DEFAULT_AUDIO_SETTING)
        if audio_setting:
            setting.update(audio_setting)
        for key, allowed in AUDIO_SETTING_ALLOWED.items():
            if key in setting and setting[key] not in allowed:
                raise ValueError(f"audio_setting.{key}={setting[key]!r} 不在候选值 {allowed} 内")
        payload["audio_setting"] = setting
        return payload

    # —— 响应解析 ——
    @staticmethod
    def parse_response(body: dict) -> MiniMaxResult:
        base_resp = body.get("base_resp") or {}
        status_code = base_resp.get("status_code")
        status_msg = str(base_resp.get("status_msg") or "")
        trace_id = body.get("trace_id")
        if status_code not in (None, 0):
            raise MiniMaxMusicError(
                status_code=200,
                kind="business",
                message=f"MiniMax 业务码 {status_code}"
                f"（{MiniMaxMusicClient.describe_business_code(status_code)}）：{status_msg}",
                status_msg=status_msg,
                trace_id=trace_id,
            )
        extra = body.get("extra_info") or {}
        data = body.get("data") or {}
        data_status = data.get("status")
        if data_status is not None and int(data_status) != DATA_STATUS_COMPLETE:
            # 官方文档 200 示例里 data.status == 2（完成）。同步接口本不该回
            # 「进行中」，若真回别的码值说明理解与实现已分叉 —— 响亮失败。
            raise MiniMaxMusicError(
                status_code=200,
                kind="business",
                message=f"MiniMax data.status={data_status}（官方示例完成值为 "
                f"{DATA_STATUS_COMPLETE}），疑似生成未完成，拒绝当成功处理",
                status_msg=str(data_status),
                trace_id=trace_id,
            )
        audio = data.get("audio")
        audio_bytes: bytes | None = None
        audio_url: str | None = None
        if isinstance(audio, str):
            if audio.startswith("http"):
                audio_url = audio
            else:
                audio_bytes = bytes.fromhex(audio)
        return MiniMaxResult(
            audio_bytes=audio_bytes,
            audio_url=audio_url,
            duration_ms=extra.get("music_duration"),
            sample_rate=extra.get("music_sample_rate"),
            channels=extra.get("music_channel"),
            bitrate=extra.get("bitrate"),
            size_bytes=extra.get("music_size"),
            trace_id=trace_id,
            data_status=data_status,
        )

    def _headers(self, model: str | None = None) -> dict:
        if not self.api_key:
            # 缺 key 不是「跳过」，是必须让人看见的配置缺失。
            # 只有官方已公告停服的 model 才连带停服公告，避免误导。
            used = model or self.model
            try:
                discontinued = get_model_spec(used).discontinued
            except ValueError:
                discontinued = False
            msg = "缺少 MINIMAX_API_KEY"
            if discontinued:
                msg += f" —— {DISCONTINUATION_NOTICE}"
            raise MiniMaxMusicError(None, "auth", msg)
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    @staticmethod
    def _classify(status_code: int | None, text: str) -> str:
        tail = (text or "").lower()
        if status_code == 401 or status_code == 403:
            return "auth"
        if status_code in (400, 422):
            return "param"
        if status_code == 429:
            return "rate_limit"
        if status_code is not None and 500 <= status_code < 600:
            return "server"
        if status_code is None:
            return "network"
        # HTTP 200 但业务失败时按错误文案判定（免费档停服等会以 base_resp 形式返回）
        if any(k in tail for k in ("rate", "too many request", "quota")):
            return "rate_limit"
        if status_code == 200:
            return "business"
        return "unknown"

    @staticmethod
    def describe_business_code(code: int | None) -> str:
        """把 base_resp.status_code 翻成人话（映射表来自公开资料整理，非官方错误码表）。"""
        if code is None:
            return "无业务码"
        return BUSINESS_CODE_HINTS.get(int(code), f"未收录业务码（原始码 {code}）")

    async def generate(
        self,
        *,
        prompt: str,
        lyrics: str | None = None,
        is_instrumental: bool = False,
        lyrics_optimizer: bool = False,
        output_format: str = OUTPUT_FORMAT,
        audio_setting: dict | None = None,
        model: str | None = None,
    ) -> MiniMaxResult:
        """同步生成一次。返回前已完成 RPM 限速。"""
        payload = self.build_payload(
            prompt=prompt,
            lyrics=lyrics,
            is_instrumental=is_instrumental,
            lyrics_optimizer=lyrics_optimizer,
            output_format=output_format,
            audio_setting=audio_setting,
            model=model,
        )
        await self._pacer.acquire()
        url = f"{self.base}/music_generation"
        headers = self._headers(str(payload.get("model") or self.model))
        try:
            if self.client is not None:
                async with self.client as c:
                    resp = await c.post(url, headers=headers, json=payload)
            else:
                async with httpx.AsyncClient(timeout=self.timeout) as c:
                    resp = await c.post(url, headers=headers, json=payload)
        except httpx.HTTPError as e:  # 网络层（超时/连接失败）
            raise MiniMaxMusicError(
                None, "network", f"MiniMax 网络层错误：{e}", trace_id=None
            ) from e

        if resp.status_code >= 400:
            detail = _extract_error(resp)
            kind = self._classify(resp.status_code, detail)
            msg = f"MiniMax POST {resp.status_code} [{kind}]：{detail}"
            used_model = str(payload.get("model") or self.model)
            if kind == "auth" and get_model_spec(used_model).discontinued:
                msg += f" —— {DISCONTINUATION_NOTICE}"
            raise MiniMaxMusicError(resp.status_code, kind, msg, status_msg=detail[:300])
        try:
            body = resp.json()
        except Exception as e:
            raise MiniMaxMusicError(
                resp.status_code, "business", f"MiniMax 返回非 JSON：{(resp.text or '')[:200]}"
            ) from e
        result = self.parse_response(body)
        if result.audio_bytes is None and result.audio_url is None:
            raise MiniMaxMusicError(
                resp.status_code,
                "business",
                f"MiniMax 响应既无 hex 音频也无 url：{body}",
                trace_id=result.trace_id,
            )
        return result


def _extract_error(resp: httpx.Response) -> str:
    try:
        data = resp.json()
    except Exception:
        return (resp.text or "")[:300]
    if isinstance(data, dict):
        for k in ("message", "msg", "error", "detail", "status_msg"):
            v = data.get(k)
            if isinstance(v, str) and v:
                return v
    return (resp.text or "")[:300]


def save_audio(result: MiniMaxResult, out_path: str) -> str:
    """把结果落盘（hex 走本地文件，url 先下载再落盘）。

    ⚠️ output_format=url 时官方明示链接 **24 小时过期**，落盘不可拖延。
    """
    if result.audio_bytes is None and result.audio_url:
        raise ValueError("save_audio 仅处理 hex 结果；url 结果请先下载")
    if result.audio_bytes is None:
        raise ValueError("MiniMaxResult 无音频内容可落盘")
    with open(out_path, "wb") as f:
        f.write(result.audio_bytes)
    return out_path


def upload_audio(result: MiniMaxResult, key: str, content_type: str = "audio/mpeg") -> str | None:
    """有对象存储则上传，返回绝对 URL；未配置则返回 None（调用方须自行兜底）。"""
    if result.audio_bytes is None:
        return None
    try:
        from app.storage import s3 as storage

        if not storage.is_configured():
            return None
        return storage.upload_bytes(key, result.audio_bytes, content_type=content_type)
    except Exception:  # 上传失败不吞成成功，交由调用方决定
        raise
