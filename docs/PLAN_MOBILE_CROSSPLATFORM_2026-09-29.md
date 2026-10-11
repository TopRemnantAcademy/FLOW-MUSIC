# FLOW CCM 音乐 AI 平台 — iOS + Android 跨平台应用方案

> **性质**：纯规划 + 差距审计，**不含代码改动**。每条结论附 `file:line` 实证或官方来源；无法验证的放入 §9。
> 撰写 2026-09-29 · 作者：移动应用开发工程师
> 前置文档：`docs/PLAN_MUSIC_APP_2026-09-28.md`（已有，本文在其基础上做**遗漏审计**与**追加设计**）

---

## 0. 结论先行

| 判定项 | 结论 |
|---|---|
| 技术路线 | ✅ **Capacitor 8**（不是现有的 6）。静态导出链路已跑通，`out/` 现成 |
| 🔴 最紧急 | **Capacitor 6 已 EOL，且 target API 34 在 2026-08-31 后无法提交 Play** —— 现有 FLOW APK 与 CCM 规划**双线均不合格** |
| 🔴 最大技术债 | 播放器是 WebView 里的 `HTMLAudioElement` + blob objectURL → **后台必停、原生播放器无法使用** |
| 🔴 最大外部阻塞 | 公网 HTTPS API base（preflight 实测 FAIL） |
| 最大非技术风险 | 合规：Apple 3.1.1（IAP）、4.2（最低功能）、国内 APP 备案 |
| 能否立即开工 | **可以**。工程侧（升级 Capacitor 8 / 建 android 平台 / 原生播放）不依赖域名；域名只阻塞"产出可发布包" |

**一句话**：不是"把现有 Web 套个壳"，而是要把**播放链路原生化 + 鉴权可 URL 化 + 基线升级到 2026 商店标准**，否则做出来的包既不能上架，上架了也不像个音乐 App。

---

## 1. 现状实证（已具备，利好）

| 资产 | 证据 |
|---|---|
| 静态导出已成功 | `frontend/out/` 已产出：`index.html` + 8 个 HTML，`apk-build-manifest.json` 记录 `gitSha=d070595` |
| `/admin` 已彻底隔离 | `apk-build-manifest.json`：`"adminExcludedFromBuild": true`；preflight 实测「디렉터리·청크 모두 없음」 |
| CSP 已注入 | 同上：`"cspInjected": 8`（8/8 HTML 均有 `<meta http-equiv=Content-Security-Policy>`） |
| 后端已预留 Capacitor origin | `backend/app/config.py:38` — `capacitor://localhost,http://localhost,https://localhost` |
| 独立 appId 已定 | `native/capacitor.config.json:2-3` — `com.flowccm.app` / `FLOW CCM`；`:4` `webDir: "../frontend/out"` |
| 独立原生工作区已建 | `native/package.json` + `native/preflight.mjs`（只读体检脚本，设计良好） |
| 可复用的原生播放服务 | **FLOW 主 App** 已有 `android/app/src/main/java/com/gospelai/app/MediaPlaybackService.java`（MediaSessionService + media3 ExoPlayer + 音频焦点自动恢复） |
| 本机工具链 | Node **22.22.2**（`node -v`）✅；Android build-tools **36.0.0** ✅ |

---

## 2. 技术选型对比

| 维度 | **A. Capacitor 8（推荐）** | B. Flutter | C. React Native |
|---|---|---|---|
| 复用现有前端 | ✅ 直接吃 `frontend/out` | ❌ 全部重写 | ❌ 全部重写 |
| 首版工作量 | 小（2–4 周工程） | 大（3–6 月） | 大（2–4 月） |
| 后台音频 | 需原生插件（有免费成熟方案） | 原生级 | 需原生模块 |
| iOS 26 SDK / API 36 | ✅ 官方模板即达标 | ✅ | ✅ |
| 商店"套壳"风险(4.2) | ⚠️ **有**，需靠原生能力化解 | 无 | 无 |
| 长期演进 | 中（WebView 性能天花板） | 高 | 高 |
| 团队栈匹配 | 现有 TS/React，零学习成本 | 需 Dart | 需 RN 生态 |
| 何时该换 | 若要做波形编辑/实时可视化等重交互 → 换 B | — | — |

