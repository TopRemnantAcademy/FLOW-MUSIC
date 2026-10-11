# FLOW CCM — 访问控制规划（收听限制 / 防下载 / 单账号 2 设备）

> 状态：**PLANNING（仅规划，未改动任何代码）**
> 日期：2026-09-11
> 范围：`ai-music-platform` 后端（FastAPI）+ 前端（Next.js）
> 决策风格：用户授权我直接拍板（"你都批准 / 你来决策"），故下文每个模块都给出**推荐方案 + 备选 + 决策点**。

---

## 0. 现状核查（已读源码，非推测）

| 能力 | 现状 | 证据 |
|------|------|------|
| 免费用户只能试听 | ✅ 已实现 | `songs.py:148` `full = can_play_full(user)`；`audio = full ? song.audio_url : song.preview_audio_url` |
| 试听片段生成 | ✅ ffmpeg 裁剪前 N 秒，fail-closed 404 | `songs.py:906` `preview_audio`，`entitlements.preview_seconds()` 默认 60s |
| 订阅才能下载完整版 | ✅ 已实现 | `songs.py:875` `if not can_download(user): 403`（`can_download`=`is_subscribed`） |
| 下载链接不暴露给前端 | ✅ 已实现 | 前端 `api.ts:358` 走 fetch+blob，无静态直链 |
| 播放频控 / 试听配额 | ❌ 无 | `play_song` (`songs.py:859`) 仅 `play_count +1` |
| 下载频控 / 总量上限 | ❌ 无 | `download_song` 无 rate-limit |
| 溯源水印 | ❌ 无 | 下载件无任何用户绑定标记 |
| 设备登记 / 绑定 | ❌ 无 | JWT 无 `did`；`User`(`models.py:18`) 无设备列；`login`(`auth.py:158`) 不采集设备 |
| 单账号多端并发限制 | ❌ 无 | 完全无状态 JWT |

**结论**：收听侧的 freemium 闸门已具备，本规划在其上**补配额/防刷**；下载侧补**签名票据+频控+水印**；设备侧是**从零建设**。

---

## 1. 收听他人作品的限制

### 1.1 设计目标
- 免费用户：继续只能听前 N 秒试听（保留），并加**转化导向的每日试听配额**与**防刷限流**，避免被当免费流媒体刷量。
- 订阅用户：不限量听完整版（已付费，不应再限制）。

### 1.2 推荐方案（决定：采用）

**1.2.1 免费用户每日试听配额（转化导向，config 驱动）**
- 新增配置 `free_daily_preview_songs`（默认建议 `50`，0=不限）与 `free_daily_preview_minutes`（默认建议 `30`，0=不限）。
- 新增轻量表 `listen_log(user_id, song_id, is_preview, created_at, date_utc)` 或复用内存滑动窗口（参考 `ratelimit.py` 的 `deque` 模式，但需跨进程→生产用 Redis）。MVP 用 DB 按 UTC 日聚合。
- 超限后 `_to_out` 仍返回预览，但 `play_song`/`preview_audio` 返回 `429` + 升级提示文案（"今日试听额度已用完，订阅后畅听完整版"）。
- 仅作用于**他人公开作品**的预览；自己作品的试听不计入（创作者对自己内容无配额）。

**1.2.2 防刷 / 防抓取限流（复用 `ratelimit.py`）**
- 新增 `rate_limit_preview(user_key)`（建议 60 次/分钟/用户）与 `rate_limit_play(user_key)`（建议 120 次/分钟/用户）。
- 在 `preview_audio` 与 `play_song` 入口调用，Key = `f"preview:{user.id}"` / `f"play:{user.id}"`。
- 防热链：预览端点必须带 JWT（`songs.py:910` 已有 `Depends(get_current_user)`），URL 无法被外部直接盗刷，配合限流双保险。

**1.2.3 订阅用户**：放行，无配额、无限流之外的限制。

### 1.3 影响文件
- `backend/app/config.py`：加 `free_daily_preview_songs` / `free_daily_preview_minutes`。
- `backend/app/routers/songs.py`：`play_song`(859) / `preview_audio`(906) 加配额+限流判定。
- `backend/app/ratelimit.py`：加两个限流函数。
- 新迁移：`listen_log` 表（或 `daily_listen_quota` 聚合表）。

### 1.4 决策点
- 配额默认值取多少？（推荐 50 首 / 30 分钟，可运营调）
- 是否对自己作品也计配额？（推荐**不计**，降低创作者摩擦）

---

## 2. 不论订不订阅：防随意下载

> 核心矛盾：订阅用户**有权**下载自己/已购内容，但**不能**让它变成"随意转售/分发"。所以用**分层防御**，而不是"禁止下载"。

### 2.1 推荐分层（决定：2.2.2 + 2.2.3 + 2.2.4 全部纳入 MVP，2.2.5 顺手做，2.2.6 不做）

