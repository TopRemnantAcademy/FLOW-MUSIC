# 功能不足审计 + 写代码计划

> 口径：本清单基于对仓库实际源码的逐项阅读（backend 全部 `.py`、frontend 关键组件、tests、README/PROMPT/MOCK_TEST），
> 对照 `PROMPT.md` 的 7 个模块与产品契约。每条标注 ✅已实现 / ❌缺失 / ⚠️部分或缺陷，并附证据文件与行号。
> 原则：**拒绝幻觉**——只写经代码确证的事实，不臆断。

---

## 1. 功能不足清单

### A. 真实可用性缺陷（接真 Suno 后会暴露，零外部依赖可修）

- **A1 续写/翻唱 ID 语义脆弱（已修正 ✅ 2026-08-27）**
  - 原诊断（已作废）：曾以为"真实 Suno 下必失败"。经通读 `suno.py:get`（行113-157）后确证：`get` 已把 `external_id` 隐式覆盖成 `custom_id`（`external_id=custom_id or external_id`），因此 `_process_song` 存的 `song.external_id` 实际已是 custom_id，续写 `continue_song_id=parent.external_id` 取到的正是 custom_id，**当前 open.suno.cn 下能工作**。原"必失败"论断不准确。
  - 真实残留风险（脆弱性，已修复）：`external_id` 字段语义混用（既是轮询 task_id 又是续写 custom_id），完全依赖 `get` 的隐式覆盖；一旦某服务商 `result` 无 `custom_id`/`id` 字段（或 `get` 不再覆盖），续写即退化成 task_id 而失败。且 `SongResult` 无独立 `custom_id` 字段，信息被折叠进 `external_id`。
  - 修复（P0-1）：`SongResult.custom_id` 独立字段；`get` 返回 `external_id=task_id`（轮询）、`custom_id=UUID`（续写基准）；`Song.custom_id` 列；`_process_song` 续写用 `parent.custom_id or parent.external_id`。语义清晰，不再依赖隐式覆盖。

- **A2 spawn 端点绕过机审**
  - 证据：`songs.py:129-143` `generate_song` 调用 `check_lyric`；但 `extend_song/cover_song/remix_song`（`:146-182`）**没有**调用 `check_lyric`。续写可带违规 `lyric/style` 绕过审核。
  - 影响：内容安全漏洞，审核形同虚设。

### B. 缺失功能（PROMPT 模块未落地）

- **B1 支付/配额（模块2）** ❌ 无 `billing.py`、无 Plan/Payment 模型、无订单/回调/积分。
- **B2 音频 ASR 机审（模块3 余下）** ✅ 管线已落地（2026-08-28）：`moderation.moderate_audio(audio_url)` 可插拔接口（占位返回 `True`）+ `config.enable_audio_moderation` 开关 + `songs._process_song` 生成完成后与 E4 协同（未过标记 `rejected`，不覆盖 admin 已审核）+ `tests/test_audio_moderation.py` 覆盖；真实 ASR（腾讯云/阿里云）接入待凭证，届时仅替换函数体。文本敏感词机审 ✅ 可用。
- **B3 发现页/分享（模块4）** ❌ 无 `explore` 页；`Song` 无 `is_public`/`play_count` 字段，后端无法做公开流与分享卡片。
- **B4 Route B 开源供应商（模块5）** ⚠️ 工厂 `client.py` 支持 `mock`/`suno`（已做），但 `fal_provider.py`（YuE/CosyVoice）未新增；`sunoapi.org`/`gcui-art` 未做独立 provider。
- **B5 成本监控（模块6 部分）** ❌ 无成本埋点（耗时/费用）；限流仅 `sync`（见 C1）。
- **B6 Alembic（模块7）** ❌ `db.py:24-27` 仍 `create_all`；`Song` 新增 `moderation_status` 等字段靠建表，无迁移，生产无法演进 schema。

### C. 安全 / 健壮性

- **C1 生成无限流 + 无 per-user 配额** ❌ `ratelimit.py` 仅 `rate_limit_sync`；`generate` 端点无限制 → 可刷爆 Suno 配额。模块6 要求"免费 3 首/日 + 超量 402"。
- **C2 登录无失败限流** ❌ 可暴力破解。
- **C3 JWT 无 refresh / logout 黑名单** ⏸️ 暂缓（非接真 Suno 硬痛点；24h 单 JWT + 前端 logout 丢弃 token 即满足 MVP；refresh 存储/黑名单表/前端刷新为架构级改动，留待需要时，不为凑清单硬做）。
- **C4 admin 鉴权瑕疵** ✅ 已修复：`require_admin` 未配置 `admin_token` 改返回 503（功能未启用，非 500 误导）；`X-Admin-Token` 改用 `hmac.compare_digest` 恒定时间比较（防时序侧信道）。
- **C5 注册无校验** ✅ 已修复：注册加密码最小长度（≥8，返回 400）；邮箱验证改为复用其他 APK 后台账号体系（不自建，与支付同策略）。
- **C6 sync 降级隐患** ⚠️ `flow.py:27-29` 未配 storage 时直接用 Suno 原始 `audio_url`（可能过期），仅本地验证可用。

