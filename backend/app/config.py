"""全局配置：从 .env 读取（参考 .env.example）。"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # 基础
    database_url: str = "sqlite:///./app.db"
    redis_url: str = "redis://localhost:6379/0"
    jwt_secret: str = "change-me-please"
    access_token_expire_minutes: int = 1440
    # ── [SEC] HttpOnly cookie 认证（localStorage → cookie，阻断 XSS 令牌窃取，OWASP A07）──
    # 认证 JWT 与「管理员共享密钥」不再存前端 JS 可读的 localStorage，改为后端写入
    # HttpOnly+Secure+SameSite cookie，浏览器随同源请求自动携带、JS 不可读。
    auth_cookie_name: str = "flow_auth"
    admin_cookie_name: str = "flow_admin"
    # 生产(HTTPS)必须 True；本地明文 HTTP 开发可临时设 COOKIE_SECURE=false（代码也会在
    # 请求为 http 时自动降级为不 secure，避免浏览器拒绝写入）。
    cookie_secure: bool = True
    # 同源部署(如 localhost:3000↔:8000)用 lax；若生产 frontend/API 跨站，须改为 "none" 且必须 secure。
    cookie_samesite: str = "lax"
    admin_cookie_max_age_seconds: int = 28800  # 8h
    # JWT 声明加固（2026-09-13 审计，MED）：补 aud/iss 防令牌跨服务误用/伪造。
    jwt_issuer: str = "flow-music-platform"
    jwt_audience: str = "flow-music-platform"
    # ── GAP-013 无状态分享链接签名密钥 ──
    # 独立密钥的意义：轮换它只作废**已发出的分享链接**，不必把所有用户的登录态一起踢下线。
    # 留空则回落 jwt_secret（routers/share.py:share_secret()），于是不配也能用。
    # ⚠ 只从 env 读，绝不落 git（仓库 Public）。生成：python -c "import secrets; print(secrets.token_urlsafe(32))"
    share_secret: str = ""
    # 允许的前端来源（逗号分隔）。默认覆盖三类开发/打包来源：
    #   · Web 前端（Next.js dev/prod：3000）
    #   · FLOW APP PWA 开发服务（4174）
    #   · Capacitor 打包 WebView 来源（capacitor://localhost 为 iOS，http(s)://localhost 为 Android）
    # ⚠ 生产部署务必在 .env 的 CORS_ORIGINS 显式列出真实来源（如 https://music.example.com）；
    #   否则浏览器端跨源请求（例如 FLOW APP 拉取歌曲详情里的 lyrics_sync 逐字歌词）会被 CORS 拦，
    #   而 curl / 服务端调用不受影响 —— 故该问题只在真机浏览器端暴露。
    cors_origins: str = (
        "http://localhost:3000,http://127.0.0.1:3000,"
        "http://localhost:4174,http://127.0.0.1:4174,"
        "capacitor://localhost,http://localhost,https://localhost"
    )

    # ── 对象存储 / CDN（S3 兼容：腾讯云 COS / Cloudflare R2 / AWS S3；可留空）──
    # 未配置（storage_endpoint / bucket / access_key / secret_key 任一为空）→
    # `app.storage.s3.is_configured()` 为 False，**全链路优雅降级**：直接回上游
    # `audio_url`（易过期但不报错），绝不 500。prod 未配 storage_* 即走这条。
    storage_endpoint: str = ""
    storage_region: str = "ap-hongkong"
    storage_bucket: str = ""
    storage_access_key: str = ""
    storage_secret_key: str = ""
    # CDN 加速域名（如 https://cdn.example.com）。**播放链接一律经它出口**：
    #   · 配了 → 上游回源 URL 由它拼装（s3._public_url），主播放链路下发的是它；
    #   · 没配 → 退化用 {endpoint}/{bucket}（仍可用，只是没有边缘加速）。
    # 之所以单列：回源端点（COS/R2 源站）常与对外加速域名不同，混用会把源站地址
    # 暴露给客户端并绕过 CDN 缓存。
    storage_public_base: str = ""

    # ── GAP-019 归档 / 保留（存储类别 + 生命周期）──
    # 默认存储类别（S3 StorageClass）。STANDARD / STANDARD_IA / INTELLIGENT_TIERING /
    # GLACIER_IR ...。留空 = 用桶默认。**低频档适合老歌**：省 40~60% 存储费，代价是首次
    # 读取延迟升高（播放链路已在客户端 CDN 侧缓存，命中时无感）。
    storage_class: str = ""
    # 归档阈值（天）：`audio_cdn_url` 非空且 `archived_at` 为空、创建时间早于该值的曲目，
    # 由 scripts/archive_cdn.py 迁移到 storage_class_archive。默认 90；<=0 = 关闭归档。
    storage_archive_after_days: int = 90
    # 归档目标存储类别（默认 STANDARD_IA 低频；GLACIER 类不可直接 GET，会打断播放，勿用）。
    storage_class_archive: str = "STANDARD_IA"
    # 生成完成时**同步**把音频回源到自有桶（在后台生成任务里 await，不占请求线程）。
    # 开 → 新曲第一个响应就是 CDN 链接（推荐，也是本 GAP 的验收口径）；
    # 关 → 仅靠请求路径的 write-behind 回填（首播一次走上游，之后走 CDN）。
    # 两者都受 storage 未配置时的优雅降级保护：未配 storage_* 时无论如何都直连上游。
    enable_cdn_mirror_on_generate: bool = True

    # 音乐供应商：切换生成后端（Route B 供应商抽象）。
    #   suno : 商业 API（open.suno.cn 协议基线），需配置 SUNO_API_KEY
    #   mock : 本地开发 / CI，不调用任何外部 API，返回固定假数据
    # 业务代码只依赖 MusicProvider 接口（create/get），切换供应商无需改动业务层。
    # 注：真实引擎选择已改为 DB 后台配置驱动（app.orchestrator.selection），
    # 此处的 music_provider 仅作为「无后台配置时的最后回退默认值」。
    music_provider: str = "mureka"
    # Suno 商业 API 基址。协议基线以 open.suno.cn 为准（详见 app/orchestrator/suno.py）。
    # 接入 sunoapi.org / api.suno.ai 等其他服务时，需按各自文档调整端点与字段。
    suno_api_base: str = "https://open.suno.cn/api/v1"
    suno_api_key: str = ""  # Bearer key（open.suno.cn / sunoapi.org）
    suno_email: str = ""  # 无 key 时，gcui-art/suno-api 风格邮箱密码登录换 token
    suno_password: str = ""
    song_poll_interval: int = 8
    song_poll_timeout: int = 240

    # Mureka 商业 API（昆仑万维 Skywork AI）：主引擎（中文+英文主线）。
    # 官方文档：https://platform.mureka.ai/docs/api/ ；基址 https://api.mureka.ai/v1
    # 鉴权：Authorization: Bearer <MUREKA_API_KEY>；无官方 webhook → 轮询（同 Suno）。
    mureka_api_base: str = "https://api.mureka.ai/v1"
    mureka_api_key: str = ""
    mureka_model: str = "mureka-9"  # V9（与 Suno V5.5 Elo 持平、韩语反超）
    mureka_n: int = 1  # 每次生成首数（1=单首省成本；2~3 需多 Song 落盘，见 P1.5）
    mureka_max_concurrent: int = 1  # 提交并发信号量上限，与采购档位对齐（入门1/基础5/标准15…）
    # 启动期「出口自检」模式：off=不探测 / warn=仅告警（默认，不阻断启动）/ strict=地域封锁时拒绝启动。
    # 背景：Mureka 仅允许特定区域访问，非允许区域出口返回 403。若等用户歌曲失败才发现，
    # 排查成本极高。故在启动期用**免费**的 GET /account/billing 探一次出口，把
    # 「403 地域封锁」从「事后被动失败」变成「部署即见」。
    mureka_preflight_mode: str = "warn"

    # ── MiniMax Music 3（**备选供应商评估用**，非生产默认引擎）──
    # 官方文档：https://platform.minimax.io/docs/api-reference/music-generation
    # 端点：POST https://api.minimax.io/v1/music_generation（基址填到 /v1）
    # 鉴权：Authorization: Bearer <MINIMAX_API_KEY>
    # ⚠️ 2026-09-26 核对官方公告：免费音乐生成接口（music-3.0-free / music-2.6-free /
    #    music-cover-free）自 **2026-08-20 起停止服务**；付费「音乐生成 / 歌词生成」
    #    接口自同日起不再面向新用户（历史付费用户可继续用 music-3.0）。
    #    → 默认 key 留空，评估脚本在缺 key / 被拒时**响亮失败**，不静默跳过。
    minimax_api_base: str = "https://api.minimax.io/v1"
    minimax_api_key: str = ""
    # 官方列为「推荐」且仍是限免档停服后唯一在册的付费档 id。
    # 历史付费账号可继续用；新账号自 2026-08-20 起拿不到音乐生成权限。
    minimax_music_model: str = "music-3.0"
    minimax_music_rpm: int = 120  # 官方：music-3.0 档 RPM=120（-free 档为 3）
    # 同步接口，整首歌生成耗时以分钟计；超时给足，避免误判成网络故障。
    minimax_music_timeout: float = 300.0

    # ── ACE-Step 1.5 私有引擎（本地 GPU 自托管，开源 MIT 可商用）──
    # 协议基线来自 CNB 仓库 chguanw/ace-step-1.5（MIT, © 2026 ACEStudio），
    # 经 cnb git get-archive 实取源码核对（vendor/ace-step-1.5/acestep/api_server.py）。
    # 官方默认端口 8001（`python acestep/api_server.py` → --port 默认 ACESTEP_API_PORT|8001）。
    ace_step_api_base: str = "http://127.0.0.1:8001"
    # 对应 ACE-Step 服务 --api-key / ACESTEP_API_KEY；留空 = 服务端免鉴权（仅建议绑 127.0.0.1）。
    ace_step_api_key: str = ""
    #  DiT 模型：turbo(8GB, 极快) / sft / base(50B, ~6GB 起, 需更多显存)。
    ace_step_model: str = "acestep-v15-turbo"
    ace_step_max_concurrent: int = 1  # 本地 GPU 串行化，避免显存 OOM
    # 音频回源超时（ACE-Step 把音频写在**服务端本地磁盘**，须经 GET /v1/audio 拉回）。
    ace_step_download_timeout: float = 180.0

    # 文案辅助 LLM（创作页 ✨自动优化/自动生成/自动取名；OpenAI 兼容 /chat/completions）。
    # 默认指向腾讯混元 OpenAI 兼容端点（hunyuan-lite 免费档，国内合规）；
    # 换 DeepSeek / Qwen / GLM 等仅改 ASSIST_LLM_BASE_URL / ASSIST_LLM_MODEL / KEY，代码零改动。
    # ASSIST_LLM_API_KEY 为空 = 功能关闭 → 辅助按钮自动降级本地确定性规则（永不 5xx）。
    assist_llm_base_url: str = "https://api.hunyuan.cloud.tencent.com/v1"
    assist_llm_api_key: str = ""
    assist_llm_model: str = "hunyuan-lite"
    assist_llm_timeout_seconds: float = 20.0

    # 限流（MVP 内存实现；多实例生产改 Redis）
    enable_rate_limit: bool = True
    sync_rate_limit_per_minute: int = 10
    generate_rate_limit_per_minute: int = 5  # 单用户每分钟生成突发上限（429）
    login_rate_limit_per_minute: int = 10  # 单 IP 每分钟登录尝试上限（429）
    # [SEC] 2026-09-13: 등록은 **익명** 엔드포인트인데 제한이 없어, 스크립트로 계정을
    # 대량 생성해 계정당 무료 일일 쿼터를 N 배로 증폭(쿼터 우회 + 상류 비용 소진) 할 수 있었다.
    register_rate_limit_per_minute: int = 5  # 单 IP 每分钟注册上限（429）
    play_rate_limit_per_minute: int = 120  # 单 IP 每分钟播放计数上限（防刷量/滥用）
    assist_rate_limit_per_minute: int = 20  # 单 IP 每分钟 AI 辅助上限（上游 LLM 按量计费）
    preview_rate_limit_per_minute: int = (
        30  # 单用户每分钟试听片段生成上限（防 ffmpeg 资源耗尽 CWE-400）
    )
    download_rate_limit_per_minute: int = 20  # 单用户每分钟完整下载上限（防带宽/对象存储成本滥用）
    # GAP-028 批量导出限流：一次导出 = N 首完整音频（下载 + 打包），放大倍数远高于单曲下载
    export_rate_limit_per_minute: int = 3  # 单用户每分钟批量导出上限（429）
    # GAP-022 开放平台：token 创建 / webhook 注册都是**低频、有副作用**的动作
    # （注册 webhook 会让我们对外发请求 —— 被滥用就是 SSRF 探测跳板）。
    developer_rate_limit_per_minute: int = 10  # 单用户每分钟开发者配置写入上限（429）
    # GAP-028 打包护栏（防滥用 CWE-400）：曲目数上限是**硬 400**（见 routers/export.py），
    # 这两项是体积闸门 —— 单曲超限的跳过并在 zip 内 export-notes.txt 如实汇报，不整批失败。
    export_max_total_mb: int = 512  # 单次导出 zip 的原始音频总体积上限（MB）
    export_max_song_mb: int = 100  # 单曲体积上限（MB），超过则跳过该曲
    # 一次性下载链接有效期（秒）：对象存储路径 = presign 有效期；本地降级路径 = 临时包 TTL。
    export_url_ttl_seconds: int = 3600
    vocal_clone_rate_limit_per_minute: int = 10  # 单用户每分钟音色克隆上限（上游按量计费，防烧钱）
    # ⏸ 音色克隆（vocal-clone）**产品暂停**（2026-09-13 决策）：默认关闭 →
    #   POST /api/songs/vocal-clone 返回 501「功能已暂停」。
    #   ⚠ **实现与测试全部保留、禁止删除**（MurekaProvider.vocal_clone / 文件头魔数校验 /
    #   限流器 / 用例）。重新上线只需置 True（.env: ENABLE_VOCAL_CLONE=true）。
    enable_vocal_clone: bool = False
    per_user_daily_quota: int = 3  # 免费用户每日生成配额（超限 402 引导订阅）

    # P1 — FLOW 钱包跨域「按次扣费」轨总开关（默认关；见 docs/PAID_USER_DOMAINS_PLAN_2026-09-12.md §4 P1）。
    # 关 = 完全走既有订阅轨（tier daily_quota 内免费生成），本开关外的钱包代码零行为变更。
    # 开 = 非订阅/超额用户可走 FLOW credit 按次扣费（需 songs.py 接线 + 前端「用 FLOW credit 支付」选项）。
    enable_flow_wallet_track: bool = False
    # P1 — 超配额按次扣费的「单首价」（CNY）。与用户 tier 的 price_cny 解耦：
    # free 档 price_cny=0（loss-leader，见 resolve_price_cny），不能作为扣费额（cny_to_krw(0) 会拒），
    # 故超额扣费一律走本固定单价（占位默认 0.99，上线前由产品校准；不影响套利下限：krw_to_cny≥本价）。
    flow_wallet_per_song_cny: float = 0.99

    # 审核（默认启用文本机审；P3 音频机审默认关闭）
    # 启用：enable_moderation=True 开文本机审（blocklist.txt 实际词库，见 moderation._blocklist_path）；
    #       enable_audio_moderation=True 开 P3 音频机审（需接 ASR+审核 API）。
    # [D33] 공존 출시 기준: 중국 대상 서비스이므로 텍스트 심의를 기본 ON 으로 한다.
    #       OFF 로 두면 생성곡이 미심의(pending) 로 남아 explore/sync 에서 제외된다.
    enable_moderation: bool = True
    # 音频机审（P3）：生成完成后对最终音频做内容审核（需接 ASR+审核 API）。
    # 默认关闭：MVP 不阻塞生成、不依赖外部；启用后未通过标记 rejected。
    enable_audio_moderation: bool = False

    # ── GAP-016 音频机审真实化：ASR 供应商 + 可选专业审核 API ──
    # 供应商选择（实证口径见 app/asr.py 头部；未配置即视为「不可用」→ fail-closed）：
    #   ""         未接 ASR —— enable_audio_moderation=True 时**不放行**（转 pending 走人工），
    #              这是替换旧占位「恒 True」的核心：开了开关但没有 ASR 必须拦下而不是放行。
    #   "whisper"  自建/faster-whisper 兼容 HTTP 服务（POST /transcribe，见 asr.WhisperASR）
    #   "openai"   OpenAI 兼容 /audio/transcriptions（whisper-1）；换任意网关只改 api_base
    asr_provider: str = ""
    asr_api_base: str = ""  # whisper 自建：服务根地址；openai：默认 https://api.openai.com/v1
    asr_api_key: str = ""  # openai 兼容端点必填；自建可留空
    asr_model: str = "whisper-1"
    asr_language: str = "zh"  # 转写语言提示（zh 提升中文歌词识别率，非强制）
    asr_timeout_seconds: float = 60.0  # 单次转写整体上限（1 分钟音频实测约 3~8s，见 asr.py）
    asr_max_bytes: int = 25 * 1024 * 1024  # 转写单文件上限（OpenAI 官方 25MB 限制）
    # 专业内容审核 API（可选）。配置后 check_lyric/音频审核在**词库命中之外**再调一次
    # 云审核；未配置则仅词库。base 留空 = 关闭。
    moderation_api_base: str = ""  # OpenAI 兼容 /moderations；腾讯/阿里雲需各自适配器
    moderation_api_key: str = ""
    moderation_api_model: str = "omni-moderation-latest"
    moderation_api_timeout_seconds: float = 15.0
    # 审核失败（ASR 报错/超时/云审核 5xx）时的策略。fail-closed=不放行（默认，安全优先）：
    # 任何异常都不得让音频「静默通过」，这是本模块唯一的安全底线。
    moderation_fail_closed: bool = True

    # 音频后处理（F1 响度 / F2 母带 / F3 降噪 / F4 分离；默认关闭，需 storage 下载/重托管）
    enable_audio_postprocess: bool = False
    audio_postprocess_steps: str = "loudness,mastering,denoise"  # 可选追加 separation（需 GPU）
    audio_postprocess_ffmpeg_bin: str = "ffmpeg"
    # ---- 未订阅试听门控（freemium：完整生成，未订阅只给前 N 秒试听） ----
    # 说明：生成始终是完整歌曲（Mureka 无 duration 参数，见 orchestrator/mureka.py 注释）；
    #      门控只发生在「分发」环节——未订阅者拿到的是裁剪后的试听片段链接。
    enable_preview_clip: bool = True  # 生成完成时产出试听片段（ffmpeg 裁剪上传）
    preview_seconds: int = 60  # 未订阅者可试听时长（秒）
    # 试听片段存放位置：
    #   · 配好 storage_*  → 上传到对象存储桶（生产推荐，走 CDN）
    #   · 未配 storage_*  → 落本地缓存目录，由 GET /api/songs/{id}/preview 按需生成并下发
    #     （⚠ 必须有这条回退：否则未订阅者因片段生成失败而「完全听不到」，门控变功能停止）
    preview_cache_dir: str = ".cache/preview"
    # 按需生成试听片段时需先下载完整音频——上游慢/卡住会一直占着请求线程，
    # 而 httpx 的 timeout 是「两次读取之间的空闲」上限，对"匀速慢速"上游无效，故另设整体护栏。
    preview_fetch_timeout: float = 25.0  # 整体下载上限（秒），超时按片段不可用处理
    preview_fetch_max_mb: int = 80  # 单曲音频体积上限（MB），防止异常大文件拖垮本机

    # 管理后台
    admin_token: str = ""  # 管理台访问密钥（X-Admin-Token）；为空则禁用 admin 路由
    admin_viewer_token: str = ""  # 只读管理员令牌；仅允许 GET，变更类接口返回 403
    google_client_id: str = ""  # Google OAuth Client ID（비우면 Google 로그인 비활성화）
    encryption_key: str = ""  # Fernet 密钥（base64 32字节或任意口令自动派生）；生产必须设置

    # ---- GAP-003 OAuth 授权码登录（Google / Kakao / Naver）----
    # 与上面 google_client_id（GIS ID 令牌直传腿）并存，二者互不影响：
    #   · /api/auth/google         ：前端拿 ID 令牌 POST（已存在）
    #   · /api/auth/oauth/{p}/...  ：标准授权码 302 回跳腿（本组配置驱动）
    # provider 的 enabled 判定 = client_id 非空（Kakao 允许**空 Client Secret**，见下）。
    oauth_google_client_secret: str = ""
    oauth_kakao_client_id: str = ""
    # ⚠ Kakao 允许空 Secret：其「Client Secret」是可选安全增强（Kakao 后台可关闭），
    #   关闭时令牌端点只校验 client_id → 此处必须允许空值，不得对空 Secret fail-closed，
    #   否则「按官方最简配置接入的 Kakao 登录」会 503 不可用。
    oauth_kakao_client_secret: str = ""
    oauth_naver_client_id: str = ""
    oauth_naver_client_secret: str = ""
    # 回跳地址（必须与 provider 控制台登记值逐字一致，否则授权码换令牌报 invalid_grant）。
    # 空则按 oauth_public_base_url + /api/auth/oauth/{provider}/callback 推导。
    oauth_redirect_base_url: str = ""
    # 前端落点：成功 → `{frontend_base}?oauth=1`；失败 → `?oauth=0&reason=...`。
    oauth_frontend_redirect_url: str = "/account"
    # state 票据（服务端签名短寿命 JWT）寿命，秒。够走完一次授权页往返即可。
    oauth_state_expire_seconds: int = 600
    # ⚠ 客户端（provider）网络访问一律**不走代理**：代理会带来凭据泄露面
    #   （HTTP_PROXY 上游可见授权码/令牌），且与 SSRF 出口策略冲突。
    oauth_http_timeout: float = 10.0

    # FLOW ↔ 音乐 co-location 令牌交换（server-to-server 内部密钥）。
    # 为空则 /api/auth/flow-exchange 返回 503（功能未启用）。绝不向客户端暴露。
    flow_internal_token: str = ""
    # flow-exchange 发放的 scope 限定(sync:flow) JWT 寿命（分钟）。默认 60。
    flow_exchange_token_expire_minutes: int = 60

    # ---- 计费 / 支付 (GAP-004 真实支付基座) ----
    # billing_mode:
    #   offline — 允许 mock /api/auth/subscribe 与 manual 确认(开发·运维对账). 默认, 不破坏现有前端.
    #   live    — 仅真实 provider 流程; mock /subscribe 返回 410(防止绕过支付直接升档).
    billing_mode: str = "offline"
    # 🔒 安全加固（2026-09-13 审计）：mock 自助升档必须**显式**开启。
    # 默认关 —— 否则任何登录用户 POST /api/auth/subscribe 即可直接拿到任意档位权益
    # （绕过支付 = CWE-840 业务逻辑缺陷）。测试由 conftest 显式打开。
    enable_mock_subscribe: bool = False
    # 🔒 生产默认关闭 /docs /openapi.json /redoc（接口结构泄露面，CWE-200 / OWASP A05）。
    # 需要时由环境变量 DOCS_ENABLED=true 显式打开（开发环境）。
    docs_enabled: bool = False
    # billing_provider: manual | stripe | kakao | naver | wechat | alipay
    #   step-1: manual = 全功能(无外部依赖, 可测可运营); stripe = webhook 验签已实现, checkout SDK 为 step-2 seam.
    billing_provider: str = "manual"
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    billing_return_url: str = ""  # 支付完成回跳前端地址(Stripe success_url 等)
    # ---- 多币种(USD/KRW) — step-2 provider 子轨 ----
    # billing_currencies: 允许的计费币种(逗号分隔). 默认仅 CNY.
    billing_currencies: str = "CNY,USD,KRW"
    # 固定汇率(以 CNY 为基准): 1 CNY = x 外币.
    # ⚠ MVP 固定值, 非实时行情(拒绝幻觉: 不臆造实时汇率 API; 真实部署应定期刷新这些值或接入汇率服务).
    fx_rate_cny_to_usd: float = 0.14  # ¥1 ≈ $0.14 (≈ $1 ≈ ¥7.14)
    fx_rate_cny_to_krw: float = 190.0  # ¥1 ≈ ₩190 (≈ ₩1 ≈ ¥0.00526)
    # Stripe Checkout 订阅周期(默认月付)
    stripe_recurring_interval: str = "month"

    # ================= 亚洲本地 PG(step-4) =================
    # 说明: 以下字段全部默认空 → 对应 provider 未配置时抛出明确 RuntimeError(503),
    #       绝不静默降级为"看起来成功"。所有端点/签名算法均取自各家官方文档。

    # ---- KakaoPay (KRW) ----
    # ⚠ 实证(2026-09-04, 카카오페이 개발자센터 공식): 구 카카오디벨로퍼스 경로
    #   (kapi.kakao.com/v1/payment/*) 는 **공식 종료**되었다. 현행 공식값은 아래 하나뿐이다.
    #     Host : https://open-api.kakaopay.com   (개발/샌드박스 호스트 없음)
    #     Path : /online/v1/payment/ready , /online/v1/payment/approve
    #     인증 : Authorization: SECRET_KEY ${SECRET_KEY}
    #     바디 : JSON 객체 (Content-Type: application/json)
    #   아래 kakao_pay_api_base / kakao_pay_auth_scheme 의 KakaoAK 는 레거시 호환용으로만 남긴다.
    kakao_pay_cid: str = ""  # 가맹점 코드(테스트: TC0ONETIME)
    kakao_pay_credential: str = ""  # Secret key (레거시 호환: ADMIN_KEY)
    kakao_pay_auth_scheme: str = "SECRET_KEY"  # SECRET_KEY(공식/기본) | KakaoAK(레거시)
    kakao_pay_api_base: str = "https://open-api.kakaopay.com"

    # ---- NaverPay (KRW) ----
    # 결제창은 프론트 JS SDK(Naver.Pay.create → oPay.open) handoff 방식.
    # 서버 승인(apply) API 는 **공식 문서로 확정**(docs.pay.naver.com, 2026-09-04 실증):
    #     POST https://{dev-pay|pay}.paygate.naver.com/naverpay-partner/naverpay/payments/v2.2/apply/payment
    #     body(form-urlencoded): paymentId
    #     header: X-Naver-Client-Id / X-Naver-Client-Secret / X-NaverPay-Chain-Id / X-NaverPay-Idempotency-Key
    #   → 따라서 naver_pay_apply_url 을 비워도 NAVER_PAY_MODE 로 공식 호스트를 유도한다.
    #     (계약상 별도 도메인을 쓰는 경우에만 naver_pay_apply_url 로 전체 URL 덮어쓰기)
    naver_pay_client_id: str = ""
    naver_pay_chain_id: str = ""
    naver_pay_secret_key: str = ""
    naver_pay_mode: str = "development"  # development | production (JS SDK + API 호스트)
    # 서버 승인 API 의 **전체 URL** 오버라이드(파트너 계약 문서 기준). 비우면 위 공식 호스트 사용.
    naver_pay_apply_url: str = ""
    naver_pay_api_base: str = ""  # 조회/취소 등 확장용(현재 미사용, 계약 문서 값)

    # ---- WeChat Pay v3 (CNY) ----
    # Native 下单: POST {api_base}/v3/pay/transactions/native   (官方 native_prepay.md)
    #   主域名 https://api.mch.weixin.qq.com / 备域名 https://api2.mch.weixin.qq.com
    #   必填头: Authorization + Accept: application/json + Content-Type: application/json
    #   description 上限 127 字符; out_trade_no 6~32 位, 仅 数字/字母/_-|*
    # 认证头: Authorization: WECHATPAY2-SHA256-RSA2048 mchid=..,nonce_str=..,timestamp=..,serial_no=..,signature=..
    #   待签名串 = f"{METHOD}\n{PATH}\n{ts}\n{nonce}\n{body}\n"(五行, 含末尾换行)
    #   官方明确: 五项签名信息无顺序要求(signature-generation.html 第 4 节)
    # 回调(native_notify.md):
    #   验签头 = Wechatpay-Timestamp/Nonce/Signature/**, Wechatpay-Serial 用于选择验签密钥**
    #     官方原文: "微信支付公钥的序列号固定采用 PUB_KEY_ID_数字串 格式; 若请求头中的序列号
    #                不符合该格式, 则应使用平台证书进行验签。"
    #   官方 SDK(wechatpay-java NotificationParser) 对 serialNumber 缺失直接抛异常
    #   业务数据在 resource 里, 用 APIv3 密钥(32 字节)做 AES-256-GCM 解密
    #   应答: 验签通过 → 200/204(无需报文); 不通过 → 4xx/5xx + {"code":"FAIL"}
    #   官方会发送 WECHATPAY/SIGNTEST/ 前缀的假签名探测, 必须正常验签并拒答(4013053249 第 5 节)
    wechat_pay_mch_id: str = ""
    wechat_pay_app_id: str = ""
    wechat_pay_api_v3_key: str = ""  # APIv3 密钥(32 字节), 回调 AES-256-GCM 解密用
    wechat_pay_cert_serial_no: str = ""  # 商户 API 证书序列号
    wechat_pay_private_key: str = ""  # 商户私钥 PEM 内容(\n 可写成字面 \\n)
    wechat_pay_private_key_path: str = ""  # 或私钥文件路径(二者取其一, inline 优先)
    wechat_pay_platform_public_key: str = ""  # 微信支付平台公钥 PEM(回调验签)
    wechat_pay_platform_public_key_path: str = ""
    # 微信支付公钥 ID(形如 PUB_KEY_ID_3000000001). 空 = 平台证书模式.
    # 官方(sign_verify.md 2.2)把「微信支付公钥」标为【推荐】, 平台证书为可选项.
    # 两种模式的 PEM 都是公钥, 区别只在"回调头 Wechatpay-Serial 应与哪个标识匹配":
    #   非空 → 公钥模式: 回调 serial 必须与此值完全一致, 否则拒绝(防密钥错配)
    #   空   → 平台证书模式: 若回调 serial 形如 PUB_KEY_ID_*, 说明微信在用公钥签名而我们
    #          配的是平台证书 → 明确报错引导配置, 不用错密钥静默验签失败
    wechat_pay_pub_key_id: str = ""
    wechat_pay_notify_url: str = ""  # 回调地址(须公网 HTTPS)
    wechat_pay_api_base: str = "https://api.mch.weixin.qq.com"

    # ---- Alipay (CNY) ----
    # 电脑网站支付: alipay.trade.page.pay(FAST_INSTANT_TRADE_PAY), 网关 https://openapi.alipay.com/gateway.do
    # 签名(RSA2 = SHA256withRSA): 待签名串 = 剔除 sign(保留 sign_type) → 字母序 → "k=v&k=v"(值不编码)
    # 验签(rsaCheckV1, 官方指定用于支付接口): 待验签串 = **同时剔除 sign 与 sign_type** → 其余参数
    #   URLDecode 后字母序拼接。官方依据:
    #     - 官方文档 opendocs.alipay.com/open/02pa44「异步通知结果验签」:
    #       "在通知返回参数列表中, 除去 sign、sign_type 两个参数外, 凡是通知返回回来的参数皆是待验签的参数"
    #     - 官方 SDK Java AlipaySignature.getSignCheckContentV1: params.remove("sign"); remove("sign_type")
    #     - 官方 SDK PHP AopClient.rsaCheckV1: unset($params['sign']); unset($params['sign_type'])
    #   (rsaCheckV2 保留 sign_type, 仅用于生活号/服务窗异步, 不适用于本接口)
    # 应答: 必须返回纯文本 success, 其它一律视为失败并多次重投.
    alipay_app_id: str = ""
    alipay_private_key: str = ""  # 应用私钥 PEM(签名)
    alipay_private_key_path: str = ""
    alipay_public_key: str = ""  # 支付宝公钥 PEM(验异步通知签名)
    alipay_public_key_path: str = ""
    alipay_gateway: str = "https://openapi.alipay.com/gateway.do"
    alipay_notify_url: str = ""
    alipay_sign_type: str = "RSA2"  # 仅支持 RSA2(RSA/SHA1 已被支付宝弃用)

    # ---- PayPal (Subscriptions API · 多币种) ----
    # 官方实证(2026-09-05, developer.paypal.com/*.md 原文):
    #   Host  : sandbox = https://api-m.sandbox.paypal.com   live = https://api-m.paypal.com
    #   取 token: POST /v1/oauth2/token  Basic(client_id:client_secret)
    #             Content-Type: application/x-www-form-urlencoded  body: grant_type=client_credentials
    #   建订阅 : POST /v1/billing/subscriptions  必需字段仅 plan_id(26 位, ^P-[A-Z0-9]*$)
    #             custom_id = 商户自定义 ID(1~127 位可打印 ASCII) ← 用于回调回贴本地订单
    #             application_context.user_action=SUBSCRIBE_NOW 时买家批准后自动激活
    #             响应 links[].rel == "approve" 的 href 即买家批准页(https://www.paypal.com/webapps/billing/subscriptions?ba_token=...)
    #   回调验签: **不是本地 HMAC**, 而是把回调回传给 PayPal 服务器核验:
    #             POST /v1/notifications/verify-webhook-signature
    #             body = {auth_algo, cert_url, transmission_id, transmission_sig,
    #                     transmission_time, webhook_id, webhook_event}
    #             五个字段分别取自回调头 PAYPAL-AUTH-ALGO / PAYPAL-CERT-URL /
    #             PAYPAL-TRANSMISSION-ID / PAYPAL-TRANSMISSION-SIG / PAYPAL-TRANSMISSION-TIME
    #             成功响应 = {"verification_status": "SUCCESS"}
    #   回调应答: 2xx 视为收到; 非 2xx 重试最多 25 次 / 3 天.
    #
    # ⚠ 官方币种表(developer.paypal.com/reference/currency-codes)共 25 种, **不含 KRW**
    #   → 韩国主体不可能用 PayPal 收韩元。
    # ⚠ CNY / BRL / MYR 官方脚注: "supported as a payment currency or settlement currency
    #   only for in-country PayPal accounts" —— 非中国主体收 CNY 会被换算为账户主币种并加价差。
    #   → 默认受理集合已剔除这三种; 若确为 PayPal 中国主体账户, 在 PAYPAL_CURRENCIES 里显式加回 CNY.
    paypal_client_id: str = ""
    paypal_client_secret: str = ""
    paypal_mode: str = "sandbox"  # sandbox | live
    paypal_webhook_id: str = ""  # Developer Portal 里该 webhook 的 ID(验签必传)
    # tier_key → plan_id 的 JSON 映射(plan 需在 PayPal 侧预先建好).
    # 支持两种 key: "standard" (不区分币种) 与 "standard:USD" (按币种指定). 查表顺序: 带币种优先.
    # 例: {"standard": "P-5ML4271244454362WXNWU5NQ", "pro:USD": "P-XXXX", "pro:EUR": "P-YYYY"}
    paypal_plan_ids: str = ""
    paypal_return_url: str = ""  # 留空则回退 billing_return_url
    # 可受理币种(逗号分隔). 默认 = 官方 25 种中剔除 CNY/BRL/MYR(限本国账户)后的全部.
    paypal_currencies: str = (
        "AUD,CAD,CZK,DKK,EUR,GBP,HKD,HUF,ILS,JPY,MXN,NZD,NOK,PHP,PLN,SGD,SEK,CHF,THB,TWD,USD"
    )

    # ================= GAP-002 이메일 인증 / 비밀번호 재설정 / MFA =================
    # 이메일 발송 채널 (표준 라이브러리 smtplib만 사용 — 제3자 메일 API를 날조하지 않는다).
    # smtp_host 가 비어 있으면 "개발 모드": 실제 발송하지 않고 로그만 남긴다(warning).
    # 자격증명이 준비되면 app/services/email.py 의 send_email() 함수 본문만 교체하면 된다.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    # STARTTLS 사용 여부. 465(implicit TLS) 를 쓰는 경우엔 smtp_use_ssl=True 로 두고
    # smtp_use_starttls=False 로 설정한다(둘은 상호배타 — 동시에 켜면 의미가 없다).
    smtp_use_starttls: bool = True
    smtp_use_ssl: bool = False
    smtp_from: str = "no-reply@flow-music.local"  # From 헤더(서버가 거부하지 않는 주소로 교체)
    smtp_timeout: float = 10.0  # 발송 타임아웃(초) — SMTP 서버 다운이 요청 스레드를 붙잡지 않게
    # ENABLE_EMAIL_DELIVERY=True 인데 smtp_host 가 비어 있으면 → 503(조용한 "성공처럼 보임" 금지).
    enable_email_delivery: bool = False

    # 프론트엔드 베이스 URL — 메일 본문의 링크 생성용(비우면 cors_origins 첫 항목으로 폴백).
    frontend_url: str = ""

    # token 유효기간(분) / 재발송 쿨다운(초)
    email_verify_token_expire_minutes: int = 60 * 24  # 24h
    password_reset_token_expire_minutes: int = 30  # 30min
    auth_email_resend_cooldown_seconds: int = 60  # 재발송 쿨다운

    # 계정 잠금 (IP 레이트리밋과 **상보적** — IP 제한은 분산 공격에 뚫리므로
    # "단일 계정 브루트포스"는 계정 레벨 잠금이 정답이다).
    auth_max_failed_logins: int = 5
    auth_lockout_minutes: int = 15

    # ================= GAP-021 通知通道（邮件 + Web Push） =================
    # 邮件**复用**上面的 SMTP_* 一组变量（GAP-002 已落地），此处刻意不新造
    # SMTP2_* / NOTIFY_MAIL_* 一套平行配置 —— 两套配置必然漂移，
    # 且运维要维护两份凭证。通知邮件走的正是 app/services/email.py:send_email()。
    #
    # Web Push（VAPID，RFC 8292）：
    #   `VAPID_PRIVATE_KEY` = 服务端私钥（PEM 或单行 base64url 的 DER 均可，见下）。
    #   `VAPID_PUBLIC_KEY`  = 交给浏览器 pushManager.subscribe() 的应用服务器公钥。
    #   `VAPID_SUBJECT`     = 联系地址，**必须是 mailto: 或 https://**（RFC 8292 §2.1），
    #                         推送网关（FCM/Mozilla autopush）会据此在滥用时联系站长。
    # 三者缺一 = 未配置 → 通知通道优雅降级（登记待推送 + 返回 202），
    # 而不是抛 500，也不是假装"推送成功"。
    # ⚠ 私钥绝不落 git（仓库 public）。生成方式见 backend/.env.example 的 GAP-021 段
    #   （一段可直接跑的 python -c；PEM 与单行 base64url DER 两种形态都吃）。
    vapid_private_key: str = ""
    vapid_public_key: str = ""
    vapid_subject: str = "mailto:ops@flow-music.local"
    # 推送 TTL（秒）。0 = 立即丢弃（设备离线即丢），给 24h 让离线设备上线后仍能收到。
    web_push_ttl_seconds: int = 86400
    # 单个用户最多保留的订阅端点。浏览器每次 subscribe() 都可能产出新端点（同一设备
    # 也可能因清缓存而换端点），不设上限则「同一人无限写」——每端点在推送时都是一次
    # 外网请求，等于给攻击者一个放大面。超限时逐出最旧的（见 notification_store.save）。
    web_push_max_subscriptions_per_user: int = 10
    # 通知端点限流（次/分钟）。测试推送会真的发信/发推，无限流即可被用来刷爆配额。
    notifications_test_rate_limit_per_minute: int = 5
    notifications_subscribe_rate_limit_per_minute: int = 20
    # Web Push 订阅存储路径（追加式 JSONL，**不建表 / 不加迁移**，理由见
    # app/services/notification_store.py 模块头）。留空 = 订阅端点整体关闭
    # （返回 503 + 明确原因，而不是假装存下了）。生产务必指向**持久卷**：
    # 容器内 /tmp 会随重启清空，届时用户会发现"订阅过但收不到"。
    notification_store_path: str = "/tmp/flow_notifications/subscriptions.jsonl"

    # TOTP window: ±1 스텝(=±30s). 시계 드리프트는 허용하되 "±1분"처럼 과장하지 않는다.
    mfa_totp_valid_window: int = 1

    # 로그인 / 이메일 관련 엔드포인트 IP 레이트리밋(분당)
    verify_rate_limit_per_minute: int = 20
    resend_verification_rate_limit_per_minute: int = 5
    forgot_password_rate_limit_per_minute: int = 5
    reset_password_rate_limit_per_minute: int = 10

    @property
    def frontend_base_url(self) -> str:
        """메일 링크용 프론트엔드 베이스 URL. 명시값 우선, 없으면 cors_origins 첫 항목."""
        if self.frontend_url.strip():
            return self.frontend_url.strip().rstrip("/")
        origins = self.cors_origin_list
        return (origins[0] if origins else "http://localhost:3000").rstrip("/")

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @staticmethod
    def _load_pem(inline: str, path: str) -> str:
        """PEM 取值: inline 优先(支持 .env 里把换行写成字面 \\n), 否则读文件; 都无则空串。

        返回空串代表"未配置" —— 调用方须据此抛出明确错误, 不得静默继续.
        """
        if inline and inline.strip():
            return inline.replace("\\n", "\n")
        if path and path.strip():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return f.read()
            except OSError:
                return ""
        return ""

    @property
    def wechat_pay_private_key_pem(self) -> str:
        return self._load_pem(self.wechat_pay_private_key, self.wechat_pay_private_key_path)

    @property
    def wechat_pay_platform_public_key_pem(self) -> str:
        return self._load_pem(
            self.wechat_pay_platform_public_key, self.wechat_pay_platform_public_key_path
        )

    @property
    def alipay_private_key_pem(self) -> str:
        return self._load_pem(self.alipay_private_key, self.alipay_private_key_path)

    @property
    def alipay_public_key_pem(self) -> str:
        return self._load_pem(self.alipay_public_key, self.alipay_public_key_path)


settings = Settings()