**选 A**。理由：唯一能零成本复用已跑通的 Next.js 静态导出的方案；风险（套壳/后台播放）**有明确的可下载成熟插件化解**，且已有原生服务可复用。

> ⚠️ 若未来要做"多轨波形编辑 / 实时频谱交互"这类重图形功能，应把那部分单独做成原生或换 Flutter —— 但**不影响首版决策**。

---

## 3. 🔴 遗漏项审计（Omissions）

> 与 `PLAN_MUSIC_APP_2026-09-28.md` 相比，以下项**该文档未覆盖或结论已过期**。

### P0 — 阻断级

| # | 遗漏 | 实证 | 后果 | 修复 |
|---|---|---|---|---|
| **O-1** | **Capacitor 6 已 EOL + 不满足 2026 商店基线** | `native/package.json:17-20` 锁 `^6.1.2`；FLOW 根 `package.json` 同；`android/variables.gradle`（FLOW）：`minSdkVersion=22 / compileSdkVersion=34 / targetSdkVersion=34` | ① Capacitor 6 扩展支持 **2026-01-20 终止**；② Play 自 **2026-08-31** 起新应用/更新须 **target API 36**；③ Apple 自 **2026-04-28** 起上传须 **iOS 26 SDK（Xcode 26）** | 升 **Capacitor 8**（Active，2025-12-08 发布，`@capacitor/core` 最新 **8.5.2**）。v8 要求 Node 22+、Xcode 26+、AS Otter 2025.2.1+、minSdk 24 / compile&target **36**、iOS 15+ |
| **O-2** | **播放器是 WebView `HTMLAudioElement`，后台必停** | `frontend/lib/audioBus.ts:37,54,56`（`new Audio()` 全局单例）；`components/Player.tsx`（全局总线驱动）；`components/SyncedLyrics.tsx:8-9`（依赖 `HTMLAudioElement` 读 `currentTime`） | iOS 锁屏/切后台后 WKWebView 被挂起 → **声音停、下一曲不续播、锁屏控件无响应**；Android 亦不可靠 | 播放原生化：`@capgo/native-audio@8.4.2`（**免费**，Capacitor 8 对齐，iOS `MPNowPlayingInfoCenter`/`MPRemoteCommandCenter`，Android `MediaSession`+`NotificationCompat.MediaStyle`，支持 playlist 原生续播）。付费替代：Capawesome Audio Player（自带 MediaSession 8.4.0+） |
| **O-3** | **音频鉴权方式与原生播放器不兼容** | `frontend/lib/api.ts:399-408` `previewObjectUrl()`：fetch blob → `URL.createObjectURL`；`:380-398` `downloadSong()` 同。注释明写「`<audio src>` 无法携带 Authorization」；认证靠 **HttpOnly cookie `flow_auth`** + `credentials: "include"` | ExoPlayer/AVPlayer **拿不到 WebView 的 cookie**，也播不了内存里的 `blob:` URL → 原生播放器**根本无法取流** | 后端新增**短期签名 URL / token 端点**（如 `GET /api/songs/{id}/play-url` → 302 到带签名 query 的对象存储 URL，TTL 5–15 分钟）。这是**原生化的前置**，不做则 O-2 无解 |
| **O-4** | **无公网 HTTPS API base** | `native/preflight.mjs` 实测 `❌ 공개 HTTPS API base`；`frontend/out/apk-build-manifest.json` 的 `apiBase` 仍是占位 `https://api.flowccm.example.com` | 无域名则产出的包**连不上后端**，无意义 | 采购域名 + TLS + 后端可达（与 FLOW 共享的 P0） |
| **O-5** | **iOS 三件套全缺**：工程 / 签名 / 构建环境 | preflight 实测 `⚠️ ios 플랫폼 없음`、`⚠️ 현재 win32`；仓库无 `ios/` | iOS 线**零起点** | ① `npx cap add ios`（需 macOS）；② Apple Developer 账号 + 证书 + Provisioning + ASC 条目；③ 构建环境选 **GitHub Actions `macos-26` runner**（2026-02-26 已 GA，Apple Silicon arm64，预装 Xcode 26.x） |

