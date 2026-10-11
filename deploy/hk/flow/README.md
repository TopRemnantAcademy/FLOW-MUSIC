# deploy/hk/flow — FLOW(CCM) 共置部署脚手架

把 **FLOW(CCM)** 与音乐平台共置于同一台中国香港服务器。本脚手架**不重复维护
compose / Caddy** —— 它直接复用 FLOW 仓库自带的：

- `docker-compose.prod.yml`（qdrant + backend + ui + caddy 四服务）
- `Caddyfile`（`:443` self_signed + `auto_https off`，无域名即可起）

本目录只额外提供两件事：

1. **`bootstrap.sh`** —— 凭据闸门 + 构建启动 + 健康检查（fail-closed）。
2. **`env.prod.example`** —— LLM / Qdrant / 密钥等部署必填项模板（从 FLOW 根
   `.env.example` 摘录的关键项；完整变量以 FLOW 根 `.env.example` 为准）。

---

## ⛔ 硬阻塞：LLM 凭据

FLOW 的主链路（채팅 / 질의 / 설교 생성）**强依赖 LLM**。脚本在部署前的闸门与
后端 `/ready`（`backend/app/main.py` 的 `llm:key_missing`）判读口径**完全一致**：

| LLM_PROVIDER | 需要的密钥 |
|---|---|
| `gemini` | `GOOGLE_API_KEY` |
| `openai` | `OPENAI_API_KEY` |
| `anthropic` / `claude` | `ANTHROPIC_API_KEY` |
| `deepseek` | `DEEPSEEK_API_KEY` |
| `tencent` | `TENCENT_API_KEY` |
| `ollama` | `OLLAMA_HOST`（本地，无需 key） |

- `LLM_PROVIDER` 缺失或非法 → 脚本 **中止**，不构建、不起服。
- 所选 provider 的 key 为空 / 仍是占位符（`change-me` / `dummy` / `your-key` / `<PASTE_KEY>`）
  → 脚本 **中止**，并打印「后端 /ready 会因 `llm:key_missing` 返回 503」。

> 这是为了**先 fail-closed 再部署**：宁可起不来，也不要起一个「能跑但聊天全降级」
> 的半成品（缺 key 时 `/ready` 恒 503，移动端/Web 主功能不可用）。

密钥到位后，脚本才会 `up -d --build` 并依次探测 `/health`（存活）与 `/ready`（就绪）。

---

## 共置冲突（务必先读）

FLOW 的 Caddy 在 `docker-compose.prod.yml` 里发布 **`443:443` 与 `8501:8501`**。
同机若已跑音乐平台（其 Caddy 也占 **`:443`**），**两个 Caddy 会争抢 443 而冲突**。
可选方案（二选一）：

1. **共用一个 Caddy**：把 FLOW 的 `route` 段并入音乐平台 Caddyfile（或反之），只留一个
   Caddy 进程监听 443；FLOW 的 ui/backend 经容器网络反代。为此，本目录的 `bootstrap.sh`
   需设置 `SKIP_CADDY=1` 并在外部 Caddyfile 中接管 FLOW 路由。
2. **各自独立**：给 FLOW 换端口或独立域名（例如 `flow.example.com` 走 443，音乐走别的
   域名），即保留 FLOW 自带 Caddy。注意 `:8501` 是 FLOW UI 兼容入口，共置时也可能与别的
   服务撞端口，按需调整。

⚠ 默认骨架**自带 Caddy**（`docker-compose.prod.yml` 内）。共置场景下**不要**两个都默认起。

---

## 部署步骤

```bash
# 0) 准备（一次性）
#   · 把 FLOW 仓库配成可 clone（私有仓库请先在服务器放部署密钥 / SSH key）
#   · 确定 LLM provider 并拿到真实 key

# 1) 跑引导脚本（必须 sudo -E 保留环境变量）
export FLOW_GIT_URL=https://github.com/<you>/flow.git
export APP_DIR=/opt/flow-ccm          # 可选，默认即此
sudo -E ./deploy/hk/flow/bootstrap.sh

# 2) 首次会因缺 .env.production 而中止并生成模板 —— 填好必填项后重跑
sudo -E nano /opt/flow-ccm/.env.production   # 至少填 LLM_PROVIDER + 对应 key
sudo -E ./deploy/hk/flow/bootstrap.sh
```

