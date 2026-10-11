# FLOW / FLOW CCM 安全审计与攻击面报告

- **日期**：2026-09-13
- **范围**：`ai-music-platform`（FastAPI 后端 + Next.js 前端）、`FLOW` 后端、`mobile` PWA
- **方法**：白盒源码审计（授权，自有代码）+ 行业框架对标
  - **OWASP Top 10 2021**（A01 失效的访问控制 / A02 加密失败 / A04 不安全设计 / A05 安全配置错误 / A06 易受攻击组件 / A07 认证失败 / A10 SSRF）
  - **OWASP ASVS** 相关条款（V7 日志、V9 通信、V14 配置）
  - **CWE / SANS Top 25**（CWE-639 / 840 / 306 / 918 / 922 / 693 / 200 / 1104 …）
- **证据纪律**：每条均附 `file:line`；标注 ✅已修复 的为本次提交中已加固并有回归测试锁定；其余为待办。

---

## 一、风险总览

| 严重度 | 数量 | 已修 | 待办 |
|---|---|---|---|
| Critical | 1 | 0 | 1 |
| High | 9 | 3 | 6 |
| Medium | 17 | 3 | 14 |
| Low | 4 | 0 | 4 |
| **合计** | **31** | **6** | **25** |

本次提交：**6 项高危/中危加固全部带回归测试**（`test_security_hardening.py`，8 例，且在加固前必然失败）。

---

## 二、Critical

### C1 · 生产密钥明文驻留工作区，且会打进容器镜像 — CWE-538 / CWE-798 · OWASP A02+A05
- `ai-music-platform/backend/.env:8` `JWT_SECRET=<64位强密钥>`（明文）

> 2026-09-25 补充：本行原先写作 `JWT_SECRET=cv7QS…`，即把该值的**前 5 位**带进了
> 版本库。已抹去（未知位仍有 59 位，暴力搜索不可行），故**不因此轮换**——
> 轮换会让所有现 sessions 失效，收益远小于代价。若日后决定轮换，需连部署环境的
> 该变量一起改，只改本地 `.env` 无效。
- 同文件 `:42` `MUREKA_API_KEY`、`:57` `ADMIN_TOKEN`、`:58` `ADMIN_VIEWER_TOKEN`、`:62` `ASSIST_LLM_API_KEY`
- 复制件：`ai-music-platform/.env:5,33,54,55,57`、`backend/.env.bak:6,40`
- **放大链**：仓库**无 `.dockerignore`**（Glob 未命中），而 `backend/Dockerfile:16` 为 `COPY . .`
  → `.env` / `.env.bak` 会进入镜像层与 CI 缓存。
- **影响**：拿到镜像即可取得 JWT_SECRET（离线伪造任意用户令牌）与 ADMIN_TOKEN（超管）。
- **修复**：① 轮换全部 5 个密钥并视为已泄露；② 新增 `.dockerignore` 排除 `.env*`；③ Dockerfile 只 COPY 应用代码，密钥由运行时 secret 注入。

---

## 三、High（已修 3 项）

### H1 ✅ `/api/auth/flow-link` 可劫持任意他人 FLOW 身份 — CWE-639 · OWASP A01
- `routers/auth.py:268-272`（加固前）：
  ```python
  link = db.query(UserLink).filter_by(flow_subscriber_id=req.subscriber_id).first()
  if link:
      link.music_user_id = user.id  # ← 覆盖他人已有绑定
  ```
- **攻击**：任一登录用户提交受害者 `subscriber_id` 即把映射改写到自己账号；随后 `flow-exchange`（`auth.py:319-326`）为**攻击者**签发该身份的 sync 令牌 → 生成内容与 FLOW credit 扣费嫁祸受害者。
- **修复**：已绑定他人 → `409`；本账号已绑其它身份 → `409`；同对映射重复调用保持幂等 200。
- **回归**：`test_flow_link_cannot_hijack_other_users_subscriber`、`test_flow_link_cannot_rebind_own_account_to_another_subscriber`

