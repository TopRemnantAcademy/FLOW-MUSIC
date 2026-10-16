/**
 * Wave5 #150 回归 —— 播放计数不虚高 + 播放动作不重复拉详情。
 *
 * 这不是「静态读源码」型契约测试：它真的用 jsdom + React 18 挂载 <Player>，
 * 把 api.play / api.getSong 换成探针，数**真实调用次数**。
 *
 * 背景（修复前实测，见 PR 证据）：列表里点一次「播放」，队列连播到末尾，
 *   POST /api/songs/{id}/play 的调用序列是 `1,1,2,3,4` —— 第 1 首被记了两次；
 *   同时每首还多出一次 `GET /api/songs/{id}`（play 响应体本身就带完整 SongOut）。
 *
 *  根因是一条「复活环」：onPlay → playFrom（默认 autostart=true）→ effect
 *  看到 autostart+currentSongId 一致 → consumeAutostart + onPlay() → 又置回 true…
 *  实测该 effect 在 `autostart=true / currentSongId=2 / song.id=1` 时被调用，
 *  被自己的守卫挡下（还没轮到它），autostart 就这么**悬着**，等另一张卡接手。
 *
 * 运行：npx tsx tests/player-play-count.test.tsx
 */
import { JSDOM } from "jsdom";

// ── DOM 必须先于 React 建立（react-dom 在导入期读 document）─────────────────
const dom = new JSDOM("<!doctype html><html><body><div id='root'></div></body></html>", {
  pretendToBeVisual: true,
  url: "https://music.anistar.studio/library",
});
const g = globalThis as any;
g.window = dom.window;
g.document = dom.window.document;
Object.defineProperty(g, "navigator", { value: dom.window.navigator, configurable: true });
for (const k of [
  "HTMLElement", "HTMLDivElement", "HTMLAudioElement", "Element", "Node", "Event",
  "MouseEvent", "KeyboardEvent", "CustomEvent",
]) {
  g[k] = (dom.window as any)[k];
}
g.getComputedStyle = dom.window.getComputedStyle;
g.requestAnimationFrame = (cb: FrameRequestCallback) =>
  setTimeout(() => cb(Date.now()), 0) as unknown as number;
g.cancelAnimationFrame = (id: number) => clearTimeout(id);
(dom.window as any).requestAnimationFrame = g.requestAnimationFrame;
(dom.window as any).cancelAnimationFrame = g.cancelAnimationFrame;
// jsdom 缺 scrollIntoView / matchMedia（SyncedLyrics 会碰到）
(dom.window as any).Element.prototype.scrollIntoView = function () {};
(dom.window as any).matchMedia = () => ({
  matches: false, media: "", addListener() {}, removeListener() {},
  addEventListener() {}, removeEventListener() {}, dispatchEvent: () => false,
});
// jsdom 不实现 new Audio()（audioBus 用它做全局音频元素）→ 补最小实现。
// 关键点：`src` 与 `currentSrc` 要真实联动，onPlay 的去重判据读的就是 currentSrc。
class FakeAudio extends (dom.window as any).EventTarget {
  private _src = "";
  currentTime = 0; volume = 1; muted = false; duration = 0; paused = true; preload = "";
  get src() { return this._src; }
  set src(v: string) { this._src = v ?? ""; }
  get currentSrc() { return this._src; }
  load() {}
  play() {
    this.paused = false;
    this.dispatchEvent(new dom.window.Event("play"));
    return Promise.resolve();
  }
  pause() { this.paused = true; this.dispatchEvent(new dom.window.Event("pause")); }
}
g.Audio = FakeAudio;
(dom.window as any).Audio = FakeAudio;

import * as React from "react";
import { createRoot, type Root } from "react-dom/client";

