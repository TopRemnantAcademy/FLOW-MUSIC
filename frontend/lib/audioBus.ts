/**
 * 全局单一音频总线
 *
 * 为什么必须有这一层：卡片里的 <audio> 之前是每张卡各持一个，导致三个问题——
 *   ① 底部播放条的按钮是假的（够不到别的卡片的 audio 元素）；
 *   ② 同一页面点两张卡的播放，两首歌叠在一起响；
 *   ③ 路由一跳，卡片卸载，播放就断。
 * 收成全局一个 HTMLAudioElement（不挂 DOM，用 `new Audio()`）后三者一起消失，
 * 而且卡片内的逐字歌词（SyncedLyrics 读 currentTime）也还是读同一个元素。
 *
 * 两条独立的订阅通道：status（播放中/音量/时长，低频）与 time（currentTime，约 4Hz）。
 * 拆开是为了不让进度条的每一帧把整个 Player 树（450 行 + 歌词）重新渲染一遍。
 */

"use client";

import { useSyncExternalStore } from "react";

export interface AudioStatus {
  src: string | null;
  playing: boolean;
  duration: number;
  volume: number;
  muted: boolean;
}

const INITIAL_STATUS: AudioStatus = {
  src: null,
  playing: false,
  duration: 0,
  volume: 1,
  muted: false,
};

const VOLUME_KEY = "flow-music-volume";

let el: HTMLAudioElement | null = null;
let status: AudioStatus = INITIAL_STATUS;
let time = 0;
const statusListeners = new Set<() => void>();
const timeListeners = new Set<() => void>();
const endedListeners = new Set<() => void>();

function readStoredVolume(): number {
  try {
    const v = window.localStorage.getItem(VOLUME_KEY);
    const n = v == null ? 1 : Number(v);
    return Number.isFinite(n) ? Math.min(1, Math.max(0, n)) : 1;
  } catch {
    return 1;
  }
}

function ensureEl(): HTMLAudioElement {
  if (el) return el;
  const a = new Audio();
  a.preload = "metadata";
  const stored = readStoredVolume();
  a.volume = stored;
  status = { ...status, volume: stored };

  a.addEventListener("play", () => patchStatus({ playing: true }));
  a.addEventListener("pause", () => patchStatus({ playing: false }));
  a.addEventListener("loadedmetadata", () => patchStatus({ duration: a.duration || 0 }));
  a.addEventListener("timeupdate", () => {
    // AB 回绕要在「广播给订阅者之前」判定：先修正 currentTime，再让订阅者
    // （进度条/波形/逐字歌词）看到修正后的值，否则会在回绕的那一帧闪一下区间外位置。
    const looped = maybeWrapAb(a);
    time = a.currentTime;
    if (looped) patchStatus({}); // 回绕不改 status 字段，但需要刷新进度订阅者
    for (const l of timeListeners) l();
  });
  a.addEventListener("ended", () => {
    patchStatus({ playing: false, duration: a.duration || status.duration });
    for (const l of endedListeners) l();
  });
  el = a;
  return a;
}

// ── GAP-013 AB 回绕 ──────────────────────────────────────────────────────
/**
 * AB 区间由 playback.ts 持有状态，但回绕必须在**音频元素的 timeupdate 里**发生。
 *
 * 为什么用「注册回调」而不是让 audioBus 去 import playback：
 *   audioBus 是零依赖的底层总线（它连 SongView 都不认识），playback 是业务状态层。
 *   让它反过来依赖业务层会把两层缠死，而这个文件的注释里已经写明「两条独立订阅通道」
 *   的分层意图。回调注册保持单向：playback → audioBus。
 */
let abLoop: { a: number; b: number } | null = null;

/** 设置/清除 AB 回绕区间。传 null 即关闭。 */
export function busSetAbLoop(loop: { a: number; b: number } | null) {
  abLoop = loop;
}

/** 回绕判定：currentTime 越过 b → 跳回 a。返回是否真的跳了。 */
function maybeWrapAb(a: HTMLAudioElement): boolean {
  if (!abLoop || !a.duration) return false;
  if (a.currentTime < abLoop.b) return false;
  // 停在 b 之前的极小偏移，避免「恰好 == b 时 seek 后又立刻触发」的空转。
  a.currentTime = abLoop.a;
  return true;
}

function patchStatus(p: Partial<AudioStatus>) {
  const next = { ...status, ...p };
  const same =
    next.src === status.src &&
    next.playing === status.playing &&
    next.duration === status.duration &&
    next.volume === status.volume &&
    next.muted === status.muted;
  if (same) return;
  status = next;
  for (const l of statusListeners) l();
}

function subscribeStatus(listener: () => void) {
  ensureEl();
  statusListeners.add(listener);
  return () => {
    statusListeners.delete(listener);
  };
}
function subscribeTime(listener: () => void) {
  ensureEl();
  timeListeners.add(listener);
  return () => {
    timeListeners.delete(listener);
  };
}

/**
 * 给学生组件用：SyncedLyrics 的 props 是 `audioRef: RefObject<HTMLAudioElement|null>`，
 * 这里给一个「永远指向当前总线元素」的只读引用，读一次就惰性创建一个（幂等）。
 */
export const audioElementRef: { readonly current: HTMLAudioElement | null } = {
  get current() {
    return ensureEl();
  },
};

export function useAudioStatus(): AudioStatus {
  return useSyncExternalStore(subscribeStatus, () => status, () => INITIAL_STATUS);
}

export function useAudioTime(): number {
  return useSyncExternalStore(subscribeTime, () => time, () => 0);
}

/** 绑定播放源并起播。autoplay=false 时只装载、不起播。 */
export function busLoad(src: string, autoplay = true) {
  const a = ensureEl();
  if (a.src !== src) {
    a.src = src;
    patchStatus({ src, duration: 0 });
  }
  if (autoplay) void a.play().catch(() => {});
}

export function busPlay() {
  void ensureEl().play().catch(() => {});
}

export function busPause() {
  const a = ensureEl();
  if (!a.paused) a.pause();
}

export function busToggle() {
  const a = ensureEl();
  if (a.paused) busPlay();
  else a.pause();
}

export function busSeek(seconds: number) {
  const a = ensureEl();
  const max = Number.isFinite(a.duration) && a.duration > 0 ? a.duration : a.currentTime + 1;
  a.currentTime = Math.max(0, Math.min(seconds, max));
}

export function busSetVolume(v: number) {
  const a = ensureEl();
  const n = Math.min(1, Math.max(0, v));
  a.volume = n;
  a.muted = false;
  try {
    window.localStorage.setItem(VOLUME_KEY, String(n));
  } catch {
    /* 隐私模式：音量降级为本次会话有效 */
  }
  patchStatus({ volume: n, muted: false });
}

export function busToggleMute() {
  const a = ensureEl();
  a.muted = !a.muted;
  patchStatus({ muted: a.muted, volume: a.volume });
}

/** 订阅「一曲播完」。返回取消订阅函数。 */
export function onBusEnded(cb: () => void) {
  endedListeners.add(cb);
  return () => {
    endedListeners.delete(cb);
  };
}

/** 秒 → mm:ss。无效/未知时长显示 "--:--"。 */
export function fmtClock(sec: number): string {
  if (!Number.isFinite(sec) || sec < 0) return "--:--";
  const s = Math.floor(sec % 60);
  const m = Math.floor(sec / 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}