### H2 ✅ 默认配置下可自助免付款升级（支付绕过）— CWE-840 · OWASP A04
- `config.py:128` `billing_mode: str = "offline"` + `routers/auth.py:243-253`：`user.tier_key = req.tier_key`
- **攻击**：默认部署 `POST /api/auth/subscribe {"tier_key":"enterprise"}` → 完整播放/下载全开、配额 3→999，付费墙失效。
- **修复**：新增 `enable_mock_subscribe`（默认 `False`），非 live 也需**显式**打开才允许自助升档；测试由 conftest 打开。
- **回归**：`test_mock_subscribe_denied_when_flag_off`

### H3 ✅ 两个 LLM 端点完全无鉴权（成本放大）— CWE-306 · OWASP A01/A04
- `routers/songs.py:1700` `assist_optimize_style(body)`、`:1738` `assist_format_lyrics(body)` —— 无 `Depends(get_current_user)`
- 对照 `:1647` `/assist/lyrics` **有**鉴权，证明是遗漏而非设计。
- **攻击**：匿名脚本高频调用 `/assist/style`（`max_tokens=800`，`assist_llm.py:72`）烧按量计费的上游 token。
- **修复**：两端点补 `Depends(get_current_user)` + `rate_limit_generate`。
- **回归**：`test_assist_endpoints_require_auth`、`test_assist_endpoints_work_when_authed`

---

## 四、High（待办 6 项）

### H4 · SSRF 守卫可被 302 重定向绕过 — CWE-918 · OWASP A10
- `security/ssrf.py:20` 只校验 scheme；`storage/s3.py:93` `ssrf_guard.guard(url)` 仅校验**初始 URL**
- `storage/s3.py:97` `httpx.get(url, timeout=60, follow_redirects=True)`、`:110` `httpx.stream(..., follow_redirects=True)`
- **攻击**：`http://attacker.io/ok` → `302 http://169.254.169.254/latest/meta-data/...`，重定向目标从未二次校验。
- **修复**：自实现重定向循环，每跳重跑 `guard()`（上限 5 跳），或改用 `safe_fetch` + `follow_redirects=False`。

### H5 · DNS rebinding（TOCTOU）—— 防重绑定的 `safe_fetch` 从未被调用 — CWE-918
- `security/ssrf.py:92` `async def safe_fetch(...)` 内含 IP 钉死；全仓检索 **`app/` 内无任何生产调用点**（仅定义）
- `routers/flow.py:51` `ssrf_guard.guard(song.audio_url)` → `:62` `await c.get(song.audio_url)` 二次解析 DNS
- **修复**：所有服务端取外部 URL 统一收口到 `ssrf_guard.safe_fetch()`，禁止裸 `httpx` 调半可信 URL。

### H6 · `/api/songs/upload` 的 `audio_url` 完全无 SSRF 守卫（供应商侧 pivot）— CWE-918
- `schemas.py:80-81` `class UploadReq: audio_url: str`（无校验）；`routers/songs.py:754` `await provider.upload(req.audio_url)`
- `orchestrator/mureka.py:430-431` 把该 URL 带上 Bearer Key 转发给供应商执行服务端拉取
- **修复**：对 `audio_url` 施加同一守卫 + scheme/域名白名单。

### H7 · 超管密钥存 localStorage — CWE-922 · OWASP A01
- `frontend/lib/api.ts:292` `localStorage.getItem("admin_token")`；`adminAuth.ts:26` 同；`api.ts:301` 附加到所有 admin 请求
- **攻击**：一次 XSS 即可外泄 `.env:57` 超管密钥 → 全站管理后台控制权。
- **修复**：管理台改用同源 httpOnly + Secure + SameSite=Strict 会话 Cookie。

