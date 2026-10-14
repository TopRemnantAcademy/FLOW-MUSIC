# deploy/hk — 中国香港服务器部署套件

把一台**全新的 Ubuntu 22.04/24.04** 变成可对外服务的音乐平台后端。
重复执行是安全的（`.env` 与已生成的密钥不会被覆盖）。

---

## 你需要先准备（只有 3 项，其余脚本自动完成）

| # | 项目 | 说明 |
|---|---|---|
| 1 | **域名** | 例如 `music.example.com`，A 记录指向该服务器 |
| 2 | **Mureka API Key** | platform.mureka.ai 控制台获取。**留空会导致全部曲目 401 后静默失败** |
| 3 | **放行 80/443** | 云厂商安全组 + 系统防火墙。⚠ **不要**对外暴露 8000（后端只监听 127.0.0.1） |

`ADMIN_TOKEN`（管理台密钥）与 `ENCRYPTION_KEY`（静态加密）**不用你准备** ——
bootstrap 会像 `JWT_SECRET` 一样自动生成并写入 `.env`（已存在则不覆盖）。
它们之所以是必填：前者为空会让**管理台整体禁用**，后者为空会回退到代码里的
**固定开发密钥**去加密库里的引擎密钥（`security/crypto.py:16`，该文件注释明写“운영 금지”）。

---

## 三步完成部署

```bash
# 1) 取得代码（服务器能访问 GitHub 时）
git clone -b feature/company-quality-2026-05-26 \
  https://github.com/TopRemnantAcademy/ai-music-platform.git /opt/flow
cd /opt/flow

# 2) 设置必填项并执行（必须用 sudo -E 保留环境变量）
export MUSIC_DOMAIN=music.example.com
export MUREKA_API_KEY=sk-你的真实密钥
sudo -E ./deploy/hk/bootstrap.sh
# 若提示权限不足（拷贝时未保留执行位），改用：
sudo -E bash deploy/hk/bootstrap.sh

# 3) 按脚本结尾的提示做 4 项验证（出口自检 / 管理端点 / /health / 备份）
```

脚本会自动：安装 Docker → 生成 64 位 `JWT_SECRET` → 写入 `CORS_ORIGINS`
→ **部署前校验必填项**（缺失即中止）→ 构建启动 → **探测 Mureka 出口并判读 403**
→ **构建前端静态站点** → 启动 Caddy（自动 HTTPS + 静态托管）。

### 可选开关

| 变量 | 作用 |
|---|---|
| `SKIP_DOCKER_INSTALL=1` | 已装 Docker 时跳过安装 |
| `SKIP_CADDY=1` | 已有自己的反代时跳过 Caddy |
| `SKIP_FRONTEND_BUILD=1` | 沿用已有 `frontend/out/`（**产物缺失时会中止**，避免“能起来但打不开”） |
| `APP_DIR=/opt/flow` | 部署目录 |

### 前端是怎么部署的（重要）

前端是 Next.js 14，**无动态路由**（`app/` 下全是静态页面），因此走
`APK_BUILD=1 next build` 的**静态导出**路径（与 APK 构建同源，已验证可行），
产物 `frontend/out/` 由 Caddy 以 `file_server` 托管：

| 路径 | 去向 |
|---|---|
| `/api/*`、`/health`、`/docs`、`/openapi.json` | `127.0.0.1:8000`（backend） |
| 其余 | `frontend/out/`（静态文件） |

⚠ `NEXT_PUBLIC_API_BASE` 是**构建期内联**进客户端包的（`frontend/lib/api.ts:2`），
bootstrap 会自动注入 `https://$MUSIC_DOMAIN`；换域名必须**重新构建前端**
（改 `.env` 无效）。重跑 bootstrap 即可。

---

## 文件说明