### P1 — 影响体验/上架

| # | 遗漏 | 实证 | 修复 |
|---|---|---|---|
| **O-6** | **Android 前台服务需自建** | `@capgo/native-audio` 文档明确：**插件不会自动创建/管理 Foreground Service**；Android 14+ 需 `FOREGROUND_SERVICE_MEDIA_PLAYBACK` | 复用 FLOW 现有 `MediaPlaybackService.java`（MediaSessionService + 音频焦点自动恢复）——**是复用不是重写** |
| **O-7** | **无应用内购买**（Apple IAP / Play Billing） | `backend/app/billing/pg/` 仅 `alipay/kakao/naver/paypal/wechat` 五个适配器 | 新增两个 PG 适配器 + 端上 `capacitor-plugin-cdv-purchase`（**MIT 免费**，Capacitor 6+，iOS StoreKit 2 / iOS 15+，Android Play Billing 8.3 / API 23+）+ **服务端票据校验** |
| **O-8** | **UGC 端上治理缺失** | 有 `backend/app/routers/plaza.py`（广场） | Apple 指南 1.2 UGC 要求：内容过滤、举报、屏蔽用户、开发者联系方式。后端 moderation 已有，需**端上入口** |
| **O-9** | **无 PWA manifest / service worker** | `frontend/out/` 无 `manifest.json`/`sw.js`；`app/layout.tsx` 亦无 | 加剧 App Store **4.2 最低功能**风险（纯壳判定）；同时失去 Web 端降级渠道。建议补 PWA 作为第三条交付渠道 |
| **O-10** | **Android 安全区/刘海与返回键未适配** | `frontend/app/layout.tsx` 无 `viewport-fit=cover`；前端零 Capacitor 引用（grep `Capacitor` 无匹配） | `viewport-fit=cover` + `env(safe-area-inset-*)`；`@capacitor/app` 处理 Android 返回键 |
| **O-11** | **preflight 门槛过时** | `native/preflight.mjs:33` 判 `nodeMajor >= 20`，但 Capacitor 8 要 **22+**；且未检查 SDK 36 / Xcode 26 / 签名 / API base 真实连通 | 升级 preflight 检查项（Node 22、platform android-36、JDK、keystore、API base 探活） |

### P2 — 工程化

| # | 遗漏 | 实证 | 修复 |
|---|---|---|---|
| **O-12** | 本机缺 **Android SDK Platform 36** | 实测 SDK：`platforms/` 仅 `android-34 / 34-ext8 / 37.0 / 37.2-beta1`，**无 36** | 装 `platforms;android-36`（或用 37）；JDK 现为 17.0.20，AGP 8.13 建议 **JDK 21** |
| **O-13** | 无图标 / 启动图资产 | `native/` 仅 `capacitor.config.json`、`package.json`、`preflight.mjs` | 生成 icon + splash（可用 `@capacitor/assets`） |
| **O-14** | 无隐私清单 | — | iOS 需 `PrivacyInfo.xcprivacy`（2024-05-01 起上传校验）；第三方 SDK 亦须带 manifest（2025-02-12 起） |
| **O-15** | 无应用内账号删除 | Apple 5.1.1(v)，2022-06-30 起强制 | 端上删除入口 + 后端端点 |
| **O-16** | 无 CI | `.github/workflows/` 仅 `ci.yml`（629 B） | 加 Android（windows/ubuntu）+ iOS（macos-26）双流水线，含"生产 API base 门禁" |

---

## 4. ➕ 追加项设计（Additions）

> 这些不是"缺了会挂"，而是"音乐 AI 应用该有、且能显著抬升上架成功率与留存"的能力。

### 4.1 播放体验（核心，决定像不像音乐 App）

