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

import type { KeyboardEvent as ReactKeyboardEvent, PointerEvent as ReactPointerEvent } from "react";
import type { SongView } from "@/lib/api";
import {
  AB_MIN_SPAN,
  activeAbLoop,
  advance,
  back,
  clearAb,
  cycleRepeat,
  setAbA,
  setAbB,
  toggleShuffle,
  usePlayback,
} from "@/lib/playback";
import { currentSong } from "@/lib/playlist";
import Waveform from "@/components/Waveform";
import {
  busSeek,
  busSetAbLoop,
  busSetVolume,
  busToggle,
  busToggleMute,
  fmtClock,
  useAudioStatus,
  useAudioTime,
} from "@/lib/audioBus";
import { useEffect, useState } from "react";

// 循环模式中文名：状态要能被读屏单独读出来，而不只是一个可点按钮
const REPEAT_CN: Record<string, string> = {
  none: "关闭",
  all: "列表循环",
  one: "单曲循环",
};

export default function BottomPlayer() {
  const { playlist, prefs, ab } = usePlayback();
  const { playing, duration, volume, muted, src: busStatusSrc } = useAudioStatus();
  const t = useAudioTime();
  const [abHint, setAbHint] = useState("");

  // AB 区间 → 音频总线回绕钩子。放 effect 里同步：区间可能来自 localStorage 恢复、
  // 也可能来自上一次会话，不能只在按钮点击时推给总线。
  const loop = activeAbLoop(ab);
  useEffect(() => {
    busSetAbLoop(loop);
    return () => busSetAbLoop(null);
  }, [loop?.a, loop?.b]); // eslint-disable-line react-hooks/exhaustive-deps

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

// ── GAP-013 AB 重复 ────────────────────────────────────────────────────
  // 交互刻意做得「无聊」：A 就是打 A，B 就是打 B。不搞「第二次按同一键当 B」的
  // 状态复用 —— 那种设计在 AB 关掉后按 A 会莫名其妙造出区间。
  const onMarkA = () => {
    setAbA(t);
    setAbHint(`A 点已设在 ${fmtClock(t)}`);
  };
  const onMarkB = () => {
    if (!ab) {
      setAbHint("请先打 A 点");
      return;
    }
    if (!setAbB(t)) {
      setAbHint(`B 点需在 A 点之后至少 ${AB_MIN_SPAN}s`);
      return;
    }
    setAbHint(`循环 ${fmtClock(ab.a)} → ${fmtClock(t)}`);
  };
  const onClearAb = () => {
    clearAb();
    setAbHint("");
  };

  // a11y（GAP-024）：进度条是 div[role=slider]，必须能用键盘操作。
  // ←/→ 5 秒；↑/↓ 10 秒；Home/End 到首尾；PageUp/PageDown 30 秒，
  // 与原生 range 的键盘语义对齐（见 docs/A11Y_CHECKLIST.md）。
  const seekBy = (delta: number) => {
    if (duration <= 0) return;
    busSeek(Math.min(duration, Math.max(0, t + delta)));
  };
  const seekFromKey = (e: ReactKeyboardEvent<HTMLDivElement>) => {
    const map: Record<string, number> = {
      ArrowRight: 5,
      ArrowLeft: -5,
      ArrowUp: 10,
      ArrowDown: -10,
      PageUp: 30,
      PageDown: -30,
    };
    if (e.key in map) {
      e.preventDefault();
      seekBy(map[e.key]);
      return;
    }
    if (e.key === "Home") {
      e.preventDefault();
      busSeek(0);
    } else if (e.key === "End") {
      e.preventDefault();
      busSeek(duration);
    }
  };
  };

  return (
    <>
    {/* 播放状态改由下方 aria-live 播报，图标/文字本身对读屏隐藏以免重复朗读 */}
    <span className="sr-only" role="status" aria-live="polite">
      {playing ? "正在播放" : "已暂停"}：{song.title || "未命名"}（队列 {pos}/{playlist.songs.length}）
    </span>
    <div className="fixed inset-x-0 bottom-0 z-30 border-t border-[var(--border)] bg-[var(--bg)]/95 backdrop-blur">
      {/* 波形 seek（GAP-013）：可点击/拖动即跳转，复用同一个 audioBus 元素。
          ⚠️ 这里**没有传 peaksUrl** —— 底部条播的是任意曲目，而峰值端点目前只在
          `routers/share.py`（`/api/share/songs/{token}/peaks`，需分享 token）。
          给「任意曲目」开一个 `/api/songs/{id}/peaks` 需要改 `songs.py`，
          本批次该文件被划为不可改（并行任务冲突面），故这里如实退化为
          「进度条 seek」而不是画一根编出来的假波形。
          波形能力在**分享页**（`/share/[token]`）是完整可用的。 */}
      <Waveform src={busStatusSrc} height={44} label={`${song.title || "曲目"} 波形`} peaksUrl={null} />

      {/* 进度条：整条可点可拖；不单独放 range 是因为移动端 thumb 太小，点更准 */}
      <div
        role="slider"
        tabIndex={0}
        aria-label="播放进度"
        aria-valuemin={0}
        aria-valuemax={Math.round(duration)}
        aria-valuenow={Math.round(t)}
        // 5s 一格 = 进度条的可访问粒度（不是 1 秒，否则按一次跳 1 秒太慢）
        aria-valuetext={`${fmtClock(t)} / ${fmtClock(duration)}`}
        onClick={seekFromPointer}
        onKeyDown={seekFromKey}
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
            aria-label={muted ? "取消静音（当前已静音）" : "静音"}
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
          {/* AB 重复：练唱/编曲比对。三个按钮常驻但语义单一，
              比「一个按钮循环切换 A→B→关」更好按 —— 后者在手机上极容易按错到清空。 */}
          <button
            type="button"
            onClick={onMarkA}
            className={`btn btn-sm ${ab ? "btn-primary" : "btn-ghost"}`}
            aria-label="设置 A 点"
            title="在当前位置打 A 点（区间起点）"
          >
            A
          </button>
          <button
            type="button"
            onClick={onMarkB}
            className={`btn btn-sm ${loop ? "btn-primary" : "btn-ghost"}`}
            aria-label="设置 B 点"
            title={`在当前位置打 B 点（区间终点，须比 A 点晚至少 ${AB_MIN_SPAN}s）`}
          >
            B
          </button>
          <button
            type="button"
            onClick={onClearAb}
            disabled={!ab}
            className="btn btn-ghost btn-sm"
            aria-label="清除 AB 区间"
            title="清除 AB 区间"
          >
            AB 清
          </button>
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
            aria-label={`循环模式：${REPEAT_CN[prefs.repeat] ?? "关"}`}
            title="点击切换：关 → 列表循环 → 单曲循环"
          >
            {prefs.repeat === "none" ? "循环关" : prefs.repeat === "all" ? "列表循环" : "单曲循环"}
          </button>
        </div>
      </div>

      {/* AB 状态/提示行：只在有区间或有提示时占位，平时不占垂直空间 */}
      {(loop || ab || abHint) && (
        <p className="mx-auto max-w-5xl px-4 pb-1 text-xs text-[var(--muted)]">
          {loop
            ? `AB 循环中 ${fmtClock(loop.a)} → ${fmtClock(loop.b)}`
            : ab
            ? `A 点 ${fmtClock(ab.a)}（待设 B 点）`
            : ""}
          {abHint ? `${loop || ab ? " · " : ""}${abHint}` : ""}
        </p>
      )}
    </div>
    {/* 固定条不占位，用一条等高的空 div 把正文顶上来，否则最后一张卡会被压在条下面 */}
    <div aria-hidden="true" style={{ height: "5.5rem" }} />
    </>
  );
}
