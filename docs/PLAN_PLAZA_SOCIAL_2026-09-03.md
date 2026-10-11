# 广场（社区分享）功能规划书

> 目标：把用户做好的歌共享到「广场」页面，让别人能听、能看创作者「设计理念」、能互动；互动**只允许点赞（赞），禁止文字评论**；用户自己也能收藏。
> 原则：**先不写代码**，以现有代码为事实基准（`ai-music-platform/backend/app` + `frontend`），拒绝臆造字段/端点。参考 Suno / Udio / SoundCloud 的真实社区形态，补齐你**没想到**的部分。
> 日期：2026-09-03

---

## 0. 你明确的需求（必须覆盖）

| # | 需求 | 现有代码是否已具备 |
|---|---|---|
| R1 | 用户做好的歌共享到广场页 | ✅ 部分：`Song.is_public`(models.py:43) + `GET /api/songs/explore`(songs.py:297) 已存在 |
| R2 | 其他用户能听到 | ⚠️ 部分：播放链路在，但**广场音频 URL 过期风险**（见 §6 关键风险） |
| R3 | 看他的设计理念等 | ❌ 缺：Song 无「创作理念」字段；User 无 昵称/简介/头像 |
| R4 | 能互动 | ⚠️ 缺：目前只有「播放+1」「同步 FLOW」「续写/翻唱/Remix」，无社交互动 |
| R5 | **只给赞，不许文字评论** | ❌ 缺：Like 模型/端点都不存在（且全平台都做评论，这里是明确反向约束） |
| R6 | 自己能收藏 | ❌ 缺：Favorite 模型/端点都不存在 |

---

## 1. 代码现状盘点（file:line 实证）

### 1.1 已有的（地基）
- **公开开关**：`Song.is_public`(models.py:43) + `PATCH /api/songs/{id}` 切换(songs.py:393-407，仅所有者)。
- **广场列表**：`GET /api/songs/explore`(songs.py:297-324)：筛选 `is_public AND moderation_status=='approved' AND status=='completed'`，按 `play_count DESC, id DESC`，支持 `q` 标题/歌词搜索，**匿名可访问**。
- **播放计数**：`POST /api/songs/{id}/play`(songs.py:563-576)：owner 或公开歌 +1。
- **封面**：`Song.cover_url` 由 provider 回填（mureka.py:136 / suno.py:140 / mock.py），完成歌有封面。
- **衍生关系**：`Song.parent_song_id`(models.py:40) + `extend/cover/remix` 端点(songs.py:252-294) 已支持。
- **内容分类**：`Song.content_category`(models.py:47) = general/christian_worship/instrumental（引擎路由用，广场可复用做频道）。
- **前端广场页**：`frontend/app/explore/page.tsx` 已存在（列表+搜索+加载更多），但只渲染 `<Player>`，**无赞/收藏/资料/理念/详情**。
- **前端音频客户端**：`frontend/lib/api.ts` 有 `explore`/`play`，**无 like/favorite/detail**。
- **规划书已有粗糙设想**：`规划/AI音乐平台规划书.md:116-119` §3.3「个人主页/作品库、收藏、公开私密」「分享卡片(封面+歌名+二维码)」；第 428/435 行 TODO「分享卡片/H5」「社区/排行榜/推荐」。

### 1.2 缺失的（必须新建）
- **无 Like（赞）模型/端点** —— 全库 grep 无 `like` 表。
- **无 Favorite（收藏）模型/端点**。
- **无关注(Follow) 模型/端点**（如需社交图谱）。
- **无公开详情端点**：`GET /api/songs/{id}`(songs.py:363-368) **仅所有者可见**（非 owner 返回 404），广场点进去取不到。
- **无匿名可选鉴权依赖**：只有 `get_current_user`（401 强校验，auth.py:83）。广场需「匿名可听、登录才能赞」。
- **无创作者资料字段**：`User`(models.py:7-16) 只有 `email`（且不能对外暴露），缺 `display_name/avatar_url/bio`。
- **无「设计理念/创作笔记」字段**：`Song`(models.py:19-50) 无 description 类字段，现有 `prompt/style/lyric` 是生成参数非人文叙述。
- **无举报/审核入口**：公开 UGC（简介、昵称、创作理念）需要文本审核，现有 `moderation.check_lyric` 只审歌词。
- **`SongView`/`SongOut` 无创作者信息**：explore 返回的 `SongOut`(schemas.py:100-123) 不含 `user_id/creator`，广场卡片无法显示「谁做的」。