脚本会自动：
1. 若 `APP_DIR` 无源码则 `git clone $FLOW_GIT_URL`（需提前配部署密钥）；
2. 检查 Docker；
3. 缺 `.env.production` 则从脚手架模板生成并中止（等你填 key）；
4. **LLM 凭据 fail-closed 闸门**；
5. 弱默认值告警（`ADMIN_API_KEY` / `AUTH_SECRET` / `ENCRYPTION_KEY`）；
6. `docker compose ... up -d --build`；
7. 轮询 `/health` 再 `/ready`，任一不通过则打印后端日志并中止。

### 可选开关

| 变量 | 作用 |
|---|---|
| `FLOW_GIT_URL` | 源码 clone 地址（无源码时必填） |
| `APP_DIR` | 部署目录（默认 `/opt/flow-ccm`） |
| `SKIP_CADDY=1` | 已有外部 Caddy 接管路由时跳过 FLOW 自带 Caddy（共置方案 1） |

---

## 拿到域名后启用 Let's Encrypt

当前 `Caddyfile` 用 `auto_https off` + `tls self_signed`（无域名临时方案）。
正式上线：

1. 把 `:443 {` 改成 `你的域名 {`（如 `flow.example.com {`）；
2. 删掉该行上方的 `auto_https off`；
3. 确保域名 A 记录指向服务器、80/443 放行；
4. 重跑 `bootstrap.sh`（或仅 `fc up -d caddy` 重启 Caddy）。

Caddy 会自动申请并续期 Let's Encrypt 证书。

---

## 上线前自检（改脚本后必跑）

`bootstrap.sh` 是「一次写对、上线才发现错」的高风险脚本，所以配了 mock 自检：把
`docker / git / sleep` 全部换成 mock 后**完整跑一遍**（不装 Docker、不联网、不 clone 真实仓库）。

```bash
./deploy/hk/flow/selftest.sh        # bootstrap 自检：60 项断言，退出码 0 = 全通过
./deploy/hk/flow/backup_selftest.sh # 备份脚本自检：24 项断言，退出码 0 = 全通过
```

bootstrap 覆盖：正常部署 / 缺源码时的 clone 与中止 / **模板必须从脚手架(而非 APP_DIR)生成** /
**LLM_PROVIDER 或 key 整行缺失时必须给可读提示** / provider 非法 / 占位符与空串 key
/ 6 种 provider→key 映射 / 大小写归一化 / 弱密钥只告警不阻塞 / `/health` 不通即中止 /
`/ready` 不通要透出 `problems` 并拉日志 / 所有 compose 调用必须带 `--env-file`。

备份脚本自检覆盖：正常在线备份 / 缺 gospel.db 优雅跳过 / 密钥默认跳过（明文保护）/
`BACKUP_SECRETS=1` + 口令加密 / 缺口令即 die / `HOST_BACKUP_DIR` 异地同步 /
`BACKUP_QDRANT=1` 本地快照 / 云端 `QDRANT_URL` 跳过本地快照。

> 为什么用 `-e` 的脚本仍要专门测「给可读提示」：见下方「自检抓到的真 bug」。
> `sleep` 被 mock 掉是为了把负用例的 30×(2s|3s) 等待压成 0，否则跑一轮要几分钟。

### 自检抓到的真 bug（务必保留这些用例）

`bootstrap.sh` 带 `set -euo pipefail`，而下面这种写法会让脚本**静默退出、不给任何提示**：

```bash
PROV="$(env_val LLM_PROVIDER | tr '[:upper:]' '[:lower:]')"     # 变量行整行缺失时
KEY_VAL="$(env_val "$NEED")"                                     # key 行整行缺失时
```