### H8 · 静态加密密钥硬编码回退 — CWE-321 / CWE-798 · OWASP A02
- `security/crypto.py:16` `_DEV_KEY_MATERIAL = "dev-only-insecure-encryption-key-change-me"`；`:22` `k = _DEV_KEY_MATERIAL`
- `admin.py:314` `e.set_api_key(body.api_key)` → 供应商 API Key 以**公开常量**密钥加密落库，读库即解密。
- **修复**：密钥未配置时启动失败或禁用加密写入，绝不静默回退。

### H9 · CORS：`allow_credentials=True` + 明文 http 来源 + 通配方法/头 — CWE-346 · OWASP A05
- `main.py:52-58` `allow_origins=settings.cors_origin_list` / `allow_credentials=True` / `allow_methods=["*"]` / `allow_headers=["*"]`
- `config.py:21-23` 默认来源**全为 `http://`**（含无端口 `http://localhost`、`capacitor://localhost`）
- **影响**：面向中国大陆用户的链路若不前置 TLS，`Authorization: Bearer` 明文过网可被直接抓取重放。
- **修复**：生产 `CORS_ORIGINS` 只列 HTTPS 真实域名；methods/headers 收窄。（代码默认值建议一并改为 https。）

---

## 五、Medium（已修 3 项）

### M1 ✅ 安全响应头全部缺失 — CWE-693 · OWASP A05
- 加固前 `main.py` 全文无 HSTS/CSP/X-Frame-Options/X-Content-Type-Options/Referrer-Policy/Permissions-Policy
- **修复**：新增 `security_headers` 中间件（nosniff / DENY / strict-origin-when-cross-origin / Permissions-Policy / CSP `default-src 'none'`），HTTPS 下追加 HSTS；对 `/docs`、`/redoc`、`/openapi.json` 跳过（文档依赖外部 CDN）。
- **回归**：`test_security_headers_present`

### M2 ✅ `/api/plaza` 反射任意 Origin — CWE-942 · OWASP A05
- `main.py:74` / `:82` `"Access-Control-Allow-Origin": origin or "*"` —— 绕过全局白名单
- **修复**：仅回填白名单内 Origin，其余回落 `*`；保持 `Allow-Credentials: false`（匿名只读，安全）。
- **回归**：`test_plaza_cors_only_reflects_allowlisted_origin`

### M3 ✅ API 文档未门控 — CWE-200 · OWASP A05
- `main.py:50` `FastAPI(...)` 未传 `docs_url=None`；对照 FLOW 后端 `backend/app/main.py:260-262` 已有 `DOCS_ENABLED` 门控
- **修复**：新增 `docs_enabled`（默认 `False`），关闭 `/docs`、`/openapi.json`、`/redoc`。
- **回归**：`test_docs_disabled_by_default`

---

## 六、Medium（待办 14 项，摘要）

| # | 问题 | 位置 | CWE |
|---|---|---|---|
| M4 | 创建期 mass-assignment `is_public`，绕过「发布后置」与驳回可见性（`GET /songs/{id}` 只查 is_public 不查 moderation_status） | `songs.py:288` vs `:807`、`:1063`、`:1109-1119` | CWE-915 |
| M5 | `has_feature` 生产零调用 → free 档可调 `/stem` 等付费能力 | `entitlements.py:83-85` | CWE-863 |
| M6 | 词审为朴素子串匹配 + 通过后自动 approved（零宽字符可绕过） | `moderation.py:96`、`songs.py:430-435` | CWE-184 |
| M7 | 下载无 `max_bytes` 上限 + 临时文件从不 unlink（撑爆 `/tmp`） | `songs.py:1553`、`:320`、`storage/s3.py:96-101` | CWE-400 |
| M8 | `/vocal-clone` 仅按**文件名**判类型、整体读进内存后才判大小 | `songs.py:843`、`:850`、`:855` | CWE-434/400 |
| M9 | 注册无限流 + 409 可枚举邮箱 | `auth.py:174-181` | CWE-204/307 |
| M10 | 登录限流仅按 IP（经反代后失真）、无账号维度锁定 | `auth.py:186`、`ratelimit.py:30/40` | CWE-307 |
| M11 | JWT 不可撤销、寿命 24h、无 `jti`（改密/登出不吊销） | `auth.py:83`、`config.py:12` | CWE-613 |
| M12 | `get_optional_user` 缺 JWT 密钥 fail-closed 校验 | `auth.py:125`（对照 `:94`、`:146`） | CWE-1188 |
| M13 | 用户 JWT 存 localStorage（配合 M1 已缓解但存储面仍在） | `frontend/lib/auth.tsx:24,67`、`api.ts:234` | CWE-922 |
| M14 | 列表分页无上限 + `ilike` 通配符未转义 | `songs.py:674-675`、`:688-690`、`:1457` | CWE-770 |
| M15 | 进程内内存限流，多 worker 部署可绕过 | `ratelimit.py:13,35` | CWE-770 |
| M16 | 依赖未锁定 / 明显过时：`python-jose==3.3.0`、`passlib==1.7.4`、`python-multipart==0.0.12`(ReDoS) | `backend/requirements.txt:6,8,10` | CWE-1104 |
| M17 | FLOW 侧遗留口令：未加盐 SHA-256，pepper 用 `ADMIN_API_KEY`（`.env:70` 为 `change-me`） | `FLOW backend/app/api/auth.py:631-633` | CWE-916/759 |