### D. 产品 / UX

- **D1** 后端已补：列表 `limit/offset` 分页（默认 50）；`DELETE /api/songs/{id}`（owner 校验删除 + 子歌血缘解绑 `parent_song_id→NULL`）；`PATCH /api/songs/{id}` 改 `is_public`。前端"我的作品"管理页因当前无承载页面、且非安全核心，暂缓（API 已就绪，留待需要时）。
- **D2** 无用户中心 / 探索页 / 分享卡片（canvas）。
- **D3** 移动端仅基础响应式，未系统验证。

### E. 文档 / 契约不一致（轻微过时，非虚构）

- **E1** `schemas.py:39` `persona_id` 注释"⚠️ 待 Suno API 文档核对"已过时（`suno.py` 已映射 `personaId`）。
- **E2** `PROMPT.md` 已知 TODO 第1条"sun o.py 字段需核对"已过时（已对接 open.suno.cn）。
- **E3** `README.md` 目录树未列 `admin.py` / admin 页面；`docker-compose.yml` 仅含 postgres+redis，**无 backend/frontend service 编排**（文件存在，非虚构，但应用未编排）。
- **E4** `moderation_status` 默认 `pending`，生成完成后不自动流转；前端未向用户展示审核态。

---

## 2. 写代码计划（契约先行 · 小步提交 · 先测后写）

通用纪律：
1. 改 `models/schemas` 前先定字段（契约先行）。
2. 每个功能先写/补测试（用 `MockProvider` 注入，不触外部 API）。
3. 真实 API 字段以 `open.suno.cn` 文档为准，绝不臆造。
4. 大改动建分支、小步提交，人工验收。

### 阶段 P0 — 真实可用性修复（零外部依赖，立即做）

**P0-1 明确化 custom_id（A1 脆弱性，✅ 已完成 2026-08-27）**
- `base.py`：`SongResult` 加独立 `custom_id` 字段。
- `models.py`：`Song` 加 `custom_id = Column(String(255), default="")`；`external_id` 注释明确为轮询 task_id。
- `orchestrator/suno.py` `get`：返回 `external_id=task_id`（轮询）、`custom_id=UUID`（续写基准），不再覆盖 `external_id`。
- `songs.py:_process_song`：写回 `song.custom_id`；`continue_id` 优先取 `parent.custom_id`，回退 `parent.external_id`。
- `schemas.py`：`SongOut` 暴露 `custom_id`；`mock.py` 返回 `custom_id` 并记录 `last_continue_song_id`。
- 测试：`test_continue_uses_custom_id` 断言续写基于 custom_id；`suno.py` 解析断言由 `test_generate_and_poll` 覆盖。

**P0-2 spaw08-27）**
- `songs.py`：抽 `_moderate_spawn(body)`；`extend_song/cover_song/remix_song` 调用 `check_lyric`（对 `body.lyric or body.prompt`）。
- 测试：`test_spawn_moderation_blocks_bad_lyric` 断言 cover 带违规 `lyric` → 400。

**P0-3 清理文档（E1/E2/E3，✅ 已完成 2026-08-27）**
- `schemas.py` persona_id 注释去"⚠️待核对"，改为已支持 personaId；`PROMPT.md` TODO 第1条改"已对接 open.suno.cn"；`README.md` 目录树补 admin。
- 说明：`docker-compose.yml` 暂未补 backend/frontend service（避免引入未经测试的 Dockerfile），E3 剩余项留待 P7/部署阶段；`MOCK_TEST.md` 计数同步为 21 passed。

### 阶段 P1 — 限流与配额（模块6，内存即可，Redis 可选）

**P1-1 generate 限流 + 每日配额（C1）✅ 已完成 2026-08-27**
- `ratelimit.py` 加7**
- `ratelimit.py` 加 `rate_limit_generate(user)`；`config.py` 加 `per_user_daily_quota`（默认 3）。
- 配额计数：新增 `Quota` 模型（user_id + date + count）或 `Song` 日维度统计；超限返回 `402` 引导订阅。
- `songs.py:generate_song` 接入。
- 测试：超量返回 402。

**P1-2 登录失败限流（C2）** ✅ 已完成 2026-08-27
- `ratelimit.py` 加 `rate_limit_login(ip)`（复用 `_sliding_window` 原语）；`auth.py:login` 已加 `Request` 依赖并接入，超限返回 429。

