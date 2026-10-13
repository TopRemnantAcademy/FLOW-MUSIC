/* eslint-disable no-undef */
/**
 * FLOW PWA Service Worker — 手写，零构建依赖（자세한 근거는 docs/PWA.md / PR 설명 참고）。
 *
 * 为什么不用 next-pwa：
 *   1) next-pwa@5.6.0 最后发布于 2022-08，依赖 babel-loader@8 / workbox-webpack-plugin@6，
 *      而本仓库是 Next 16 (Turbopack) + React 18。引入即等于为 SW 单独背一套 webpack 构建。
 *   2) 它默认挂 webpack 插件改写产物 —— 与 `APK_BUILD=1` 的 `output:'export'` 分支叠加，
 *      正是「不得破坏 APK 静态导出」这条硬验收最脆弱的地方（构建期注入 = 额外失败面）。
 *   3) 本项目缓存诉求很窄：应用壳 + 音频「听过即缓存」。Workbox 的 runtimeCaching 能做，
 *      但为此付出一套 webpack 依赖 + 构建期不确定性的代价不划算。
 *   → 手写 SW(约 200 行) 行为可审计、缓存上限/过期/更新策略可读，且 **零 npm 依赖**(不影响 APK 构建)。
 *
 * 缓存上限 / 过期 / 更新策略（硬性约定，勿随意放宽）：
 *   - 壳(core)：版本化缓存 flw-core-<SW_VERSION>。install 预缓存核心路径；activate 删除
 *              所有非当前版本前缀的 flw- 缓存（旧版本自动 GC）。
 *   - 静态资产：CacheFirst，白名单前缀 /_next/static/ 与 /icons/。
 *   - 音频：CacheFirst + LRU 上限 FLW_AUDIO_MAX_ENTRIES(64 首)，
 *           **只在响应成功(200)且非 opaque 时写入**；跨域音频单独分桶 flw-audio-<v>。
 *           Range 请求(206)不缓存（避免半截文件）——播放器 seek 时才发 Range。
 *   - 过期：LRU 上限既是容量策略也是事实上的过期（超限即逐出）。
 *   - 更新：SW 自身 `skipWaiting()` + 客户端 `controllerchange` → 整页 reload 一次；
 *           壳哈希资源随构建文件名自然失效；manifest/SW 本体走 NO-CACHE 直连。
 *   - 离线兜底：导航请求 NetworkFirst(networkTimeout 3s) → 失败回退到预缓存 /index.html
 *           （trailingSlash 下实际为 / 的落盘文件）；音频不在缓存 → 返回 503 + JSON，
 *           由页面弹出「离线提示」。
 *
 * ⚠️ APK 路径：Capacitor WebView 以 file:// 或本地 server 加载，`serviceWorker` 在
 *    capacitor:// / file:// origin 上不可用（navigator.serviceWorker 为 undefined）。
 *    PwaSetup 已做 feature-detect，APK 侧自动跳过 —— 本文件对 APK 构建只作为静态资源存在。
 */

const SW_VERSION = "v1";
const CORE_CACHE = `flw-core-${SW_VERSION}`;
const STATIC_CACHE = `flw-static-${SW_VERSION}`;
const AUDIO_CACHE = `flw-audio-${SW_VERSION}`;
const OWN_PREFIX = "flw-";

/** 预缓存：**只放构建产物里稳定存在的路径**（out/ 根 index.html），不做递归爬取。 */
const CORE_ASSETS = ["/", "/index.html", "/manifest.webmanifest"];

const FLW_AUDIO_MAX_ENTRIES = 64;
const NETWORK_TIMEOUT_MS = 3000;

const STATIC_PREFIXES = ["/_next/static/", "/icons/"];
const NO_CACHE_PATHS = ["/sw.js", "/manifest.webmanifest"];

const AUDIO_EXT = /\.(mp3|m4a|aac|wav|flac|ogg|opus|webm|mp4)(\?|$)/i;
const isAudioRequest = (req) =>
  req.destination === "audio" || AUDIO_EXT.test(new URL(req.url).pathname);

