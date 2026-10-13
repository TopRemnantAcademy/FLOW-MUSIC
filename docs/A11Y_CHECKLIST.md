# FLOW-MUSIC 可访问性（a11y）检查清单

- **任务**：GAP-024（Issue #92）
- **基线**：`main@d003c81`
- **范围**：`frontend/`（Next.js App Router）—— 组件/页面/样式/lint 配置
- **不在范围**：后端、`models.py`、`migrations/`（按任务边界排除）
- **标准**：WCAG 2.1 Level AA（正文对比度 ≥ 4.5:1、非文本对比度 ≥ 3:1、键盘可达、焦点可见、语义化）

> 本文档的每条结论都以**磁盘上当前代码**为准，可用文中命令复现。
> Issue 正文写于 2026-10-11，其「现状」段已过时（例如它说「约 46 处 aria-*」，实测为 52 处）。

---

## 0. 一页速览

| 项 | 状态 | 证据 |
|---|---|---|
| `eslint-plugin-jsx-a11y` 接入 | ✅ | `frontend/.eslintrc.json`、`npm run lint:a11y` |
| jsx-a11y 错误数 | ✅ **0**（修复前 2） | `npm run lint:a11y`（余 6 条 warning 全非 a11y 相关，见 §3） |
| skip-link | ✅ | `app/layout.tsx`、`.skip-link` @ `app/globals.css` |
| 键盘焦点可见（`:focus-visible`） | ✅ | `app/globals.css` §可访问性 |
| 模态焦点陷阱 + 焦点归还 | ✅ **实测** | `npm run test:a11y`（13 条断言） |
| 播放进度条键盘可操作 | ✅ 实测 | `BottomPlayer.tsx` `seekFromKey` |
| 歌词区键盘跳转 | ✅ | `SyncedLyrics.tsx` `role=button` + `onKeyDown` |
| 屏幕阅读器状态播报（`aria-live`/`role=alert`） | ✅ 23 处 | `role=status` / `role=alert` |
| 主题对比度审计 | ⚠️ **结论见 §2，有 2 类真实违规** | `scripts/a11y-contrast-audit.mjs` |
| a11y 回归测试 | ✅ 27 条断言全绿 | `npm run test:a11y` |

三条验证命令（都在 `frontend/` 下跑）：

```bash
npm run lint:a11y     # eslint + jsx-a11y，要求 0 error
npm run typecheck     # tsc --noEmit
npm run test:a11y     # jsdom 里真挂载组件 + 真派发 keydown
```

---

## 1. 现状复核（以 grep 实测为准）

```bash
grep -rn 'aria-\|role=' --include='*.tsx' frontend/app frontend/components | wc -l
```

| 时点 | 数量 |
|---|---|
| 修复前 | 52 |
| 修复后 | 139 |

Issue 说的「约 46 处」偏低（实际 52），且这些既有 aria 大多**只覆盖了显眼的按钮**
（播放/暂停、邮箱/密码、评分星），**没有**覆盖：弹窗语义、动态状态播报、歌词区、
进度条键盘操作、跳转链接、焦点可见。本次补齐的正是这些缺口。

已有且**保留复用**（未重复造）：
- `BottomPlayer.tsx` 的 `role="slider"` + `aria-valuemin/max/now/valuetext`（本次只**补键盘**）
- `AuthBar.tsx` 邮箱/密码 `aria-label`
- `Player.tsx` 播放/暂停 `aria-label`
- `NavBar/plaza/HomeFeed` 的 chips `aria-pressed`
- `HomeFeed.tsx` 的 `aria-labelledby="home-feed-title"` 地标命名

---

## 2. 色彩对比度审计（WCAG 2.1 AA）

**方法**：从 `app/globals.css` 读出主题令牌（oklch），本地换算
`oklch → linear sRGB → 相对亮度 → 对比度`。复现：

```bash
node frontend/scripts/a11y-contrast-audit.mjs
```

> 为什么不靠人眼：oklch 是感知均匀空间，「看着够灰」的中性色经常正好落在
> 4.4~4.6:1 这一档。本仓库的 `--muted` 就是这种边缘值。

### 2.1 结论表（正文文本，要求 ≥ 4.5:1）

