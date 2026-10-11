# FLOW CCM 多端打包规划（Android APK + iOS App）

> **性质**：本文为**纯规划**，不含任何代码改动。所有结论均附 `file:line` 实证。
> 撰写 2026-09-28。

---

## 0. 结论先行

| 判定项 | 结论 |
|---|---|
| 技术可行性（Android） | ✅ **高**。静态导出链路已建成，后端 CORS 已为 Capacitor 预留 |
| 技术可行性（iOS） | ⚠️ **中**。无 macOS、无 `ios` 平台、无签名体系，三项缺口需新建 |
| 最大风险 | 🔴 **不是技术，是合规**：应用内购买（IAP/Play Billing）、国内备案、UGC 审核 |
| 前置依赖 | 🔴 **与 FLOW APK 同一个 P0：缺少公开 HTTPS 域名**（见 §6） |

**推荐路线**：Android 与 iOS 各自**独立建工程**（不并入 FLOW），复用 FLOW 的**流程经验**（variant / 签名 / CI 门禁）而非代码。

---

## 1. 现状实证

### 1.1 已具备的资产（利好）

| 资产 | 证据 |
|---|---|
| 静态导出开关 | `frontend/next.config.js` — `APK_BUILD=1` → `output:"export"` + `images.unoptimized` + `trailingSlash:true` |
| 专用打包脚本 | `frontend/scripts/build-apk.mjs`（265 行）：admin 隔离 / CSP `<meta http-equiv>` 注入 / loopback 拦截 |
| 后端已预留 Capacitor origin | `backend/app/config.py:38` 默认含 `capacitor://localhost,http://localhost,https://localhost` |
| 前端"导出友好" | 无 `route.ts`、无 middleware、无 `cookies()/headers()`、无 `[动态路由]`；23 个 tsx 中 **20 个是 `"use client"`** |
| CSP 单源 | `frontend/security-headers.js:36` `connect-src 'self' ` + apiBase |

> 这四项合起来意味着：**`output:'export'` 几乎不会因为服务端特性而失败**。
> 这是本次规划最重要的正面发现——多数 Next.js 应用卡在这里，本项目不会。

### 1.2 关键缺口

| 缺口 | 证据 |
|---|---|
| 未安装 Capacitor | `frontend/package.json` 依赖仅 next/react/react-dom/qrcode.react，**无 `@capacitor/*`** |
| 无 iOS 平台 | FLOW 侧 `package.json` 只有 `@capacitor/android ^6.1.2`，**无 `@capacitor/ios`**；无 `ios/` 目录 |
| 无 macOS 构建能力 | `.github/workflows/` 全部作业无 `macos`/`xcode` |
| 无应用内购买 | `backend/app/billing/pg/` 仅 alipay / kakao / naver / paypal / wechat，**无 Apple IAP、无 Google Play Billing** |
| UGC 无端上治理 | 存在 `backend/app/routers/plaza.py`（广场） |
| 既有决策是"iOS 不做" | `docs/MULTI-PLATFORM-PLAN.md:47`「iOS 本期不做」；`:234` 记 iOS 需 `AVPlayer` + `MPNowPlayingInfoCenter` |

---

## 2. 路线选择：独立 App vs 并入 FLOW

| 维度 | A. 独立 App（推荐） | B. 并入 FLOW 音乐 tab |
|---|---|---|
| 前端栈 | Next.js 静态导出 | FLOW 自研 `mobile/`（不同栈） |
| 合并成本 | 无 | 高（两套渲染/路由/状态） |
| 品牌 | FLOW CCM 独立品牌 | 与 FLOW 共用 `com.gospelai.app` |
| 发布节奏 | 音乐独立迭代 | 与 FLOW 耦合 |
| 商店合规 | 各自承担 | 一处违规拖累全部 |
| 与既有规划一致性 | 规划书 §14 明写「APK 由本人开发（**独立工程，不在本仓库**）」 | 与 §14 冲突 |

**选 A**。理由：技术栈不同使合并成本远高于收益，且规划书 §14 已把 APK 定位为独立工程。

> ⚠️ 需你确认一点：FLOW App 内已有 music tab（찬송/광장）。独立 App 与它**功能重叠**，
> 建议明确「CCM App = 生成+社区」「FLOW App music tab = 聆听」，否则两端会互相蚕食。

---

## 3. Android 方案

### 3.1 结构

```
ai-music-platform/
└── frontend/
    ├── next.config.js        (已有 APK_BUILD 开关)
    ├── scripts/build-apk.mjs (已有 → 产出 out/)
    ├── capacitor.config.json (新建: webDir="out", appId=com.flowccm.app)
    └── android/              (npx cap add android)
```

### 3.2 关键约束（来自既有脚本，不可省）

1. **`--api` 必须是公网 HTTPS**。脚本明确拦截 loopback：`127.0.0.1` 在真机上指"设备自身"，必然连不通。
2. **`/admin` 必须隔离**。`build-apk.mjs` 注释记录实测：仅删 `out/admin/` **不够**，`_next/static/chunks/app/admin/*.js` 仍残留 → 必须在**编译前**把 `app/admin` 移出。
3. **CSP 靠 meta 注入**（静态导出下 `headers()` 失效）。

### 3.3 可复用 FLOW 的经验（非代码）

