/**
 * 播放队列状态机（纯逻辑层，不含 React / 不含 I/O）
 *
 * 移植来源：vendor/self-music/frontend/src/lib/playlist-manager.ts
 *   CNB `onmicrosoft/Self-Music` — MIT © 2018-present, Yuxi(Evian) You and Vve
 *   原文件是「静态方法 + 内部直写 localStorage」的隐式全局状态；这里改为
 *   **纯函数（state in → state out）**，副作用（持久化）上移到 lib/playback.ts。
 *   这么做的原因：① 可单测（无需 localStorage / window）；② SSR 安全；
 *   ③ 队列来源从「固定全局」变为「由所在页面列表锚定」。
 *   MIT 要求保留版权声明 → 见 vendor/self-music/LICENSE（本文件头部亦注明）。
 *
 * 与上游的三处有意偏离：
 *   1) 删除 getRecommendedPlaylist / initializeDefaultPlaylist —— 上游假设后端有
 *      `getHotSongs`；我们没有该端点形状（api.explore 已标记 legacy，plaza 才是超集）。
 *      队列由各页面用自己已加载的列表锚定，不凭空造一个用户没见过的队列。
 *   2) 增加 replaceSong —— 广场点「发布/续写」后歌曲对象会被就地更新，需保索引。
 *   3) 顺序/随机/循环的索引计算抽成 nextIndex/prevIndex 纯函数。
 */

import type { SongView } from "./api";

export type RepeatMode = "none" | "all" | "one";

export const REPEAT_MODES: RepeatMode[] = ["none", "all", "one"];

export interface PlaylistState {
  songs: SongView[];
  currentIndex: number;
  currentSongId: number | null;
  createdAt: string;
  lastUpdated: string;
}

export interface PlaybackPrefs {
  shuffle: boolean;
  repeat: RepeatMode;
}

export const DEFAULT_PREFS: PlaybackPrefs = { shuffle: false, repeat: "none" };

const nowIso = () => new Date().toISOString();

/** 把索引夹到 [0, songs.length-1]；空列表返回 -1，调用方需自行处理 */
export function clampIndex(index: number, length: number): number {
  if (length <= 0) return -1;
  return Math.max(0, Math.min(index, length - 1));
}

/** 构造（或重建）播放列表状态。索引非法时自动夹取，currentSongId 缺省取索引处曲目。 */
export function createPlaylistState(
  songs: SongView[],
  currentIndex = 0,
  currentSongId?: number | null,
): PlaylistState {
  const safeIndex = clampIndex(currentIndex, songs.length);
  const ts = nowIso();
  return {
    songs,
    currentIndex: safeIndex,
    currentSongId:
      currentSongId ?? (safeIndex >= 0 ? songs[safeIndex]?.id ?? null : null),
    createdAt: ts,
    lastUpdated: ts,
  };
}

/** 仅为持久化回来的对象做结构校验；坏数据交由调用方丢弃。 */
export function isValidPlaylistState(v: unknown): v is PlaylistState {
  if (!v || typeof v !== "object") return false;
  const p = v as Partial<PlaylistState>;
  return (
    Array.isArray(p.songs) &&
    Number.isInteger(p.currentIndex) &&
    typeof p.currentSongId === "number"
  );
}

export function indexOfSong(state: PlaylistState, songId: number): number {
  return state.songs.findIndex((s) => s.id === songId);
}

export function currentSong(state: PlaylistState | null): SongView | null {
  if (!state || state.songs.length === 0) return null;
  return state.songs[state.currentIndex] ?? null;
}

/**
 * 下一首索引。
 * @returns 下一个索引；null 表示「按当前播放模式到此为止」（顺序模式且已是末首）。
 *          repeat==='one' 返回当前索引，由调用方决定是否真的重播。
 */
export function nextIndex(
  state: PlaylistState,
  prefs: PlaybackPrefs,
): number | null {
  const { songs, currentIndex } = state;
  if (songs.length === 0) return null;

  if (prefs.repeat === "one") return currentIndex;

  if (prefs.shuffle) {
    if (songs.length === 1) {
      return prefs.repeat === "all" ? 0 : null;
    }
    // 随机换一首：排除「与当前相同」以避免点了下一首却纹丝不动
    let pick = currentIndex;
    while (pick === currentIndex) {
      pick = Math.floor(Math.random() * songs.length);
    }
    return pick;
  }

  const next = currentIndex + 1;
  if (next >= songs.length) {
    return prefs.repeat === "all" ? 0 : null; // null = 列表播放结束
  }
  return next;
}

/** 上一首索引。顺序模式到头即绕回末尾（与上游一致）。 */
export function prevIndex(state: PlaylistState): number {
  const { songs, currentIndex } = state;
  if (songs.length === 0) return -1;
  if (currentIndex > 0) return currentIndex - 1;
  return songs.length - 1;
}

/** 下一首（不改状态）。null = 无可播放的下一首。 */
export function nextSong(
  state: PlaylistState | null,
  prefs: PlaybackPrefs,
): SongView | null {
  if (!state) return null;
  const i = nextIndex(state, prefs);
  if (i == null) return null;
  return state.songs[i] ?? null;
}

export function canPlayNext(
  state: PlaylistState | null,
  prefs: PlaybackPrefs,
): boolean {
  if (!state || state.songs.length === 0) return false;
  if (prefs.repeat === "one" || prefs.repeat === "all") return true;
  if (prefs.shuffle) return state.songs.length > 1;
  return state.currentIndex < state.songs.length - 1;
}

export function canPlayPrevious(state: PlaylistState | null): boolean {
  if (!state || state.songs.length <= 1) return false;
  return true;
}

/**
 * 就地替换列表中的一首（例如续写/翻唱/发布后后端返回了新对象）。
 * 索引保持不变，只换掉歌曲内容 —— 否则用户点一次「续写」就会被踢回列表第一首。
 */
export function replaceSong(
  state: PlaylistState,
  updated: SongView,
): PlaylistState {
  const i = indexOfSong(state, updated.id);
  if (i === -1) return state;
  const songs = [...state.songs];
  songs[i] = updated;
  return { ...state, songs, lastUpdated: nowIso() };
}

/** Fisher-Yates 洗牌；当前曲目洗完仍回到原位置，避免「点随机后正在播的被换走」。 */
export function shufflePlaylist(state: PlaylistState): PlaylistState {
  if (state.songs.length <= 1) return state;
  const current = currentSong(state);
  const shuffled = [...state.songs];
  for (let i = shuffled.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [shuffled[i], shuffled[j]] = [shuffled[j], shuffled[i]];
  }
  const keepId = current?.id ?? state.currentSongId;
  // 极端情况：列表里所有歌都没有 id（后端异常）→ 洗牌后无从追踪当前曲目，退回索引 0
  const next = keepId == null ? 0 : indexOfSong({ ...state, songs: shuffled }, keepId);
  return {
    ...state,
    songs: shuffled,
    currentIndex: next >= 0 ? next : 0,
    currentSongId: keepId,
    lastUpdated: nowIso(),
  };
}