`env_val` 在「grep 不到该行」时返回 1，而**赋值语句的退出码来自命令替换** —— 于是脚本在
`set -e` 下当场退出，**根本走不到下面那句友好的 `die`**。用户只会看到「非 0 退出」，
完全不知道缺的是 LLM_PROVIDER 还是对应的 key。修复是在命令替换里补 `|| true`，
把控制权交还给 `die`。用例 5 / 9 就是这两条回归门禁。

---

## 备份与恢复（D1：FLOW 备份策略）

FLOW 的 `gospel.db`（`docker-compose.prod.yml` 里 `./gospel.db:/app/gospel.db` 的**主机侧文件**）
是备份核心。本目录提供 `backup.sh` / `restore.sh` / `crontab.example` / `backup_selftest.sh`。

### 关键前提：DATABASE_URL 注入（P0 数据持久化修复）

`bootstrap.sh` 部署后，`backend` 的 `environment` 已注入
`DATABASE_URL=${DATABASE_URL:-sqlite:////app/gospel.db}`（`docker-compose.prod.yml`）。
原因：`db.py` 在 `DB_PATH` / `DATABASE_URL` 均未设时默认写 **容器内的** `/app/.gospel.db`
（非绑定挂载），与 `./gospel.db` 挂载点不一致 → **容器重建即丢全部订阅/对话**。注入后
应用与 Alembic 都写入绑定挂载的主机文件，数据持久化、且备份目标一致。
（本地开发未注入时回退 `.gospel.db`，backup.sh 会自动回退识别。）

### backup.sh

```bash
./backup.sh                                           # 备份 gospel.db + 完整性自检 + 清理过期
KEEP_DAYS=30 ./backup.sh                             # 保留 30 天（默认 14）
BACKUP_SECRETS=1 BACKUP_PASSPHRASE='***' ./backup.sh  # 同时用 openssl AES-256 加密备份 .env.production
HOST_BACKUP_DIR=/mnt/nas/flow ./backup.sh             # 备份后同步到异地
```

- **gospel.db**：用一次性 `python:3.11-slim` 容器挂载主机文件（ro）做在线一致性备份
  （`sqlite3.backup()`），备份后立即 `integrity_check`，并清理超过 `KEEP_DAYS` 天的旧备份。
  不依赖 backend 是否在跑，cron 更稳。
- **`.env.production`（密钥）**：默认**跳过**（避免明文密钥落盘）；设 `BACKUP_SECRETS=1` +
  `BACKUP_PASSPHRASE` 才 `openssl enc -aes-256-cbc` 加密为 `backups/.env.production.enc`。
- **Qdrant**：默认**跳过**（生产用云端 `QDRANT_URL`，由厂商备份）；设 `BACKUP_QDRANT=1`
  且未配云端 URL 时才对本地 qdrant 做快照并导出。
- 备份默认落在 `$APP_DIR/backups/`（已被 `.dockerignore` 排除，绝不会烤进镜像）。

### restore.sh（有备份必须配恢复流程）

```bash
./restore.sh                              # 恢复最新一份备份
./restore.sh gospel-20260914-120000.db    # 指定文件名
```

恢复前先抢救当前库（`gospel-prerestore-<时间戳>.db`），校验待恢复文件完整性，停 backend →
原子替换（清旧 WAL/SHM）→ 起 backend → 健康检查通过才算成功。破坏性操作，默认二次确认（`FORCE=1` 跳过）。

### 定时任务

`crontab.example`：每天 03:17 备份（保留 14 天）+ 周一清理 dangling 镜像；另附加密备份、
异地同步的可选示例（口令建议经 secret manager 注入，勿硬编码）。

```bash
crontab -e   # 粘贴 crontab.example 内容
```

---

## 运维：成本看板与评测集（D2/D3/D4）

### 1. LLM 成本看板（D3）

