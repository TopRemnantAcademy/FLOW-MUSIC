"""请求/响应数据契约（Pydantic）。前端与后端以此为准。"""

from typing import Optional

from pydantic import BaseModel, ConfigDict, EmailStr


# ---------- 鉴权 ----------
class RegisterReq(BaseModel):
    email: EmailStr
    password: str


class LoginReq(BaseModel):
    email: EmailStr
    password: str
    # GAP-002 2단계 인증: MFA 활성 계정은 otp(TOTP 6자리) 동반 시에만 토큰 발급.
    otp: Optional[str] = None


class TokenResp(BaseModel):
    access_token: str
    token_type: str = "bearer"


class MeResp(BaseModel):
    """当前登录用户（GET /api/auth/me）。"""

    id: int
    email: EmailStr
    # GAP-002: 프론트가 "인증 메일 재발송" 배너를 띄울지 판단하는 근거.
    email_verified: bool = False
    mfa_enabled: bool = False


# ---------- GAP-002 이메일 인증 / 비밀번호 재설정 / MFA ----------
class VerifyEmailReq(BaseModel):
    token: str


class ForgotPasswordReq(BaseModel):
    email: EmailStr


class ResetPasswordReq(BaseModel):
    token: str
    new_password: str


class MfaCodeReq(BaseModel):
    """MFA enable 단계: TOTP 6자리 코드."""

    code: str


class MfaDisableReq(BaseModel):
    """MFA disable: 비밀번호 재확인(2차 인증 없이 끌 수 없게)."""

    password: str


class MfaSetupResp(BaseModel):
    """MFA setup 응답. ⚠ secret 은 **이 응답에서만 1회** 노출된다.

    이후 조회 경로는 없다(다시 발급 = 새 secret). otpauth URI 는 QR 생성용 표준 문자열.
    """

    secret: str
    otpauth_uri: str
    enabled: bool = False


class StatusResp(BaseModel):
    """단순 상태 응답(과도한 전용 모델 남발 회피)."""

    status: str
    detail: Optional[str] = None


