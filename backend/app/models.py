"""ORM 模型：User / Song / 后台可控配置（engine_configs / routing_rules / pricing_tiers）。"""

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    false,
    text,
    true,
)

from app.db import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, index=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    # §21.4 G-Consent (T3.11c): FLOW 구독자(subscriber_id) ↔ 음악 사용자 연결은
    # **UserLink 단일 브리지 테이블** 로만 관리된다(models.py:UserLink).
    # [D35] User.flow_subscriber_id 는 읽기/쓰기 0건의 데드 컬럼 + 마이그레이션 부재여서 삭제.
    tier_key = Column(
        String(32), nullable=True
    )  # 用户当前套餐（计费归因；空=未订阅/按 free 标准价）
    # ---- GAP-002 邮箱验证 / 密码重置 / MFA / 失败锁定 ----
    # 验证与重置 token **只存 sha256 摘要**：明文仅出现在邮件链接里，DB 备份或日志
    # 泄露也换不出可用 token（存明文 = 任何一次库泄露都等于账号接管）。
    email_verified = Column(Boolean, default=False, nullable=False, server_default=false())
    verify_token = Column(String(64), nullable=True)  # 邮箱验证 token 的 sha256 摘要
    verify_sent_at = Column(DateTime(timezone=True), nullable=True)  # 上次发信时刻（重发冷却）
    reset_token = Column(String(64), nullable=True)  # 密码重置 token 的 sha256 摘要
    reset_sent_at = Column(DateTime(timezone=True), nullable=True)  # 重置发信时刻（冷却+过期）
    mfa_secret = Column(String(64), nullable=True)  # TOTP base32 密钥；永不进任何响应模型
    # 与 mfa_secret 拆成两列：绑定必须 setup→enable 两步确认。若以「secret 非空」当启用开关，
    # 用户扫错码的那一刻就把自己锁在门外（无法登录、也无法自行关闭）。
    mfa_enabled = Column(Boolean, default=False, nullable=False, server_default=false())
    failed_logins = Column(Integer, default=0, nullable=False, server_default=text("0"))  # 连续失败计数
    # GAP-002 登录失败锁定 vs GAP-023 运营封禁：两件事，刻意分成两组列。
    # 锁定 = 自动、临时、由失败次数触发；封禁 = 人工、持续、由运营决定。
    # 复用 locked_until 表达封禁会让"锁定期满自动恢复"把封禁一并解除。
    locked_until = Column(DateTime(timezone=True), nullable=True)  # 账户锁定截止时刻
    is_banned = Column(Boolean, default=False, nullable=False, server_default=false())  # 运营封禁
    banned_at = Column(DateTime(timezone=True), nullable=True)  # 封禁时刻（None = 从未封禁）
    ban_reason = Column(String(500), default="", nullable=False, server_default="")  # 封禁理由（申诉/审计用）

    # GAP-003 OAuth 身份绑定（Google / Kakao / Naver 授权码登录）。
    # 与 email 并存而非替代：email 是「展示/联系/密码登录」的标识，
    # (oauth_provider, oauth_sub) 才是 OAuth 侧的**稳定身份**（sub 不随邮箱变更而变，
    # 故身份查找永远优先于邮箱查找，见 app/oauth/service.py:_resolve_user）。
    # 两者皆空 = 纯邮箱账号；二者同时存在 = 已绑定 OAuth。
    oauth_provider = Column(String(32), nullable=True)  # google | kakao | naver
    oauth_sub = Column(String(255), nullable=True)  # provider 侧唯一用户标识
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    # GAP-021（生成完成通知）：用户级偏好开关。
    # ⚠ 默认关（opt-in）= 严格保守：避免无条件邮件骚扰 + 未配置 SES 时的无效发信成本。
    # 与「Web Push 订阅不落表」的硬约束（test_notifications.py:test_store_does_not_touch_orm_metadata）
    # 是**两件事**：那是「订阅端点」（设备凭据，刻意不入业务表），这是「是否愿意被通知」
    # 的账户偏好，语义不同，故合法地落在 users 上。
    notify_generation_done_enabled = Column(
        Boolean, default=False, nullable=False, server_default=false()
    )

    __table_args__ = (
        # 同一 provider 的同一 sub 只能对应一个账号（并发首登的最终防线）。
        # 用唯一约束而非仅靠应用层 check-then-insert：两个并发请求会同时通过查询。
        # 注：SQLite/Postgres 的唯一索引对 NULL 不生效，故纯邮箱账号（双 NULL）
        # 不受影响，可无限存在。
        UniqueConstraint("oauth_provider", "oauth_sub", name="uq_users_oauth_identity"),
    )


