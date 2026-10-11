# MOCK_TEST.md — 后端 Mock 测试方案

> 目标：在不依赖真实 Suno API / 对象存储 / 外部服务的条件下，验证核心业务链路与异常路径。
> 结果：`python -m pytest tests/ -q` → **66 passed, 3 skipped**（2026-08-29 实测）。
> - 66 passed 覆盖：健康/注册登录/生成轮询/机审/extend_cover_remix/sync_flow/alembic/explore/play/auto_approve/后台控制（引擎·路由·计费 CRUD 与 DB 实时生效）/引擎路由骨架（resolve_engine 回退与启用后实时路由）/安全打磨（弱密码·分页·owner·可见性）/音频后处理 loudness 归一与 F4 分离报错/解析。
> - 3 skipped：`test_postprocess.py` 中的 `test_master`/`test_denoise`/`test_run_pipeline`，依赖可选重型音频库 `pedalboard`、`noisereduce`（requirements.txt 标注「按需启用」）。mock 上线门禁用 `@pytest.mark.skipif` 自动跳过；装上这两个库后这 3 项自动转为 passed（共 69 passed），无需改代码。

---

## 1. 设计原则

- **拒绝幻觉**：绝不臆造 Suno 返回字段。`MockProvider` 只返回已定义的契约字段（`audio_url/cover_url/lyric/title/model_version/duration/status/external_id`）；`suno.py` 已按 open.suno.cn 官方协议对接（2026-08-27 调研），并对 sunoapi.org / gcui-art 响应做容错解析。
- **外部依赖全部 mock**：编排层用 `MockProvider` 注入；数据库用临时 SQLite 文件库，绝不触碰生产库 `./app.db`。
- **隔离性**：单一 session 级 `TestClient` + 临时库；每个测试后 autouse fixture 清空所有表，用例互不干扰。

---

## 2. 运行方式

```bash
cd backend
pip install -r requirements.txt      # 含 pytest==8.3.2、bcrypt==4.0.1
python -m pytest tests/ -q
```

---

## 3. 关键修复记录（踩坑，供复现时避雷）

1. **conftest 名字遮蔽（致命）**
   - 不能用 `from app.main import app` 把 FastAPI 实例绑到顶层变量名 `app`。
   - 因为 conftest 里有 `import app.db as dbmod` 这类语句，会把顶层 `app` 这个名字绑成 **「app 包模块」本身**；虽然后续 `from app.main import app` 覆盖成实例，但 pytest 运行时该名字又被解析回模块，导致 `TestClient(app)` 收到 module → `TypeError: 'module' object is not callable`。
   - 修复：`import app.main as _app_main; fastapi_app = _app_main.app`，fixture 引用 `fastapi_app`，彻底避开名字遮蔽。

2. **DB session 绑定错位**
   - `songs.py` / `auth.py` 等用 `from app.db import SessionLocal` 持有的是 **sessionmaker 对象引用**。
   - 必须通过 `SessionLocal.configure(bind=测试引擎)` 重绑**同一个对象**，而不是 `dbmod.SessionLocal = 新SessionLocal`（旧引用仍指向默认 `./app.db`，后台写库会写到另一个库，测试读到的仍是 pending）。

3. **BackgroundTasks 在 TestClient 下会执行**
   - starlette 的 `with TestClient(app)` 中，响应返回后后台任务会被同步执行。即 `POST /generate` 返回时歌曲已是 `completed`。
   - 因此 "未完成即同步拦截" 测试不能依赖 "generate 后歌曲仍 pending"，改为 **直接 DB 插入一条 `status='pending'` 的歌曲** 来构造未完成任务，再验证 `sync-flow` 返回 400。

4. **bcrypt 版本固定**
   - `passlib 1.7.4` 与 `bcrypt 4.1+` 不兼容（报 `password cannot be longer than 72 bytes` 且读不到 bcrypt 版本）。
   - `requirements.txt` 固定 `bcrypt==4.0.1` 解决。

---

## 4. 覆盖矩阵