---

## 2. 同行平台参考（2026-09 真实检索，拒绝拍脑袋）

| 平台 | 可借鉴 | 明确**不抄** |
|---|---|---|
| **Suno** | Explore 持续歌流、Like、关注创作者、资料页(置顶5首+封面+简介+社交链)、移动端 For You 滑动流、Hooks Feed(❤/分享/加入歌单/举报)、免费歌默认公开 | 评论（与你的 R5 冲突） |
| **Udio** | 内置社交 feed、Like/Save、公开资料页(展示代表作+风格)、genre 冲浪、Community Spotlight、Extend/Remix 溯源 | 评论 |
| **SoundCloud** | ❤ Like（入 Likes 合集+反哺推荐）、Repost（转发到关注者 feed=endorsement）、关注、公开/私密歌单、**波形时间戳评论**、可嵌入播放器、排行榜(热门) | 时间戳评论（仍属文字评论） |

**结论**：互动核心 = **赞（唯一）** + **收藏** + **关注(可选)** + **外部分享**；**绝不**做评论/弹幕/时间戳评论。

---

## 3. 功能清单（含你没想到的）

### A. 发布到广场（Publish）
- A1. 生成时 `GenerateReq.is_public`(schemas.py:47) 已可设；补充「创作理念」`description` 字段（新建）。
- A2. 生成完成后若 `is_public=True`，**自动上传音频到 CDN** → 回填 `audio_cdn_url`（见 §6，否则广场歌会过期失效）。
- A3. 频道标签复用 `content_category`（general/christian_worship/instrumental）作为广场分频道。
- A4. 发布前对 `description` 做文本机审（复用 `moderation.check_lyric` 思路）。

### B. 广场浏览 / 发现（Discover）
- B1. **排序 Tab**：热门(按 play_count/赞数)、最新(created_at)、飙升(近 24h 赞增速)。当前 explore 仅 `play_count DESC`(songs.py:319)。
- B2. **频道筛选**：general / christian_worship / instrumental 胶囊；当前 explore 无 category 参数。
- B3. **卡片信息增强**：封面 + 歌名 + 创作者昵称 + 赞数 + 播放数 + 时长；`SongOut` 需加 `creator_id/creator_name/like_count`（不直接暴露 email）。
- B4. **详情页**：点卡片进 `/song/[id]`，展示 大封面 + 歌词 + **创作理念** + 创作者资料卡 + 赞/收藏按钮 + 「基于 X 的 Remix」衍生链。
- B5. **公开详情端点**：新增 `GET /api/songs/public/{id}`（或扩展 explore 系列），返回详情 + `like_count` + `favorite_count` + `creator{id,name,avatar,bio}` + 当前用户 `liked/favorited` 状态（需可选鉴权）。

### C. 听（Listen）
- C1. 广场播放用 **`audio_cdn_url`（稳定）** 而非 `audio_url`（过期）。播放计数走现有 `/play`。
- C2. 波形/进度条（可选，SoundCloud 风格但**不带时间戳评论**）。

### D. 创作者资料 + 设计理念（Creator & Philosophy）★你重点要的
- D1. **User 加字段**：`display_name`、`avatar_url`、`bio`（models.py:7-16 扩展）。
- D2. **创作者主页** `/user/[id]`：头像 + 昵称 + 简介 + 其公开作品列表 + 获赞总数。
- D3. **歌曲级「创作理念」**：`Song.description` 字段，详情页突出展示（「为什么做这首歌 / 设计思路」）。
- D4. 隐私：对外**只暴露 display_name**，绝不暴露 email（现有 `MeResp`(schemas.py:23) 仅 id+email，需新增公开 `PublicUserOut`）。

