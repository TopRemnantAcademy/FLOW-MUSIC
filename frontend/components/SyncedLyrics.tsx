"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type { RefObject } from "react";
import { flattenSync, findActiveWordIndex, findActiveLineIndex } from "@/lib/syncedLyrics";

type Props = {
  /** 播放器 <audio> 元素引用（用于读取 currentTime / 跳转） */
  audioRef: RefObject<HTMLAudioElement | null>;
  /** 后端 lyrics_sync 原始 JSON 字符串；为空则走降级 */
  raw: string | null | undefined;
  /** 降级用的纯文本歌词（无逐字数据时显示） */
  plainLyric?: string | null;
};

type View = { line: number; word: number };

export default function SyncedLyrics({ audioRef, raw, plainLyric }: Props) {
  // 仅 raw 变化时重新 flatten（纯函数、幂等）
  const data = useMemo(() => flattenSync(raw ?? ""), [raw]);
  // O2：按行预分组，避免渲染时 data.lines × data.words 的 O(n²) filter
  const wordsByLine = useMemo(() => {
    const m = new Map<number, typeof data.words>();
    for (const w of data.words) {
      const arr = m.get(w.line);
      if (arr) arr.push(w);
      else m.set(w.line, [w]);
    }
    return m;
  }, [data.words]);

  const [view, setView] = useState<View>({ line: -1, word: -1 });
  const lineRefs = useRef<(HTMLParagraphElement | null)[]>([]);
  const activeRef = useRef<View>({ line: -1, word: -1 });

  // rAF 时钟：播放期间逐帧计算当前词/行，仅在状态变化时 setState（避免无谓重渲染）
  useEffect(() => {
    const audio = audioRef.current;
    if (!audio || !data.ok) return;
    let raf = 0;

    const tick = () => {
      const t = audio.currentTime * 1000;
      const w = findActiveWordIndex(data.words, t, activeRef.current.word);
      const l = w >= 0 ? data.words[w].line : findActiveLineIndex(data.lines, t, activeRef.current.line);
      if (w !== activeRef.current.word || l !== activeRef.current.line) {
        activeRef.current = { line: l, word: w };
        setView({ line: l, word: w });
      }
      raf = requestAnimationFrame(tick);
    };
    const onPlay = () => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(tick);
    };
    const onStop = () => cancelAnimationFrame(raf); // pause / ended

    audio.addEventListener("play", onPlay);
    audio.addEventListener("pause", onStop);
    audio.addEventListener("ended", onStop);
    if (!audio.paused) raf = requestAnimationFrame(tick);

    return () => {
      cancelAnimationFrame(raf);
      audio.removeEventListener("play", onPlay);
      audio.removeEventListener("pause", onStop);
      audio.removeEventListener("ended", onStop);
    };
  }, [data, audioRef]);

  // 当前行滚动到可视区中心
  useEffect(() => {
    if (view.line >= 0) {
      lineRefs.current[view.line]?.scrollIntoView({ block: "center", behavior: "smooth" });
    }
  }, [view.line]);

  const seekTo = (ms: number) => {
    const audio = audioRef.current;
    if (!audio || ms == null) return;
    audio.currentTime = ms / 1000;
    audio.play().catch(() => {});
  };

  const activeWi = view.word >= 0 ? data.words[view.word].wi : -1;

  // O1 修复：降级返回必须放在所有 hook 之后。
  // 原先在 data.ok 为假时提前 return，会导致 data.ok 在渲染间翻转时
  // hook 调用数量 4↔6 变化 → React “Rendered fewer hooks than expected” 崩溃。
  // 触发场景：广场卡片经 enrichPlazaLyrics 注入 lyrics_sync（raw null→有效）造成翻转。
  if (!data.ok) {
    return plainLyric ? (
      <pre className="mt-3 whitespace-pre-wrap text-sm text-[var(--muted)]">{plainLyric}</pre>
    ) : null;
  }

  // 当前行文本：供下方 aria-live 播报（读屏用户无法从视觉高亮得知"唱到哪了"）
  const activeLineText = view.line >= 0 ? data.lines[view.line]?.text ?? "" : "";

  return (
    <>
      {/* 歌词区状态播报：行变化时才更新（view.line 去重见上方 rAF 时钟） */}
      <p className="sr-only" role="status" aria-live="polite" aria-atomic="true">
        {activeLineText ? `当前歌词：${activeLineText}` : ""}
      </p>
    <div className="sl-lyrics" role="group" aria-label="逐字同步歌词（可按行/按词跳转）">
      {data.lines.map((ln, li) => {
        const lineWords = wordsByLine.get(li) ?? [];
        const isActiveLine = li === view.line;
        return (
          <div
            key={li}
            role="button"
            ref={(el) => {
              lineRefs.current[li] = el;
            }}
            className={`sl-line${isActiveLine ? " is-active" : ""}`}
            onClick={() => seekTo(ln.start)}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                seekTo(ln.start);
              }
            }}
            tabIndex={0}
            aria-current={isActiveLine ? "true" : undefined}
            title="回车跳转到本行"
          >
            {lineWords.length === 0 ? (
              <span className="sl-word sl-word--done">{ln.text}</span>
            ) : (
              lineWords.map((w) => {
                let state = "todo";
                if (w.line < view.line) state = "done";
                else if (w.line > view.line) state = "todo";
                else if (w.wi < activeWi) state = "done";
                else if (w.wi === activeWi) state = "cur";
                const cls =
                  state === "cur"
                    ? "sl-word sl-word--cur"
                    : state === "done"
                    ? "sl-word sl-word--done"
                    : "sl-word";
                return (
                  <span
                    key={w.wi}
                    role="button"
                    tabIndex={0}
                    className={cls}
                    onClick={(e) => {
                      e.stopPropagation();
                      seekTo(w.start);
                    }}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        e.stopPropagation();
                        seekTo(w.start);
                      }
                    }}
                    aria-current={state === "cur" ? "true" : undefined}
                    title={state === "cur" ? "正在播放，回车重播本词" : "回车跳转到本词"}
                  >
                    {w.text}
                    {" "}
                  </span>
                );
              })
            )}
          </div>
        );
      })}
    </div>
    </>
  );
}
