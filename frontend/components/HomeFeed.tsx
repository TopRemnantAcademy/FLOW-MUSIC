"use client";

import { useEffect, useRef, useState, type CSSProperties } from "react";
import Link from "next/link";
import { api, SongView } from "@/lib/api";
import Player from "@/components/Player";

const PAGE = 12;

type SortKey = "hot" | "new" | "top";

// 排序口径与 /plaza 对齐（plaza/page.tsx SORTS）；黑白体系下去掉 emoji
const SORTS: { key: SortKey; label: string }[] = [
  { key: "hot", label: "热门" },
  { key: "new", label: "最新" },
  { key: "top", label: "高分" },
];

/** 首页「其他人的作品」：下拉可见的广场精选流。
 *  复用 /plaza 的数据面（api.plaza）与播放卡片（Player），
 *  视觉上做滚动入场（.reveal + IntersectionObserver stagger）与悬停浮起（.lift）。 */
export default function HomeFeed() {
  const [sort, setSort] = useState<SortKey>("hot");
  const [tick, setTick] = useState(0); // 手动重试句柄
  const [songs, setSongs] = useState<SongView[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const sectionRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError("");
    api
      .plaza({ sort, limit: PAGE })
      .then((d) => {
        if (alive) setSongs(d);
      })
      .catch((e: any) => {
        if (alive) setError(e?.message || "加载失败");
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [sort, tick]);

  // 滚动入场：卡片进入视口时补 .in（.reveal 定义在 globals.css；错峰用 --reveal-delay）
  useEffect(() => {
    const root = sectionRef.current;
    if (!root) return;
    const els = Array.from(root.querySelectorAll<HTMLElement>(".reveal"));
    if (els.length === 0) return;
    if (typeof IntersectionObserver === "undefined") {
      els.forEach((el) => el.classList.add("in"));
      return;
    }
    const io = new IntersectionObserver(
      (entries) => {
        for (const en of entries) {
          if (en.isIntersecting) {
            en.target.classList.add("in");
            io.unobserve(en.target);
          }
        }
      },
      { threshold: 0.1, rootMargin: "0px 0px -8% 0px" }
    );
    els.forEach((el) => io.observe(el));
    return () => io.disconnect();
  }, [loading, sort, songs]);

  return (
    <section
      ref={sectionRef}
      aria-labelledby="home-feed-title"
      className="mx-auto max-w-5xl px-4 pb-20"
    >
      <div className="mb-6 flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="enter text-xs font-medium uppercase tracking-[0.2em] text-[var(--muted)]">
            FLOW广场
          </p>
          <h2
            id="home-feed-title"
            className="enter enter-delay-1 mt-1 text-2xl font-bold tracking-tight text-[var(--ink)]"
          >
            其他人的作品
          </h2>
        </div>
        <Link
          href="/plaza"
          className="enter enter-delay-2 text-sm text-[var(--muted)] underline-offset-4 transition hover:text-[var(--ink)] hover:underline"
        >
          进广场听更多 →
        </Link>
      </div>

      {loading && (
        <p className="sr-only" role="status" aria-live="polite">
          正在加载广场作品…
        </p>
      )}

      <div className="mb-5 flex flex-wrap gap-2">
        {SORTS.map((s) => (
          <button
            key={s.key}
            type="button"
            onClick={() => setSort(s.key)}
            className="chip rounded-full px-3 py-1.5 text-sm"
            data-on={sort === s.key}
            aria-pressed={sort === s.key}
          >
            {s.label}
          </button>
        ))}
      </div>

      {loading && (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3" aria-hidden="true">
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="card h-56 animate-pulse" />
          ))}
        </div>
      )}

      {!loading && error && (
        <div className="card p-6 text-center">
          <p className="mb-3 text-sm text-[var(--danger)]" role="alert">
            {error}
          </p>
          <button type="button" onClick={() => setTick((t) => t + 1)} className="btn btn-secondary">
            重试
          </button>
        </div>
      )}

      {!loading && !error && songs.length === 0 && (
        <div className="card p-8 text-center">
          <p className="mb-4 text-[var(--muted)]">广场里还没有公开作品 —— 抢先创作第一首。</p>
          <div className="flex flex-wrap items-center justify-center gap-2">
            <Link href="/create" className="btn btn-primary">
              创作第一首
            </Link>
            <Link href="/plaza" className="btn btn-secondary">
              进广场逛逛
            </Link>
          </div>
        </div>
      )}

      {!loading && !error && songs.length > 0 && (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {songs.map((s, i) => (
            <div
              key={s.id}
              className="reveal lift"
              style={{ "--reveal-delay": `${Math.min(i, 8) * 60}ms` } as CSSProperties}
            >
              {sort === "hot" && i < 3 && (
                <p className="mb-1 px-1 text-xs font-semibold tracking-widest text-[var(--ink-soft)]">
                  TOP {i + 1}
                </p>
              )}
              <Player
                song={s}
                queue={songs}
                onReplaceSong={(u) => setSongs((prev) => prev.map((x) => (x.id === s.id ? u : x)))}
              />
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
