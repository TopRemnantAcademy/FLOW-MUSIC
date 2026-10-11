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

export interface PlaybackSnapshot {
  playlist: PlaylistState | null;
  prefs: PlaybackPrefs;
  /** 队列已切到下一首、等对应卡片挂载后自动起播（一次性的，消费后清零） */
  autostart: boolean;
}

const EMPTY: PlaybackSnapshot = {
  playlist: null,
  prefs: DEFAULT_PREFS,
  autostart: false,
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
  if (playlist || prefs.shuffle || prefs.repeat !== DEFAULT_PREFS.repeat) {
    commit({ playlist, prefs, autostart: false }, false);
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
  commit({ playlist: null, prefs: snapshot.prefs, autostart: false }, true);
}

/** 组件内订阅入口。字段变化才换引用，避免每帧重渲染。 */
export function usePlayback(): PlaybackSnapshot {
  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}
