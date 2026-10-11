# FLOW 音乐 APK 化详细方案

> 编制：2026-09-13 · 基于实读代码 + 竞品调研（Suno 2026-09 现状）
> 范围：`ai-music-platform`（音乐）打包为 Android APK/AAB，复用 FLOW 现有 Capacitor 管道
> 已排除：**声音克隆**（产品暂停，代码封存保留，见 §7）

---

## 0. 结论摘要

**能做，且比从零起步容易得多**——FLOW 已有成熟 Android 管道；音乐前端是纯客户端 SPA，可静态导出。
**但不是"改个配置就出包"**：有一个**只在真机才暴露**的硬障碍（Cookie 跨站），必须先解决。

- **推荐主路径**：本地打包静态资源 + `server.hostname` 与 API 共享父域 → **不动任何安全属性**，且离线可用。
- **工作量**：核心闭环约 **3–7 工作日**；进阶期（分轨/区间编辑/IAP/车载）约 **+10–15 工作日**。不含商店审核。
- **定位原则（对齐竞品实证）**：**手机负责「起歌」，浏览器负责「收尾」**——Suno 的 iOS/Android/网页三端对比表证实了这条分工，不要试图在手机上复刻完整工作台。

---

## 1. 现状盘点（全部实读代码）

### 1.1 已有管道（可直接复用）

| 资产 | 位置 / 证据 |
|---|---|
| Capacitor 6 工程 | `capacitor.config.json` + `android/`（appId `com.gospelai.app`，webDir `mobile/dist/android`） |
| 多渠道构建 | `package.json` 脚本：`android:sync` / `android:sync:cn` / `android:sync:play`；`build:apk-release` / `build:aab-release` |
| 变体与市场 | `scripts/variants.js`、`build-config.js`（`--market=hk/cn/global`、`--channel=googleplay`） |
| 版本与发布 | `version.json` + `scripts/bump-version.js`、`scripts/collect-build.js`、`mobile/APK_RELEASE_ZH.md` |
| 已装原生插件 | `@capacitor/app`、`haptics`、`keyboard`、`status-bar`、`splash-screen` |
| JDK | 构建脚本已固定 `JAVA_HOME=...jdk-17.0.20.8-hotspot` |

### 1.2 音乐前端（可行性前提）

- Next.js App Router，`frontend/app/` 下 **无** `getServerSideProps` / `next/headers` / `route.ts` / `revalidate`；9 个页面为 `'use client'`；经 `NEXT_PUBLIC_API_BASE` 访问远端 API。
- **结论：本质是「客户端 SPA + 远端 API」→ 静态导出（`output: 'export'`）可行。**

### 1.3 后端现有能力（可作为 APK 功能候选）

生成类：`generate` / `instrumental` / `easy-generate` / `extend` / `cover` / `remix` / `region-edit` / `stem`（同步 zip）
消费类：`library`（我的）、`plaza`（广场：hot/new/top + 点赞/收藏/评分）、`play`、`download`、`preview`（试听前 N 秒）
辅助类：`assist/lyrics`、`assist/style`、`update_visibility`（发布）、逐字歌词 `lyrics_sync`
账号/计费：`register`/`login`/`logout`、`me`、`billing/*`（checkout/orders/webhook）
管理类：`admin/*`（审核、引擎、路由、定价、账单对账、审计、跨域桥接）

### 1.4 已就绪项

- **CORS**：`config.py:34-37` 默认已含 `capacitor://localhost`、`https://localhost`（Capacitor 6 默认 origin 为 `https://localhost`）。

---

## 2. 必须先解决的 4 个硬约束

### A. Cookie 跨站失效（最关键，且只在真机暴露）

- 现状：`config.py:22` `cookie_samesite = "lax"`（上一轮为防 XSS 将令牌改为 HttpOnly Cookie）。
- 问题：APK 内 WebView origin 为 `https://localhost`，请求 `https://api.你的域名` 属**跨站** → Lax Cookie 不携带 → **登录成功但随后全部鉴权接口 401**。本地/CORS 测试**看不出来**。
- **推荐解法（安全性零回退）**：Capacitor 配置 `server: { hostname: "music.你的域名", androidScheme: "https" }`，并让 API 位于**同父域子域**（如 `api.music.你的域名`）。此时二者同为 `music.你的域名` 的注册域 → **同站** → Lax 天然成立，且静态资源仍本地打包（离线可用）。
  - 前提：API 必须是同父域的子域；跨域第三方域名无效。