class Song(Base):
    __tablename__ = "songs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True)
    title = Column(String(255), default="未命名")
    style = Column(String(512), default="")
    prompt = Column(Text, default="")
    lyric = Column(Text, default="")
    status = Column(String(32), default="pending")  # pending/processing/completed/failed
    external_id = Column(
        String(255), default=""
    )  # 供应商任务 id（轮询用，open.suno.cn 为数字 task_id）
    custom_id = Column(
        String(255), default=""
    )  # 供应商歌曲 id（续写/翻唱基准，open.suno.cn 为 UUID custom_id）
    audio_url = Column(Text, default="")
    cover_url = Column(Text, default="")
    # 逐字时间轴（mureka 返回 choices[].lyrics_sections 的 JSON 字符串）。
    # 仅详情接口按需曝光；列表/广场接口不返回以控制包体（每首 100~300 字歌词 → ~10KB JSON）。
    lyrics_sync = Column(Text, nullable=True)
    # 回源到自有桶 / CDN 后的**稳定**链接。主播放链路（SongOut.audio_url、FLOW sync、
    # 下载、试听）都优先走它；为空时按需回填，回填不可用才回退上游 audio_url。
    audio_cdn_url = Column(Text, default="")
    # ---- GAP-019 归档 / 保留 ----
    # 归档迁移发生的时间（None = 未归档）。存时间戳而非布尔：既标记状态，又能算出
    # 「归档多久了」，供「归档 → 回热」的老歌点播策略（见 docs/CDN_ARCHIVE_RUNBOOK.md）。
    archived_at = Column(DateTime(timezone=True), nullable=True)
    # 该曲音频对象在桶里的存储类别（STANDARD / STANDARD_IA / ...）。空 = 桶默认。
    # 落库原因：存储类别是**运维事实**（费用与读取延迟都取决于它），只存在于
    # S3 控制台时无法与曲目对齐排查，也无法断言「归档作业是否真的生效」。
    storage_class = Column(String(32), server_default="", default="")
    # ---- GAP-010 音频格式（下载侧可选）----
    # ⚠️ Mureka 生成入参无格式/码率字段（官方 OpenAPI spec 实证，见 orchestrator/mureka.py 头部），
    # 故这里**不存**「用户点选的质量参数」而是存「下载时选定的格式」：
    # 上游 choices[] 已同时回传 url(mp3)/flac_url/wav_url，本产品只在其间路由。
    # 不设 quality_kbps：上游无法按码率出音频，存了就是臆造字段。
    audio_format = Column(
        String(8), default="mp3", server_default="mp3", nullable=False
    )  # 下载格式 mp3|wav|flac（默认 mp3）
    # ---- GAP-016 内容权利：授权类型 ----
    # commercial / non_commercial。生成时选择；默认 non_commercial（保守）：
    # 未显式声明商用的产物不得被下游当作可商用素材（比"默认商用"误用风险低一个量级）。
    # 该字段随分享/下载联动展示（SongOut.license），便于用户与运营判定使用边界。
    license = Column(
        String(16), default="non_commercial", server_default="non_commercial", nullable=False
    )
    audio_wav_url = Column(Text, default="")  # 上游无损 WAV 链接（choices[].wav_url）
    audio_flac_url = Column(Text, default="")  # 上游无损 FLAC 链接（choices[].flac_url）
    preview_audio_url = Column(Text, default="")  # 试听片段（前 N 秒）；未订阅者只能拿到这个链接
    preview_seconds = Column(Integer, nullable=True)  # 试听片段时长（秒）；空=未生成片段
    make_instrumental = Column(Boolean, default=False)
    synced_to_flow = Column(Boolean, default=False)  # 是否已同步到 FLOW APP 音乐单
    # 生成任务元数据（对标 Suno 真实能力）
    task_type = Column(String(32), default="generate")  # generate|custom|extend|cover|remix
    model_version = Column(String(64), default="")  # 模型版本，如 chirp-v4.5
    duration = Column(Integer, default=0)  # 歌曲时长（秒）
    parent_song_id = Column(
        Integer, ForeignKey("songs.id"), nullable=True
    )  # extend/cover/remix 血缘
    moderation_status = Column(String(32), default="pending")  # pending|approved|rejected 人工复核
    moderation_note = Column(Text, default="")  # 驳回原因
    is_public = Column(
        Boolean, default=False, server_default=false(), nullable=False
    )  # 是否公开到发现页
    play_count = Column(
        Integer, default=0, server_default=text("0"), nullable=False
    )  # 播放次数（发现页排序）
    vocal_gender = Column(
        String(16), nullable=True
    )  # 声线性别 male/female/None（对标 Suno vocalGender）
    # 音色克隆 ID（Mureka /v1/vocal/clone 产出）。落库原因：后台任务从 DB 重建 GenerateRequest，
    # 不落库则 generate 路径拿不到（easy/instrumental 是一次性参数，不入此列）。
    vocal_id = Column(String(64), nullable=True)
    clip_seconds = Column(Integer, nullable=True)  # extend 续写起点（秒，对标 Suno continueAt）
    # extend 衔接方向：tail(从尾部续) | head(从头部续)。
    # 落库原因同 vocal_id：后台任务 `_process_song` 是从 DB 的 Song 重建 GenerateRequest，
    # 不落库则 extend_type 永远重建为 None → 供应商侧一律按 tail 处理，
    # "从头部衔接" 在 API/UI 上都变成静默丢弃的假功能（Mureka mureka-8 该字段必填）。
    extend_type = Column(String(16), nullable=True)
    content_category = Column(
        String(32), nullable=True, default="general"
    )  # 内容分类：general|christian_worship|instrumental（引擎路由依据）
    engine_name = Column(String(64), nullable=True)  # 实际选用引擎（计费归因；P1 起按分类路由）
    # ---- 计费三要素（单位核算，人民币元） ----
    cost_cny = Column(
        Numeric(10, 4), nullable=True, default=0
    )  # 本次生成成本（供应商实际计费估算；= 引擎单价 × 生成首数 n）
    price_cny = Column(
        Numeric(10, 4), nullable=True, default=0
    )  # 本次生成对用户计费（按其套餐标准价快照；未接支付时为名义收入 notional revenue）
    margin_cny = Column(
        Numeric(10, 4), nullable=True, default=0
    )  # 毛利 = price_cny - cost_cny（负=亏损）
    tier_key = Column(
        String(32), nullable=True, index=True
    )  # 生成时适用的套餐（计费归因；空=free/未订阅）
    # ---- GAP-011 回收站：软删时间戳（NULL = 不在回收站）----
    # 为何是软删而不是硬删：硬删（DELETE /songs/{id}）之后用户没有任何反悔路径，
    # 而生成是有成本的（每首都在 wallet/配额上扣过）——一次误点等于把已付费的产物烧掉。
    # 回收站只解决「找回」；「不留数据」由 30 天后清理任务负责（见 routers/library.py:purge）。
    # ⚠ 不建数据库级默认值：默认值（如 now()）会让「刚插入的歌 = 已删除」，
    #   而这里的 NULL 是有语义的（未删除），不能靠 server_default 兜底。
    deleted_at = Column(DateTime(timezone=True), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class SongLike(Base):
    """广场互动：点赞。

    一人一首一赞（song_id+user_id 唯一约束，切换式幂等）。
    设计约束：广场**不做留言/评论**，只保留无文本互动（赞/收藏/星标），
    以降低 UGC 审核成本与中国大陆合规风险。
    """

    __tablename__ = "song_likes"
    __table_args__ = (UniqueConstraint("song_id", "user_id", name="uq_song_likes_song_user"),)

    id = Column(Integer, primary_key=True, index=True)
    song_id = Column(Integer, ForeignKey("songs.id"), index=True, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class SongFavorite(Base):
    """广场互动：收藏（与点赞分离，语义不同：赞=认同，收藏=想再听）。"""

    __tablename__ = "song_favorites"
    __table_args__ = (UniqueConstraint("song_id", "user_id", name="uq_song_favs_song_user"),)

    id = Column(Integer, primary_key=True, index=True)
    song_id = Column(Integer, ForeignKey("songs.id"), index=True, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class SongRating(Base):
    """广场互动：星标评分 1..5（覆盖式，一人一首一个分数）。"""

    __tablename__ = "song_ratings"
    __table_args__ = (UniqueConstraint("song_id", "user_id", name="uq_song_ratings_song_user"),)

    id = Column(Integer, primary_key=True, index=True)
    song_id = Column(Integer, ForeignKey("songs.id"), index=True, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    score = Column(Integer, nullable=False)  # 1..5
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class SyncRecord(Base):
    __tablename__ = "sync_records"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True)
    song_id = Column(Integer, ForeignKey("songs.id"), index=True)
    status = Column(String(32), default="pending")  # pending | synced | failed
    audio_cdn_url = Column(Text, default="")
    flow_song_id = Column(String(255), default="")  # Push 模式（独立 FLOW 后端）预留
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class UserLink(Base):
    """FLOW 구독자(subscriber_id) ↔ 음악 사용자(user_id) 매핑(좁은 브리지).

    - 구독 상태는 음악 도메인 only; FLOW 는 자기 토큰으로 음악 구독을 추론/요구하지 않음.
    - 이 테이블만 공유 경계(브리지)에 속하며 PII 원문은 보관하지 않음(가명키 매핑만).
    - co-location 결정(기획서 §2.3 / §3.2): 별도 identity + user_link 브로커.

    [2026-10-11 #72 결정 1 — A안: 독립 DB + 좁은 브리지]
    `music_user_id` 는 **일반 컬럼**(외래키 아님)이다. 두 서비스가 각자 자기 DB 의
    주권을 갖는 이상, FLOW 쪽 DB 에서 이 값을 `users.id` 로 FK 참조할 수 없다
    (cross-database FK 는 성립하지 않음). 정합성은 FK 대신 좁은 브리지의
    upsert 경로(`auth.py` flow-link)와 토큰 교환(`flow-exchange`)이 책임진다.
    ※ 종전 `ForeignKey("users.id")` 는 공유 DB 전제(MUSIC_PLATFORM_MIGRATION_2026-09-10)
      잔재 — 해당 문서는 본 결정으로 supersede 됨.
    """

    __tablename__ = "user_links"

    id = Column(Integer, primary_key=True, index=True)
    flow_subscriber_id = Column(String(255), unique=True, index=True, nullable=False)
    music_user_id = Column(Integer, index=True)  # cross-DB: FK 아님(#72 결정 1)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class EngineConfig(Base):
    """引擎注册与开关（后台控制）。provider 即引擎名，与 MusicProvider 名空间一致。

    api_key 当前明文存储、仅管理员可读且 GET 时脱敏返回；生产应结合 KMS/环境变量加密，
    本层不做伪加密（拒绝幻觉：未接入加密库时不声称已加密）。
    """

    __tablename__ = "engine_configs"

    provider = Column(
        String(32), primary_key=True
    )  # suno/mock/musicgen/ace_step_private/tencent_mps/volcano
    display_name = Column(String(64), default="")
    enabled = Column(Boolean, default=True, nullable=False, server_default=true())
    is_default = Column(Boolean, default=False, nullable=False, server_default=false())
    api_base = Column(String(512), default="")
    api_key = Column(String(512), default="")  # 仅管理员可写；存储时为密文(GAP-007)，GET 脱敏
    cost_per_song_cny = Column(
        Numeric(10, 4), default=0, nullable=False, server_default=text("0")
    )  # 估算成本（成本报表/计费回退）
    priority = Column(Integer, default=0, nullable=False, server_default=text("0"))
    notes = Column(Text, default="")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # ---- api_key 静态加密(at-rest, GAP-007) ----
    def set_api_key(self, plain: str | None) -> None:
        """写入时加密存储；空值 → 空字符串(비밀키 미설정)。"""
        from app.security.crypto import encrypt_secret

        self.api_key = encrypt_secret(plain) or ""

    def get_api_key(self) -> str | None:
        """读取时解密；구 plaintext 행은 그대로 반환(호환)."""
        from app.security.crypto import decrypt_secret

        return decrypt_secret(self.api_key) or None


class RoutingRule(Base):
    """内容分类 -> 引擎（后台控制，替代硬编码 _ROUTE_TABLE）。

    同一 category 可有多条规则，按 priority 降序取第一条 enabled 规则。
    """

    __tablename__ = "routing_rules"

    id = Column(Integer, primary_key=True, index=True)
    category = Column(
        String(32), nullable=False, index=True
    )  # general|christian_worship|instrumental
    provider = Column(String(32), nullable=False)  # 指向 engine_configs.provider
    priority = Column(Integer, default=0, nullable=False, server_default=text("0"))
    enabled = Column(Boolean, default=True, nullable=False, server_default=true())
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class PricingTier(Base):
    """套餐分层计费（后台控制）。不同引擎名差异化收费的依据。"""

    __tablename__ = "pricing_tiers"

    id = Column(Integer, primary_key=True, index=True)
    # ⚠ 不要写 unique=True + index=True: 那样在 PG 上会同时落 **两条** 唯一索引
    #   （`pricing_tiers_tier_key_key` UNIQUE CONSTRAINT + `ix_pricing_tiers_tier_key`
    #   UNIQUE INDEX），与迁移（仅 unique=True）不一致 → `alembic check` 报漂移。
    #   sqlite 恰好把二者折叠成一条 autoindex，所以这个漂移在 sqlite 上一直看不见。
    #   迁移文件 c2d3e4f5a6b7 的真相是 `unique=True`，此处对齐它即消除漂移。
    tier_key = Column(
        String(32), unique=True, nullable=False
    )  # free|lite|standard|sacred|enterprise
    tier_name = Column(String(64), default="")
    engine_provider = Column(String(32), nullable=True)  # 该套餐默认引擎（None=按路由规则）
    price_cny = Column(Numeric(10, 4), default=0, nullable=False, server_default=text("0"))
    # step-2 多币种: 各币种标价的套餐价; 缺省 0 → 结账时按 fx_rate_* 由 CNY 换算.
    price_usd = Column(Numeric(10, 4), default=0, nullable=False, server_default=text("0"))
    price_krw = Column(Numeric(10, 4), default=0, nullable=False, server_default=text("0"))
    credits_per_song = Column(Integer, default=1, nullable=False, server_default=text("1"))
    # 该档位绑定的生成模型（如 mureka-7.6 / mureka-9）。空 = 用引擎默认模型(settings.mureka_model)。
    # ⚠ 저가 티어(lite) 는 "값싼 모델 바인딩" 으로만 진짜 원가절감이 된다 —
    #   가격만 내리고 모델을 안 바꾸면 원가가 그대로여서 마진이 음수로 뒤집힌다.
    model_version = Column(String(64), default="", nullable=True)
    description = Column(Text, default="")
    enabled = Column(Boolean, default=True, nullable=False, server_default=true())
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class AuditLog(Base):
    """管理后台审计日志：记录所有 /api/admin 访问（含变更与失败尝试）。

    GAP-016 起同时承载**审核动作审计**：admin 的批准/驳回（单条与批量）在
    middleware 的访问日志之外，再写一条结构化记录（action/target_*），
    使「谁在何时对哪首歌做了什么判定」可独立查询与追责。
    `method/path/status_code` 对审核动作行填等价信息（POST + 端点路径 + 200），
    保持与访问日志同表可查（不另立一张 audit 表使查询分叉）。
    """

    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    method = Column(String(8), nullable=False)
    path = Column(String(255), nullable=False, index=True)
    status_code = Column(Integer, nullable=False)
    actor_token_prefix = Column(String(16), default="")  # 令牌前 6 字符，便于追溯但不泄露全令牌
    actor_role = Column(String(16), default="none")  # super / viewer / none(无效令牌)
    client_ip = Column(String(64), default="")
    # ---- GAP-016 审核动作结构化字段（访问日志行为 NULL/空）----
    action = Column(String(32), nullable=True, index=True)  # approve|reject|takedown|report_resolve
    target_type = Column(String(16), nullable=True)  # song|report
    target_id = Column(Integer, nullable=True, index=True)
    detail = Column(Text, nullable=True)  # 备注/原因（驳回理由、下架原因等）
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class ContentReport(Base):
    """内容举报 / DMCA 侵权通知（GAP-016）。

    与「审核队列」分开建表的原因：审核队列是对**自有生成物**的事前/事后把关，
    举报是**外部权利人或用户**发起的投诉，两件事的触发方、时效、法务含义都不同。
    举报可指向任何歌曲（含已公开曲），未处理前进入人工下架流程。

    `status`：open（待处理）| resolved（已处理，含下架/驳回举报）| dismissed（举报不成立）。
    `resolution`：处理结论（takedown / rejected / duplicate ...），供审计与申诉回溯。
    """

    __tablename__ = "content_reports"

    id = Column(Integer, primary_key=True, index=True)
    song_id = Column(Integer, ForeignKey("songs.id"), index=True, nullable=False)
    reporter_user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    # 举报人联系邮箱：DMCA 场景举报人可能非注册用户（外部权利人），故单列且可为空。
    reporter_email = Column(String(255), default="")
    # copyright | hate | violence | sexual | spam | other —— 前端枚举，服务端校验。
    reason = Column(String(32), nullable=False, default="other")
    detail = Column(Text, default="")
    status = Column(String(16), nullable=False, default="open", server_default="open", index=True)
    resolution = Column(String(32), default="")  # takedown | rejected | duplicate | ...
    resolved_by = Column(String(64), default="")  # admin actor 标识（role:prefix）
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


# ═══════════════════════════════════════════════════════════════════════════════
# GAP-011 库组织：播放列表 / 文件夹 / 回收站
#
# 设计取舍（结论先行，理由随各模型注释）：
#   · 播放列表与文件夹 **分成两个模型**，不给 Playlist 加 type 字段。
#   · 收藏**复用** SongFavorite（models.py:119），不另立 song_playlist 之外的收藏表。
#   · 回收站只在 Song 上加 deleted_at（软删），不建 trash 表——副表要么与 songs 等宽
#     （纯冗余），要么只存 id（删完只剩悬空指针，还要 JOIN 才能列清单）。
# ═══════════════════════════════════════════════════════════════════════════════


class Playlist(Base):
    """用户歌单（自己排的一张表）。

    与 Folder 分家（而不是在 Playlist 上开 type 字段）的理由——三处语义分叉，
    任何一处用 type 都要在**每个使用点**加 if，那才是真正的分叉：

    1. 水合：歌单要有序地展开成歌曲流（播放/排序），文件夹只是给别的实体贴归类。
    2. 归属：一首歌可同时属于多个歌单（多对多），但只能在一个文件夹（树形分区）。
    3. 删除：删歌单 = 解绑成员（歌还在），删文件夹 = 子项退回根（歌也还在）。
       二者都不能级联删歌。

    is_public 与 Song.is_public 同义：公开歌单可被他人浏览（歌单本身的分享）。
    """

    __tablename__ = "playlists"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    name = Column(String(255), nullable=False, default="未命名歌单")
    description = Column(Text, default="")
    cover_url = Column(Text, default="")  # 可为空：前端未上传时回退用首曲封面
    is_public = Column(Boolean, default=False, server_default=false(), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class PlaylistSong(Base):
    """歌单成员 + 手动排序位。

    position 是**稀疏整数**而非连续序号：一行改动（拖拽）只写一行，
    不必为「插到第 2 位」把后面所有行整体 +1（那是 O(n) 写放大 + 并发覆盖）。
    同歌单内 position 由应用层去重（见 routers/library.py:_normalize_positions）。
    ⚠ 不能只靠 (playlist_id, position) 唯一约束来维持：重排过程中必然出现
    临时重复值，唯一约束会让「拖拽」直接 500。
    """

    __tablename__ = "playlist_songs"
    __table_args__ = (
        # 同一首歌在同一歌单里只能出现一次（重复添加 = 幂等 no-op，不是两条记录）
        UniqueConstraint("playlist_id", "song_id", name="uq_playlist_songs_playlist_song"),
        Index("ix_playlist_songs_playlist_position", "playlist_id", "position"),
    )

    id = Column(Integer, primary_key=True, index=True)
    playlist_id = Column(
        Integer, ForeignKey("playlists.id", ondelete="CASCADE"), index=True, nullable=False
    )
    song_id = Column(Integer, ForeignKey("songs.id"), index=True, nullable=False)
    position = Column(Integer, default=0, nullable=False, server_default=text("0"))
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class Folder(Base):
    """文件夹（分组容器）。

    为什么是「一层」而不是父子树：库组织的实际需求是「把作品分堆」，
    树形目录带来的移动/递归删除/循环引用校验在这里没有用户价值，只增加出错面。
    真要嵌套，后续加 parent_id 即可，当前不预留。

    为什么容器与成员分两张表（而不是每行自带 name）：
    用户心智里「文件夹」是一个可以整组改名/整组删除的东西。把 name 复制到每条
    成员行上，改名就要 update 全部行、而「空文件夹」根本无法表达（没有行 = 不存在）。
    成员行是 FolderItem。
    """

    __tablename__ = "folders"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_folders_user_name"),)

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    name = Column(String(255), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class FolderItem(Base):
    """文件夹成员：一条 = 某个实体被归入某个文件夹。

    item_type 多态（song | playlist）而非两张关联表：文件夹对不同实体的操作
    完全一致（放入 / 移出 / 删除），分表会变成两套重复端点。
    代价是不能建数据库级外键到 item_id —— 由端点校验归属兜底（只允许放自己的东西），
    与本项目既有取舍一致（见 UserLink.music_user_id 注释）。
    """

    __tablename__ = "folder_items"
    __table_args__ = (
        # 同一项在同一个文件夹里只允许一条（重复添加 = 幂等 no-op）
        UniqueConstraint("folder_id", "item_type", "item_id", name="uq_folder_items_member"),
    )

    id = Column(Integer, primary_key=True, index=True)
    folder_id = Column(
        Integer, ForeignKey("folders.id", ondelete="CASCADE"), index=True, nullable=False
    )
    item_type = Column(String(16), nullable=False)  # song | playlist
    item_id = Column(Integer, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class TrashKeepRule(Base):
    """回收站保留策略（单行配置表）。

    ⚠ 자리표시가 아니라 **설정의 거처**: 「保留 30 天」这条策略若只写死在代码常量里，
    运维要调整（合规要求 7 天 / 付费用户 90 天）就得改代码发版。
    这里放一行默认 30，端点读它决定过期与否（见 routers/library.py:TRASH_KEEP_DAYS）。
    """

    __tablename__ = "trash_keep_rules"

    id = Column(Integer, primary_key=True, index=True)
    keep_days = Column(Integer, default=30, nullable=False, server_default=text("30"))
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


# ═══════════════════════════════════════════════════════════════════════════════
# GAP-014 搜索 / 发现：结构化标签（Tag / SongTag）
#
# 设计取舍（结论先行，理由见各模型注释）：
#   · Tag 是**实体表**而不是每首歌自带 keywords 列 —— 标签的价值在「多对多」，
#     一个词要能反查到一批歌；把标签塞进 Song 的 Text 列会让「按标签浏览」
#     退化成每首歌一次 LIKE 全表扫。
#   · Tag 有 (category, value) 唯一约束 —— 同义词必须归并到一个规范值，
#     否则 `伤感` 与 `sad` 各自成标签，浏览面被切成两半。
#   · SongTag 存 `source`（auto/manual/enum）：标签是抽取来的，抽取规则会演进，
#     没有来源就永远说不清「这个标签是哪一版规则加上去的」，重算时也不敢覆盖用户手写的。
#   · 相似推荐**不建 embedding 表**：评估结论见 app/search/similar.py 头部
#     （不臆造向量字段；标签重合 + BM25 已覆盖当前语料规模）。
# ═══════════════════════════════════════════════════════════════════════════════


class Tag(Base):
    """结构化标签：曲风 / 情绪 / 语言 / 声部 / 形式。

    `(category, value)` 唯一 —— value 是**规范值**（如 `sad` 而非 `伤感`），
    变体词写进 `aliases` 供抽取层查找。这样前端拿到的永远是同一套词，
    而抽取层可以持续补同义词而不产生新标签。

    `usage_count` 是**冗余计数**而非实时 count(*)：
    标签列表端点要按热度排 chip，而 count 需要 JOIN 全表 song_tags ——
    这是典型的读多写少，用一列计数换掉每次浏览的一次全表聚合。
    ⚠ 代价：它必须与 song_tags 同步（由 app/search/service.py:sync_song_tags 单一入口维护）。
    """

    __tablename__ = "tags"
    __table_args__ = (
        UniqueConstraint("category", "value", name="uq_tags_category_value"),
    )

    id = Column(Integer, primary_key=True, index=True)
    # genre | mood | language | vocal | form（见 app/search/tags.py:VALID_CATEGORIES）
    category = Column(String(16), nullable=False, index=True)
    value = Column(String(32), nullable=False)  # 规范值（小写/拉丁）
    label = Column(String(64), nullable=False, default="")  # 展示名（可含中文）
    aliases = Column(Text, nullable=False, default="")  # 逗号分隔的变体词（抽取用）
    usage_count = Column(Integer, nullable=False, default=0, server_default=text("0"))
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class SongTag(Base):
    """歌曲 ↔ 标签 关联。

    `source` 记录标签来源：
      · auto   —— 从 style/prompt/title 抽取（规则演进时可安全重算）
      · enum   —— 由结构化列映射（vocal_gender / make_instrumental / content_category）
      · manual —— 用户或后台显式写入（重算时**不得覆盖**，那是人的判断）

    ⚠ 唯一约束含 `source`：同一首歌的同一个标签可能同时来自 enum 与 manual
    （用户手动确认了"纯音乐"），两条都保留比强行合并更诚实 ——
    合并后 source 就只剩一个，谁写的就说不清了。
    """

    __tablename__ = "song_tags"
    __table_args__ = (
        UniqueConstraint("song_id", "tag_id", "source", name="uq_song_tags_song_tag_source"),
        Index("ix_song_tags_tag_song", "tag_id", "song_id"),
    )

    id = Column(Integer, primary_key=True, index=True)
    song_id = Column(Integer, ForeignKey("songs.id"), index=True, nullable=False)
    tag_id = Column(Integer, ForeignKey("tags.id"), index=True, nullable=False)
    source = Column(String(8), nullable=False, default="auto")  # auto | enum | manual
    created_at = Column(DateTime(timezone=True), server_default=func.now())


# ═══════════════════════════════════════════════════════════════════════════════
# GAP-023 运营：特性开关（灰度发布的最小机制）
#
# 设计取舍（结论先行）：
#   · 表而不是 env：env 开关改一次要重启容器，灰度发布要的恰恰是「不重启地开/关」。
#   · **无 targeting 字段**：只做「按用户 id 取模的百分比灰度 + 强制名单」，
#     不做地域/设备/套餐多维定向 —— 那套规则一旦写进表，就没人能说清
#     「这个用户为什么在灰度里」。真要按套餐/地区分，先用两个 flag（见 rollout）。
#   · 默认关闭（enabled=False / rollout=0）：开关的**存在**不改变任何现有行为，
#     既有的灰度基线是「全关闭」，打开必须是显式动作。
# ═══════════════════════════════════════════════════════════════════════════════


class FeatureFlag(Base):
    """特性开关：admin 可改、前端可读的灰度发布基础。

    `rollout`（0–100）是**确定性**灰度：由 `hash(key:user_id) % 100 < rollout` 决定，
    同一用户对同一开关的判定永远一致 —— 用随机数会让同一用户每次刷新在不同分支间
    跳变（体验撕裂 + 无法复现问题），这正是灰度机制最容易踩的坑。

    名称沿用 Freemium 的**积分档位**（free/lite/standard/sacred/enterprise），
    不另造一套枚举：用户侧的套餐字段已经是 `tier_key`，两处各写一套必然漂移。
    """

    __tablename__ = "feature_flags"

    id = Column(Integer, primary_key=True, index=True)
    key = Column(String(64), unique=True, nullable=False, index=True)  # 如 song_stem_export
    description = Column(String(255), nullable=False, default="")
    enabled = Column(Boolean, default=False, nullable=False, server_default=false())
    # 灰度百分比 0–100（0 = 关闭；非 0 仍需 enabled=True 才生效，见 app/feature_flags.py）
    rollout = Column(Integer, default=0, nullable=False, server_default="0")
    # 仅对这些用户强制开启（comma-separated user id）。刻意用字符串而非关联表：
    # 名单是「临时手挑的几个账号」，关联表要 JOIN 才能看全，而这里要看的就是那一串数字。
    allow_user_ids = Column(Text, default="", nullable=False, server_default="")
    # 仅对这些用户强制关闭（灰度期排障用；优先级高于 allow/rollout）
    deny_user_ids = Column(Text, default="", nullable=False, server_default="")
    # 最低套餐门槛（"" = 不限）。灰度期用来「先给付费用户开」，
    # 与 rollout 是两个正交维度：门槛决定"谁能"，百分比决定"其中多少人"。
    min_tier = Column(String(32), nullable=False, default="", server_default="")
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    created_at = Column(DateTime(timezone=True), server_default=func.now())


# ═══════════════════════════════════════════════════════════════════════════════
# GAP-022 开放平台：开发者凭证（DeveloperToken） + 出站 Webhook
#
# 设计取舍（结论先行）：
#   · **只增不改**：三张新表，不触碰 users/songs 等既有列。开放平台是**增量能力**，
#     不是既有认证的替代品 —— 既有 JWT 路径（auth.py）语义完全不变。
#   · token 只存 sha256 摘要：与 GAP-002 的邮箱验证/重置 token 同一策略。明文仅
#     在**创建响应里出现一次**；DB 泄露换不出可用凭证（存明文 = 一次库泄露 = 全量接管）。
#   · webhook 签名密钥（secret）与投递凭据是**两件事**：secret 必须可读回（要用来
#     算 HMAC 签名），所以明文存储 —— 它只用于**出站**请求签名，攻击者拿到它也只能
#     伪造"来自平台"的签名，无法借此登录任何账号。用官方密钥（JWT/管理员）签名则相反，
#     那是把登录凭据交给第三方。
# ═══════════════════════════════════════════════════════════════════════════════


class DeveloperToken(Base):
    """开发者 API 凭证：scope 限定 + 可吊销 + 可过期。

    与登录 JWT 的关键差异（也是它必须独立成表的理由）：
      · JWT 寿命由 env 一把决定，无法单独吊销某一枚 —— 开发者凭证必须能**即时吊销**
        （泄露后等 24h 自然过期不是"能吊销"）。
      · 开发者凭证要落审计（何时创建/何时最后一次使用），JWT 是无状态的，无处可落。
      · scope 是**声明的白名单**（如 `songs:generate`），不是 JWT 那种"有 scope 即降级"
        的全有全无判定。

    `token_hint` = 明文 token 的 **sha256 前 8 位**（刻意不是明文前 8 位）：
    列表页要能让开发者认出"这是在 CI 里用的那枚"，但明文前缀会把 token 熵泄露给
    任何能看到列表的人（XSS / 截图 / 后台运维）。哈希前缀同样可辨认，且不可逆。
    """

    __tablename__ = "developer_tokens"
    __table_args__ = (
        # 单列唯一索引：lookup 走 `WHERE token_hash = ?`，必须是索引（每次 API 调用都查）。
        Index("ix_developer_tokens_token_hash", "token_hash", unique=True),
        # 列表按用户过滤 + 创建时间倒序：复合索引让「我的 token 列表」免一次排序。
        Index("ix_developer_tokens_user_created", "user_id", "created_at"),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    name = Column(String(64), nullable=False, default="", server_default="")  # 人可读备注（CI/本地脚本）
    token_hash = Column(String(64), nullable=False)  # sha256 十六进制摘要（64 字符）
    token_hint = Column(String(8), nullable=False, default="", server_default="")  # 摘要前 8 位
    scopes = Column(Text, nullable=False, default="", server_default="")  # 逗号分隔白名单
    revoked = Column(Boolean, nullable=False, default=False, server_default=false())
    # NULL = 永不过期（开发者自行决定；吊销仍随时可用）。与 revoked 拆成两个字段：
    # 「已过期」与「已吊销」是不同事实 —— 前者是时间到了，后者是人为动作，
    # 合并成一个状态列会让审计追不出"谁在什么时候掐掉了它"。
    expires_at = Column(DateTime(timezone=True), nullable=True)
    last_used_at = Column(DateTime(timezone=True), nullable=True)  # 最后成功认证时刻（排查僵尸 token）
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class WebhookEndpoint(Base):
    """出站 Webhook 订阅端点。

    ⚠ `url` 在**注册时**做 SSRF 校验（拒绝内网/回环/链路本地/`file://`），
    但校验通过 ≠ 以后一直安全：DNS 可以在注册后改指向 169.254.169.254。
    因此**投递时再校验一次**（见 app/webhooks.py）。注册闸门只是把绝大多数
    恶意目标挡在门外，投递闸门才是真正的防线（DNS-rebinding / TOCTOU）。
    """

    __tablename__ = "webhook_endpoints"
    __table_args__ = (
        # 同一用户对同一 URL 不重复注册（重复注册只会造成同一事件投递两次）。
        # ⚠ 不含 secret/scopes：改了 scopes 应更新既有行而不是新建一条。
        UniqueConstraint("user_id", "url", name="uq_webhook_endpoints_user_url"),
        Index("ix_webhook_endpoints_user_created", "user_id", "created_at"),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    url = Column(String(512), nullable=False)
    secret = Column(String(64), nullable=False)  # HMAC-SHA256 签名密钥（明文：出站签名需回读）
    events = Column(Text, nullable=False, default="", server_default="")  # 逗号分隔事件白名单（""=全部）
    active = Column(Boolean, nullable=False, default=True, server_default=true())
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class WebhookDelivery(Base):
    """Webhook 投递日志：成败落库 + 可重放。

    为什么落库而不是只打日志：webhook 的失败**必须可观测**。对方服务挂了三天，
    开发者要能查到"哪些事件没送到、最后一次错在哪" —— 日志会滚掉，表不会。
    这也是「失败重试」能实现的前提：重试对象是**入库的投递记录**，不是内存里的闭包。

    `attempts` 记的是累计尝试次数（含首次）；`delivered_at` 非空即终态成功，
    之后不再重试（幂等靠对方按 `X-Flow-Delivery` 去重，不由平台担保）。
    """

    __tablename__ = "webhook_deliveries"
    __table_args__ = (
        # 待重试扫描：`WHERE delivered_at IS NULL ORDER BY created_at`。
        Index("ix_webhook_deliveries_status", "delivered_at", "created_at"),
        Index("ix_webhook_deliveries_endpoint", "endpoint_id", "created_at"),
    )

    id = Column(Integer, primary_key=True, index=True)
    endpoint_id = Column(Integer, ForeignKey("webhook_endpoints.id"), nullable=False, index=True)
    event = Column(String(64), nullable=False)
    payload = Column(Text, nullable=False, default="", server_default="")  # JSON 原文（重试要发同一份）
    attempts = Column(Integer, nullable=False, default=0, server_default=text("0"))
    status_code = Column(Integer, nullable=True)  # 最后一次 HTTP 状态码（网络失败为 NULL）
    error = Column(String(500), nullable=False, default="", server_default="")  # 最后一次失败原因
    delivered_at = Column(DateTime(timezone=True), nullable=True)  # 非空 = 成功终态
    created_at = Column(DateTime(timezone=True), server_default=func.now())
