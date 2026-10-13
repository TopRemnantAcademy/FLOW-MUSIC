/**
 * 全站共享播放队列（React 绑定层）
 *
 * 为什么不用 Context Provider：本仓库的 <Player> 散落在 library / plaza / HomeFeed /
 * CreateForm 四处，逐层套 Provider 要改 layout.tsx 与四个页面；模块级 store +
 * useSyncExternalStore 只要 import 即可订阅，且服务端快照恒定（EMPTY）不会水合错位。
 *
 * 落盘键前缀用 `flow-music-`，与上游 Self-Music 的 `self-music-` 隔离 —— 将来若同一个
 * origin 部署了两套前端，不会互相踩 localStorage。
 */

"use client";

import { useSyncExternalStore } from "react";
import type { SongView } from "./api";
import {
  DEFAULT_PREFS,
  REPEAT_MODES,
  createPlaylistState,
  indexOfSong,
  isValidPlaylistState,
  nextIndex,
  prevIndex,
  replaceSong,
  shufflePlaylist,
} from "./playlist";
import type { PlaybackPrefs, PlaylistState, RepeatMode } from "./playlist";

const PLAYLIST_KEY = "flow-music-playlist";
const PREFS_KEY = "flow-music-prefs";
const AB_KEY = "flow-music-ab";

/**
 * AB 重复区间（练唱 / 编曲比对）。
 *
 * 语义刻意做成「**相对当前曲目**的半成品状态机」：
 *   · a == null            → 还没打 A 点
 *   · a != null, b == null → 已打 A、等 B（此时不循环）
 *   · a < b                → 循环区间 [a, b]
 *   · a >= b               → 非法组合，一律**丢弃**（见 setAbB）而不是偷偷交换：
 *     交换会让「我打了 A=30s，B=10s」变成循环 10~30s —— 用户以为区间是空操作，
 *     实际听到的却是 20 秒在反复，这种「猜用户意图」比报错更坏。
 */
export interface AbLoop {
  a: number;
  b: number | null;
}

/** AB 区间的持久化形状校验（坏数据丢弃，与 isValidPlaylistState 同一策略）。 */
function isValidAb(v: unknown): v is AbLoop {
  if (!v || typeof v !== "object") return false;
  const p = v as Partial<AbLoop>;
  if (typeof p.a !== "number" || !Number.isFinite(p.a) || p.a < 0) return false;
  if (p.b == null) return true;
  return typeof p.b === "number" && Number.isFinite(p.b) && p.b > p.a;
}

/** AB 区间最短长度（秒）。太短的区间会退化成「电流声」，无音乐意义。 */
export const AB_MIN_SPAN = 0.5;

export interface PlaybackSnapshot {
  playlist: PlaylistState | null;
  prefs: PlaybackPrefs;
  /** 队列已切到下一首、等对应卡片挂载后自动起播（一次性的，消费后清零） */
  autostart: boolean;
  /** GAP-013 AB 重复区间；null = 未启用 */
  ab: AbLoop | null;
}

const EMPTY: PlaybackSnapshot = {
  playlist: null,
  prefs: DEFAULT_PREFS,
  autostart: false,
  ab: null,
};

let snapshot: PlaybackSnapshot = EMPTY;
let hydrated = false;
const listeners = new Set<() => void>();

const isBrowser = () => typeof window !== "undefined";

function emit() {
  for (const l of listeners) l();
}

function commit(next: PlaybackSnapshot, persist: boolean) {
  snapshot = next;
  if (persist && isBrowser()) {
    try {
      if (next.playlist) window.localStorage.setItem(PLAYLIST_KEY, JSON.stringify(next.playlist));
      else window.localStorage.removeItem(PLAYLIST_KEY);
      window.localStorage.setItem(PREFS_KEY, JSON.stringify(next.prefs));
      // AB 区间跨刷新保留：练唱时反复刷新页面不该丢掉刚打好的区间。
      if (next.ab) window.localStorage.setItem(AB_KEY, JSON.stringify(next.ab));
      else window.localStorage.removeItem(AB_KEY);
    } catch {
      /* 隐私模式 / 配额满：队列降级为内存态，不影响播放 */
    }
  }
  emit();
}

function hydrateOnce() {
  if (hydrated || !isBrowser()) return;
  hydrated = true;
  let playlist: PlaylistState | null = null;
  let prefs: PlaybackPrefs = DEFAULT_PREFS;
  try {
    const rawPlaylist = window.localStorage.getItem(PLAYLIST_KEY);
    if (rawPlaylist) {
      const parsed = JSON.parse(rawPlaylist);
      playlist = isValidPlaylistState(parsed) ? parsed : null;
      if (!playlist) window.localStorage.removeItem(PLAYLIST_KEY);
    }
    const rawPrefs = window.localStorage.getItem(PREFS_KEY);
    if (rawPrefs) {
      const p = JSON.parse(rawPrefs) as Partial<PlaybackPrefs>;
      prefs = {
        shuffle: !!p.shuffle,
        repeat: REPEAT_MODES.includes(p.repeat as RepeatMode)
          ? (p.repeat as RepeatMode)
          : DEFAULT_PREFS.repeat,
      };
    }
  } catch {
    playlist = null;
    prefs = DEFAULT_PREFS;
  }
  let ab: AbLoop | null = null;
  try {
    const rawAb = window.localStorage.getItem(AB_KEY);
    if (rawAb) {
      const parsed = JSON.parse(rawAb);
      ab = isValidAb(parsed) ? parsed : null;
      if (!ab) window.localStorage.removeItem(AB_KEY);
    }
  } catch {
    ab = null;
  }
  if (
    playlist ||
    ab ||
    prefs.shuffle ||
    prefs.repeat !== DEFAULT_PREFS.repeat
  ) {
    commit({ playlist, prefs, autostart: false, ab }, false);
  }
}