| 项 | 实现 | 说明 |
|---|---|---|
| 原生后台播放 + 锁屏/通知栏控制 | `@capgo/native-audio` 8.4.2 | iOS 控制中心/锁屏/CarPlay；Android 通知栏/锁屏/Android Auto，含 15s 快进快退 |
| **音频焦点策略** | 复用 FLOW `MediaPlaybackService.java` | 用户已确认需求：**他 app 出声 → FLOW 暂停；对方结束 → 自动恢复**（已实现，含 `userPaused` 防误恢复） |
| 原生续播播放列表 | 插件 playlist | 后台由原生推进下一曲，**不依赖 JS**（WebView 挂起时仍能续播） |
| UI 与系统状态同步 | `playbackState` 事件（8.3.15+） | 锁屏操作后回灌界面，避免"界面显示暂停、实际在播" |
| AirPlay / Android Auto / CarPlay | `showNotification: true` 后由系统接管 | ⚠️ iOS 开启通知会**打断**其他 app 音频（`.playback`）；这是显示 Now Playing 的必要条件，音乐 App 应接受 |

### 4.2 移动场景能力

| 项 | 插件 | 价值 |
|---|---|---|
| 离线下载 | `@capacitor/filesystem` + 后端签名 URL | 订阅者核心权益；也是化解"套壳"质疑的强原生能力 |
| 生成完成推送 | `@capacitor/push-notifications` | AI 生成耗时数十秒~数分钟，推送是刚需 |
| 分享歌曲 | `@capacitor/share` | 社区传播 |
| 深链 | `@capacitor/app` `appUrlOpen` | 分享链接直达歌曲 |
| 系统返回键 | `@capacitor/app` `backButton` | Android 必备 |
| 键盘/状态栏/启动屏 | 已在 `capacitor.config.json:16-26` 配置 | 已就绪 |
| 应用内评分 | StoreKit `SKStoreReviewController` / Play In-App Review | 榜单与转化 |

### 4.3 商业化与合规

| 项 | 说明 |
|---|---|
| 订阅 IAP | `capacitor-plugin-cdv-purchase`（MIT）+ 后端票据校验；**App 内不得引导到站外付款**（3.1.1） |
| 免费层真实可用 | 避免"看似免费实则付费才能用"的 bait-and-switch 拒审 |
| 订阅信息明示 | 价格/周期/续订/免费试听条款必须在购买前可见（3.1.2(c)） |
| 备案号展示 | 国内需 App 内显著位置展示备案号并链接备案系统 |

---

## 5. 与 FLOW 主 App 的关系（共享风险，必须一起决策）

| 项 | FLOW 主 App | CCM 音乐 App |
|---|---|---|
| appId | `com.gospelai.app`（`capacitor.config.json`） | `com.flowccm.app`（`native/capacitor.config.json:2`） |
| Capacitor | **^6.1.2（EOL）** | **^6.1.2（EOL）** |
| Android target | **34**（`android/variables.gradle`）→ **已不满足 Play 2026-08-31 要求** | 尚未建工程 |
| 原生播放服务 | ✅ 已有 `MediaPlaybackService.java` | ❌ 需复用/新建 |

**结论**：
1. **两个 App 都应升到 Capacitor 8** —— 这是同一份升级工作，做一次两处受益。
2. **保持独立 appId**（建议不变）：技术栈不同（Next.js 静态导出 vs 自研 `mobile/`），合并不划算；商店违规不连坐。
3. **可共享的三样东西**：原生播放服务 Java 代码、IAP 后端校验逻辑、签名与 CI 经验。
4. ⚠️ 已产出的 `builds/flow-ai-default-v1.0.0-c1-bfe5573.apk`（target 34）**不能提交 Play**，仅可用于真机自测/侧载。

---

## 6. 合规清单（优先级高于技术）