后端提供两个管理员端点，按 provider/model 归集每次调用的 token 与原가(KRW)：

| 端点 | 格式 | 用途 |
|---|---|---|
| `GET /admin/cost-summary?days=30` | JSON | 程序化取数 / 监控告警 |
| `GET /admin/cost-dashboard?days=30` | HTML | 浏览器直接看（无外部依赖） |

- **鉴权**：`Authorization: Bearer <ADMIN_API_KEY>` 或 `X-Admin-Token: <ADMIN_API_KEY>`。
  缺 token → `403`；`ADMIN_API_KEY=change-me` → `503`（端点已禁用）。
- **原가来源**：每条 `interaction` 行在生成时按当时的 `ccp_rate_card` 计算 `cost_krw`（KRW）并落库，
  看板只做聚合，不实时询价。若某 (provider,model) 在 `ccp_rate_card` 无卡，则 `cost_krw=NULL`（不计入总额）。
- **种子费率卡**（首次部署必须跑，否则成本恒为 0，见下方坑）：

  ```bash
  # 全新库：建表 + 种子 3 张标准卡（gemini-2.5-flash / claude-sonnet-4-6 / ollama qwen2.5:7b）
  python scripts/init_db.py
  ```

  ⚠ **坑**：`scripts/init_db.py` 与 `backend/app/db.py:init_db()` 的 `create_all` 路径**必须**导入
  `backend.app.models.ccp`，否则新鲜部署不建 `ccp_rate_card` 表，种子静默失败 → 看板成本全 0。
  （已修复：两处均补 `import backend.app.models.ccp`。）
  ⚠ **影子卡**：旧种子曾用 `datetime.now()` 每次新建行，`resolve_rate_card` 取最新会遮蔽标准卡。
  修复后的 `seed_rates.py` 在 seed 前清掉同 (provider,model) 的非标准卡 + 删除已退出的 `nvidia` 卡。

- **curl 示例**：

  ```bash
  curl -H "Authorization: Bearer $ADMIN_API_KEY" \
       'https://your-host/admin/cost-summary?days=30'
  # 返回 by_provider_model[]（calls / prompt_tokens / completion_tokens / cost_krw）
  #      + total_cost_krw + 时间窗
  ```

### 2. 评测集与回归 harness（D4）

- **数据集**：`backend/data/eval_golden.jsonl`（每行一个 golden 问答，含 `category` / `lang` /
  `question` / `expected` 等字段），用于衡量 RAG 答案质量。
- **harness**：`scripts/run_gospel_eval.py` —— 加载 golden、跑检索/生成、按 (question,category,lang)
  upsert 进 `eval_question` 表（幂等），并出对比报告。

  ```bash
  python scripts/run_gospel_eval.py --seed      # 仅把 golden 灌进 eval_question 表
  python scripts/run_gospel_eval.py --run        # 跑全量评测并出报告（需有效 LLM key）
  ```

- **测试覆盖**：`backend/tests/test_gospel_eval.py` 校验 golden 加载/字段/`seed` 幂等；
  `backend/tests/test_ccp_cost.py` 校验费率卡 seed 幂等 + `calculate_cost` 单测；
  `backend/tests/test_tracing.py` 校验 Langfuse tracing 的 PII 脱敏与 usage 归因字段。

### 3. 全量测试

```bash
venv/Scripts/python.exe -m pytest backend/tests/ -q   # 当前 17 passed
```

---

## 未包含 / 待你决策

- **FLOW 与音乐平台是否共享库**：当前各自独立 SQLite。跨产品桥 `user_links` 只在共享库时
  有意义；依赖它的「FLOW 额度支付音乐」是未批准 P1 提案，故暂不实装共享。
- **LLM provider 的最终选择**：脚手架支持 6 种，但**真正用哪个、key 由你提供**。
  这是唯一剩余硬阻塞，已通过 fail-closed 闸门在部署前拦住。
- **共置网络拓扑**：见上方「共置冲突」，需在部署前确定方案 1 或 2。