- 三 variant（default/cn/play）+ `build-config.js` 的多市场配置模式
- CI 里的"生产 API base GATE"（防止静默产出指向无效域名的签名包）
- 签名 keystore 管理流程

---

## 4. iOS 方案（缺口最大）

### 4.1 必须新建的三件事

| # | 事项 | 说明 |
|---|---|---|
| 1 | `@capacitor/ios` + `npx cap add ios` | 生成 `ios/App`，需 **macOS + Xcode** |
| 2 | 签名体系 | Apple Developer 账号、证书、Provisioning、App Store Connect 条目 |
| 3 | 构建环境 | 本机是 **Windows** → 需 macOS runner / 云构建 / 实体 Mac |

### 4.2 iOS 特有的技术点

- **后台播放**：WKWebView 内的 `<audio>` **在后台会被挂起**。音乐 App 若要锁屏续播，
  必须走原生 `AVPlayer` + `MPNowPlayingInfoCenter`（`MULTI-PLATFORM-PLAN.md:234` 已前瞻到这点）。
  这是 iOS 端**最大的一块原生工作量**，不是 Capacitor 配置能解决的。
- **ATS**：iOS 默认要求 HTTPS，`--api` 必须 https（与 P0 域名要求同向）。
- **Capacitor origin**：iOS WebView 源是 `capacitor://localhost`——后端 CORS 已包含（§1.1），此项无需改。

---

## 5. 合规阻塞（🔴 优先级高于技术）

| # | 事项 | 影响 | 现状 |
|---|---|---|---|
| C1 | **应用内购买** | 数字内容/订阅在 App 内销售，通常必须走 Apple IAP / Google Play Billing；现有 5 个 PG 适配器**都不合规** | 缺 |
| C2 | **中国大陆备案** | 在国内 iOS/Android 商店分发通常需要 ICP 备案（Android 商店还需软件著作权） | 未确认 |
| C3 | **UGC 治理** | 有广场（plaza）即需：内容过滤、举报、屏蔽用户、开发者联系方式 | 端上缺 |
| C4 | **"套壳"风险** | 纯 WebView 包装可能被以"功能不足"为由拒审；需真实原生能力（后台播放/下载/分享） | 部分满足 |
| C5 | **订阅价格与分成** | 商店抽成影响定价模型（与现有 Web 订阅体系需对齐） | 未评估 |

> ⚠️ C1–C5 我按**通行规则**列出，具体条款与费用请以 Apple / Google 官方当前页面为准（本文未实时核验官方报价，见 §8）。

---

## 6. 前置依赖：与 FLOW 共享的 P0

`build-apk.mjs` 的调用形如 `node scripts/build-apk.mjs --api=https://...`，
**没有公网 HTTPS 域名就无法产出任何可安装的包**。

而域名现状（2026-09-28 实测）：

- `gospelai.com` **未持有**——仅是 `docs/MULTI-PLATFORM-PLAN.md:67,122` 的"以后再买"
- `deploy/hk/bootstrap.sh:89-90` — 缺 `MUSIC_DOMAIN` 直接 die
- Cloudflare 快隧道主机名每次重启都变，**不能用于发布包**

**因此：域名是 Android 与 iOS 两条线的共同前置。** 本次只规划，实际开工须先解此项。

---

## 7. 分阶段路线图

| 阶段 | 目标 | 产出 | 阻塞 |
|---|---|---|---|
| **P0** | 公网 HTTPS 域名 + 后端可达 | 可访问的 API 端点 | 🔴 需域名 |
| **P1** | Android 打通（内部包） | `out/` → Capacitor → debug APK 真机可跑 | P0 |
| **P2** | 后台播放（Android 原生） | 锁屏/通知栏控制 | P1 |
| **P3** | Android 发布准备 | release AAB + 签名 + Play 商店资料 | C1, C2 |
| **P4** | iOS 工程 + 构建环境 | `ios/` + macOS 构建跑通 | 需 macOS |
| **P5** | iOS 后台播放 + 提审 | AVPlayer + MPNowPlayingInfoCenter + 提审 | C1–C4 |
| **P6** | 购买合规 | IAP / Play Billing 适配器 | 账号资质 |

**建议顺序理由**：P1–P3 全在 Windows 上可完成，能最快拿到真机验证；
P4 起才需要 macOS 投入，把昂贵资源放在验证之后。

---

## 8. 未验证 / 需核实（不臆断）

- Apple Developer 与 Google Play 的**当前费用与分成比例**（未实时核验官方页面）
- 中国大陆 App 分发的**最新备案要求**（政策可能变动）
- Capacitor 6 对 iOS 的最低版本与 Xcode 版本要求（以官方为准）
- Next.js 16 的 `output:'export'` 与静态托管细节（本项目配置已就绪，但**尚未实跑过一次完整导出**）
- 现有 `build-apk.mjs` 是否已在真机验证过（脚本成熟度高，但未见验证记录）

---

## 9. 待你决策

1. **独立 App 还是并入 FLOW music tab**？（§2，我建议独立）
2. **首发市场**：仅海外（Google Play / App Store 海外）还是含中国大陆？（决定 C2 工作量）
3. **订阅是否改走 IAP/Play Billing**？（决定 C1 与定价模型）
4. **iOS 构建环境**：GitHub Actions macOS runner / 云构建服务 / 购置 Mac？
5. **P0 域名**：是否已有、或批准采购？（此项不解，P1 起全部无法开始）
