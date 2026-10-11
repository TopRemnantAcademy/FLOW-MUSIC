# FLOW APP 对接契约（APK 开发手册）

> 适用对象：FLOW APP（自有 APK，独立工程，由你本人开发）。
> 平台后端：本仓库 `backend/`（FastAPI，部署于香港轻量服务器）。
> 最后更新：2026-08-26

---

## 0. 一句话架构

FLOW APP == 自有应用，复用平台账号（同一 JWT）。同步采用 **Pull 模式**：

1. 用户在 Web 端点「同步到 FLOW APP」→ 平台后端把音频回源香港 CDN + 写 `SyncRecord`。
2. APK 登录（同一账号）→ 调 `GET /api/sync/flow` 拉取自己的歌单 → 直接播 CDN 链接。

不需要 APK 直连 Suno，也不需要独立 FLOW 后端。平台后端是唯一音频源与鉴权源。

---

## 1. 鉴权（复用平台账号）

- 登录：APK 用平台同一套账号体系。调用 `POST /api/auth/login`（邮箱+密码）拿 `access_token`。
- 后续所有请求带头：`Authorization: Bearer <access_token>`。
- Token 过期后重新 login 或接 refresh（当前 MVP 先用 login 拿新 token）。
- Web 与 APK 共用同一 user，因此「我生成的歌」「我同步到 FLOW 的歌」天然互通。

```http
POST /api/auth/login
Content-Type: application/json

{ "email": "me@x.com", "password": "******" }

# 返回
{ "access_token": "<jwt>", "token_type": "bearer" }
```

---

## 2. 核心接口

### 2.1 同步一首歌到 FLOW（在 Web 端触发；APK 一般不直接调）
```
POST /api/songs/{song_id}/sync-flow
Authorization: Bearer <token>
```
- 校验：歌曲归属当前用户、且 `status == completed`。
- 行为：把音频回源香港 CDN（若未回源），写 `SyncRecord(status=synced)`。
- 返回：
```json
{ "sync_id": 12, "status": "synced", "audio_cdn_url": "https://cdn.xxx/songs/3/7.mp3" }
```

### 2.2 拉取「我的 FLOW 歌单」（APK 主用）
```
GET /api/sync/flow
Authorization: Bearer <token>
```
- 返回：当前用户所有已同步到 FLOW 的歌曲列表。
```json
[
  {
    "song_id": 7,
    "title": "雨夜失恋",
    "audio_cdn_url": "https://cdn.xxx/songs/3/7.mp3",
    "cover_url": "https://cdn.xxx/covers/7.jpg",
    "lyric": "窗外的雨...",
    "style": "国风 伤感 钢琴",
    "synced_at": "2026-08-26T15:00:00+00:00"
  }
]
```

### 2.3 查询某首歌的同步状态（可选）
```
GET /api/songs/{song_id}/sync-flow
Authorization: Bearer <token>
```
- 返回：`{ "sync_id": 12, "status": "synced", "audio_cdn_url": "..." }` 或 `{ "status": "not_synced" }`。

---

## 3. APK 端最小化实现要点

- **Base URL**：生产 = 香港后端域名（如 `https://api.your-domain.com`），测试 = `http://<香港内网/本地>:8000`。
- **播放**：`audio_cdn_url` 是稳定 CDN 链接，ExoPlayer / MediaPlayer 直接播，无需再做签名。
- **封面**：`cover_url` 直接加载（Glide/Coil）。
- **歌单刷新**：进入「我的 FLOW」页 `GET /api/sync/flow`，下拉刷新即可看到 Web 端刚同步的歌。
- **错误处理**：`401` 跳登录；`502` 音频回源失败提示重试；空列表显示占位 UI。

### Android (Kotlin) 伪代码
```kotlin
// Retrofit 接口
interface FlowApi {
    @GET("api/sync/flow")
    suspend fun getFlowSongs(@Header("Authorization") auth: String): List<FlowSong>

    @POST("api/auth/login")
    suspend fun login(@Body body: LoginReq): TokenResp
}

data class FlowSong(
    val song_id: Int,
    val title: String,
    val audio_cdn_url: String?,
    val cover_url: String?,
    val lyric: String?,
    val style: String?,
    val synced_at: String
)

// 拉歌单
val songs = api.getFlowSongs("Bearer $token")
// 用 ExoPlayer 播放 songs[0].audio_cdn_url
```

---

## 4. 对已确认前提的对应（避免幻觉声明）

| 前提 | 落地方式 |
|------|----------|
| FLOW APP 自有 | 本契约仅定义平台侧 REST 接口，APK 工程独立，不进本仓库 |
| 复用账号 | 同一 JWT；`/api/sync/flow` 按 `user_id` 过滤，无需额外绑定 |
| 先开发、以后公开 | 接口已带鉴权/归属校验；公开前补限流（如每用户 60 次/分）即可 |
| APK 自己开发 | 见上方契约与伪代码，Audio/封面直接用 CDN 链接 |

---

## 5. 后续可选扩展（非当前范围）

- **Push 模式**（若 FLOW 有独立后端且要实时推）：平台在 `_mark_synced` 后调用 `POST /flow/api/v1/playlist/add`，携带 `audio_cdn_url` + `title`。契约另行定义。
- **增量同步**：`GET /api/sync/flow?since=<ISO8601>` 返回增量，减少流量。
- **播放统计回流**：`POST /api/songs/{id}/play` 供平台侧做数据看板。
