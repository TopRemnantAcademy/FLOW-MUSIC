"""ORM 模型：User / Song / 后台可控配置（engine_configs / routing_rules / pricing_tiers）。"""

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
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
    email_verified = Column(Boolean, default=False, nullable=False, server_default="0")
    verify_token = Column(String(64), nullable=True)  # 邮箱验证 token 的 sha256 摘要
    verify_sent_at = Column(DateTime(timezone=True), nullable=True)  # 上次发信时刻（重发冷却）
    reset_token = Column(String(64), nullable=True)  # 密码重置 token 的 sha256 摘要
    reset_sent_at = Column(DateTime(timezone=True), nullable=True)  # 重置发信时刻（冷却+过期）
    mfa_secret = Column(String(64), nullable=True)  # TOTP base32 密钥；永不进任何响应模型
    # 与 mfa_secret 拆成两列：绑定必须 setup→enable 两步确认。若以「secret 非空」当启用开关，
    # 用户扫错码的那一刻就把自己锁在门外（无法登录、也无法自行关闭）。
    mfa_enabled = Column(Boolean, default=False, nullable=False, server_default="0")
    failed_logins = Column(Integer, default=0, nullable=False, server_default="0")  # 连续失败计数
    locked_until = Column(DateTime(timezone=True), nullable=True)  # 账户锁定截止时刻
    created_at = Column(DateTime(timezone=True), server_default=func.now())


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
    audio_cdn_url = Column(Text, default="")  # 回源到 CDN 后的稳定链接（供 APK 播放）
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
        Boolean, default=False, server_default="false", nullable=False
    )  # 是否公开到发现页
    play_count = Column(
        Integer, default=0, server_default="0", nullable=False
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
    """

    __tablename__ = "user_links"

    id = Column(Integer, primary_key=True, index=True)
    flow_subscriber_id = Column(String(255), unique=True, index=True, nullable=False)
    music_user_id = Column(Integer, ForeignKey("users.id"), index=True)
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
    enabled = Column(Boolean, default=True, nullable=False, server_default="1")
    is_default = Column(Boolean, default=False, nullable=False, server_default="0")
    api_base = Column(String(512), default="")
    api_key = Column(String(512), default="")  # 仅管理员可写；存储时为密文(GAP-007)，GET 脱敏
    cost_per_song_cny = Column(
        Numeric(10, 4), default=0, nullable=False, server_default="0"
    )  # 估算成本（成本报表/计费回退）
    priority = Column(Integer, default=0, nullable=False, server_default="0")
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
    priority = Column(Integer, default=0, nullable=False, server_default="0")
    enabled = Column(Boolean, default=True, nullable=False, server_default="1")
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class PricingTier(Base):
    """套餐分层计费（后台控制）。不同引擎名差异化收费的依据。"""

    __tablename__ = "pricing_tiers"

    id = Column(Integer, primary_key=True, index=True)
    tier_key = Column(
        String(32), unique=True, nullable=False, index=True
    )  # free|lite|standard|sacred|enterprise
    tier_name = Column(String(64), default="")
    engine_provider = Column(String(32), nullable=True)  # 该套餐默认引擎（None=按路由规则）
    price_cny = Column(Numeric(10, 4), default=0, nullable=False, server_default="0")
    # step-2 多币种: 各币种标价的套餐价; 缺省 0 → 结账时按 fx_rate_* 由 CNY 换算.
    price_usd = Column(Numeric(10, 4), default=0, nullable=False, server_default="0")
    price_krw = Column(Numeric(10, 4), default=0, nullable=False, server_default="0")
    credits_per_song = Column(Integer, default=1, nullable=False, server_default="1")
    # 该档位绑定的生成模型（如 mureka-7.6 / mureka-9）。空 = 用引擎默认模型(settings.mureka_model)。
    # ⚠ 저가 티어(lite) 는 "값싼 모델 바인딩" 으로만 진짜 원가절감이 된다 —
    #   가격만 내리고 모델을 안 바꾸면 원가가 그대로여서 마진이 음수로 뒤집힌다.
    model_version = Column(String(64), default="", nullable=True)
    description = Column(Text, default="")
    enabled = Column(Boolean, default=True, nullable=False, server_default="1")
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class AuditLog(Base):
    """管理后台审计日志：记录所有 /api/admin 访问（含变更与失败尝试）。"""

    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    method = Column(String(8), nullable=False)
    path = Column(String(255), nullable=False, index=True)
    status_code = Column(Integer, nullable=False)
    actor_token_prefix = Column(String(16), default="")  # 令牌前 6 字符，便于追溯但不泄露全令牌
    actor_role = Column(String(16), default="none")  # super / viewer / none(无效令牌)
    client_ip = Column(String(64), default="")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
