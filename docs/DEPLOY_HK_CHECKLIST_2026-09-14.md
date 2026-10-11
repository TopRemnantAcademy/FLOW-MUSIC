# 音乐平台 — 中国香港部署检查清单（2026-09-14）

> 面向「FLOW + ai-music-platform 共置（co-location）于中国香港单台大容量主机」的部署。
> 本清单只记录**实测确认过**的事实；未验证项一律标注「未验证」。

> **🚀 一键部署：`deploy/hk/README.md`（`bootstrap.sh` 全自动完成安装→密钥→校验→构建→出口判定→HTTPS）。**
> 本清单的作用是**部署后的验证与判定依据**，以及记录为什么这样配置。两者配套使用。

---

## 0. 一句话总览

部署后最关键的判定是 **Mureka 出口是否被地域封锁（403）**。这件事现在有**三层防护**：

| 层级 | 机制 | 何时可见 |
|---|---|---|
| ① | `scripts/probe_mureka.py`（免费端点，一条命令） | 部署**前/后**随时 |
| ② | 启动期出口自检（`MUREKA_PREFLIGHT_MODE=warn`） | **服务启动第一行日志** |
| ③ | `GET /api/admin/mureka/egress` | 部署后随时（需管理员密钥） |

三者**共用同一套分类逻辑**（`app/orchestrator/mureka.py::_classify_mureka_error`），
不存在「脚本说 OK、运行时说 403」的两套口径。

---

## 1. 部署前：变量可解析性校验（必做）

```bash
cd ai-music-platform
../.venv_test/Scripts/python.exe scripts/validate_compose_env.py
```

- 退出码 **0** = 可部署；**1** = 存在 `:?` 必填项为空，禁止部署。
- 只打印长度，**绝不打印密钥明文**。
- 背景：曾出现「postgres / redis 两个**代码根本不用**的服务带着 `:?`，把 `docker compose up` 卡死」的情况（2026-09-14 已禁用这两个未使用服务）。

## 2. 必需环境变量（部署机上 `.env`）

| 变量 | 要求 | 说明 |
|---|---|---|
| `JWT_SECRET` | **必填**（`:?`） | 64 位随机。公开默认值会**完全绕过认证**（D37） |
| `MUREKA_API_KEY` | **必填**（`:?`） | 实际主引擎凭证。**空值部署会导致全部曲目 401 后静默失败**（2026-09-14 修） |
| `CORS_ORIGINS` | **必须改为生产前端域名** | 默认仅 `http://localhost:3000`（dev）。生产域名未加 → 前端请求被浏览器拦截 |
| `MUREKA_PREFLIGHT_MODE` | 可选（`warn`/`off`/`strict`，默认 `warn`） | `strict` 会在地域封锁时**拒绝启动**（会 crashloop，仅在你想强暴露时用） |
| `MUSIC_PROVIDER` | 可选（默认 `mureka`） | ⚠ 曾硬编码为 `suno` 且 `SUNO_API_KEY` 为空 → 全曲失败。**已改为尊重 env** |

生成强密钥（部署机执行，勿入库）：
```bash
python -c "import secrets;print(secrets.token_hex(32))"
```

## 3. 构建与启动

手动方式（**推荐直接用 `deploy/hk/bootstrap.sh`，它已包含本节全部步骤**）：

```bash
cd ai-music-platform
docker compose up -d --build
```

镜像安全（**已核实，无需改动**）：
- `backend/Dockerfile:16` 是 `COPY . .`，但构建上下文为 `./backend`，
  且 `backend/.dockerignore` 已排除 `.env` / `.env.*` / `*.pem` / `*.key` / `*.db` / `*.log`。
  → **密钥不会进入镜像层**（CWE-538 / CWE-312 已堵）。
- 端口仅 `127.0.0.1:8000:8000` 绑定（T6.1），经反向代理对外。
- `mem_limit: 1g` / `cpus: 1.0`（T6.6）保护共置主机。

## 4. 部署后验证（按顺序，4 项）

**① 出口探测（最重要，免费）**
```bash
cd backend && ../.venv_test/Scripts/python.exe scripts/probe_mureka.py
```
| 退出码 | 含义 | 处置 |
|---|---|---|
| 0 | 出口可用、key 有效 | ✅ 继续 |
| 2 | 401 认证失败 | 检查 `MUREKA_API_KEY` |
| **3** | **403 地域封锁** | ❌ 该出口不在 Mureka 允许区域 → 换**中国香港**出口或走已备案出口代理。**重试无效** |
| 4 | 网络/超时 | 检查到 `api.mureka.ai` 的出站连通性与代理 |
| 1 | 其他 / 未配置 | 看脚本 `[REPORT]` / `[ACTION]` 行 |

**② 启动日志**（应看到类似一行）
```
Mureka 出口自检: kind=ok status=200 —— 出口可用且 API Key 有效
```
若出现 `kind=region` → 同 ① 的 403 处置；日志级别为 **ERROR**。

**③ 管理端点**（需管理员密钥）
```
GET /api/admin/mureka/egress
```
返回 `kind` / `status_code` / `message` / `remediation` / `preflight_mode` / `report`。

**④ 健康检查与机审词库**
```
GET /health                     → 200
```
并确认机审加载的是**真实词库**而非占位词：
`backend/app/moderation.py:39` 以 `__file__` 为基准解析 `backend/blocklist.txt`（50 行实词），
**不依赖容器 CWD**，故镜像内 `WORKDIR=/app` 也能正确加载（2026-09-14 核实）。
若日志出现「机审使用占位敏感词库」→ 说明该文件未随镜像分发，需排查。

---

## 5. 已知限制与待决策（诚实标注，**未**擅自改动）

1. **数据库为每服务独立 SQLite**：`DATABASE_URL=sqlite:////app/data/app.db`（`appdata` 卷）。
   compose 里的 `postgres` 服务**已被禁用**（代码不用、且 `psycopg2` 未进 requirements）。
   ⚠ 这意味着跨产品桥 `user_links`（FLOW `Subscriber` ↔ 音乐 `users` 的 0-PII 关联）
   **只在两服务共用同一 DB 时才有意义**。若 FLOW 与音乐各自独立库，该桥失效。
   → **需你决策**：走共享 Postgres（需 psycopg2 + 迁移），还是接受两库并移除桥依赖。
2. **限流为单实例内存态**：当前无 Redis（`settings.redis_url` 消费者为 0）。
   多实例部署时限流会失效 → 届时再启用 Redis。
3. **CORS 生产域名未知**：需在得知音乐生产域名后写入 `CORS_ORIGINS`。
4. **真实香港出口结论只能在香港机器上取得**：本机探测 200 不代表生产出口可用（反之亦然）。

---

## 6. 应急

- 全曲失败且日志无原因 → 先看 ① 的 `kind`。`region` 即出口问题，非代码问题。
- 启动失败并出现 `[MUREKA_PREFLIGHT_STRICT]` → 你设了 `strict` 且被判 region/auth；
  改回 `warn` 可先起服务再排查。
- 误判 `auth` 但实际 key 正确 → 核对 `MUREKA_API_KEY` 是否被 compose 的 `${...}` 正确注入
  （用 `docker compose exec backend env | grep -c MUREKA` 验证，勿打印值）。