const isSameOrigin = (url) => new URL(url).origin === self.location.origin;

// ── install: 预缓存核心壳 ────────────────────────────────────────────────────
// addAll 은 하나라도 실패하면 전체 install 이 실패한다 → 개별 add 로 바꾸고 실패는
// 개별 경고만 남긴다(예: /index.html 이 없는 서버 구성이어도 SW 설치는 계속된다).
self.addEventListener("install", (event) => {
  event.waitUntil(
    (async () => {
      const cache = await caches.open(CORE_CACHE);
      await Promise.all(
        CORE_ASSETS.map((url) =>
          cache.add(new Request(url, { cache: "reload" })).catch((e) => {
            console.warn("[sw] precache skip:", url, e && e.message);
          })
        )
      );
      // 새 SW 즉시 활성화 → 열려 있는 탭은 controllerchange 로 1회 reload.
      await self.skipWaiting();
    })()
  );
});

// ── activate: 旧版本缓存 GC + 立即接管 ──────────────────────────────────────
self.addEventListener("activate", (event) => {
  event.waitUntil(
    (async () => {
      const keep = new Set([CORE_CACHE, STATIC_CACHE, AUDIO_CACHE]);
      const names = await caches.keys();
      await Promise.all(
        names
          .filter((n) => n.startsWith(OWN_PREFIX) && !keep.has(n))
          .map((n) => caches.delete(n))
      );
      await self.clients.claim();
    })()
  );
});

// ── 音频缓存：LRU 裁剪（超限逐出最旧插入项） ────────────────────────────────
async function trimAudioCache() {
  const cache = await caches.open(AUDIO_CACHE);
  const keys = await cache.keys();
  // cache.keys() 顺序 ≈ 插入顺序(FIFO)。对「最近听过的」场景，FIFO 逐出足够，
  // 真正的 LRU 需要每次命中都重写条目（写入成本 > 收益）。
  const overflow = keys.length - FLW_AUDIO_MAX_ENTRIES;
  if (overflow <= 0) return;
  await Promise.all(keys.slice(0, overflow).map((k) => cache.delete(k)));
}

/** 只缓存「完整、成功、可复现」的音频响应。 */
function isCacheableAudioResponse(res) {
  // 206(Partial) / 416 等 Range 响应不缓存：半截文件会导致离线播放中途断。
  if (res.status !== 200) return false;
  // opaque(cross-origin no-cors) 响应无法判断体积与正确性 → 不落盘。
  if (res.type === "opaque") return false;
  // 明确标记不缓存的响应（如后端签名 URL 的一次性资源）。
  if ((res.headers.get("cache-control") || "").includes("no-store")) return false;
  return true;
}

// ── fetch 路由 ───────────────────────────────────────────────────────────────
self.addEventListener("fetch", (event) => {
  const req = event.request;

  // 只处理 GET；其余（POST 生成歌曲等）一律放行到网络。
  if (req.method !== "GET") return;

  const url = new URL(req.url);

  // 自身运维文件：永远走网络（保证 SW/manifest 可被浏览器及时拿到新版本）。
  if (isSameOrigin(req.url) && NO_CACHE_PATHS.some((p) => url.pathname === p)) {
    event.respondWith(fetch(req));
    return;
  }

  // ① 导航请求 → 离线壳（NetworkFirst + 超时回退预缓存壳）
  if (req.mode === "navigate") {
    event.respondWith(networkFirstShell(req));
    return;
  }

  // ② 音频 → CacheFirst（含 LRU 上限），离线未命中给明确 503，页面据此提示。
  if (isAudioRequest(req) && (isSameOrigin(req.url) || url.protocol === "https:")) {
    event.respondWith(cacheFirstAudio(req));
    return;
  }

  // ③ 静态资产（Next 哈希 chunk / 图标）→ CacheFirst
  if (isSameOrigin(req.url) && STATIC_PREFIXES.some((p) => url.pathname.startsWith(p))) {
    event.respondWith(cacheFirstStatic(req));
    return;
  }

  // ④ 其它（API/统计/图片等）→ 纯网络，不介入。避免缓存掉鉴权数据。
  // ④ 其它（API/统计/图片等）→ 纯网络，不介入。避免缓存掉鉴权数据。
});