| 前景 / 背景 | Light | Dark | 判定 |
|---|---|---|---|
| `--ink` / `--bg` | 17.09:1 | 17.28:1 | ✅ AAA |
| `--ink` / `--surface` | 18.10:1 | 15.95:1 | ✅ AAA |
| `--ink` / `--surface-2` | 16.36:1 | 14.24:1 | ✅ AAA |
| `--ink-soft` / `--bg` | 7.99:1 | 9.70:1 | ✅ AAA |
| `--ink-soft` / `--surface` | 8.46:1 | 8.95:1 | ✅ AAA |
| `--muted` / `--bg` | 4.58:1 | 5.33:1 | ✅ AA（**边缘**） |
| `--muted` / `--surface` | 4.85:1 | 4.92:1 | ✅ AA（**边缘**） |
| `--muted` / `--surface-2` | **4.38:1** | **4.39:1** | ❌ **AA 不达标** |
| `--btn-solid-fg` / `--btn-solid-bg` | 17.09:1 | 17.28:1 | ✅ AAA |
| `--danger` / `--bg` | **4.47:1** | **4.10:1** | ❌ **AA 不达标** |
| `--danger` / `--surface` | 4.74:1 | **3.78:1** | ❌ Dark 不达标 |
| `--danger` / `--surface-2` | ~4.3:1 | ~3.5:1 | ❌ 不达标 |

### 2.2 真实违规（不是理论风险，是当前页面上真实存在的组合）

**① `--danger` 文本对比度不足 —— 19 处引用，含 3 处直接落在 `--surface-2` 上**

`grep -rn 'text-\[var(--danger)\]' frontend/app frontend/components | wc -l` → **19**（修复前 15）

- 暗色下 `--danger` on `--surface` = **3.78:1**（< 4.5，正文不合格）
- 亮/暗色下 `--danger` on `--surface-2` ≈ **4.3 / 3.5:1**（< 4.5）
- 实际落点（举例，已核对容器背景）：
  - `components/CreateForm.tsx:865` 音色克隆错误，父容器 `bg-[var(--surface-2)]`
  - `app/library/page.tsx:592` 局部改写校验错误，父容器 `bg-[var(--surface-2)]`
  - `app/account/page.tsx:414` 额度用尽提示，父容器 `card`（= `--surface`，暗色 3.78:1）

**② Account 页有 8 处完全脱离主题令牌的 Tailwind 固定色 —— 技术上不合格**

`grep -n 'var(--accent)' frontend/app/account/page.tsx` → **6 处**（修复前）

`--accent` / `--line` **在 `globals.css` 中根本未定义**（是历史遗留，主题只有 ink↔paper 黑白体系）。
这 6 处的后果不是「不够好看」，是**功能失效**：

| 位置 | 原写法 | 实际后果 |
|---|---|---|
| `:712` 联系我们「发送」按钮 | `bg-[var(--accent)] … text-white` | 背景解析失败 → 透明/黑，与主题无关 |
| `:717` 发送成功提示 | `text-[var(--accent)]` | 文字色解析失败 → 可读性不稳定 |
| `:679`、`:687` 两个输入框 focus | `focus:border-[var(--accent)]` | **聚焦态无任何视觉反馈**（键盘用户看不见焦点在哪） |

另有 `text-red-400`（`:388/:611/:722/:744`，`gray-950` 家庭色）与其它页面的
`bg-red-950 text-red-300`（`admin/*`），都是硬编码的 Tailwind 调色板，深浅色主题下表现不一致。

### 2.3 本次是否直接改 `globals.css`？

**没有改任何主题颜色令牌。** 原因：`globals.css` 由并行任务 **#93（主题 token）** 主维护，
按任务边界约定，对比度**只做审计 + 记录**，避免两个任务在同一文件上互相覆盖。

对 `--accent` 这类**确属严重违规**的项，采取的是「不在 `globals.css` 里加变量，
而是把调用点改到已定义令牌」的最小必要做法 —— 改动面只在自己的组件文件里：