| 文件 | 作用 |
|---|---|
| `bootstrap.sh` | 一键部署主脚本（幂等） |
| `env.prod.example` | 生产 `.env` 模板（**不含真实密钥**） |
| `Caddyfile.template` | 反向代理 + 静态托管 + 自动 HTTPS（`__MUSIC_DOMAIN__` 会被替换） |
| `backup.sh` | SQLite **在线**一致性备份（backup API，无需停机）+ 备份后完整性自检 + 过期清理 |
| `restore.sh` | `backup.sh` 的逆操作：停服 → 原子替换 → 起服；恢复前先备份当前库、恢复前校验待恢复文件完整性 |
| `crontab.example` | 备份 / 镜像清理 / 出口定时探测 |
| `selftest.sh` | bootstrap 的 **mock 自检**（不装 Docker/不联网）：8 组用例 / 38 项断言 |
| `sync-code.sh` | **代码同步**：`rsync --delete` 镜像语义（无 rsync 则退化），同步后按 `git ls-tree` 自检多了/少了，差异即非零退出；支持 `--dry-run` |
| `schema-cutover.sh` | **PG schema 切换**（对齐 live 镜像模型）：计划+确认 → 代码同步提示 / 镜像重建 → 闸门 → 备份 → 停应用 → `DROP SCHEMA` → `alembic upgrade head` → 起应用 → 校验；`users`/`songs` 非空时默认拒绝，需 `--force`；支持 `--dry-run` / `--yes` |
| `backups/` | 脚本目录下的备份目录；`schema-cutover.sh` 默认落到 **`/opt/flow/backups`**（与 live 存量备份同处），可用 `BACKUP_DIR` 覆盖 |
| `flow/bootstrap.sh` | FLOW(CCM) **共置部署引导**（复用 FLOW 自带 compose/Caddy）：LLM 凭据 fail-closed 闸门 + 构建启动 + 健康检查 |
| `flow/env.prod.example` | FLOW 生产 `.env` 模板（LLM / Qdrant / 密钥 / 共置 / 杂项 5 段，不含真实密钥） |
| `flow/README.md` | FLOW 脚手架说明：硬阻塞 / 共置冲突 / 部署步骤 / 启用 Let's Encrypt |
| `flow/selftest.sh` | FLOW bootstrap 的 **mock 自检**：15 组用例 / 48 项断言（凭据闸门的回归门禁） |

---

## 代码同步与 schema 迁移

这两个动作各自踩过一次生产坑（ISSUE #107 记录），所以都脚本化了。
**结论先行：用 `sync-code.sh` 同步代码，用 `schema-cutover.sh` 切 schema，不要手搓命令。**

### 1) 为什么禁用「覆盖式同步」

旧做法是 `git archive <ref> backend | tar -x --strip-components=1`。tar 是**覆盖式、不删除**
语义，于是 main 上被**重命名**的迁移文件会在服务器留下旧副本：

```
migrations/versions/c3d4e5f6a7b8_add_song_extend_type.py     ← main 已改名为 c3d4e5f7a8b9
migrations/versions/c3d4e5f6a7b8_fix_index_unique_drift.py   ← main 已改名为 c3d4e5f6a7b9
```

后果不是「旧文件没用到无所谓」，而是容器内 `alembic heads` 直接报
`Revision ... is present more than once` —— **迁移链整体不可用**。当时是靠人工比
`git ls-tree -r --name-only` 与服务器 `find`（147 vs 149）才定位。

所以同步必须是**镜像语义**。`sync-code.sh`：

- 有 `rsync` → `rsync -a --delete`（最快）。
- 无 `rsync`（Ubuntu 最小化安装常见）→ 退化路径：**先按 `git ls-tree` 清单删掉目标端多余文件，
  再 tar 解包覆盖**。选它而不是「先清目录再解包」，是因为清目录会连带删掉 `data/`、`.env`
  等**不在 git 里**的资产 —— 那等于删数据。
- 忽略白名单：`__pycache__/`、`data/`、`.cache/`、`.env*`、`*.log`、`*.pyc`、`.pytest_cache/`。
  这些既不计入「多余」判定，也不会被清理。
