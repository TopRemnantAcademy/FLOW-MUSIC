# vendor/ — 外部参考代码的溯源记录

本目录下的代码**仅供参考/移植**，后端与前端运行时均不直接依赖（后端全仓 grep 无 import）。
它同时承担 **MIT 版权声明的留存义务**：许可证要求「复制本软件的副本时，须在副本中保留版权声明」，
因此整包入库（而非只留 URL）是合规做法。

| 目录 | 来源 | 许可证 | 本次入库了什么 | 状态 |
|---|---|---|---|---|
| `ace-step-1.5/` | CNB `chguanw/ace-step-1.5` | MIT © 2026 ACEStudio<br>（捆绑 `acestep/third_parts/nano-vllm` = MIT © 2025 Xingkai Yu） | 736 个文件全量 | ✅ 已入库，作为 `ace_step_private` provider 的协议基线 |
| `self-music/` | CNB `onmicrosoft/Self-Music` | MIT © 2018-present, Yuxi(Evian) You and Vve | 全量（未改动） | ✅ 已入库，但**只移植了 1 个文件**，其余为未消化的候选组件 |

## self-music：本次移植了什么

`frontend/src/lib/playlist-manager.ts`（348 行）→ 拆成两个文件并入我们自己的前端：

1. `frontend/lib/playlist.ts` —— 纯逻辑状态机（`nextIndex` / `prevIndex` / `shufflePlaylist` /
   `replaceSong` / `createPlaylistState` / `isValidPlaylistState`）。
2. `frontend/lib/playback.ts` —— React 绑定（模块级 store + `useSyncExternalStore`，
   持久化键前缀 `flow-music-`）。

**有意偏离上游的地方**（上游是「静态方法 + 内部直写 localStorage」的隐式全局状态）：

- 副作用上移到 `playback.ts`，逻辑层改纯函数 → 可单测、SSR 安全。
- 删除 `getRecommendedPlaylist` / `initializeDefaultPlaylist`：上游假设后端有 `getHotSongs`
  端点；我们没有这个形状（`api.explore` 已标记 legacy，`plaza` 才是其超集）。
  队列改由**用户点播放时所在页面的列表**锚定（library=我的作品 / plaza=广场榜 / HomeFeed=首页 feeds），
  不凭空造一个用户没见过的队列 —— 三份榜单不同，用同一个全局队列会串台。
- 增加 `replaceSong`：广场点「续写/翻唱/发布」后歌曲对象会被就地更新，需要保住当前位置，
  否则用户点一次续写就被踢回列表第一首。

## 未采纳的候选（备查，勿误删）

- `arsrna/os/React-LRCPlayer` —— `package.json` 写 `"license": "GNU"`（copyleft），
  按本仓库既定判据整类排除，仅留在检索记录里，**未下载、未入库**。
- `vendor/self-music/` 里其余组件（`bottom-player.tsx` / `lyrics-display.tsx` /
  `player-controls.tsx` / `playlist-panel.tsx` 等）**未移植**：栈不匹配
  （Self-Music 是 Next 15.4 / React 19 / Tailwind 4，我们是 Next 16.3 / React 18 / Tailwind 3），
  且 API 契约完全不同（ theirs 用 `@/types` 的 `Song`，我们用 `lib/api.ts` 的 `SongView`）。

## 重新获取

```bash
cnb git get-archive --repo onmicrosoft/Self-Music --ref-with-path main   # gzip tar，非 zip
cnb git get-archive --repo chguanw/ace-step-1.5 --ref-with-path main
```

## 判据（沿用本仓库既有约定）

- MIT / Apache → 可复用；AGPL / GPL（copyleft）→ 排除；
- `license: null` 或缺少 LICENSE → 版权所有，仅作 reference；
- MIT 但 100% 混淆 → 不可用（无法审阅/调试 = 不可验证的供应链）。