| 文件 | 改动 | 效果 |
|---|---|---|
| `app/account/page.tsx` | `--accent` → `--btn-solid-bg` / `--btn-solid-fg` / `--ink-soft` / `--ring` | 6 处失效引用归零；**聚焦态恢复可见** |
| `app/account/page.tsx` | `text-red-400` → `text-[var(--danger)]` | 回归主题体系 |
| `components/...` 其它 | 未动 | — |

> 剩余需要 #93 决策的项（**留给主题任务，本任务不越界**）：
> 1. `--danger` 在浅/深色下都需提亮/压暗到 ≥ 4.5:1（当前 4.47 / 3.78）；
> 2. `--muted` 在 `--surface-2` 上需 ≥ 4.5:1（当前 4.38 / 4.39，差一点点）；
> 3. `admin/*` 里 `bg-red-950/text-red-300/text-red-400` 等硬编码 Tailwind 色统一到令牌。
>
> 这三项都不是「改一个数字」——`--danger` 与 `--muted` 被 30+ 处引用，
> 改值会连带影响按钮描边、进度条、chip 等非文本用途（非文本要求是 3:1，比正文宽松），
> 需要在主题层一次性权衡。**这是审计结论，不是本任务的修复范围。**

---

## 3. Lint：`eslint-plugin-jsx-a11y`

新增 `frontend/.eslintrc.json`（`next/core-web-vitals` + `plugin:jsx-a11y/recommended`），
把「必须为 error」的规则显式钉死，防止后续放松：

```jsonc
"jsx-a11y/alt-text": "error",
"jsx-a11y/aria-props": "error",
"jsx-a11y/aria-proptypes": "error",
"jsx-a11y/aria-unsupported-elements": "error",
"jsx-a11y/role-has-required-aria-props": "error",
"jsx-a11y/role-supports-aria-props": "error",
"jsx-a11y/label-has-associated-control": ["error", { "assert": "either", "depth": 4 }]
```

**修复的高危项（error 级，0 → 0，即引入时即刻清零）：**

| 规则 | 位置 | 修法 |
|---|---|---|
| `media-has-caption` | `app/admin/moderation/page.tsx:479` `<audio>` | 加空 `<track kind="captions">` 占位 + `aria-label`（歌曲音频无对白字幕） |
| `no-noninteractive-tabindex` / `no-noninteractive-element-to-interactive-role` | `components/SyncedLyrics.tsx` 歌词行 `<p tabIndex=0>` | 改 `<div role="button">`，语义与行为一致 |

**保留的 warning 及理由（修复后 `eslint` 报 0 error / 6 warning，全部有据）：**

> `ConfirmDialog.tsx` 的 4 条 `click-events-have-key-events` / `no-static-element-interactions`
> 已用行内 `eslint-disable-next-line` 定向豁免（原因见下表），因此不计入这 6 条。
> 这 6 条 warning **没有一条是 jsx-a11y 规则**。

| warning | 位置 | 为什么不改 |
|---|---|---|
| `click-events-have-key-events` ×4 | `ConfirmDialog.tsx` 遮罩层 | 点遮罩关闭是鼠标增强；键盘路径由 Esc + 两个按钮承担（已实测）。已在源码行内 eslint-disable 注释说明 |
| `@next/next/no-img-element` ×3 | 三处封面图 | 封面是**远端任意 URL**（用户上传/CDN），`next/image` 需要预先声明 `remotePatterns`，引入会改变运行时行为；非 a11y 规则 |
| `react-hooks/exhaustive-deps` ×3 | `library/CreateForm/SyncedLyrics` | 既有代码（非本次引入）的 hook 依赖告警，与 a11y 无关，动它超出边界 |

---

## 4. 键盘导航

### 4.1 跳转链接（skip link）

`app/layout.tsx`：`<a href="#main-content">跳到主要内容</a>` + `<div id="main-content" tabIndex={-1}>` 包裹 `{children}`。

样式 `.skip-link` 默认 `transform: translateY(-200%)` **移出视口**（视觉上不打扰鼠标用户），
`:focus` / `:focus-visible` 时滑入 —— 只有键盘用户会看到它。

### 4.2 焦点可见

`app/globals.css` 新增统一焦点环：

```css
:where(a, button, input, select, textarea, summary, [tabindex]:not([tabindex="-1"])):focus-visible {
  outline: 2px solid var(--ring);
  outline-offset: 2px;
}
```