### E. 互动：赞（Like）—— 唯一允许的互动（R5）
- E1. 新表 `SongLike(user_id, song_id, unique)`。
- E2. `POST /api/songs/{id}/like` **开关式**（已赞→取消，未赞→+1），返回最新 `like_count` + `liked`。
- E3. 列表/详情响应带 `like_count` 与当前用户 `liked`（可选鉴权下匿名=未赞）。
- E4. **防刷**：like 端点加重放/频率限制（参考现有 `ratelimit.py` 登录限流模式）。
- E5. **无评论、无 @、无表情包回复** —— 明确产品约束，连「评论入口」都不做。

### F. 收藏（Favorite）—— R6
- F1. 新表 `SongFavorite(user_id, song_id, unique)`。
- F2. `POST /api/songs/{id}/favorite` 开关式，返回 `favorited`。
- F3. **「我的收藏」页** `/favorites`：列出当前用户收藏的歌。
- F4. （可选）收藏可分组为歌单（SoundCloud 私密/公开歌单思路），MVP 先不做分组。

### G. 关注（Follow）—— 可选社交图谱（你没提，建议加）
- G1. 新表 `UserFollow(follower_id, followee_id, unique)`。
- G2. `POST /api/users/{id}/follow` 开关式。
- G3. **关注流**：explore 增加「关注」Tab，只显示已关注创作者的公开歌。
- G4. 创作者获赞/被关注可发站内通知（MVP 可后置）。

### H. 外部分享（Share）
- H1. 分享卡片：封面 + 歌名 + 二维码（指向 `/song/[id]`），便于微信/朋友圈（规划书:119 已设想）。
- H2. 系统分享：`navigator.share` / 复制链接。
- H3. （注意：这是**向外分享链接**，不是广场内评论。）

### I. 衍生 / Remix 溯源（Derivative Attribution）
- I1. 详情页展示 `parent_song_id` 链：「基于《XXX》的 Remix/Cover」（代码已支持 extend/cover/remix，songs.py:252-294）。
- I2. 原歌详情页可反向列出「衍生作品」。

### J. 举报 / 审核（Report & Moderation）—— 公开 UGC 必需
- J1. `POST /api/songs/{id}/report`（原因枚举：违规/侵权/其他），落 `SongReport` 表。
- J2. 后台审核队列接入现有 `admin` 体系（admin.py 已有审核端点），违规歌 `moderation_status='rejected'` → 自动退出广场。
- J3. 创作者 `display_name/bio/description` 文本也需机审（扩展 `moderation`）。

### K. 排行榜（Leaderboard）
- K1. 热门歌曲榜（按赞/播放周榜）、创作者榜（按获赞）。规划书:435 TODO 已列。

### L. 「我的」聚合页
- L1. 我的公开作品 / 我的收藏 / 我关注的 / 我的获赞。

---

## 4. 数据模型变更（具体）

### 4.1 `User` 扩展（models.py:7-16）
```python
display_name = Column(String(64), default="")  # 对外昵称，非空时显示，否则 anonymize
avatar_url = Column(Text, default="")
bio = Column(Text, default="")  # 创作者简介/设计理念
```

### 4.2 `Song` 扩展（models.py:19-50）
```python
description = Column(Text, default="")  # 创作理念/设计笔记（人文叙述，非生成参数）
```

### 4.3 新表
```python
class SongLike(Base):
    __tablename__ = "song_likes"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True)
    song_id = Column(Integer, ForeignKey("songs.id"), index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (UniqueConstraint("user_id", "song_id", name="uq_song_like"),)


class SongFavorite(Base):
    __tablename__ = "song_favorites"
    # 同 SongLike 结构，UniqueConstraint(user_id, song_id)


class UserFollow(Base):
    __tablename__ = "user_follows"
    id = Column(Integer, primary_key=True)
    follower_id = Column(Integer, ForeignKey("users.id"), index=True)
    followee_id = Column(Integer, ForeignKey("users.id"), index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (UniqueConstraint("follower_id", "followee_id", name="uq_user_follow"),)


class SongReport(Base):
    __tablename__ = "song_reports"
    id = Column(Integer, primary_key=True)
    reporter_id = Column(Integer, ForeignKey("users.id"), nullable=True)  # 允许匿名举报
    song_id = Column(Integer, ForeignKey("songs.id"), index=True)
    reason = Column(String(32), default="other")  # violation|copyright|other
    status = Column(String(32), default="pending")  # pending|reviewed|actioned
    created_at = Column(DateTime(timezone=True), server_default=func.now())
```