- 备选（有代价，需你拍板）：`cookie_samesite = "none"` + Secure + HTTPS，但**削弱 CSRF 防护**，必须同步补 CSRF（自定义头/双提交）。

#### A′. 已完成的实证验证（2026-09-13，无真机）

真机测试不可用时，用「后端契约实测 + 同站拓扑求值」两步替代，并已固化为回归测试。

**段 1 — 后端 Cookie 契约（真实后端实测，非推断）**
`backend/scripts/probe_apk_samesite.py` 输出：

```
POST /api/auth/register (https)  ->  200
Set-Cookie: flow_auth=<JWT>; HttpOnly; Max-Age=86400; Path=/; SameSite=lax; Secure
GET /api/auth/me  无 Cookie  ->  401
GET /api/auth/me  带 Cookie  ->  200
HTTP 基址下 Set-Cookie 不含 Secure
```

结论：登录态**只**挂在 `HttpOnly + SameSite=lax` Cookie 上（auth.py:108-114），
`/api/auth/me` 不接受其它凭据形式；且 `secure` 仅在 `request.url.scheme == "https"`
时置真（auth.py:107），因此 `http://localhost` 调试不会丢 Cookie。

> ⚠ 上表「带 Cookie → 200」是 httpx 客户端的结果。httpx / okhttp / curl 都
> **不实现 SameSite**，无论跨站与否都会带上 Cookie —— 这正是该缺陷
> 只在 WebView 里暴露、本地工具永远测不出来的原因。

**段 2 — 同站拓扑求值（RFC 6265bis §5.2 可注册域比较）**

| 场景 | 应用域 | API 域 | 判定 | Lax 下结果 |
|---|---|---|---|---|
| A 现状直连 | `localhost` | `api.flow.example.com` | 跨站 | ❌ Cookie 被丢弃 |
| **B 推荐方案** | `music.flow.example.com` | `api.flow.example.com` | **同站** | ✅ 正常携带 |
| C 换主域 | `music.flow.example.com` | `api.other-example.com` | 跨站 | ❌ Cookie 被丢弃 |
| D 多标签后缀 | `flow.github.io` | `api.github.io` | 跨站 | ❌ Cookie 被丢弃 |

**段 3 — 未闭合项（诚实标注）**
同站/跨站的最终执行者是浏览器。本机**无法启动任何浏览器**（PowerShell 直调、
Bash、cmd.exe、`System.Diagnostics.Process` 四条路径全部被环境阻断，
连 `chrome --version` 都返回空输出），因此段 2 属于**规范推导**，不是浏览器实测。
最后一公里需在任意带 Chrome 的机器上执行脚本自带的 harness 收尾：

```bash
python backend/scripts/probe_apk_samesite.py --serve 4174
chrome --headless=new --no-proxy-server \
  --host-resolver-rules="MAP music.flow.test 127.0.0.1, MAP api.flow.test 127.0.0.1" \
  --user-data-dir=%TEMP%\p1 \
  "http://music.flow.test:4174/harness.html?api=http%3A%2F%2Fapi.flow.test%3A8000&tag=B"
```

预期：B 场景 `cookie_sent=true`；把 api 换成跨站域则 `false`。

**已固化的回归护栏**：`backend/tests/test_apk_cookie_samesite.py`（9 项，全量 345 项 RC=0）。
它把三件事钉死：Cookie 属性（HttpOnly / lax / HTTPS 下 Secure / HTTP 下不 Secure）、
`/me` 只认 Cookie、以及上述 A–D 四种拓扑的同站判定。
将来任何人改 `cookie_samesite`、无条件加 `Secure`、或把 APK/API 换到不同主域，
都会在这里立刻失败，而不是等到真机才发现登录态失效。

### B. 静态导出会丢失安全响应头

- 需加 `output: 'export'` + `images: { unoptimized: true }`。
- **代价**：`next.config.js` 的 `async headers()`（CSP/防护头）在静态导出下**不生效**。
- 兜底：由承载层（Caddy/Nginx，仓库已有 `Caddyfile`）下发同等头，或退化为 `<meta http-equiv>`。**不可省略**，否则等于把上一轮的 CSP 加固白做。

### C. Google Play 强制应用内购买

