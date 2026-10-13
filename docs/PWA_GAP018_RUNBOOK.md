# GAP-018 Web PWA（可安装 + 离线）实施说明

> 落地分支：`auto/gap018-pwa-*`　base = `main@d003c81`　相关 issue：#86
> 关联文档：`docs/PLAN_MOBILE_CROSSPLATFORM_2026-09-29.md`（O-9）、`docs/PLAN_MUSIC_APK_2026-09-13.md`

## 1. 一句话

给 Web 端补上**第三条交付渠道**（PWA），与既有 **Capacitor/APK** 渠道**并存**：
新增 manifest + 手写 Service Worker + A2HS 安装提示 + 离线提示，
**不改动任何后端、模型、迁移**，且 **`APK_BUILD=1` 静态导出构建照旧成功**（硬验收）。

## 2. 为什么手写 SW，而不是 `next-pwa`

| 维度 | `next-pwa@5.6.0` | 本方案（手写） |
|---|---|---|
| 最后发布 | **2022-08**（npm 实测） | — |
| 依赖 | `babel-loader@8`、`workbox-webpack-plugin@6`、`terser-webpack-plugin@5` | **零新增依赖** |
| 与 Next 16(Turbopack) | 需 webpack，Turbopack 下未验证 | 不参与构建，产物即静态文件 |
| 与 `output:'export'` 叠加 | **构建期注入**，是「不得破坏 APK」硬验收的最大风险面 | 不触碰构建，APK 分支天然不受影响 |
| 缓存策略可读性 | Workbox `runtimeCaching` 配置，间接 | ~250 行，上限/过期/更新策略逐条可读 |

**结论**：本项目缓存诉求很窄（应用壳 + 「听过即缓存」音频），
为此引入一套 2022 年的 webpack 插件链、并把风险加到 APK 构建路径上**不划算**。
故选**手写 SW**（`frontend/public/sw.js`），零 npm 依赖、行为可审计。

## 3. 交付物

| 文件 | 作用 |
|---|---|
| `frontend/public/manifest.webmanifest` | 名称/图标/`display:standalone`/**`theme_color`(唯一定义处)** |
| `frontend/public/sw.js` | Service Worker：离线壳 + 音频缓存 + 离线兜底 |
| `frontend/public/icons/*` | 192/512/maskable-512/apple-touch-icon/favicon-32（含 SVG 源） |
| `frontend/components/PwaSetup.tsx` | SW 注册 + 更新接管 + A2HS + 离线提示（全部 feature-detect） |
| `frontend/app/layout.tsx` | **仅 meta 注入**（manifest / apple-touch-icon / appleWebApp / viewport） |

## 4. 缓存策略（硬性约定，勿随意放宽）

| 资源 | 策略 | 上限 | 过期 | 更新 |
|---|---|---|---|---|
| 应用壳（导航） | NetworkFirst，超时 `3s` → 预缓存壳 | 仅根壳 1 份 | 随 SW 版本 | 新 SW `skipWaiting` + `controllerchange` → reload 1 次 |
| 静态资产 `/_next/static/`、`/icons/` | CacheFirst | 不设上限（哈希文件名） | 构建换名即失效 | 同上 |
| **音频**（`destination==='audio'` 或扩展名白名单） | CacheFirst | **`FLW_AUDIO_MAX_ENTRIES = 64`** | FIFO 逐出（超限即过期） | 同上 |
| `/sw.js`、`/manifest.webmanifest` | 纯网络（不缓存） | — | — | 保证浏览器及时拿到新版本 |
| 其它（API/统计/图片） | 不介入 | — | — | 避免缓存鉴权数据 |

音频写入**三重守卫**（`isCacheableAudioResponse`）：
1. 仅 `status===200` → **206/Range 半截文件绝不落盘**（否则离线播放中途断）
2. 仅 `type!=='opaque'` → 跨域不透明响应无法判体积/正确性
3. `cache-control: no-store` 不落盘

离线时音频**未命中缓存** → 返回 `503` + `X-Flow-Offline: 1` + JSON，页面据此提示，**不静默失败**。

## 5. 与 Capacitor/APK 共存（硬约束）

- **构建**：PWA 资产全部位于 `frontend/public/`，Next 原样拷贝到 `out/`。
  未改 `next.config.js` 的任何 APK 分支逻辑（`output`/`images`/`trailingSlash`/`turbopack` 一字未动）。
- **运行时**：`PwaSetup` 先 feature-detect，且显式跳过 Capacitor：
  ```ts
  if (!("serviceWorker" in navigator)) return;
  if (window.Capacitor) return;   // APK WebView 不注册 SW
  ```
  注册失败仅 `console.warn`，**绝不打断应用**。
- `manifest`/`apple-touch-icon` 等 meta 对 Capacitor WebView 无害（多余但合法）。

## 6. 自验（实测证据）

```bash
# ① APK 静态导出（硬验收）
cd frontend && FRONTEND_DIR=$PWD node scripts/run-export.mjs      # → BUILD_OK, out/ 产出
# ② Web 构建
cd frontend && node node_modules/next/dist/bin/next build          # → 成功
# ③ 前端类型检查（Next 16 已移除 next lint，改以 tsc 为准）
cd frontend && npx tsc --noEmit -p tsconfig.json                   # → rc=0
# ④ PWA 资产自检（manifest 可解析 / SW 语法 / meta 注入 / APK 分支未被破坏）
node frontend/scripts/verify-pwa.mjs
```

真实浏览器（Chrome 153 headless + CDP）实测结论：

- SW 注册成功且 `controller` 生效；`caches` = `flw-core-v1`/`flw-static-v1`/`flw-audio-v1`
- **真离线**（停掉静态服务器）：
  - 已缓存音频 → `200`（200 字节级一致）
  - 未缓存音频 → `503` + `X-Flow-Offline: 1` + JSON
  - Range 未缓存 → `503`
  - 深层路由 `/library/`、`/plaza/` 导航 → 命中预缓存壳，正常渲染
  - 离线横幅「当前处于离线状态 · 仅可播放已缓存的歌曲」出现
- **LRU 上限**（把上限临时降到 5 验证）：抓 12 首 → 仅保留最新 5 首（`trim8..12`），旧条目被逐出
- **更新路径**（`SW_VERSION` v1→v2）：旧 `flw-*-v1` 桶全部 GC，新建 `flw-*-v2`
- 安装提示：`beforeinstallprompt` 存在时展示；iOS Safari 走「分享→添加到主屏幕」文案；「稍后」写入 24h snooze

> ⚠️ 测试期间为验证上限曾临时改 `FLW_AUDIO_MAX_ENTRIES`/`SW_VERSION`，**已还原为 64/v1**，
> 最终提交与基线 `diff` 仅差一行注释，无测试残留。

## 7. 已知边界（不在本任务范围）

- `viewport-fit=cover` 已加，但**未加 `env(safe-area-inset-*)` 内边距** —— 属 #93/原生任务，避免冲突。
- `theme_color` 由本 manifest 定义（`#0f0f0f`）；`layout.tsx` 的 `themeColor` 与之字面一致，
  单一事实来源仍是 manifest（并行任务 #93 只核对一致性）。
- 未引入 Web Push（属 GAP-021）。
- `next lint` 在 Next 16 已移除，CI 若要 lint 需另配 ESLint（本任务以 `tsc` 为准）。