- **同步后自检**：重新比对 `git ls-tree` 与实际文件，**多了或少了都非零退出并打印差异**，
  把当时那次人工比对变成自动化。
- `--dry-run`：只打印将删除/写入的文件，不落盘。

```bash
./deploy/hk/sync-code.sh origin/main --dry-run   # 先看计划
./deploy/hk/sync-code.sh origin/main             # 真同步
```

⚠ `sync-code.sh` 的同步目标是**写死的 `$APP_DIR/backend`**（本仓在服务器上的副本）。
而 `music` 真正吃的是**构建上下文** `/opt/flow/ai-music-platform/backend`（另一个目录）。
两者不是一回事：`schema-cutover.sh` 第 1 步因此只**打印**面向构建上下文的同步命令
（`git archive` → 暂存目录 → `rsync -a --delete`），需要人工或前置步骤完成，
免得"同步了但 build 的还是旧代码"。

### 2) 为什么 cutover 必须先停应用

想「让迁移重新管辖 schema」的正确顺序是
**stop 应用 → 备份 → `DROP SCHEMA` → `alembic upgrade head` → start 应用 → 校验**。

反例是应用还在跑时就重建 schema：应用的 lifespan 会走
`Base.metadata.create_all`（`SCHEMA_CREATE_ALL=1`）**抢建** 13 张表，随后 alembic 撞上
「表已存在」而失败。**PG 的 DDL 是事务性的** → 整个 upgrade 回滚，
`alembic_version_music` 根本没建起来，而日志里**没有任何显式报错**。

`schema-cutover.sh` 据此编排，**并按 live 的真实部署模型对齐**（ISSUE #125）：

- `music` 是**镜像构建**服务（`build.context` = `/opt/flow/ai-music-platform/backend`），
  代码在**构建期**进镜像 → 只同步文件不 `build`，代码变更不会生效。
- 该构建上下文**不是 git 仓库** → 同步必须走 `git archive` + `rsync -a --delete`
  的镜像语义（第 1 步只**打印**命令，需人工或前置步骤完成，脚本不假装能自动同步）。
- 每条 compose 子命令都带 **`-p flow-hk`**（否则操作默认项目空栈）、
  **`--profile music`**（否则服务不在生效配置）、
  **`--env-file /opt/flow/deploy/hk/.env.music`**（漏掉时 `DATABASE_URL` 退回 SQLite 空库）。
- 迁移一次性容器要 **`--network flow-hk_flow-hk`**（否则解析不到 `flow-hk-music-postgres`），
  并挂**仓库根** `-v <repo>:/repo -w /repo/backend`（只挂 `backend/` 会让 `import app` 失败）。
- 校验读的版本表是 **`alembic_version_music`**（X3 与 FLOW 隔离，见 `backend/migrations/env.py`）；
  查 `alembic_version` 恒为空，会误判"迁移未应用"。
- 探针走 **Caddy `https://music.anistar.studio/health`** —— prod 的 `openapi.json` 已关闭（404），
  不能当探针；`admin/*` 预期 503（无 `admin_token` 的环境门控，非 bug）。
- 全过程**不自动执行上线动作**：先打印计划，交互输入 `yes` 才继续（非交互调用方用 `--yes`）。

```
0  计划打印 + 显式确认（交互 yes / --yes）
1  代码同步到构建上下文（打印镜像式命令；跨机请自行 ssh）
2  docker compose ... build music            ← 镜像重建，代码在构建期进镜像
3  数据保护闸门  users / songs 行数 > 0 且无 --force → 拒绝执行（防误删生产）
4  pg_dump 备份（前置，空备份/无报头即中止）→ $APP_DIR/backups/flow_music-pre-<ts>.sql
5  stop 应用     必须早于 DROP SCHEMA
6  DROP SCHEMA public CASCADE; CREATE SCHEMA public;
7  alembic upgrade head（--rm --no-deps --network flow-hk_flow-hk -v <repo>:/repo -w /repo/backend
                         -e SCHEMA_CREATE_ALL=0；变量由 --env-file 提供，脚本不插值不打印）
8  up -d --force-recreate music              ← 只 build 不 recreate 会继续跑旧镜像
9  校验 + 失败回滚指引
```