- 数字商品/订阅在 Play 渠道**必须**走 Play Billing，且不得引导到站外支付。
- 方案：**按变体分流**（天然契合现有 3 变体）——
  - `play` 变体：接 Play Billing（Capacitor 社区插件或原生封装），服务端做票据校验。
  - `default`（direct/hk）：沿用自有 Stripe/PayPal。
  - `cn`：国内商店资质/版号成本高 → 建议仅直装分发，且不在 APK 内做支付引导。
- 与现有订阅模型的关系：`user.tier_key` + `Subscription` 已是统一权益源，IAP 只需作为**新的支付入口**写入同一模型。

### D. 后台播放与通知需要原生能力

- Android 后台播放需**前台服务 + MediaSession**（否则切后台即被系统回收，用户感知为"锁屏就停"）。
- 现有插件**不含**后台音频/通知/文件系统/分享 → 需新增（§4）。
- 另注：`FcmBridge.java`（`src/play`）此前已删除、不参与编译；若要做"生成完成推送"，需重新评估远程推送方案。

### E. 全局前提（非 APK 专属但会阻断上线）

- **Mureka 403 = 地域封锁**：香港部署前必须实机探测（`backend/scripts/probe_mureka.py` 已备）。此条不通，APK 生成功能在中国香港/海外出口一律不可用。

---

## 3. 功能划分：APK 原生 vs Web 独占

### 3.1 APK 内提供（P0 核心闭环）

| 功能 | 后端接口 | 说明 |
|---|---|---|
| 文本/描述生成歌曲 | `POST /api/songs/generate` | 核心 |
| 纯器乐生成 | `POST /api/songs/instrumental` | |
| 灵感生成（官方 styles） | `POST /api/songs/easy-generate` | |
| 续写 / 翻唱 / Remix | `extend` / `cover` / `remix` | 迭代式创作 |
| 我的音乐库 + 发布 | `library` + `update_visibility` | |
| 播放（含逐字歌词 `lyrics_sync`） | — | FLOW H5 已有逐字歌词经验可复用 |
| 广场浏览/点赞/收藏/评分 | `plaza/*` | 消费端，天然适合手机 |
| 试听门控（未订阅前 N 秒） | `preview` | 与 freemium 一致 |
| 账号登录/注册 | `auth/*` | |

### 3.2 APK 内提供（P1 进阶——这是"高级功能 APK 化"的重点）

| 功能 | 后端接口 | 移动端价值 / 竞品参照 |
|---|---|---|
| **分轨导出（stem）** | `POST /{id}/stem`（同步 zip） | Suno 移动端**已提供**；我们可做到下载 zip 后解压、单轨试听/分享 |
| **区间编辑（简化版）** | `POST /{id}/region-edit` | 精确时间轴难做 → 手机端只做"选中段落→重生成"，精细调整留给 Web |
| **歌词/风格 AI 助手** | `assist/lyrics`、`assist/style` | 移动创作刚需 |
| **离线下载 + 离线播放** | `download` + 本地存储 | 竞品对下载有额度限制；我们可做成差异化优势 |
| **生成完成通知** | 轮询 + 本地/远程通知 | 生成耗时，通知是留存关键 |
| **系统分享** | — | 分享音频/链接到社交平台 |
| **播放器高级能力** | — | EQ、睡眠定时、A-B 循环、变速、音质档（FLOW H5 **已有** EQ/睡眠定时/音质，可直接复用） |
| **后台播放 + 锁屏控制 + 媒体通知** | — | 音乐 App 及格线 |
| **Android Auto 车载播放** | — | Suno 已于 2026-05 支持；MediaSession 打通后成本较低 |

### 3.3 明确留在 Web（不下发 APK）

- **管理后台全部**：审核、引擎配置、路由规则、定价档位、账单对账、审计日志、跨域账号桥接——**严禁打进 APK**（既无必要又扩大攻击面）。
- 批量导出 / ZIP、精细区间编辑（大屏时间轴）、歌词时间轴逐字精修。
- 支付账务、发票、退款管理。
- **声音克隆**：产品暂停（见 §7）。

---

## 4. 需新增的 Capacitor 原生能力

| 能力 | 插件 | 优先级 |
|---|---|---|
| 后台播放 + 锁屏/媒体通知 | 需原生（前台服务 + MediaSession）或社区后台音频插件 | P0 |
| 本地下载 / 离线播放 | `@capacitor/filesystem` | P1 |
| 通知（生成完成） | `@capacitor/local-notifications`（+ 远程需 FCM） | P1 |
| 系统分享 | `@capacitor/share` | P1 |
| 应用内购买 | Play Billing 插件（仅 `play` 变体） | P2 |
| 网络状态（离线提示） | `@capacitor/network` | P1 |