async function networkFirstShell(req) {
  const cache = await caches.open(CORE_CACHE);
  try {
    const res = await fetchWithTimeout(req, NETWORK_TIMEOUT_MS);
    if (res && res.ok) {
      // 只缓存根壳（动态路径 /library 等一律回退到同一壳，避免缓存膨胀）。
      cache.put("/index.html", res.clone()).catch(() => {});
    }
    return res;
  } catch (e) {
    const cached = (await cache.match("/index.html")) || (await cache.match("/"));
    if (cached) return cached;
    return new Response(
      "<!doctype html><meta charset=utf-8><title>FLOW 离线</title>" +
        "<body style='font-family:system-ui;background:#0f0f0f;color:#f5f5f5;display:grid;place-items:center;height:100vh;margin:0'>" +
        "<div style='text-align:center'><h1>当前处于离线状态</h1>" +
        "<p style='opacity:.7'>请恢复网络后重试。已缓存的内容仍可播放。</p></div>",
      { status: 503, headers: { "Content-Type": "text/html; charset=utf-8" } }
    );
  }
}

async function cacheFirstStatic(req) {
  const cache = await caches.open(STATIC_CACHE);
  const hit = await cache.match(req);
  if (hit) return hit;
  try {
    const res = await fetch(req);
    if (res && res.ok) cache.put(req, res.clone()).catch(() => {});
    return res;
  } catch (e) {
    return Response.error();
  }
}

async function cacheFirstAudio(req) {
  const cache = await caches.open(AUDIO_CACHE);

  // Range 请求(seek)不参与缓存：浏览器会自动从已缓存的 200 完整响应里切片。
  const isRange = req.headers.has("range");
  if (isRange) {
    const full = await cache.match(stripRange(req));
    if (full) return full;
    return fetch(req).catch(() => offlineAudioResponse());
  }

  const hit = await cache.match(req);
  if (hit) return hit;

  try {
    const res = await fetch(req);
    if (isCacheableAudioResponse(res)) {
      cache.put(req, res.clone()).then(trimAudioCache).catch(() => {});
    }
    return res;
  } catch (e) {
    return offlineAudioResponse();
  }
}

/** Range 请求匹配缓存时，必须用「同一个 key(无 Range header)」查。 */
function stripRange(req) {
  const headers = new Headers(req.headers);
  headers.delete("range");
  headers.delete("if-range");
  return new Request(req.url, { headers, method: "GET", credentials: req.credentials });
}

function offlineAudioResponse() {
  return new Response(
    JSON.stringify({ offline: true, error: "audio_not_cached", message: "该音频未缓存，恢复网络后可播放。" }),
    { status: 503, headers: { "Content-Type": "application/json; charset=utf-8", "X-Flow-Offline": "1" } }
  );
}

function fetchWithTimeout(req, ms) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), ms);
  return fetch(req, { signal: controller.signal }).finally(() => clearTimeout(timer));
}

// ── 供页面查询缓存统计（离线提示/调试用） ───────────────────────────────────
self.addEventListener("message", (event) => {
  const data = event.data || {};
  if (data.type === "FLW_SW_PING") {
    event.source &&
      event.source.postMessage({ type: "FLW_SW_PONG", version: SW_VERSION });
  }
  // 页面在检测到 waiting worker 时发来 → 促其立即接管（配合 install 的 skipWaiting）。
  if (data.type === "FLW_SW_ACTIVATE") {
    self.skipWaiting();
  }
  if (data.type === "FLW_SW_STATS") {
    event.waitUntil(
      (async () => {
        const cache = await caches.open(AUDIO_CACHE);
        const keys = await cache.keys();
        event.source &&
          event.source.postMessage({
            type: "FLW_SW_STATS_RESULT",
            version: SW_VERSION,
            audioEntries: keys.length,
          });
      })()
    );
  }
});