校验项（任一项不过即非零退出，并打印回滚指引）：

- `alembic_version_music` 存在且 **== `alembic heads`**
- `alembic current` 输出
- public 基础表数量 > 1（只有版本表说明迁移整体回滚了）
- 种子行数：`engine_configs` / `pricing_tiers` / `routing_rules` 均 > 0
- 公网 `/health` 返回 `{"status":"ok"}`（可用 `PUBLIC_PROBE=0` 跳过）

```bash
./deploy/hk/schema-cutover.sh --dry-run    # 只打印将执行的命令（不连服务器）
./deploy/hk/schema-cutover.sh              # 交互确认后执行
./deploy/hk/schema-cutover.sh --force      # users/songs 有真实数据时，显式确认后重跑
./deploy/hk/schema-cutover.sh --yes        # 非交互调用（CI/自动化）跳过确认
```

可用环境变量：`APP_DIR`、`COMPOSE_FILE`、`COMPOSE_FILE_OVERRIDES`、`COMPOSE_PROJECT_NAME`、
`ENV_FILE`、`PROFILES`、`SERVICE`、`BUILD_CONTEXT`、`REPO_DIR`、`SYNC_REF`、`PG_SERVICE`、
`PG_NETWORK`、`DB_NAME`、`PG_USER`、`BACKUP_DIR`、`PUBLIC_HEALTH_URL`、`PUBLIC_PROBE`。
路径/网络名/域名均以 live 实测为准，不确定处在脚本注释里已标注。

### 3) 回滚方式

| 场景 | 回滚动作 |
|---|---|
| 迁移后校验不过 | 用第 4 步的备份恢复（脚本失败时会打印可直接复制的三条命令）：`docker compose -p flow-hk --env-file /opt/flow/deploy/hk/.env.music --profile music stop music` → `docker exec -i flow-hk-music-postgres psql -U flow_music -d flow_music < /opt/flow/backups/flow_music-pre-<TS>.sql` → 同前缀 `up -d music` |
| PG 整体不可用（临时保命） | 改 `.env` 的 `DATABASE_URL` 回 `sqlite:////app/data/app.db`，置 `SCHEMA_CREATE_ALL=1`，`docker compose up -d --force-recreate music` |
| 代码同步后要回退版本 | 重跑 `./deploy/hk/sync-code.sh <旧 ref>` —— 镜像语义会把新版本多出的文件删掉，不留幽灵 |

`--dry-run` 输出的示例（假路径演示，见 ISSUE #107 回帖）已验证不落盘。

---

## 部署后最关键的判定：Mureka 出口

```bash
cd /opt/flow && docker compose exec -T backend python scripts/probe_mureka.py
```

| 退出码 | 含义 | 处置 |
|---|---|---|
| 0 | 出口可用 | ✅ 正常 |
| 2 | 401 认证失败 | 检查 `MUREKA_API_KEY` |
| **3** | **403 地域封锁** | ❌ 换中国香港/允许区域出口，或走已备案出口代理。**重试无效** |
| 4 | 网络/超时 | 检查到 `api.mureka.ai` 的出站连通性 |

同样的结论会在**服务启动的第一行日志**出现（出口自检），
也可通过 `GET /api/admin/mureka/egress` 随时查看。三者共用同一套分类逻辑。

---

## 常见失败