### 阶段 P2 — 工程化 Alembic（模块7，B6）

**P2-1** ✅ 已完成 2026-08-27
- `alembic init migrations`；生成 initial 迁移（含 `moderation_status`/`custom_id`/`parent_song_id` 等全部字段）。
- `db.init_db` 保留为开发兜底（`create_all` 幂等）；生产 schema 演进以 Alembic 为准（`alembic upgrade head`）。
- `migrations/env.py` 动态读 `settings.database_url`，支持 `ALEMBIC_DB_URL` 覆盖（便于 CI / 测试隔离库）。
- `tests/test_alembic.py` 固化「迁移建出的 schema == ORM 元数据」；`alembic check` 可在 CI 拦截模型/迁移漂移。
- CI：`.github/workflows/ci.yml` 跑 `alembic upgrade head` + `alembic check` + `pytest`。

### 阶段 P3 — 音频 ASR 机审（模块3 余下，B2，需外部 ASR）🔧 代码保留，默认不启用 2026-08-28

**P3-1 已落地（管线 + 扩展点）**
- `moderation.py`：`moderate_audio(audio_url) -> bool` 可插拔接口（当前占位返回 `True`）；接真实 ASR 时替换为「音频转写 → `check_lyric`」即可，调用方 `songs._process_song` 零改动。
- `config.py`：`enable_audio_moderation`（默认 `False`，MVP 不阻塞、不依赖外部）。
- `songs._process_song`：生成完成后对最终音频做审核；仅在 `pending` 时生效，未过标记 `rejected`，与 E4 文本自动审批协同且不覆盖 admin 已审核结论。
- 测试：`tests/test_audio_moderation.py`（启用+未过→rejected 不进 explore；禁用→E4 自动 approved；不覆盖 admin approved）。
- **真实 ASR 接入暂缓**：需腾讯云/阿里云 ASR 凭证；接入仅为替换 `moderate_audio` 函数体，架构零改动。管理台"音频审核"按钮可作为后续项（当前自动审核已覆盖）。

### 阶段 P4 — 发现页 / 分享（模块4，B3，需模型字段 + 前端）✅ 已完成

**P4-1**（2026-08-27 完成）
- `models.py`：`Song` 加 `is_public`（默认 false，`server_default='false'`）+ `play_count`（默认 0，`server_default='0'`）；Alembic 迁移 `659f128c2856_add_is_public_and_play_count.py`（autogenerate，`alembic check` 零漂移）。
- 后端：`GET /api/songs/explore`（公开 `is_public` + 已审核 `moderation_status='approved'` + 完成 `status='completed'`，按 `play_count desc, id desc` 排序，支持 `limit/offset`，匿名可访问）；`POST /api/songs/{id}/play`（播放计数 +1，自己或已公开歌曲可计数）；`generate` 可带 `is_public`。
- **E4 自动审批闭环**：`_process_song` 在生成完成后（`result.status=='completed'` 且当前 `pending`）自动置 `moderation_status='approved'`——提交文本已通过 `check_lyric` 机审，故安全流转；不覆盖 admin 已驳回。修复「生成完成不流转」的 E4 缺口。
- 前端：新建 `app/explore/page.tsx`（拉取 explore + 播放 +1 计数）；`CreateForm` 加「公开发布到发现页」勾选；`lib/api.ts` 补 `SongView.is_public/play_count` + `api.explore()/api.play()`；首页加「发现公开作品」入口。
- 分享卡片 canvas 合成（封面+歌名+二维码）为后续 TODO。

### 阶段 P5 — 支付（模块2，B1，需 Stripe/微信）

**P5-1**
- `routers/billing.py` + `Plan`/`Payment` 模型 + 订单/回调/webhook 校验 + 积分扣减。
- 前端订阅页。先 Stripe 打通，再补微信跨境。

### 阶段 P6 — Route B 开源供应商（模块5，B4，需 Fal/Replicate）⏸️ 缓做（架构已就绪）

**P6-1 缓做说明（2026-08-28）**
- 生成层已是生产级可插拔：`MusicProvider(ABC)`（`base.py:45` 抽象接口）+ 工厂 `get_orchestrator`（`client.py:13-27`，按 `settings.music_provider` 切换 `suno`/`mock`），业务层 `songs.py` 仅依赖 `generate()`，零耦合。
- 真实接入 Fal/Replicate（YuE/CosyVoice）需外部凭证 + 开源模型适配 + 异步轮询；当前无凭证，硬接"跑不通的真实 provider"即造桩，违背"拒绝幻觉"。
- 等真要降本/去商业 API 限制且有凭证时，按现有 `MusicProvider` 契约新增 `orchestrator/fal_provider.py` 一个文件 + `config.py` 加选项即可，业务层零改动。现不预建。