---

## 5. 分期路线图与验收

**阶段 0 — 跑通骨架（约 1–2 天）**
1. 音乐前端加 `output: 'export'` + `images.unoptimized`，产出静态包。
2. 剔除 `/admin/*` 路由（不进 APK）。
3. 承载层补 CSP/防护头（补齐静态导出丢失的头）。
4. Capacitor 新增音乐目标（webDir 指向音乐静态产物），复用 `build-config.js` 变体。
- ✅ 验收：APK 安装可打开首页、可登录且**刷新后登录态保持**（即 §2-A 已通）。

**阶段 0 执行结果（2026-09-14，真机验证按指示跳过）**

| # | 项 | 状态 | 落地位置 |
|---|---|---|---|
| 1 | 静态导出 | ✅ | `frontend/next.config.js` — `APK_BUILD=1` 일 때만 `output:'export'` + `images.unoptimized` + `trailingSlash`。Web 기본 동작 불변(실측: Web 빌드 12 라우트 유지) |
| 2 | 剔除 `/admin` | ✅ | `frontend/scripts/build-apk.mjs` — **빌드 시점에 `app/admin` 격리**(사후 삭제 아님) |
| 3 | 承载层补头 | ✅ | 동일 스크립트가 CSP 를 `<meta http-equiv>` 로 주입(APK 는 Caddy 없음) |
| 4 | Capacitor 音乐目标 | ✅ | `scripts/build-music-config.js` — appId `com.gospelai.music*`, webDir → 음악 정적 산출물, `server.hostname=music.<도메인>` |

⚠️ **실측으로 바로잡은 것: "out/admin 삭제" 는 불충분했다.**
HTML 만 지우면 `_next/static/chunks/app/admin/page-*.js` 가 **그대로 남는다**.
→ 빌드 전 `app/admin` 을 `.apk-build-stash/` 로 격리해 **컴파일 자체를 배제** 하도록 바꿨다.
라우트 14 → 8 개로 줄고(admin 6개 제외), 산출물에서 `"/admin"` 문자열이 완전히 사라졌다.
추가로 **관리 진입점 2곳**(`components/NavBar.tsx` 의 "管理" chip, `app/account` 하단 링크)도
`NEXT_PUBLIC_APK_BUILD` 로 데드코드 제거 — 이들을 빼먹으면 404 링크가 남는다.

남은 잔재(정직 표기): 공유 `lib/api.ts` 의 `/api/admin/*` **경로 문자열** 만 남는다.
자격증명·UI·라우트 없음, 백엔드 `X-Admin-Token` 게이트는 그대로 → inert.

- 验收状态：1~4 全部落地，**仅差真机**（§2-A′ 段 3 的浏览器实测，一条命令即可补跑）。

**阶段 1 — 核心闭环（约 2–4 天）**
5. 生成 / 库 / 广场 / 播放 / 试听门控联调。
6. 后台播放 + 锁屏控制 + 媒体通知。
7. 分享。
- ✅ 验收：后台切出仍播放、锁屏可控制、生成→播放→分享全链路通过。

**阶段 2 — 进阶能力（约 5–8 天）**
8. 离线下载与离线播放。9. 生成完成通知。10. 分轨导出（zip 解压/单轨分享）。11. 区间编辑简化版。12. 播放器高级能力（复用 FLOW H5 的 EQ/睡眠定时）。
- ✅ 验收：断网可播已下载曲目；生成完成有通知；分轨可试听单轨。

**阶段 3 — 商业化与增长（约 5–7 天）**
13. Play 变体接 IAP + 服务端票据校验。14. Android Auto。15. 按渠道/市场差异化分发。
- ✅ 验收：Play 内购可订阅且权益与 Web 一致。

---

## 6. 风险与不做清单

| 风险 | 应对 |
|---|---|
| Cookie 跨站导致登录失效（最易翻车） | 阶段 0 第 4 步**必须真机验证**，不通不往下走 |
| 静态导出丢失 CSP | 承载层兜底，列为阶段 0 验收项 |
| 纯 WebView 壳被 Play 判低质量 | 阶段 2 起具备离线/通知/后台播放等原生能力后再提审 |
| Mureka 403 地域封锁 | 上线前在中国香港出口实机探测（P0-CRIT） |
| 国内商店资质 | `cn` 变体暂不走商店，仅直装 |
| 多实例限流/TOCTOU | 当前单实例；规模化前需 Redis（已知架构项） |