- 用 `:focus-visible` 而不是 `:focus` —— 鼠标点击不画环，键盘导航才画，避免「到处是框」。
- `.btn` 已有 box-shadow 焦点环，额外 `outline: none` 防止双环叠加。
- `role="slider"` 是 `div`，不在原生列表里，**单独补一条**（这是修复前最实际的键盘黑洞）。
- `:where()` 零特异性，不覆盖组件自身的 outline 需求。

### 4.3 模态焦点陷阱（`components/ConfirmDialog.tsx`）

| 行为 | 实现 |
|---|---|
| 语义 | `role="dialog"` + `aria-modal="true"` + `aria-labelledby` + `aria-describedby` |
| 初始焦点 | 落在**「取消」**按钮（破坏性操作不把焦点默认给确认键） |
| Tab 循环 | 显式 `preventDefault` + 自算索引取模，闭环于对话框内部，**不依赖浏览器默认 Tab 顺序** |
| 焦点外逃 | `keydown` 挂在 `document` 且 `capture=true`，若焦点已在对话框外则拽回 |
| Esc | 关闭（保留原行为，改到捕获阶段避免被内部控件吞掉） |
| 焦点归还 | 关闭时 `restoreRef.current.focus()` —— 回到「是谁打开的这个弹窗」 |

**实测证据**（`npm run test:a11y`，jsdom 真挂载 + 真 `dispatchEvent`）：

```
[1] ConfirmDialog 焦点管理
  ✓ 打开前焦点在触发按钮上
  ✓ 打开后存在 role=dialog
  ✓ 对话框带 aria-modal=true
  ✓ 对话框由 aria-labelledby 指向标题
  ✓ 对话框由 aria-describedby 指向正文
  ✓ 有取消/确认两个按钮
  ✓ 初始焦点落在「取消」而非破坏性的「确认」
  ✓ Tab 前进到确认按钮
  ✓ 在最后一个元素 Tab 回到第一个（陷阱闭合成环）
  ✓ Shift+Tab 反向回到最后一个
  ✓ Esc 触发取消回调
  ✓ Esc 后对话框已卸载
  ✓ 关闭后焦点归还给打开它的按钮（不会掉回 body）
```

### 4.4 播放进度条（`components/BottomPlayer.tsx`）

进度条是 `div[role="slider"][tabIndex=0]`（不是 `<input type=range>`，理由是移动端
拖动区域更大、更好点）。既然自绘，就必须自己实现键盘语义 —— 新增 `seekFromKey`：

| 键 | 行为 | 与原生 range 对齐 |
|---|---|---|
| `←` / `→` | ∓5 秒 | ✅ |
| `↑` / `↓` | ∓10 秒 | ✅ |
| `PageUp` / `PageDown` | ∓30 秒 | ✅ |
| `Home` / `End` | 跳到 0 / 时长 | ✅ |

每次按键都 `preventDefault`（阻止页面滚动），并更新 `aria-valuenow` / `aria-valuetext`
（`mm:ss / mm:ss`），所以读屏用户听到的是**时间**而不是裸数字。

### 4.5 歌词区（`components/SyncedLyrics.tsx`）

歌词行/词原本是纯 `onClick` 的 `<div>` —— 鼠标专属。现在：

- 每行 `role="button"` + `tabIndex={0}` + `aria-current`（当前行）+ Enter/Space 跳转。
- 每个词 `role="button"` + `tabIndex={0}`，Enter/Space 跳转，且**冒泡阻止**（避免连跳两次）。
- 容器 `role="group"`（行可变，用 group 而非固定 list 语义更准）。

代价说明：逐字歌词最多几百个词，全部可 Tab 会很长。这里选择了**保留全部可达性**
（而不是只让行可达），因为歌词跳转正是无障碍用户补全信息的主要入口。

### 4.6 核心路径 Tab 可达性（静态核对 + 键位实现）