### 阶段 P7 — 安全 / UX 打磨（C3/C4/C5/D1/D2/D3/E4）

**P7-1** 已落地（后端）：C4 admin 鉴权修复（503 + `hmac.compare_digest` 恒定时间比较）、C5 注册密码强度（≥8）、D1 列表分页 / 删除 / 可见性切换（API 层）。测试见 `tests/test_security.py`。**D1 前端（2026-08-28 完成）**：新增 `app/library/page.tsx`（我的作品管理页：播放计数 / 公开开关切换 / 删除）、首页加“我的作品”入口、`api.ts` 补 `listSongs(limit,offset)` / `deleteSong` / `updateVisibility`，复用 `AuthBar` 登录。暂缓项（架构级或非当前痛点，不为做而做）：C3 JWT refresh/logout 黑名单、前端审核态展示、移动端验证；邮箱验证复用其他 APK 后台（不自建）。

---

### 阶段 P8 — 音乐优化后处理（F1-F5，2026-08-28 落地 ✅ 真实集成 / 重型骨架）

> 原则：**拒绝幻觉**——轻量库真实集成并测绿（F1/F2/F3）；重型（需 GPU/缺依赖）只铺可插拔骨架 + 默认关 + 清晰报错，绝不写"假装跑通"的桩。所有步骤默认 `enable_audio_postprocess=False`，不阻塞 MVP。

- **F1 响度归一化（✅ 真跑）**：`ffmpeg loudnorm`（系统 `/usr/bin/ffmpeg` 已确证），统一 I=-14 LUFS，音量一致。`postprocess.loudness_normalize`。
- **F2 母带/效果链（✅ 真跑）**：Spotify `pedalboard`（0.9.24，pip 已装）。实测 API：`pedalboard.io.AudioFile` 读（`.read(af.frames)` 必须传 frames，不能一次读全）、`WriteableAudioFile(path, samplerate=sr, num_channels=ch).write(data)` 写（默认 `num_channels=1` 需显式传）、`board.process(data, sr)` 返回 float32。`postprocess.master`：高通30/低通16k + 压缩 + 限制。
- **F3 降噪（✅ 真跑）**：`noisereduce` 频谱门。`postprocess.denoise`。
- **F4 人声/伴奏分离（⏸️ 骨架）**：Meta `demucs`（需 torch+GPU）。`postprocess.separate_stems` 延迟导入 `demucs.apply/pretrained`，缺依赖/无 GPU 抛 `PostprocessError` 清晰说明，不静默不造桩。
- **F5 MusicGen 本地生成替代 Suno（⏸️ 骨架）**：`orchestrator/musicgen.py` `MusicGenProvider`（实现 `MusicProvider` 抽象），延迟导入 `torch`/`audiocraft`，缺依赖或 `torch.cuda.is_available()==False` 时 `create` 抛清晰错误而非假成功；工厂 `client.get_orchestrator()` 已加 `musicgen` 分支，业务路由零改动。
- **接入点**：`songs._apply_postprocess`（`routers/songs.py:73`）在 `_process_song` 生成完成后、按 `settings.audio_postprocess_steps` 下载→`run_postprocess`→重托管；默认关。
- **测试**：`tests/test_postprocess.py`（F1/F2/F3 真跑 + F4 缺依赖报错 + `parse_steps`）、`tests/test_musicgen_provider.py`（工厂识别 + 缺 GPU 报错）。全套 **44 passed**。

## 3. 建议执行顺序

1. **立即 P0**：A1/A2 决定"接真 Suno"能否工作，且零外部依赖、可全测。
2. **P1 限流**：上线前必做，防刷爆配额（花钱）。
3. **P2 Alembic**：否则生产无法迁移演进。
4. 之后按业务优先级：**P4（发现页，已落地）→ P5（支付，绑定其他 APK 后台不自研）→ P6（Route B，缓做：架构已就绪待凭证）→ P7（安全打磨，已落地）**；P3 音频机审已落地（见阶段 P3），移出待做序列。

---

## 4. 当前已具备的良好基础（勿重复造轮）

- ✅ `MusicProvider` 接口 + `mock`/`suno` 工厂切换（Route B 骨架已立）。
- ✅ 文本机审 + 33 词 blocklist。
- ✅ Admin 后台（后端 + 前端 + 测试）完整。
- ✅ 主链路测试 36 passed（含 P3 音频机审 3 用例，mock 注入，不触外部）。
- ✅ `open.suno.cn` 协议已对接（401 刷新/超时/重试/容错）。