| 测试 | 验证点 |
|------|--------|
| `test_health` | 健康检查 `/health` |
| `test_register_login` | 注册 + 登录拿 token |
| `test_register_duplicate` | 重复注册 → 409 |
| `test_generate_requires_auth` | 未登录生成 → 401 |
| `test_generate_and_poll` | generate → MockProvider 完成（字段校验） |
| `test_moderation_blocks_bad_lyric` | 机审敏感词拦截 → 400 |
| `test_moderation_passes_clean` | 机审放行 |
| `test_extend_cover_remix` | extend/cover/remix 血缘与完成态 |
| `test_sync_flow_full` | 同步全链路 + APK 拉取歌单 |
| `test_sync_flow_before_complete` | 未完成同步拦截 → 400 |
| `test_sync_flow_wrong_owner` | 越权同步 → 404 |
| `test_list_songs` | 「我的作品」列表 |
| `test_factory_selects_provider_by_config` | Route B：工厂按配置选择 mock/suno provider |
| `test_factory_rejects_unknown_provider` | 未知供应商配置抛 ValueError |
| `test_continue_uses_custom_id` | 续写基于父歌 custom_id（UUID）而非 task_id；`Song.custom_id` 写回校验 |
| `test_spawn_moderation_blocks_bad_lyric` | extend/cover/remix 携带违规歌词被机审拦截 → 400 |
| `test_alembic_upgrade_builds_schema_matching_metadata` | Alembic 迁移建出的库 == ORM 元数据（表/列一致，排除 alembic_version） |
| `test_explore_returns_only_public_approved` | 探索页仅返回「公开 + 已审核 + 完成」歌曲；私有 / 驳回退出 |
| `test_play_increments_play_count` | `play` 端点播放计数 +1、+2 正确累加 |
| `test_generate_auto_approves_after_completion` | 生成完成后（提交文本已机审）自动置 `moderation_status='approved'`（E4 闭环） |

---

## 4.5 P0 加固（2026-08-27）

- **custom_id 明确化（A1 脆弱性修正）**：原 `suno.py.get` 把轮询用的 `external_id`（task_id）隐式覆盖成 `custom_id`，使 `_process_song` 存的 `song.external_id` 实际是 custom_id——续写在当前 open.suno.cn 下「碰巧能用」但语义脆弱、依赖隐式行为。现改为：`SongResult` 新增独立 `custom_id` 字段；`get` 返回 `external_id=task_id`（轮询）、`custom_id=UUID`（续写基准）；`Song` 新增 `custom_id` 列；`_process_song` 续写用 `parent.custom_id or parent.external_id`。语义清晰，不依赖隐式覆盖。
- **spawn 端点机审（A2）**：`extend/cover/remix` 路由新增 `_moderate_spawn`，对本次新提供的 `lyric/prompt` 做敏感词校验，违规 → 400（`enable_moderation=False` 时跳过）。

---

## 4.6 P2 Alembic 迁移（2026-08-27）

- `alembic init migrations` 引入迁移工具；`migrations/versions/6e64ba2229a2_initial_schema.py` 为 initial 迁移，覆盖 `users` / `songs` / `sync_records` 全部字段（含 `custom_id`、`moderation_status`、`parent_song_id` 血缘 FK）。
- `migrations/env.py` 复用项目 `Base.metadata` 作为 autogenerate 真相来源，数据库 URL 取 `ALEMBIC_DB_URL`（测试 / CI 隔离库）或 `settings.database_url`。
- 验证手段：`tests/test_alembic.py` 在独立临时库 `alembic upgrade head` 后断言表 / 列与元数据一致；`alembic check` 可在 CI 拦截「改了 models.py 却漏写迁移」的漂移。
- `app.db` 现由 Alembic 管理；`db.init_db()` 的 `create_all` 保留为开发兜底（幂等、无害）。

---

## 4.7 P4 内容公开与审核闭环（2026-08-27）