# ---------- 歌曲生成 ----------
class GenerateReq(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    task: str = "generate"  # generate | custom | extend | cover | remix
    style: Optional[str] = None  # 风格标签，如 "国风 伤感 钢琴"
    prompt: Optional[str] = None  # 主题/情绪，如 "失恋的雨夜"
    lyric: Optional[str] = None  # 自定义歌词（留空则由 LLM 生成，Route B）
    title: Optional[str] = None
    vocal_gender: Optional[str] = None  # male | female | null
    make_instrumental: bool = False
    model_version: Optional[str] = None  # Suno mv，如 chirp-v4.5
    gpt_description_prompt: Optional[str] = None
    continue_song_id: Optional[str] = None  # extend/cover/remix 外部歌曲 id
    clip_seconds: Optional[int] = None  # extend 起点（秒）
    # Mureka /v1/song/extend 衔接方向：tail(从尾部续) | head(从头部续)。
    # ⚠️ 必须显式声明：pydantic 默认拒绝未知字段（extra=forbid 语义下多传即 422/500）。
    # 合法值在白名单内校验（fail-closed，见 routers/songs.py::_normalize_extend_type）。
    extend_type: Optional[str] = None
    source_song_id: Optional[int] = None  # 站内父歌曲 id（血缘）
    persona_id: Optional[str] = None  # 已对接 open.suno.cn personaId（见 orchestrator/suno.py）
    is_public: Optional[bool] = False  # 是否公开到发现页
    content_category: Optional[str] = (
        None  # 内容分类：general | christian_worship | instrumental（引擎路由依据）
    )
    # 音色克隆 ID（Mureka /v1/vocal/clone 产出）。⚠️ mureka-o2 不支持，后端会自动剔除并 log。
    vocal_id: Optional[str] = None
    # GAP-010 下载格式偏好（mp3|wav|flac）。⚠️ **不是生成质量参数**：
    # Mureka 生成入参无格式/码率控制（官方 spec 实证，见 orchestrator/mureka.py 头部），
    # 上游响应同时回传三种链接，本字段只是「下载时默认取哪种」的偏好。
    # 非白名单值由路由层 400 fail-closed（见 songs.py::_normalize_audio_format）。
    audio_format: Optional[str] = None
    # P1 显式扣费同意：超额时是否用 FLOW credit 按次支付。
    # ⚠️ 必须用户主动勾选才为 True —— 未经同意自动扣款属业务逻辑缺陷 + 合规风险
    # （消费者知情同意 / CWE-840 Business Logic Errors）。默认 False = 超额一律 402。
    use_flow_credit: bool = False


class CropReq(BaseModel):
    start_time: int
    end_time: int


class SpeedReq(BaseModel):
    speed: float


class AlignedLyricsReq(BaseModel):
    lyrics: str


class SoundReq(BaseModel):
    title: str
    tags: Optional[str] = None
    model_version: Optional[str] = None
    tempo: Optional[int] = None
    key: Optional[str] = None
    loop: bool = False
    use_flow_credit: bool = False  # P1 显式扣费同意（见 GenerateReq 注释）


class UploadReq(BaseModel):
    audio_url: str


class UploadResp(BaseModel):
    custom_id: str


class RegionEditReq(BaseModel):
    """Mureka /v1/song/region-edit：局部改词。

    改写区间 edit_end - edit_start **必须 ≥3000ms**（官方约束），否则供应商会拒绝。
    """

    lyrics: str  # 新歌词（必填，≤3000）
    edit_start: int  # 改写区间起点（毫秒）
    edit_end: int  # 改写区间终点（毫秒）
    use_flow_credit: bool = False  # P1 显式扣费同意（见 GenerateReq 注释）


class StemResp(BaseModel):
    """Mureka /v1/song/stem：分轨导出（**同步**返回，非异步任务）。

    ⚠️ zip_url 会过期（expires_at 为秒级 Unix 时间戳）。前端应在到期前转存到自有存储，
    或提示用户重新导出。语义是「导出分轨包」而非生成新歌。
    """

    zip_url: Optional[str] = None
    midi_zip_url: Optional[str] = None
    expires_at: Optional[int] = None
    # 便于前端直接展示「链接何时失效」，避免各处自行做秒级时间戳换算出错。
    expires_at_iso: Optional[str] = None


class VocalCloneResp(BaseModel):
    """Mureka /v1/vocal/clone：音色克隆结果。"""

    vocal_id: str


class EasyGenerateReq(BaseModel):
    """Mureka /v1/song/easy-generate：Prompt to song（**无歌词**，低门槛创作入口）。"""

    model_config = ConfigDict(protected_namespaces=())

    prompt: str  # ≤2000（注意与 /song/generate 的 1024 不同）
    styles: Optional[list[str]] = None  # 官方枚举含 gospel / lo-fi / k-pop ...
    model_version: Optional[str] = None
    n: Optional[int] = None
    title: Optional[str] = None
    is_public: Optional[bool] = False
    content_category: Optional[str] = None
    vocal_id: Optional[str] = None  # 音色克隆（mureka-o2 不支持）
    audio_format: Optional[str] = None  # GAP-010 下载格式偏好（mp3|wav|flac，非生成参数）
    use_flow_credit: bool = False  # P1 显式扣费同意（见 GenerateReq 注释）


class InstrumentalReq(BaseModel):
    """Mureka /v1/instrumental/generate：纯器乐（prompt-only，无歌词字段）。"""

    model_config = ConfigDict(protected_namespaces=())

    prompt: str  # ≤1024
    model_version: Optional[str] = None  # 合法值**不含** mureka-o2
    n: Optional[int] = None
    title: Optional[str] = None
    is_public: Optional[bool] = False
    content_category: Optional[str] = None
    audio_format: Optional[str] = None  # GAP-010 下载格式偏好（mp3|wav|flac，非生成参数）
    use_flow_credit: bool = False  # P1 显式扣费同意（见 GenerateReq 注释）


class SpawnReq(BaseModel):
    """extend / cover / remix 的可选覆盖参数。"""

    model_config = ConfigDict(protected_namespaces=())

    prompt: Optional[str] = None
    style: Optional[str] = None
    title: Optional[str] = None
    lyric: Optional[str] = None
    make_instrumental: Optional[bool] = None
    model_version: Optional[str] = None
    clip_seconds: Optional[int] = None
    # 仅 extend 有意义：tail(从尾部衔接) | head(从头部衔接)。
    # 此前 SpawnReq 没有该字段 → 前端传了会被 pydantic 直接拒绝，用户无从选择方向。
    extend_type: Optional[str] = None
    use_flow_credit: bool = False  # P1 显式扣费同意（见 GenerateReq 注释）


class GenerateResp(BaseModel):
    song_id: int
    status: str


class SongOut(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    id: int
    title: str
    custom_id: Optional[str] = None
    style: Optional[str] = None
    prompt: Optional[str] = None
    lyric: Optional[str] = None
    audio_url: Optional[str] = None
    cover_url: Optional[str] = None
    status: str
    make_instrumental: bool = False
    task_type: str = "generate"
    duration: int = 0
    parent_song_id: Optional[int] = None
    # extend 衔接方向：tail(从尾部衔接) | head(从头部衔接)；非 extend 为 None。
    # 下发原因：前端选择器需回显、且用户/运维要能看出「这首是怎么续的」。
    extend_type: Optional[str] = None
    # 逐字时间轴 JSON（仅详情接口按需返回，列表接口不暴露以控制包体）。
    # 字符串格式，结构由 mureka 提供：[{section_type,start,end,lines:[{start,end,text,words:[...]}]}]
    lyrics_sync: Optional[str] = None
    moderation_status: str = "pending"
    moderation_note: Optional[str] = None
    is_public: bool = False
    play_count: int = 0
    is_owner: bool = False
    # ---- 广场互动（2026-09-10 新增：赞 / 收藏 / 星标；无留言功能）----
    like_count: int = 0
    favorite_count: int = 0
    rating_avg: Optional[float] = None  # 平均分（1.0–5.0），无人评分时为 None
    rating_count: int = 0  # 评分人数
    liked_by_me: bool = False
    favorited_by_me: bool = False
    my_rating: Optional[int] = None  # 当前用户给出的分数（未评分 None）
    author_name: Optional[str] = None  # 广场展示用发布者标识（不出邮箱）
    content_category: Optional[str] = None
    cost_cny: Optional[float] = None
    # ---- 프리미엄 시청 게이트（미구독 = 앞 N초 프리뷰） ----
    preview_audio_url: Optional[str] = None  # 试听片段链接（前 N 秒）
    preview_seconds: Optional[int] = None  # 试听片段时长（秒）
    can_play_full: bool = False  # 是否可播放完整版（= 已订阅）
    can_download: bool = False  # 是否可下载完整音频（= 已订阅）
    # ---- GAP-010 音频格式（下载侧可选；仅订阅者可见无损链接）----
    audio_format: str = "mp3"  # 下载格式 mp3|wav|flac
    # 该曲**实际可用**的下载格式列表（上游返了 flac_url/wav_url 才有 wav/flac）。
    # 前端据此禁用「无损」选项，避免点了才发现 402/缺失（不撒谎的能力声明）。
    available_formats: list[str] = []
    created_at: str


# ---------- 管理后台（歌曲审核） ----------
class AdminSongOut(SongOut):
    user_email: Optional[str] = None
    # 以下字段仅后台可见（计费归因 / 模型版本 / 单位经济）；公共 SongOut 已剥离，避免向终端用户暴露引擎名与成本
    engine_name: Optional[str] = None
    model_version: Optional[str] = None
    price_cny: Optional[float] = None  # 对用户计费（名义收入）
    margin_cny: Optional[float] = None  # 毛利 = price - cost（负=亏损）
    tier_key: Optional[str] = None  # 适用套餐


class AdminStatsOut(BaseModel):
    total: int
    by_status: dict = {}
    by_moderation: dict = {}
    # 单位经济汇总（全量歌曲累计）
    total_cost_cny: float = 0
    total_revenue_cny: float = 0
    total_margin_cny: float = 0


class RejectReq(BaseModel):
    note: Optional[str] = None


# ---------- 管理后台（引擎/路由/计费：后台控制） ----------
class EngineConfigOut(BaseModel):
    provider: str
    display_name: Optional[str] = None
    enabled: bool = True
    is_default: bool = False
    api_base: Optional[str] = None
    api_key_set: bool = False  # 脱敏：仅表示是否配置，不返回明文
    cost_per_song_cny: float = 0
    priority: int = 0
    notes: Optional[str] = None


class EngineConfigCreate(BaseModel):
    provider: str  # suno/mock/musicgen/ace_step_private/tencent_mps/volcano
    display_name: Optional[str] = None
    enabled: bool = True
    is_default: bool = False
    api_base: Optional[str] = None
    api_key: Optional[str] = None
    cost_per_song_cny: Optional[float] = None
    priority: Optional[int] = None
    notes: Optional[str] = None


class EngineConfigUpdate(BaseModel):
    display_name: Optional[str] = None
    enabled: Optional[bool] = None
    is_default: Optional[bool] = None
    api_base: Optional[str] = None
    api_key: Optional[str] = None  # 空字符串/不传表示不改
    cost_per_song_cny: Optional[float] = None
    priority: Optional[int] = None
    notes: Optional[str] = None


class RoutingRuleOut(BaseModel):
    id: int
    category: str
    provider: str
    priority: int
    enabled: bool


class RoutingRuleCreate(BaseModel):
    category: str
    provider: str
    priority: int = 0
    enabled: bool = True


class RoutingRuleUpdate(BaseModel):
    category: Optional[str] = None
    provider: Optional[str] = None
    priority: Optional[int] = None
    enabled: Optional[bool] = None


class PricingTierOut(BaseModel):
    id: int
    tier_key: str
    tier_name: Optional[str] = None
    engine_provider: Optional[str] = None
    price_cny: float = 0
    credits_per_song: int = 1
    model_version: Optional[str] = None  # 绑定生成模型（如 mureka-7.6）；空=引擎默认
    description: Optional[str] = None
    enabled: bool


class PricingTierCreate(BaseModel):
    tier_key: str
    tier_name: Optional[str] = None
    engine_provider: Optional[str] = None
    price_cny: Optional[float] = None
    credits_per_song: Optional[int] = None
    model_version: Optional[str] = None
    description: Optional[str] = None
    enabled: bool = True


class PricingTierUpdate(BaseModel):
    tier_name: Optional[str] = None
    engine_provider: Optional[str] = None
    price_cny: Optional[float] = None
    credits_per_song: Optional[int] = None
    model_version: Optional[str] = None
    description: Optional[str] = None
    enabled: Optional[bool] = None


class AuditLogOut(BaseModel):
    id: int
    method: str
    path: str
    status_code: int
    actor_token_prefix: str = ""
    actor_role: str = "none"
    client_ip: str = ""
    created_at: Optional[str] = None


class ConfigDashboardOut(BaseModel):
    engines: list[EngineConfigOut]
    routing_rules: list[RoutingRuleOut]
    pricing_tiers: list[PricingTierOut]
    # 成本（供应商计费估算）
    cost_by_engine: dict = {}
    cost_by_category: dict = {}
    # 收入 / 毛利（按套餐标准价快照）
    revenue_by_tier: dict = {}
    margin_by_tier: dict = {}
    revenue_by_category: dict = {}
    margin_by_category: dict = {}
    # 全量汇总
    total_cost_cny: float = 0
    total_revenue_cny: float = 0
    total_margin_cny: float = 0
    margin_rate: float = 0  # 毛利率 0~1（毛利/收入）
    pending_moderation: int = 0


# ---------- FLOW APP 同步 ----------
class SyncStatusOut(BaseModel):
    sync_id: Optional[int] = None
    status: str  # not_synced | pending | synced | failed
    audio_cdn_url: Optional[str] = None


class FlowSongOut(BaseModel):
    song_id: int
    title: str
    audio_cdn_url: Optional[str] = None
    cover_url: Optional[str] = None
    lyric: Optional[str] = None
    style: Optional[str] = None
    synced_at: str


# ---------- FLOW APP 同步：令牌交换 / 绑定 ----------
class FlowLinkReq(BaseModel):
    """用户授权绑定：APP 同时持有音乐 JWT 与 FLOW subscriber_id 时调用（幂等 upsert）。"""

    subscriber_id: str


class FlowExchangeReq(BaseModel):
    """FLOW 后端 server-to-server：用 subscriber_id 换取 scope 限定(sync:flow) JWT。"""

    subscriber_id: str


class FlowExchangeResp(BaseModel):
    access_token: str
    token_type: str = "bearer"
    scope: str = "sync:flow"