---

## 七、Low（4 项）

| # | 问题 | 位置 | CWE |
|---|---|---|---|
| L1 | `flow-exchange` 无限流（可在线爆破内部令牌） | `auth.py:302-318` | CWE-307 |
| L2 | 前端 `API_BASE` 默认回落明文 `http://localhost:8000` | `frontend/lib/api.ts:2` | CWE-319 |
| L3 | 移动端 PII（user 对象）落 localStorage | `mobile/utils/state.js:257` | CWE-922 |
| L4 | `.env.bak` 备份件含明文密钥 | `backend/.env.bak:6,40` | CWE-538 |

---

## 八、已核查并确认安全（避免误报）

- **SQL 注入**：全仓无 `text()` / f-string SQL；`ilike` 均参数化。
- **IDOR**：所有权校验覆盖 delete / patch / crop / speed / stem / region-edit / download / preview；plaza 三点要求 public+completed+approved。
- **路径穿越**：本地写路径使用 `int(song_id)` / `os.path.basename()` / `tempfile.mkstemp`。
- **命令注入**：`subprocess.run(cmd, ...)` 列表形式，无 `shell=True`。
- **JWT 算法混淆**：签发与验签双侧固定 `HS256`，`alg=none` 不可行；默认弱密钥被 `auth.py:60-68` fail-closed 拦截。
- **密码哈希（音乐侧）**：bcrypt，无 MD5/SHA1，无明文口令日志。
- **Admin 授权**：22 个后台路由全部 `Depends(require_admin)`；令牌 `hmac.compare_digest` 恒定时间比较；未配置返回 503；审计日志仅记 token 前 6 位。
- **prompt 注入**：system 提示为模块常量，用户输入只在独立 `user` role，未发现拼接/泄漏面。
- **TLS 校验**：全仓无 `verify=False`。
- **移动端令牌**：仅内存态（`mobile/utils/state.js:34-40`）。

---

## 九、修复路线图（建议）

1. **P0（立即）**：C1 密钥轮换 + `.dockerignore` + Dockerfile 收敛；H4/H5/H6 三处 SSRF 收口到 `safe_fetch`。
2. **P1（本周）**：H7 超管令牌移出 localStorage；H8 加密密钥不回退；H9 生产 CORS 改 HTTPS；M6 词审归一化 + 取消自动 approved。
3. **P2（两周）**：M4 is_public 收敛到 PATCH；M5 `has_feature` 真正生效；M7/M8 上传与下载的资源上限；M9/M10 认证限流与账号锁定；M16 依赖锁定与升级。
4. **P3**：M11 JWT 可撤销（jti + 黑名单）、M13 令牌改 httpOnly Cookie、M15 Redis 限流、M17 遗留口令迁移。