- **`缺少 MUSIC_DOMAIN` / `缺少真实的 MUREKA_API_KEY`** → 脚本故意中止。补齐环境变量重跑，注意用 `sudo -E`。
- **`缺少前端产物`** → 前端没构建成功（多半是 `npm ci` 网络问题）。按提示重跑构建，或用 `SKIP_FRONTEND_BUILD=1` 使用已有产物。
- **打开域名白屏 / 404** → 先看 Caddy 是否挂上产物：`docker exec flow-caddy ls /srv/index.html`；再看 Caddy 日志 `docker logs flow-caddy`。
- **页面能开但接口 401/404** → 域名变更后**没有重新构建前端**，`NEXT_PUBLIC_API_BASE` 仍指向旧地址（构建期内联）。
- **`error while interpolating ... required variable X is missing a value`** → 某条 compose 命令漏了 `--env-file`。套件内所有调用都已统一走 `dc()`（自动带 `--env-file`）；自己手敲命令时要带上。
- **探测报「探测脚本缺失」** → 出口状态**未知**（退出码 5，与 2=401 / 3=403 / 4=网络 区分开）。按提示手工 `docker compose cp backend/scripts backend:/app/scripts` 后重跑。
- **备份“执行了但没生成文件”** → 同样是 `--env-file` 缺失（见上一条）；`backup.sh` 已修。

---

## 备份与恢复（backup.sh / restore.sh）

数据库是 SQLite（容器内 `/app/data/app.db`，落在 `appdata` volume）。

**备份（每日 cron 跑，无停机）：**

```bash
/opt/flow/deploy/hk/backup.sh            # 在线一致性备份 + 完整性自检 + 清理 14 天前
KEEP_DAYS=30 /opt/flow/deploy/hk/backup.sh   # 自定义保留天数
```

备份写到容器内 `/app/data/backups/app-<UTC时间戳>.db`，**每次备份后立刻跑 `integrity_check`**，
坏备份会直接报错退出 —— 不会让你到真要恢复那天才发现备份一直是坏的。

**恢复（破坏性，会覆盖现网库）：**

```bash
/opt/flow/deploy/hk/restore.sh                        # 恢复最新一份备份（二次确认）
/opt/flow/deploy/hk/restore.sh app-20260914-200048.db # 恢复指定文件
FORCE=1 /opt/flow/deploy/hk/restore.sh                # 跳过二次确认（仅脚本/定时调用用）
```

恢复流程固化了最容易踩的坑：

1. 恢复前**先把当前线上库也备份一份**（恢复错了还能滚回去）；
2. 校验待恢复文件的完整性（拒绝把坏文件写进线上库）；
3. 停 backend → 原子替换（并清掉旧的 `-wal`/`-shm`，否则旧 WAL 会覆盖新数据）
   → 起 backend → 健康检查通过才算成功。

⚠ 恢复是**覆盖现网库**的操作。无论何时做，恢复后都请抽查一条业务数据（登录 / 曲目列表）确认无误。

---

## 真机验证记录（2026-09-15，Docker 29.6.2 实跑）

mock 自检只能证明“流程对”，所以又用**真 Docker** 逐项跑了一遍：

| 项目 | 结果 |
|---|---|
| `docker compose build backend` | ✅ 31.6s 成功 |
| 镜像泄密扫描（`.env` / `*.pem` / `*.db`） | ✅ 无（原先把 `app.db.bak-20260903` 等**真实备份**烤进了镜像，已修 `.dockerignore`） |
| 容器内 `/health` | ✅ `{"status":"ok"}` |
| 管理台 `/api/admin/stats`（带 token） | ✅ 200（`ADMIN_TOKEN`/`ENCRYPTION_KEY` 现已真正透传进容器） |
| 容器内跑 `probe_mureka.py` | ✅ 真连通 api.mureka.ai，假 Key → `401 / kind=auth / exit 2`（分类链路端到端正确） |
| `frontend` 静态导出（node:20-alpine） | ✅ 全部页面 ○Static 预渲染成功，`NEXT_PUBLIC_API_BASE` 已内联 |
| `backup.sh`（在线备份） | ✅ 产出 13 表、`integrity_check = ok` |
| `restore.sh`（真机恢复） | ✅ 注入脏表 `_restore_marker` → 备份干净库 → 再注入脏表 → 恢复 `exit=0`；恢复后 `table_count=13`、`_restore_marker` 不存在、`integrity=ok`；第 4 步自动生成了救援备份 |
| `caddy validate`（官方镜像） | ✅ `Valid configuration` |
| `caddy` 运行时路由（官方镜像） | ✅ `:80` 站点反代 `backend:8000`：`/health`→200（与后端原样 `{"status":"ok"}`）、`/api/*`→后端（404 来自后端而非 caddy 502）、`/`→`/srv` 静态 `index.html`、`/missing`→404；安全头（HSTS / X-Content-Type-Options / X-Frame-Options / `-Server` 移除）齐全 |
| `validate_compose_env.py` | ✅ 完整 env → 0；缺 `ENCRYPTION_KEY` → 1 并列出 MISSING |