### 4.4 Schema 扩展
- `SongOut`(schemas.py:100) 增加：`creator_id`, `creator_name`, `like_count`, `favorite_count`, `description`, `liked`(可选), `favorited`(可选)。
- 新增 `PublicUserOut`：`id`, `display_name`, `avatar_url`, `bio`（**不含 email**）。
- 新增 `SongDetailOut`：在 `SongOut` 基础上加 `creator: PublicUserOut` + `parent: {id,title}|None` + `derivatives: [...]`.

---

## 5. API 端点设计

| 方法 | 路径 | 鉴权 | 说明 |
|---|---|---|---|
| GET | `/api/songs/explore` | 匿名 | 现有；增 `sort`(hot/new/rising)、`category` 参数；响应加创作者/赞数 |
| GET | `/api/songs/public/{id}` | 可选 | **新建**：公开歌详情（creator + like/favorite 计数 + 当前用户状态） |
| POST | `/api/songs/{id}/like` | 登录 | **新建**：赞开关 |
| POST | `/api/songs/{id}/favorite` | 登录 | **新建**：收藏开关 |
| POST | `/api/songs/{id}/report` | 可选 | **新建**：举报 |
| POST | `/api/users/{id}/follow` | 登录 | **新建**：关注开关 |
| GET | `/api/users/{id}` | 匿名 | **新建**：公开创作者资料 + 其公开作品 |
| GET | `/api/me/favorites` | 登录 | **新建**：我的收藏 |
| GET | `/api/me/following` | 登录 | **新建**：关注流（关注者的公开歌） |
| GET | `/api/leaderboard` | 匿名 | **新建**：排行榜 |
| 改 | `POST /api/auth/register` | — | 注册时可选填 `display_name` |
| 改 | `PATCH /api/auth/me` | 登录 | **新建**：改昵称/头像/简介 |
| 改 | `POST /api/songs/generate` | 登录 | `GenerateReq` 加 `description` |

> 需要新增依赖 `get_optional_user`（仿 `get_current_user`，token 缺失/无效时返回 None 而非 401），供 explore / public detail / report 使用。

---

## 6. ⚠️ 关键风险（必须前置解决，否则广场是坏的）

### 6.1 广场音频 URL 过期（最高优先级）
- 事实：`Song.audio_url` 来自 Suno/open.suno.cn（**下载链接 1h 过期**，见工作记忆）、MiniMax（24h 过期）；`audio_cdn_url`(models.py:33) 才是稳定链接，但**仅 `sync_flow` 时回填**(flow.py:142)，绝大多数歌为空。
- 后果：若广场直接放 `audio_url`，歌过 1–24h 就 404，广场名存实亡。
- 方案（二选一，推荐 ①）：
  1. **发布即归档 CDN**：`is_public=True` 且 `status=completed` 时，后端把 `audio_url` 重新拉取 → 上传 S3/兼容 CDN（`app/storage/s3.py:upload` 已存在）→ 写 `audio_cdn_url`；广场**只播 `audio_cdn_url`**。需确认 CDN/对象存储已配置（docker-compose 未显见，需你确认 infra）。
  2. 后端代理播放端点 `/api/songs/{id}/stream` 实时重取过期 URL 并流式转发（无 CDN 依赖，但占用后端带宽）。
- **前置依赖**：CDN/对象存储凭证与配置。未配前广场无法可靠播放。