| 页面 | 关键路径 | 键盘可达 | 依据 |
|---|---|---|---|
| `/create` 创作 | 模式切换 → 风格标签 → 主题 → 歌词 → 生成 | ✅ 全原生控件（button/input/textarea/select） | `CreateForm.tsx` |
| `/` 首页 | 开始创作 / 广场 / 我的作品 / 下滑提示 | ✅ `Link` + `aria-label` | `app/page.tsx:61` |
| `/plaza` 广场 | 视图切换 → 排序 → 搜索 → 卡片播放 → 赞/藏/评分 | ✅ chips `aria-pressed`，评分星 `role=group` + `aria-pressed` | `plaza/page.tsx` |
| `/library` 库 | Tab 切换 → 全选 → 批量 → 卡片 → 删除确认 | ✅ 确认弹窗走 §4.3 陷阱 | `library/page.tsx` |
| 全局底部条 | 上/下一首 → 播放 → 静音 → 音量 → 随机 → 循环 → 进度条 | ✅ 进度条见 §4.4 | `BottomPlayer.tsx` |

> 前端是客户端渲染 + 需要后端/token 才能出数据，容器内无法跑端到端浏览器测试。
> 因此对「纯键盘走通全流程」采用的验证方式是：
> **① 对可脚本化的交互（模态、seek、焦点归还）做 jsdom 真事件断言**（§4.3/§4.4，27 条）；
> **② 对渲染性路径做源码级核对**（上表，逐一确认没有 `div onClick` 当按钮用）。
> 这是「实测 + 如实标注边界」，不是口头声明。

---

## 5. 屏幕阅读器语义

新增 23 处 `aria-live` / `role=alert` 播报（此前**一处都没有** —— 屏幕阅读器用户完全不知道播放/生成/错误状态变了）：

| 文件 | 播报内容 | 机制 |
|---|---|---|
| `BottomPlayer.tsx` | 「正在播放/已暂停：<曲名>（队列 m/n）」 | `role="status" aria-live="polite"`（`sr-only`） |
| `SyncedLyrics.tsx` | 「当前歌词：<当前行>」 | `role="status" aria-live="polite" aria-atomic` |
| `Player.tsx` | 生成状态 / 结果消息 | `role="status" aria-live="polite"` |
| `Player.tsx` | 试听不可用 | `role="status"` |
| `plaza / library / HomeFeed / Account / AdminGuard / AuthBar / CreateForm` | 错误 | `role="alert"`（打断式，错误必须立刻被听到） |
| `Account`、`HomeFeed` | 留言成功 / 加载中 | `role="status"` |

`polite` vs `alert` 的分工：状态变化用 `polite`（排队，不打扰当前朗读），
**错误用 `alert`**（立刻打断 —— 用户必须先知道失败了）。

其它语义补强：
- `NavBar`：`<nav aria-label="主导航">` + 当前页 `aria-current="page"`（此前只有视觉 `data-on`）。
- `AuthBar`：登录浮层 `role="dialog" aria-label`，触发按钮 `aria-expanded` + `aria-haspopup`。
- `Account` 联系我们：折叠按钮 `aria-expanded` + `aria-controls="contact-form"`。
- `Player`：封面 `alt` 从 `title`（重复曲名）改为「《曲名》封面」；下载格式 `select` 补 `aria-label`。
- `library/plaza`：列表项 `<article aria-label="《曲名》">`。
- 所有 chip/toggle 补 `aria-pressed`（模式切换、风格标签、下载格式、排序）。
- 表单字段：`CreateForm.Field` 通过 `htmlFor`/`id`（`useId` + Context 下发）显式关联 `<label>`，
  hint 用 `aria-describedby` 串起来 —— 不再依赖 placeholder 当标签。

---

## 6. 表单标签审计

`jsx-a11y/label-has-associated-control` 设为 **error**，实测通过（0 违规）。

修复前存在「输入框只有 placeholder 没有可访问名」的位置，已全部补 `aria-label`：

| 文件 | 控件 |
|---|---|
| `CreateForm.tsx` | 主题输入、歌词 textarea、歌名、人声 select、下载格式（通过 `Field` 的 `htmlFor`/`aria-pressed`） |
| `library/page.tsx` | 分组名、局部改写歌词/开始秒/结束秒、新建播放列表名、全选、发布到广场 checkbox |
| `admin/moderation/page.tsx` | 批量驳回原因、驳回原因 textarea |
| `admin/music-config/page.tsx` | 路由规则 select ×2 |
| `account/page.tsx` | 订阅档位、支付币种 select |