⚠ 唯一仍需香港真机确认的：**该出口是否被 Mureka 403 地域封锁**（本机出口非香港，
上面那次 401 说明链路通、但无法证明香港出口可用）。上线第一步就是看启动日志首行的
`Mureka 出口自检: kind=...`。
- **`克隆失败`** → 私有仓库需先在服务器配置部署密钥。
- **`存在必填变量缺失`** → `scripts/validate_compose_env.py` 拦截。按提示补齐 `.env` 后重跑。
- **证书申请失败** → 确认域名 A 记录已生效（`dig +short 域名`）且 80/443 可达。
- **Caddy 连不上后端** → 本套件用 `--network host` 运行 Caddy，故 `127.0.0.1:8000` 指向宿主机。若改成 bridge 网络会连不通。

---

## 上线前自检（推荐）

部署脚本是“一次写对、上线才发现错”的高风险脚本，所以配套了 mock 自检：把
`docker / git / apt-get / id / python3` 全部替换成 mock，**完整跑一遍 bootstrap**
（不装 Docker、不联网、不碰真实 `.env`）：

```bash
REAL_PYTHON=$(command -v python3) ./deploy/hk/selftest.sh
```

覆盖：特殊字符 API Key（`| & $ \`，sed 的經典陷阱）/ 守卫条件 / 出口 403 不中断部署
/ 幂等性（不覆盖 `JWT_SECRET`、源码未变时不重复构建前端）/ 前端构建与反代路由 / 产物缺失必须中止。
退出码 0 = 全部通过。**当前：38/38 PASS。**

FLOW 共置脚手架另有**独立**自检（专测 LLM 凭据闸门与健康检查，**不需要** `REAL_PYTHON`，
因为 `flow/bootstrap.sh` 全程纯 bash、不调用 python）：

```bash
./deploy/hk/flow/selftest.sh          # 15 组用例 / 48 项断言，当前 48/48 PASS
```

> ⚠ **`REAL_PYTHON` 不是可选的，必须传。** 不传时 mock 的 `python3` 只“假装成功”、
> **不会真的写入 `.env`**，于是 bootstrap 自身的回读校验（`写入值与传入值不一致`）会被触发并中止，
> 结果是一整片红（实测 12 通过 / 24 失败），**与脚本是否有 bug 无关**。
> 传了真实解释器才会真正执行 `set_env_var()` 的替换逻辑（含 `| & $ \` 等特殊字符处理）。

---

## 未包含 / 待你决策（诚实标注）

1. **FLOW 部署脚手架已完成**（`flow/bootstrap.sh` + `flow/env.prod.example` +
   `flow/README.md`）。硬阻塞只剩一项：**LLM 凭据须由你提供** —— 选定 provider 并填入
   真实 key 后，脚本即可 fail-closed 闸门通过并一键拉起 FLOW（与音乐平台共置时还需先定
   共置方案：共用 Caddy 或独立端口/域名，详见 `flow/README.md`）。
2. **数据库为每服务独立 SQLite**。跨产品桥 `user_links` 只在**共享库**时有意义；
   依赖它的功能（FLOW 额度支付音乐）目前是**未批准的 P1 提案**，故暂不共享
   （容器间共享 SQLite 会增加写冲突风险，当前收益为 0）。
   待该功能批准时再决定：共享 Postgres（需 psycopg2 + 迁移）或同机 bind-mount 共享 SQLite。