### 6.2 公开 UGC 审核
- `description`/`display_name`/`bio` 是新 UGC 文本，需机审（扩展 `app/moderation.py`），避免广场出现违规文案。

### 6.3 匿名 vs 登录边界
- 听/浏览/排行榜 = 匿名；赞/收藏/关注/举报/改资料 = 登录。需要 `get_optional_user`，且前端对匿名点击赞时引导登录。

### 6.4 防刷赞 / 播放计数
- like 端点加频率限制；`/play` 当前每调用 +1（songs.py:573），建议按 `user_id+day` 去重或限频，防刷播放量。

### 6.5 隐私
- 对外绝暴露 `email`；`PublicUserOut` 只含昵称/头像/简介。创作者主页不显示真实邮箱。

---

## 7. 前端页面/组件规划

| 页面/组件 | 路径 | 说明 |
|---|---|---|
| 广场 | `app/explore/page.tsx`(改) | 加 排序 Tab + 频道胶囊 + 卡片(封面/昵称/赞/播放) |
| 歌曲详情 | `app/song/[id]/page.tsx`(新) | 大封面+歌词+**创作理念**+创作者卡+赞/收藏+衍生链 |
| 创作者主页 | `app/user/[id]/page.tsx`(新) | 资料卡+公开作品 |
| 我的收藏 | `app/favorites/page.tsx`(新) | 收藏列表 |
| 关注流 | explore 内 Tab 或 `app/following`(新) | 关注者公开歌 |
| 排行榜 | `app/leaderboard/page.tsx`(新) | 热门歌/创作者 |
| 资料编辑 | `app/settings/profile`(新) | 改昵称/头像/简介 |
| 分享卡 | 组件(新) | 封面+二维码，微信/朋友圈 |
| `Player`(改) | `components/Player.tsx` | 加 ❤赞 / ⭐收藏 按钮（**不加评论**） |
| `api.ts`(改) | `lib/api.ts` | 加 like/favorite/follow/detail/user/favorites/leaderboard |

---

## 8. 分阶段交付（建议）

**MVP（必做，覆盖 R1–R6）**
1. 数据层：User(display_name/avatar/bio) + Song.description + SongLike + SongFavorite + 迁移。
2. API：explore 增强(排序/频道/创作者) + public detail + like + favorite + get_optional_user。
3. 前端：explore 改造 + 详情页 + Player 加赞/收藏 + 我的收藏页。
4. **前置**：§6.1 CDN 归档（否则播放不可靠）。

**Phase 2（你没提但建议）**
5. 关注 + 关注流；创作者主页；排行榜；外部分享卡；举报+审核队列；资料编辑；播放去重；`description` 机审。

**明确不做**：评论 / 弹幕 / 时间戳评论 / @提及 / 任何文字评价入口（R5 硬约束）。

---

## 9. 待你拍板的决策点（Open Decisions）

1. **§6.1 CDN/对象存储**：是否已有 S3/兼容桶？决定广场播放方案 ① or ②。
2. **关注(Follow) 是否做**：G 是可选增强，MVP 可只做 赞+收藏。
3. **排行榜/分享卡** 优先级：是否纳入首版。
4. **昵称策略**：注册必填昵称 or 允许空（空时匿名化显示「音乐人 #id」）。
5. **`description` 是否必填**：创作理念强制填 or 可选。
6. **播放去重粒度**：按 user/day 还是不限制（影响排行榜公平性）。

---

## 10. 与现有架构的一致性（防幻觉备注）
- 社交表遵循现有 ORM 风格（Base/Column，见 models.py）。
- like/favorite 开关式复用 `/play` 的「先查后写」模式（songs.py:563-576）。
- 审核复用 `moderation` + `admin` 审核端点（admin.py 已存在），不另起炉灶。
- 迁移走 Alembic（参考现有 `migrations/`，注意 D45/D46 版本表 `alembic_version_music` 陷阱，新迁移从 HEAD 续写，勿改基线）。
- 测试隔离遵循 `.venv_test` + `-c scripts/_music_pytest.ini` 模式（见工作记忆），新增社交测试同此。