function subscribe(listener: () => void) {
  hydrateOnce();
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

const getSnapshot = () => {
  hydrateOnce();
  return snapshot;
};
const getServerSnapshot = () => EMPTY;

/** 把某张卡片「设为当前并起播」，同时用所在页面的列表锚定队列。 */
export function playFrom(
  songs: SongView[],
  index: number,
  opts: { autostart?: boolean } = {},
) {
  if (songs.length === 0) return;
  const playlist = createPlaylistState(songs, index);
  commit({ ...snapshot, playlist, autostart: opts.autostart ?? true }, true);
}

/** 只换当前曲目、不动队列所在列表（用于续写/翻唱后把新对象塞回原位）。 */
export function replaceCurrent(updated: SongView) {
  if (!snapshot.playlist) return;
  commit({ ...snapshot, playlist: replaceSong(snapshot.playlist, updated) }, true);
}

/**
 * 前进。返回切到的曲目（可能是同一首，repeat=one）；null = 列表已放完。
 * 调用方拿到曲目后需把它交给对应卡片（onReplaceSong），autostart 会驱动自动起播。
 */
export function advance(): SongView | null {
  const st = snapshot.playlist;
  if (!st) return null;
  const i = nextIndex(st, snapshot.prefs);
  if (i == null) return null;
  const song = st.songs[i];
  if (!song) return null;
  commit(
    {
      ...snapshot,
      playlist: { ...st, currentIndex: i, currentSongId: song.id, lastUpdated: new Date().toISOString() },
      autostart: true,
    },
    true,
  );
  return song;
}

export function back(): SongView | null {
  const st = snapshot.playlist;
  if (!st) return null;
  const i = prevIndex(st);
  if (i < 0) return null;
  const song = st.songs[i];
  if (!song) return null;
  commit(
    {
      ...snapshot,
      playlist: { ...st, currentIndex: i, currentSongId: song.id, lastUpdated: new Date().toISOString() },
      autostart: true,
    },
    true,
  );
  return song;
}

export function jumpTo(songId: number) {
  const st = snapshot.playlist;
  if (!st) return;
  const i = indexOfSong(st, songId);
  if (i < 0) return;
  const song = st.songs[i];
  commit(
    {
      ...snapshot,
      playlist: { ...st, currentIndex: i, currentSongId: song.id, lastUpdated: new Date().toISOString() },
      autostart: true,
    },
    true,
  );
}

export function consumeAutostart() {
  if (!snapshot.autostart) return;
  commit({ ...snapshot, autostart: false }, false);
}

export function toggleShuffle() {
  const st = snapshot.playlist;
  const prefs = { ...snapshot.prefs, shuffle: !snapshot.prefs.shuffle };
  const nextPlaylist = st && prefs.shuffle ? shufflePlaylist(st) : st;
  commit({ ...snapshot, prefs, playlist: nextPlaylist, autostart: false }, true);
}

export function cycleRepeat(): RepeatMode {
  const order = REPEAT_MODES;
  const cur = order.indexOf(snapshot.prefs.repeat);
  const next = order[(cur + 1) % order.length];
  commit({ ...snapshot, prefs: { ...snapshot.prefs, repeat: next }, autostart: false }, true);
  return next;
}

export function clearPlaylist() {
  commit({ playlist: null, prefs: snapshot.prefs, autostart: false, ab: null }, true);
}

// ── GAP-013 AB 重复 ──────────────────────────────────────────────────────
/**
 * 打 A 点（区间起点）。
 * 重复按 A 的行为：**重置区间**（a=新值, b=null）。
 * 为什么不「第二次按就等于按 B」：那样按钮的含义会随内部状态漂移，
 * 用户在 AB 关掉后按 A 会莫名其妙建出一个区间。A 就是 A，B 就是 B。
 */
export function setAbA(seconds: number) {
  const a = Math.max(0, seconds);
  commit({ ...snapshot, ab: { a, b: null } }, true);
}

/**
 * 打 B 点（区间终点）。未打 A 时无效（返回 false，调用方据此提示「请先打 A 点」）。
 * B <= A + AB_MIN_SPAN → 丢弃区间并返回 false：不静默交换、不建零长区间。
 */
export function setAbB(seconds: number): boolean {
  const ab = snapshot.ab;
  if (!ab) return false;
  const b = Math.max(0, seconds);
  if (b <= ab.a + AB_MIN_SPAN) return false;
  commit({ ...snapshot, ab: { a: ab.a, b } }, true);
  return true;
}

/** 清除整个 AB 区间（含只打了 A 点的半成品）。 */
export function clearAb() {
  if (!snapshot.ab) return;
  commit({ ...snapshot, ab: null }, true);
}

/** 区间是否已成型（A、B 都有且 B > A）。仅成型后才需要播放器做回绕。 */
export function activeAbLoop(ab: AbLoop | null): { a: number; b: number } | null {
  if (!ab || ab.b == null) return null;
  if (ab.b <= ab.a + AB_MIN_SPAN) return null;
  return { a: ab.a, b: ab.b };
}

/** 组件内订阅入口。字段变化才换引用，避免每帧重渲染。 */
export function usePlayback(): PlaybackSnapshot {
  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}
