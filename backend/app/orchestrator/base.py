"""音乐供应商抽象层。

定义统一接口 MusicProvider 与数据结构。
- Route A（当前）：S unoProvider 封装商业 API
- Route B（后续）：实现同一接口，换成 Replicate/Fal 跑开源模型（YuE/CosyVoice）
切换供应商只需改 settings.music_provider，业务代码无需改动。
"""

from abc import ABC, abstractmethod

from pydantic import BaseModel, ConfigDict


class ModerationBlockedError(Exception):
    """供应商在提交阶段（create）明确判定内容违规/被审核拦截时抛出。

    client.generate 捕获后切换到引擎链中的下一引擎（Failover, P2）；
    若所有引擎均拦截，则最终以 failed + moderation_blocked=True 收尾。
    """


class GenerateRequest(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    prompt: str | None = None
    style: str | None = None  # 风格标签（对应 Suno tags）
    lyric: str | None = None
    title: str | None = None
    vocal_gender: str | None = None
    make_instrumental: bool = False
    # —— 对标 Suno 真实产品能力（2026-08-27 对照 open.suno.cn 协议）——
    task: str = "generate"  # generate | custom | extend | cover | remix
    model_version: str | None = None  # Suno mv，如 "chirp-v4"（chirp-fenix=V5.5），空则供应商默认
    gpt_description_prompt: str | None = None
    continue_song_id: str | None = None  # extend/cover/remix：基于的外部歌曲 custom_id
    clip_seconds: int | None = None  # extend：续写起点（秒，部分服务商支持）
    persona_id: str | None = None  # Suno Persona（open.suno.cn / sunoapi.org 支持 personaId）
    # Mureka /v1/song/extend：tail(从尾部续) | head(从头部续)。
    # 官方约束：model=mureka-8 时本字段必填；model=mureka-7.6 仅支持 tail 且会忽略本字段。
    extend_type: str | None = None
    # Mureka /v1/vocal/clone 产出的音色 ID，可用于 song/generate 与 song/easy-generate。
    # ⚠️ **mureka-o2 不支持** vocal_id —— 供应商实现须自行剔除并留痕，不能静默带上（会被 400）。
    vocal_id: str | None = None


class SongResult(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    external_id: str = ""
    custom_id: str | None = None  # 供应商歌曲 id（续写/翻唱基准，open.suno.cn 为 UUID）
    status: str = "pending"  # pending | processing | completed | failed
    audio_url: str | None = None
    cover_url: str | None = None
    lyric: str | None = None
    title: str | None = None
    model_version: str | None = None
    duration: int | None = None
    # 逐字时间轴（mureka 返回 choices[].lyrics_sections: section→line→word 三级 ms 戳）。
    # 按 JSON 字符串落库；详情接口按需曝光（避免发现页被 100+KB 同步歌词撑大）。
    # None = 该引擎/此次生成未提供（如 Suno、prompt-only 等），前端按 lyric 字段降级渲染。
    lyrics_sync: str | None = None
    # P2 Failover / 明确审核提示：
    moderation_blocked: bool = False  # True=判定为内容拦截（非基础设施失败）
    moderation_note: str | None = None  # 拦截/失败原因，供前端明确提示


class MusicProvider(ABC):
    #: 该 provider 的 create() **真正支持（会按其分流）** 的 task 类型。
    #:
    #: 背景（2026-09-11 规划 B 阶段 0 止血）：songs.py 的 /{id}/extend、/{id}/cover、
    #: /{id}/remix 把 task 塞进 GenerateRequest 交给 create()。但部分 provider
    #: （如 MurekaProvider）的 create() 完全忽略 req.task 与 req.continue_song_id，
    #: 永远走普通生成 → 用户点「续写/翻唱/Remix」得到一首与父歌毫无关联的新曲
    #: （**假功能，比明确报错更糟**）。
    #: 故新增本声明：songs.py 在派发前校验 engine chain 中是否有引擎真正支持该 task，
    #: 均不支持则 fail-closed 返回 **501**，而不是静默产出错误结果。默认仅 generate（最保守）。
    supported_tasks: frozenset[str] = frozenset({"generate"})

    def supports(self, capability: str) -> bool:
        """能力探测：本 provider 是否实现了某项后期处理 / 上传能力。

        crop / speed / whole_song / aligned_lyrics / sound / upload **不是**
        MusicProvider 抽象契约，由各 provider 自行实现（目前仅 SunoProvider 与
        MockProvider 提供，见 suno.py:183-219 / mock.py:54-70）。
        线上引擎（Mureka）缺这些方法时，`getattr(provider, op)` 会抛 AttributeError，
        而该调用发生在后台任务里 → 被吞成 HTTP 500，用户却先收到误导性的 200。
        调用方须先探测，缺失则 fail-closed 返回 501（见 songs.py._require_capability）。
        """
        return callable(getattr(self, capability, None))

    @abstractmethod
    async def create(self, req: GenerateRequest) -> SongResult:
        """提交一次生成，返回任务/歌曲 external_id（状态 processing）。"""

    @abstractmethod
    async def get(self, external_id: str) -> SongResult:
        """查询生成结果；completed 时带 audio_url 等。"""