**2.2.1 不暴露静态直链（已具备，保留）**
- 下载走 `GET /api/songs/{id}/download` 鉴权后后端回源 `FileResponse`（`songs.py:875`），前端 fetch+blob（`api.ts:358`）。用户拿不到可复用的 CDN URL。**这是地基，必须保留。**

**2.2.2 签名短时效下载票据（推荐核心）**
- 新增 `POST /api/songs/{id}/download-ticket` → 返回 `{ ticket, expires_at }`。
  - ticket = HMAC-SHA256(`user_id|song_id|device_id|exp`)，时效 **60 秒**，单次使用（用后即焚，记录到 `used_tickets` 或 DB 标记）。
  - 下载端点改为 `GET /api/songs/{id}/download?ticket=...`，校验 ticket 有效+未用+绑定本人+未过期；否则 403/404。
- 效果：即使 JWT 泄露，下载 URL 也无法被他人复用/转发——把"凭令牌无限下载"变成"一次性短时授权"。
- 前端 `downloadSong`（`api.ts:358`）改为：先取 ticket，再带 ticket fetch。

**2.2.3 下载频控 + 总量上限**
- `rate_limit_download(user_key)`（建议 20 次/小时/用户）。
- 可选 `daily_download_cap`（订阅用户默认 200 首/日，0=不限；免费本就不能下载）。防脚本批量扒库。
- 在 `download_song` 入口校验。

**2.2.4 溯源水印（防"随意分发"的关键）**
- **MVP（必做，低成本可溯源）**：下载时由后端用 ffmpeg/eyed3 写入 **ID3 `TXXX` 字段** `{ "flow_user": <user_id>, "flow_ts": <ISO>, "flow_did": <device_id> }`。
  - 价值：泄露文件可追溯到账号+设备+时间，构成法律与威慑依据；成本极低（复用现有 ffmpeg 链路）。
  - 局限：可被刻意清除（聊胜于无的"软水印"）。
- **Phase 2（升级，不可闻）**：在音频波形嵌**不可闻伪随机水印**（PN 序列/回声编码），需轻量 DSP 库。抗剪辑、抗重编码。列为后续迭代，不在 MVP。

**2.2.5 客户端加固（低价值，顺手做）**
- 播放器禁用右键/长按保存（`onContextMenu` 阻止）；blob URL 用完即 `revokeObjectURL`（`api.ts:376` 已做）。
- 明确告知：这只能挡"最随意"的用户，**不是安全边界**，不能据此声称"防下载"。

**2.2.6 不采用 DRM**
- Widevine/FairPlay 对 CCM 小众福音内容成本/复杂度过高，且仍可被模拟音频录制绕开。MVP 用 2.2.2–2.2.4 即可。

### 2.3 影响文件
- `backend/app/routers/songs.py`：`download_song`(875) 改 ticket 校验+频控；新增 `download_ticket` 端点。
- `backend/app/ratelimit.py`：加 `rate_limit_download`。
- `backend/app/config.py`：加 `download_ticket_ttl_sec`、`daily_download_cap`。
- 新工具：`app/watermark.py`（ID3 注入；Phase 2 加 DSP 分支）。
- `frontend/lib/api.ts`：`downloadSong`(358) 改为 ticket 流程。

### 2.4 决策点
- 总量上限订阅默认多少？（推荐 200/日，可关）
- 是否现在就做 Phase 2 不可闻水印？（推荐**暂缓**，先上 ID3 软水印）

---

## 3. 单账号限 2 台设备（手机/平板通用）

> 用户原话："一个账号只能登陆两台机器如何？不管是手机还是平板电脑，如何实现？"
> 含义：**任何 2 台设备**（手机/平板/桌面/Web 都算 1 台）同时在线，第 3 台登录时按策略处理。

### 3.1 设计（决定：采用"登记 + 踢最旧 + JWT 绑定设备 + 管理页"）

**3.2.1 新表 `user_devices`（models.py）**
```
user_devices(
  id, user_id FK, device_id TEXT,        # 客户端稳定 UUID（Web: localStorage 生成一次；App: OS 稳定 ID）
  device_type TEXT,                      # 'phone'|'tablet'|'desktop'|'web'
  device_name TEXT NULL,                 # 用户可读名（"我的 iPhone"），可选
  user_agent TEXT NULL,
  last_seen TIMESTAMP, is_active BOOL DEFAULT 1,
  created_at TIMESTAMP,
  UNIQUE(user_id, device_id)
)
```

**3.2.2 登录/注册时登记设备（auth.py）**
- 新增 `DeviceLoginReq{ email, password, device_id, device_type, device_name? }`（注册同理 `DeviceRegisterReq`）。
- Web 端：首次加载若无 `localStorage.flow_device_id` 则生成 UUID 并持久化；每次登录带上。
- App 端（FLOW APK/Capacitor）：用 `Device.getInfo()` + 屏幕尺寸启发式（min(dp)≥600 视为 tablet）确定 `device_type`。
- `login`(`auth.py:158`)：upsert `user_devices`（相同 `device_id` 仅刷新 `last_seen`，**不占新名额**）。