---

## 7. 声音克隆（产品暂停 · 代码封存）

- 决策（2026-09-13）：**停止对外提供**，但**代码与测试全部保留、禁止删除**。
- 封存方式（已实施，提交 `f61e740`）：新增 `settings.enable_vocal_clone`（默认 `False`）→ `POST /api/songs/vocal-clone` 返回 **501**；实现与用例原样保留。
- 保留清单：
  - 供应商实现 `MurekaProvider.vocal_clone`（`orchestrator/mureka.py`，含官方 multipart 路径与约束注释）
  - 端点 `clone_vocal`（`routers/songs.py`，含文件头魔数校验、大小校验、限流）
  - 文件头魔数校验 `_VOCAL_CLONE_MAGIC_OK` 及其用例 `tests/test_upload_security.py`
  - 端到端用例：`test_stage0_capability_gate.py`、`test_stage2_mock_e2e.py`（测试环境 `conftest.py` 显式开启以保住覆盖）
  - 前端 `api.ts: vocalClone`（保留，附休眠注释）
- **重新上线**：仅需 `ENABLE_VOCAL_CLONE=true`，前端无需改动。
- ⚠️ 注意区分：`vocal_id`（生成入参，仍广泛使用）≠ `vocal_clone`（上传音频建音色，已封存）。

---

## 8. 立即可执行的下一步

~~建议先只做**阶段 0**（静态导出 + 剔除 admin + 承载层补头 + 音乐 Capacitor 目标），并在真机验证登录态保持。~~
**→ 阶段 0 已完成（2026-09-14）**，见 §5 上面的执行结果表。

**现在的下一步 = 阶段 1（核心闭环）**，且**必须先补一次真机/浏览器验证**：

1. **补 §2-A′ 段 3 的浏览器实测**（唯一未闭合门禁）——任意带 Chrome 的机器一条命令：
   ```bash
   python backend/scripts/probe_apk_samesite.py --serve 4174
   chrome --headless=new --no-proxy-server \
     --host-resolver-rules="MAP music.flow.test 127.0.0.1, MAP api.flow.test 127.0.0.1" \
     --user-data-dir=%TEMP%\p1 \
     "http://music.flow.test:4174/harness.html?api=http%3A%2F%2Fapi.flow.test%3A8000&tag=B"
   ```
   预期 B 场景 `cookie_sent=true`。**这一步不通就不要往下走**（否则登录态在真机必丢）。

2. 拿到真实域名后出首个 APK：
   ```bash
   cd ai-music-platform/frontend
   node scripts/build-apk.mjs --api=https://api.<你的域名>
   cd ../..
   node scripts/build-music-config.js --domain=<你的域名>
   npx cap sync android && npm run build:apk-release
   ```
   ⚠️ 之后若要回到 FLOW 主 App，必须 `node scripts/build-config.js android --market=hk`
   （音乐脚本会覆盖 `capacitor.config.json` / `build.gradle` / `strings.xml`）。

3. 阶段 1 功能联调：生成 / 库 / 广场 / 播放 / 试听门控；后台播放 + 锁屏控制 + 媒体通知；分享。

已知缺口（不阻塞打包，但阻塞上架）：
- **Mureka 403 地域封锁**未在中国香港出口实测（P0-CRIT）。
- ~~`bump-version.js` 尚未支持音乐版本线（`version.json.music`），需补；否则 Play 上传会因 versionCode 未递增被拒。~~
  ✅ **已补齐（2026-09-26）**：新增 `--app=music`，走 `version.json.music[.variants.<key>]` 独立版本线，
  与 flow 版本线互不影响。用法 `node scripts/bump-version.js patch --app=music --variant=play`
  （或 `npm run version:music:patch`）。回归测试 `tests/test_bump_version_music_line.py`。
  注意：不写 `--app=music` 时 `--app` 默认 `flow`，过去会**静默把版本加到 FLOW 本体线上**（不是报错），
  现已由参数校验 + 测试守住两条线互不干扰。
- 同一根因的第二处（产物命名）：`collect-build.js` 的产物前缀按 `appByAppId()` 区分
  `flow-ai-*` / `flow-music-*`，否则音乐 Play 包与 FLOW 本体 Play 包**文件名完全相同**，
  会出现「做的是音乐包、上传的是本体包」。