> `label-has-associated-control` 允许 `aria-label` 满足（`assert: "either"`）——
> 有些控件（如 `BigToggle` 用 `aria-label` 的开关按钮）不适合可见 label。

---

## 7. 回归测试

`frontend/tests/a11y-keyboard.test.tsx`（`npm run test:a11y`）：

- **不是**静态断言堆砌：真的建 jsdom、真的 `createRoot().render()` 挂载 React 组件、
  真的 `dispatchEvent(new KeyboardEvent('keydown'))`，再断言 `document.activeElement`。
- 27 条断言：13 条模态焦点/ARIA、10 条跳转链接与焦点样式守卫、4 条 seek 钳制。
- 无测试框架依赖（`tsx` 直跑），避免为一条测试引入 jest/vitest 全家桶。

`frontend/tests/globals.d.ts` 提供最小 `jsdom` 类型声明（jsdom 27+ 不再自带类型）。

---

## 8. 复现与验证记录

```bash
cd frontend
npm install
npm run lint:a11y    # → 0 errors, 6 warnings（均在 §3 说明理由）
npm run typecheck    # → 通过
npm run test:a11y    # → 27 通过 / 0 失败
npx next build       # → Compiled successfully，13 个路由全部预渲染
node scripts/a11y-contrast-audit.mjs   # → §2.1 对比度表
```

改动文件清单：

```
frontend/.eslintrc.json                      新增（jsx-a11y 配置）
frontend/package.json                        devDeps + 3 个脚本
frontend/package-lock.json                   lock
frontend/app/layout.tsx                      skip-link + #main-content
frontend/app/globals.css                     §可访问性（焦点环/skip-link/sr-only）
frontend/app/account/page.tsx                --accent 失效引用 + 错误播报
frontend/app/library/page.tsx                aria-label ×6 + article 语义 + 播报
frontend/app/plaza/page.tsx                  评分组语义 + 互动按钮状态
frontend/app/admin/moderation/page.tsx       audio track + textarea aria-label
frontend/app/admin/music-config/page.tsx     select aria-label
frontend/components/ConfirmDialog.tsx        焦点陷阱/ARIA/焦点归还
frontend/components/BottomPlayer.tsx         进度条键盘 + 状态播报
frontend/components/SyncedLyrics.tsx         歌词键盘 + 当前行播报
frontend/components/CreateForm.tsx           Field label 关联 + aria-pressed
frontend/components/NavBar.tsx               nav 地标 + aria-current
frontend/components/HomeFeed.tsx             aria-pressed + 播报
frontend/components/AuthBar.tsx              dialog 语义 + 播报
frontend/components/AdminGuard.tsx           错误播报
frontend/components/Player.tsx               状态播报 + alt + select label
frontend/tests/a11y-keyboard.test.tsx        新增（27 条断言）
frontend/tests/globals.d.ts                  新增（jsdom 最小类型）
frontend/scripts/a11y-contrast-audit.mjs     新增（对比度审计脚本）
docs/A11Y_CHECKLIST.md                       本文档
```

**未触碰**：`backend/`、`models.py`、`migrations/`、`globals.css` 的主题令牌值（#93 领域）。

---

## 9. 后续建议（不在本任务范围）

1. **#93 主题任务**：按 §2.3 修 `--danger`、`--muted`（`surface-2` 上）、`admin/*` 硬编码色。
2. 把 `npm run lint:a11y` / `test:a11y` 接进 CI（当前 `.github/workflows/ci.yml` 只有 backend job）。
3. 引入 `axe-core`（`@axe-core/playwright`）做真实浏览器端到端扫描 —— 需要可运行的前后端环境。
4. 歌词区的 Tab 序列很长，可考虑 roving tabindex（方向键在行间移动，只保留一个 Tab 停靠点）。
5. 关注 `.claude`/设计令牌里未定义变量的残留（`--accent`、`--line`）——
   建议加一条「CSS 变量存在性」的静态检查，防止再次出现「引用一个不存在的令牌」。