| # | 事项 | 依据 | 现状 |
|---|---|---|---|
| C1 | 数字内容/订阅必须走 Apple IAP / Play Billing | Apple 指南 **3.1.1**（App Review Guidelines, Feb 2026 版目录含 3.1.1 / 3.1.1(a) / 3.1.2(c)）；Play 同理 | ❌ 缺 |
| C2 | 最低功能（非纯壳） | Apple **4.2 Minimum Functionality** | ⚠️ 靠 §4 原生能力化解 |
| C3 | UGC 治理 | Apple **1.2** User-Generated Content | ⚠️ 端上缺 |
| C4 | 国内分发：**APP 备案**（工信部）+ 软著 | 《移动互联网应用程序备案工作的通知》（2023-07）；2023-09-01 起新 App 须先备案后上架；Android 商店普遍另要软著（约 30 工作日）；若含付费会员还需 **ICP 许可证** | ❌ 未启动 |
| C5 | 隐私清单 / 第三方 SDK manifest | 2024-05-01 / 2025-02-12 起 | ❌ 缺 |
| C6 | 账号删除 | Apple 5.1.1(v)，2022-06-30 | ❌ 缺 |

> C4 若**首发只做海外（Google Play / App Store 非中国大陆区）**可整体跳过，工作量差一个数量级——这是最该先定的决策。

---

## 7. 分阶段路线图

| 阶段 | 目标 | 产出 | 阻塞 | 可在 Windows 完成 |
|---|---|---|---|---|
| **P0** | 公网 HTTPS 域名 + 后端可达 | 可访问 API | 域名 | — |
| **P1** | **基线升级**：Capacitor 6→8；建 `android/`；装 SDK 36 | debug APK 可跑 | 无 | ✅ |
| **P2** | **后端签名 URL 端点** | `/api/songs/{id}/play-url` | 无 | ✅ |
| **P3** | **播放原生化**（Android） | 后台播放 + 锁屏控制 + 焦点自动恢复 | P1,P2 | ✅ |
| **P4** | 移动场景能力 | 下载/推送/分享/深链/返回键/安全区 | P1 | ✅ |
| **P5** | Android 发布准备 | release **AAB** + 签名 + Play 资料 | C1,C4 | ✅ |
| **P6** | iOS 工程 + CI（macos-26 runner） | `ios/` + 归档跑通 | Apple 账号、macOS | ❌（用 GH Actions） |
| **P7** | iOS 播放原生化 + 提审 | 后台播放 + Now Playing | P6 | ❌ |
| **P8** | 内购合规 | IAP / Play Billing + 票据校验 | 账号资质 | 部分 |

**排序理由**：P1–P5 **全部在 Windows 可完成**，最快拿到真机验证；P6 起才投入 macOS，把稀缺资源放在验证之后。

---

## 8. 立即可执行（不依赖域名）

1. `native/package.json` 依赖升至 `^8.0.0`（core/cli/android/ios）
2. `npx cap add android` → 校验 `variables.gradle`（minSdk 24 / compile&target 36）
3. 装 Android SDK `platforms;android-36`；确认 JDK 21
4. 升级 `preflight.mjs`：Node 22、platform-36、keystore、API base 探活
5. 后端设计签名 URL 端点（P2）

---

## 9. 未验证 / 需核实（不臆断）

- `@capgo/native-audio` 的**确切许可证名称**（npm 页面仅显示 license 徽章，未取到明文）—— 安装前须确认
- Capacitor 8 模板默认的 `androidScheme`（v6 起为 `https`）对现有 cookie 鉴权的影响 —— 与 O-3 联动验证
- 本机 JDK 17 与 AGP 8.13 / Gradle 8.14.3 的实际兼容性（未实测构建）
- `frontend/out` 的 `index.html` 未确认含 `viewport-fit=cover`（`app/layout.tsx` 未见，Next 16 可能自动注入）
- Apple / Google 的**当前费用与分成比例**、国内备案**最新口径**（政策会变，以官方当期页面为准）
- `frontend/components/CreateForm.tsx`（41 KB）在移动窄屏下的可用性 —— 未做真机/响应式验证

---

## 10. 待你决策

1. **首发市场**：仅海外，还是含中国大陆？（决定 C4 备案工作量，差一个数量级）
2. **是否接受升级 Capacitor 8**？（不升则两条线都不能上架）
3. **iOS 构建环境**：GitHub Actions `macos-26` runner（推荐，按分钟计费）/ 云构建 / 购 Mac？
4. **订阅是否改走 IAP**？（决定定价与分成模型）
5. **是否补 PWA** 作为第三渠道（同时化解 4.2 套壳风险）？
6. **P0 域名**：是否已有或批准采购？