**3.2.3 2 台上限策略（推荐：踢最旧 + 可管理）**
- 登录 upsert 后，统计该用户 `is_active` 设备数：
  - ≤2 → 放行。
  - >2 → 取 `last_seen` 最旧的 `is_active` 设备置 `is_active=0`（踢出），并（若开启了推送/通知）标记"其他设备已下线"。
- 体验更顺滑：**不拒绝新登录**，而是"保最近 2 台，自动顶掉最久未用那台"。用户可在管理页主动注销。
- 备选(A)：第 3 台直接 `409` 拒绝 → 简单但 UX 生硬（不推荐为主，留作配置项 `device_policy='reject|kick_oldest'`）。

**3.2.4 手机/平板识别（回答"如何实现"）**
- 服务端**不信任** UA 做安全判断，仅做展示/默认归类；真值由**客户端声明** `device_type`。
- Web 统一报 `'web'`（浏览器不算"机器"上限的一部分？——按用户口径**算 1 台**，避免 Web+手机+平板=3 台绕过）。
- 平板与手机**同等对待**，都计入 2 台名额（用户明确"不管是手机还是平板"）。

**3.2.5 JWT 绑定设备 + 吊销校验（让"踢"真正生效）**
- `create_token`(`auth.py:70`) 在 payload 加 `did`（device_id）。
- `get_current_user`(`auth.py:90`)：若 `settings.enforce_device_binding`：
  - 查 `user_devices` 中 `(user_id, did, is_active=1)` 是否存在；不存在 → `401`（被踢/注销的设备立即失效）。
- 这样"踢最旧"不只是登记侧，而是**下次任意请求即 401**，客户端收到 401 自动清 token 并提示"已在其他设备登录"。
- 代价：每次请求多一次 DB 查 device —— 可缓存（内存/Redis）设备活跃集合缓解。

**3.2.6 设备管理 API + UI**
- `GET /api/auth/devices` → 当前用户设备列表（含 `is_current`、`last_seen`、`type`），隐藏他人设备。
- `DELETE /api/auth/devices/{device_id}` → 注销指定设备（置 `is_active=0`）。
- `POST /api/auth/logout-everywhere` → 除当前 `did` 外全清。
- 前端：设置页"我的设备"展示 2 个槽位 + 注销按钮 + "全部退出"。

### 3.3 迁移与兼容
- 新 Alembic 迁移：`user_devices` 表。
- **向后兼容**：老用户首次登录自动登记前 2 台，无破坏；`enforce_device_binding` 默认 `False` 灰度，验证稳定后开 `True`。
- 不改动现有 `get_current_user` 主路径语义（仅追加可选校验分支）。

### 3.4 决策点
- 上限是 2 还是可配置？（推荐**写死 2**，但 `MAX_DEVICES_PER_USER=2` 常量便于改）
- 第 3 台策略：踢最旧（推荐）还是拒绝（409）？
- Web 浏览器是否计入 2 台？（推荐**计入**，防绕过）
- `enforce_device_binding` 是否默认开？（推荐灰度：先 `False` 跑一周再 `True`）

---

## 4. 实施顺序（建议）

| 阶段 | 内容 | 风险 | 依赖 |
|------|------|------|------|
| P0 | 下载签名票据(2.2.2) + 频控(2.2.3) | 低 | 无 |
| P1 | 溯源 ID3 水印(2.2.4 MVP) | 低 | ffmpeg 已有 |
| P2 | 收听配额+防刷(1.2) | 低-中 | listen_log 表 |
| P3 | 设备表+登录登记+踢最旧(3.2.1–3.2.3) | 中 | 迁移 |
| P4 | JWT 绑设备+吊销(3.2.5) 灰度开启 | 中 | P3 |
| P5 | 设备管理 UI(3.2.6) | 低 | P3 |
| P6 | 不可闻水印(2.2.4 Phase 2) | 高(DSP) | 独立迭代 |

## 5. 风险与回滚
- 配额/频控误伤正常用户 → 所有阈值 config 化，默认值偏宽松，可热改。
- 设备绑定误踢 → `enforce_device_binding` 默认关；`device_policy` 可切 `reject`。
- 水印损坏音频 → ffmpeg 写入失败 fail-closed，回退"无水印原文件下发"，不阻断下载。
- 全部新增均为**增量接口/字段**，不破坏现有 freemium 闸门（`can_download`/`can_play_full` 逻辑不变）。

---

## 附：与现有 freemium 闸门的关系（不可破坏）
- `entitlements.can_play_full` / `can_download`（`entitlements.py:112/117`）仍是**订阅判定真理源**，本规划所有下载/收听限制都**叠加在其之上**，不修改其语义。
- `songs.py:148` 的 `audio = full ? audio_url : preview_audio_url` 分发逻辑保持不变。