- `models.py`：`Song` 新增 `is_public`（`Boolean, default=False, server_default='false', nullable=False`）+ `play_count`（`Integer, default=0, server_default='0', nullable=False`）。两列带 `server_default` 使 SQLite 下 autogenerate 的 `ALTER ADD COLUMN` 不报 NOT NULL 兼容错误。
- Alembic 迁移 `migrations/versions/659f128c2856_add_is_public_and_play_count.py`（autogenerate），`alembic upgrade head` 应用到 `app.db` 且 `alembic check` 零漂移；`tests/test_alembic.py` 在临时库 `upgrade head` 后断言表 / 列 == 元数据（自动覆盖新列）。
- 后端：`GET /api/songs/explore` 过滤 `is_public + moderation_status='approved' + status='completed'`，按 `play_count desc, id desc` 排序，支持 `limit/offset`，**匿名可访问**；`POST /api/songs/{id}/play` 计数 +1（自己或已公开歌曲均可计数）。
- **E4 自动审批**：`_process_song` 在 `result.status=='completed'` 且当前 `pending` 时置 `moderation_status='approved'`（提交文本已通过 `check_lyric`）；不覆盖 admin 已驳回。修复「生成完成不流转」缺口。
- 前端：`app/explore/page.tsx`（拉取 explore + 播放 +1）、`CreateForm` 加「公开发布到发现页」勾选、`lib/api.ts` 补 `SongView.is_public/play_count` 与 `api.explore()/api.play()`、首页加「发现公开作品」入口。
- 路由顺序坑：`GET /explore` 必须在 `GET /{song_id}` 之前注册，否则被参数路由抢匹配且 `get_current_user` 先于路径参数类型转换执行 → 返回 401。已按精确路由优先原则修正。

---

## 5. Mock 边界（哪些被绕过、哪些必须接真实服务）

- **编排层（Suno）**：`MockProvider` 返回固定完成态，不验证真实字段；`suno.py` 已按 open.suno.cn 协议对接真实 API，并对其它服务响应做容错（`MUSIC_PROVIDER=mock` 可完全绕过）。
- **对象存储（S3/COS）**：`sync-flow` 在 storage 未配置时降级用 Suno 原始 `audio_url`；测试不配置 storage。
- **音频机审**：当前仅敏感词（`blocklist.txt`）；ASR 转写 + 审核模型为 TODO（见 PROMPT.md 模块 3）。
- **限流 / 配额**：测试中关闭限流（`enable_rate_limit=False`）以保证确定性；生成突发限流（429）与每日配额（402）已在 P1 实现，并经 `test_rate_limit_generate_throttles` / `test_daily_quota_blocks_after_limit` 验证；支付尚未实现。

---

## 6. 供应商切换（Route B 供应商抽象）

编排层定义统一接口 `MusicProvider`（`create` / `get`），`SunoProvider` 与 `MockProvider` 都实现它。业务层（`routers/songs.py`）只依赖接口，切换供应商无需改业务代码。

**两种切换方式：**

1. **配置切换（推荐，生产 / 本地开发 / CI）**：改 `music_provider` 配置项即可。
   - `music_provider=suno`（默认）：初始化 `SunoProvider`，调用真实商业 API。
   - `music_provider=mock`：初始化 `MockProvider`，不调用任何外部 API，返回固定假数据——适合本地开发、CI、demo。
   - 工厂 `get_orchestrator()` 按配置懒加载单例；未知值抛 `ValueError`。

2. **运行时注入（测试用）**：`set_orchestrator(MockProvider())` 覆盖全局单例，无需改配置即可让全部用例走 mock。

**接真实 Suno 的步骤**：在 `orchestrator/suno.py` 按官方文档核对字段、去掉占位；配置 `SUNO_API_KEY` 并保持 `music_provider=suno` 即可。接入 `sunoapi.org` / `gcui-art` 等其它服务时，新增一个实现 `MusicProvider` 的类并在 `client.py` 工厂登记即可，业务层零改动。

**验证切换闭环**：`tests/test_api.py` 的 `test_factory_selects_provider_by_config` 直接驱动工厂按配置选择 provider，不依赖注入。

---

## 7. 已知 TODO（测试侧）

- `blocklist.txt` 已扩充为多类别示例词库（33 词），生产须接入专业内容安全 API。
- `suno.py` 已对接 open.suno.cn；后续可补充 sunoapi.org / gcui-art 的独立断言，以及真实 API 的端到端联调（需 SUNO_API_KEY）。
- 补充「生成失败 → `failed` 分支」与并发提交的测试。
- 前端 `npm install && npx tsc --noEmit` 语法校验（需本地或 CI 环境执行）。
