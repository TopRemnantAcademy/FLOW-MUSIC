"use client";

/**
 * 固定底部播放条
 *
 * 参考（不是照搬）vendor/self-music/frontend/src/components/bottom-player.tsx 的布局骨架：
 * 左=封面标题、中=传输控件、右=队列模式与音量。上游那份依赖 framer-motion / lucide-react /
 * shadcn Button，我们都没有，所以只取「三栏 + 顶部一条可点进度条」这个骨架，样式全部用
 * 本仓库既有的 CSS 变量与 .btn-* 类重写。
 *
 * 播放由 lib/audioBus 的全局音频元素承载，这里只发指令 + 反映状态；
 * 上下首调 lib/playback 的 advance/back，队列一前移，对应卡片的 <Player> 会自己接手起播。
 */

import type { PointerEvent as ReactPointerEvent } from "react";
import type { SongView } from "@/lib/api";
import {
  advance,
  back,
  cycleRepeat,
  toggleShuffle,
  usePlayback,
} from "@/lib/playback";
import { currentSong } from "@/lib/playlist";
import {
  busSeek,
  busSetVolume,
  busToggle,
  busToggleMute,
  fmtClock,
  useAudioStatus,
  useAudioTime,
} from "@/lib/audioBus";

export default function BottomPlayer() {
  const { playlist, prefs } = usePlayback();
  const { playing, duration, volume, muted } = useAudioStatus();
  const t = useAudioTime();

  const song: SongView | null = playlist ? currentSong(playlist) : null;
  if (!playlist || !song) return null;

  const pct = duration > 0 ? Math.min(100, Math.max(0, (t / duration) * 100)) : 0;
  const pos = playlist.currentIndex + 1;

  const seekFromPointer = (e: ReactPointerEvent<HTMLDivElement>) => {
    const el = e.currentTarget;
    const rect = el.getBoundingClientRect();
    if (rect.width <= 0 || duration <= 0) return;
    const ratio = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width));
    busSeek(ratio * duration);
  };

  return (
    <>
    <div className="fixed inset-x-0 bottom-0 z-30 border-t border-[var(--border)] bg-[var(--bg)]/95 backdrop-blur">
      {/* 进度条：整条可点可拖；不单独放 range 是因为移动端 thumb 太小，点更准 */}
      <div
        role="slider"
        tabIndex={0}
        aria-label="播放进度"
        aria-valuemin={0}
        aria-valuemax={Math.round(duration)}
        aria-valuenow={Math.round(t)}
        aria-valuetext={`${fmtClock(t)} / ${fmtClock(duration)}`}
        onClick={seekFromPointer}
        className="h-1.5 w-full cursor-pointer bg-[var(--border)]"
      >
        <div
          className="h-full bg-[var(--ink)] transition-[width] duration-150 ease-out"
          style={{ width: `${pct}%` }}
        />
      </div>

      <div className="mx-auto flex max-w-5xl items-center gap-3 px-4 py-2">
        {/* 左：封面 + 曲目 */}
        <div className="flex min-w-0 flex-1 items-center gap-2">
          {song.cover_url ? (
            <img
              src={song.cover_url}
              alt=""
              loading="lazy"
              onError={(e) => {
                (e.currentTarget as HTMLImageElement).style.display = "none";
              }}
              className="h-10 w-10 shrink-0 rounded-md border border-[var(--border)] object-cover"
            />
          ) : (
            <div className="h-10 w-10 shrink-0 rounded-md border border-[var(--border)]" />
          )}
          <div className="min-w-0">
            <p className="truncate text-sm font-medium">{song.title || "未命名"}</p>
            <p className="truncate text-xs text-[var(--muted)]">队列 {pos}/{playlist.songs.length}</p>
          </div>
        </div>

        {/* 中：传输控件 */}
        <div className="flex items-center gap-1.5">
          <button
            type="button"
            onClick={back}
            className="btn btn-ghost btn-sm"
            aria-label="上一首"
          >
            上一首
          </button>
          <button
            type="button"
            onClick={busToggle}
            className="btn btn-primary btn-sm min-w-[4.5rem]"
            aria-label={playing ? "暂停" : "播放"}
          >
            {playing ? "暂停" : "播放"}
          </button>
          <button
            type="button"
            onClick={advance}
            className="btn btn-ghost btn-sm"
            aria-label="下一首"
          >
            下一首
          </button>
        </div>

        {/* 右：时间 / 队列模式 / 音量 */}
        <div className="flex flex-1 items-center justify-end gap-2">
          <span className="hidden text-xs tabular-nums text-[var(--muted)] sm:inline">
            {fmtClock(t)} / {fmtClock(duration)}
          </span>
          <button
            type="button"
            onClick={busToggleMute}
            className="btn btn-ghost btn-sm"
            aria-label={muted ? "取消静音" : "静音"}
            aria-pressed={muted}
          >
            {muted ? "已静音" : "音量"}
          </button>
          <input
            type="range"
            min={0}
            max={1}
            step={0.01}
            value={muted ? 0 : volume}
            onChange={(e) => busSetVolume(Number(e.target.value))}
            aria-label="音量"
            className="hidden h-1 w-24 cursor-pointer accent-[var(--ink)] sm:block"
          />
          <button
            type="button"
            onClick={toggleShuffle}
            className="btn btn-ghost btn-sm"
            aria-label="随机"
            aria-pressed={prefs.shuffle}
          >
            随机{prefs.shuffle ? "开" : "关"}
          </button>
          <button
            type="button"
            onClick={cycleRepeat}
            className="btn btn-ghost btn-sm"
            aria-label="循环模式"
            title="点击切换：关 → 列表循环 → 单曲循环"
          >
            {prefs.repeat === "none" ? "循环关" : prefs.repeat === "all" ? "列表循环" : "单曲循环"}
          </button>
        </div>
      </div>
    </div>
    {/* 固定条不占位，用一条等高的空 div 把正文顶上来，否则最后一张卡会被压在条下面 */}
    <div aria-hidden="true" style={{ height: "5.5rem" }} />
    </>
  );
}