(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;
const act: (cb: () => void | Promise<void>) => Promise<void> = (React as any).act;

// ── 探针：替换真实网络客户端，只记调用次数 ────────────────────────────────
const calls = { play: [] as number[], getSong: [] as number[] };
const songOf = (i: number) => ({
  id: i, title: `S${i}`, status: "completed",
  audio_url: `https://cdn.example.com/${i}.mp3`, lyric: `plain ${i}`,
  lyrics_sync: null as string | null, play_count: 0,
});
(globalThis as any).__SONGS = [] as ReturnType<typeof songOf>[];

vi_mockApi();
function vi_mockApi() {
  // api 模块是 ESM 导出常量对象；直接原地覆写它的方法即可（Player 持有同一引用）
  const api = require("../lib/api").api;
  api.play = async (id: number) => {
    calls.play.push(id);
    const base = (globalThis as any).__SONGS.find((s: any) => s.id === id) ?? songOf(id);
    // 真后端：POST /play 返回完整 SongOut，且 _to_out(..., include_sync=True) → lyrics_sync 有值
    return {
      ...base,
      play_count: (base.play_count ?? 0) + 1,
      lyrics_sync: JSON.stringify([
        { section_type: "verse", lines: [{ start: 0, end: 900, text: `line-${id}` }] },
      ]),
    };
  };
  api.getSong = async (id: number) => { calls.getSong.push(id); return songOf(id); };
  api.previewObjectUrl = async () => null;
}

const Player = require("../components/Player").default;
const playback = require("../lib/playback");

let passed = 0;
const failures: string[] = [];
function check(name: string, cond: boolean, extra = "") {
  if (cond) { passed++; console.log(`  ✓ ${name}`); }
  else { failures.push(name + (extra ? ` — ${extra}` : "")); console.log(`  ✗ ${name}${extra ? ` — ${extra}` : ""}`); }
}

const root: Root = createRoot(document.getElementById("root")!);
const flush = async (ms = 25) =>
  act(async () => { await new Promise((r) => setTimeout(r, ms)); });

let current: any = null;
let queue: any[] = [];
const render = () => {
  act(() => {
    root.render(
      React.createElement(Player, {
        song: current, queue,
        onReplaceSong: (s: any) => { current = s; },
      }),
    );
  });
};

function click(label: RegExp) {
  const btn = [...document.querySelectorAll("button")].find((b) => label.test(b.textContent ?? ""));
  if (!btn) throw new Error("找不到按钮 " + label);
  act(() => { btn.dispatchEvent(new dom.window.MouseEvent("click", { bubbles: true })); });
}

// ═══════════════════════════════════════════════════════════════════════════
(async () => {
  console.log("── Wave5 #150：连播 5 首，POST /play 必须每首恰一次 ──");

  const N = 5;
  playback.clearPlaylist();
  queue = Array.from({ length: N }, (_, i) => songOf(i + 1));
  (globalThis as any).__SONGS = queue;
  current = queue[0];
  render();

  calls.play.length = 0;
  calls.getSong.length = 0;

  // ① 用户在列表里点这张卡的「播放」
  click(/^(播放|暂停)$/);
  await flush();

  // ② 之后自然播完，连播到底：与 BottomPlayer 的 onClick={advance} + 页面切槽同形
  for (let i = 0; i < N - 1; i++) {
    let next: any = null;
    act(() => { next = playback.advance(); });
    if (!next || next.id === current?.id) break;
    current = next;
    render();
    await flush();
  }

  const dupes = calls.play.filter((id, i) => calls.play.indexOf(id) !== i);
  check(
    "连播 5 首，没有任何一首被打超过一次 /play",
    dupes.length === 0,
    `重复 = ${JSON.stringify(dupes)}，实际序列 = ${calls.play.join(",")}`,
  );
  check(
    "POST /play 总次数不超过歌曲数（无虚高）",
    calls.play.length <= N,
    `实际 ${calls.play.length} 次`,
  );
  check(
    "首曲只被计一次（修复前此处为 [1,1,…]）",
    calls.play.filter((id) => id === 1).length <= 1,
    `实际 ${calls.play.filter((id) => id === 1).length} 次`,
  );

  console.log("── 同一时刻：播放动作不应再额外拉详情 GET ──");
  check(
    "播放全流程 0 次 GET /api/songs/{id}（详情由 play 响应携带）",
    calls.getSong.length === 0,
    `实际 ${calls.getSong.length} 次：${calls.getSong.join(",")}`,
  );

  // ── 补一条：play 响应里的 lyrics_sync 确实被消费（否则「省掉详情」会顺手弄丢逐字歌词）
  // 逐字歌词是**点击那一刻**并进来、但队列连播几轮后，卡上渲染的是最后一首；
  // 为免把「队列推进」混进来，这里重建一次「只点播放、不连播」的最小场景再断言。
  playback.clearPlaylist();
  queue = [songOf(1)];
  (globalThis as any).__SONGS = queue;
  current = queue[0];
  render();
  click(/^(播放|暂停)$/);
  await flush();
  const bodyText1 = document.body.textContent ?? "";
  check(
    "play 响应里的 lyrics_sync 被并入播放器状态（省详情 ≠ 丢歌词）",
    bodyText1.includes("line-1"),
    `播放器未渲染出 play 响应带来的逐字歌词（body: ${bodyText1.slice(0, 160)}…）`,
  );
  // 反向确认：上一条不是假绿 —— 入队时的 song 对象 lyrics_sync 就是 null，
  // 「line-1 出现」只可能来自 play 响应。若哪天探针改成预置歌词，这条会先红。
  check(
    "入队曲目本身不带 lyrics_sync（故 line-1 只可能来自 play 响应）",
    queue[0].lyrics_sync === null,
  );

  act(() => root.unmount());

  console.log(`\n结果：${passed} 通过 / ${failures.length} 失败`);
  if (failures.length) {
    failures.forEach((f) => console.log("  - " + f));
    process.exit(1);
  }
  console.log("全部通过 ✅");
})();
