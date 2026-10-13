"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { api, SongView } from "@/lib/api";
import { trackSearch } from "@/lib/eventReporter";
import Player from "@/components/Player";

const PAGE = 20;

type SortKey = "hot" | "new" | "top";

/* 单色描边图标（黑白体系：不用 emoji，统一 currentColor） */
function IconHot() {
  return (
    <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M3 17l6-6 4 4 8-8" />
      <path d="M21 7h-5M21 7v5" />
    </svg>
  );
}
function IconNew() {
  return (
    <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v5l3 2" />
    </svg>
  );
}
function IconTop() {
  return (
    <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M12 3.5l2.6 5.3 5.9.9-4.3 4.1 1 5.8L12 17l-5.2 2.6 1-5.8L3.5 9.7l5.9-.9z" />
    </svg>
  );
}
function IconSearch() {
  return (
    <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="11" cy="11" r="7" />
      <path d="M21 21l-4.3-4.3" />
    </svg>
  );
}
function IconPlaza() {
  return (
    <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <rect x="3" y="3" width="7" height="7" rx="1" />
      <rect x="14" y="3" width="7" height="7" rx="1" />
      <rect x="3" y="14" width="7" height="7" rx="1" />
      <rect x="14" y="14" width="7" height="7" rx="1" />
    </svg>
  );
}
function IconFav() {
  return (
    <svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M6 3h12a1 1 0 0 1 1 1v17l-7-4-7 4V4a1 1 0 0 1 1-1z" />
    </svg>
  );
}

const SORTS: { key: SortKey; label: string; Icon: () => JSX.Element }[] = [
  { key: "hot", label: "热门", Icon: IconHot },
  { key: "new", label: "最新", Icon: IconNew },
  { key: "top", label: "高分", Icon: IconTop },
];

/** 星标评分控件：1–5 星，点击即可评分 / 改分。广场不做留言，只保留无文本互动。 */
function StarRating({
  value,
  onRate,
  disabled,
  songTitle,
}: {
  value: number | null | undefined;
  onRate: (n: number) => void;
  disabled?: boolean;
  songTitle?: string | null;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const shown = hover ?? value ?? 0;
  const who = songTitle ? `《${songTitle}》` : "本曲";
  return (
    <span
      className="inline-flex items-center gap-0.5"
      role="group"
      aria-label={`${who}评分（当前 ${value ?? 0} 星）`}
    >
      {[1, 2, 3, 4, 5].map((n) => (
        <button
          key={n}
          type="button"
          disabled={disabled}
          onMouseEnter={() => setHover(n)}
          onMouseLeave={() => setHover(null)}
          onClick={() => onRate(n)}
          className={`text-base leading-none transition disabled:opacity-50 ${
            n <= shown ? "text-[var(--ink)]" : "text-[var(--muted)]"
          } ${!disabled && "hover:scale-110"}`}
          aria-label={`给${who}打 ${n} 星`}
          aria-pressed={value === n}
        >
          ★
        </button>
      ))}
    </span>
  );
}

/** 单首歌的互动条：作者 / 赞 / 收藏 / 星标。无文本互动，保持极简。 */
function InteractRow({
  song,
  busy,
  onLike,
  onFavorite,
  onRate,
}: {
  song: SongView;
  busy: boolean;
  onLike: () => void;
  onFavorite: () => void;
  onRate: (n: number) => void;
}) {
  return (
    <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2 border-t border-[var(--border)] pt-3 text-sm">
      {/* GAP-015：作者名可点进公开资料页（/api/users/{id}）。
          author_id 缺失时（老数据/异常行）退化为纯文本 —— 宁可不可点，
          也不要造一个 href="/u/undefined" 的死链接。 */}
      {song.author_id != null ? (
        <Link
          href={`/u/${song.author_id}`}
          className="text-[var(--muted)] underline-offset-4 transition hover:text-[var(--ink)] hover:underline"
          aria-label={`查看 ${song.author_name || "作者"} 的主页`}
        >
          {song.author_name ? `by ${song.author_name}` : "社区作品"}
        </Link>
      ) : (
        <span className="text-[var(--muted)]">
          {song.author_name ? `by ${song.author_name}` : "社区作品"}
        </span>
      )}

      <button
        onClick={onLike}
        disabled={busy}
        className={`transition disabled:opacity-50 ${
          song.liked_by_me
            ? "text-[var(--ink)]"
            : "text-[var(--muted)] hover:text-[var(--ink-soft)]"
        }`}
        title="点赞（可取消）"
        aria-label={`${song.liked_by_me ? "取消点赞" : "点赞"}（当前 ${song.like_count || 0} 赞）`}
        aria-pressed={!!song.liked_by_me}
      >
        {song.liked_by_me ? "♥" : "♡"} {song.like_count || 0}
      </button>

      <button
        onClick={onFavorite}
        disabled={busy}
        className={`transition disabled:opacity-50 ${
          song.favorited_by_me
            ? "text-[var(--ink)]"
            : "text-[var(--muted)] hover:text-[var(--ink-soft)]"
        }`}
        title="收藏（可取消）"
        aria-label={`${song.favorited_by_me ? "取消收藏" : "收藏"}（当前 ${song.favorite_count || 0} 次收藏）`}
        aria-pressed={!!song.favorited_by_me}
      >
        {song.favorited_by_me ? "★已藏" : "☆收藏"} {song.favorite_count || 0}
      </button>

      <span className="flex items-center gap-1.5">
        <StarRating
          value={song.my_rating}
          disabled={busy}
          onRate={onRate}
          songTitle={song.title}
        />
        <span className="text-xs text-[var(--muted)]">
          {song.rating_count
            ? `${song.rating_avg?.toFixed(1)} (${song.rating_count})`
            : "暂无评分"}
        </span>
      </span>
    </div>
  );
}

export default function PlazaPage() {
  const [songs, setSongs] = useState<SongView[]>([]);
  const [sort, setSort] = useState<SortKey>("hot");
  const [view, setView] = useState<"feed" | "favorites">("feed");
  const [query, setQuery] = useState("");
  const [q, setQ] = useState(""); // 防抖后的实际查询词
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [offset, setOffset] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [busyId, setBusyId] = useState<number | null>(null);
  // 当前视图 ref + 请求序号：异步返回时按「当下」视图判定（防陈旧闭包），过期响应直接丢弃（防竞态覆盖）
  const viewRef = useRef<"feed" | "favorites">("feed");
  const reqSeq = useRef(0);

  useEffect(() => {
    viewRef.current = view;
  }, [view]);

  // 输入停止 300ms 后再触发搜索
  useEffect(() => {
    const t = setTimeout(() => setQ(query.trim()), 300);
    return () => clearTimeout(t);
  }, [query]);

  async function load(initial = false) {
    const seq = ++reqSeq.current;
    if (initial && q) trackSearch(q);
    if (initial) setLoading(true);
    else setLoadingMore(true);
    setError("");
    try {
      const next = initial ? 0 : offset;
      const data = await api.plaza({ sort, limit: PAGE, offset: next, q });
      if (seq !== reqSeq.current) return; // 过期响应（视图/条件已变）→ 丢弃，不覆盖新视图
      setSongs((prev) => (initial ? data : [...prev, ...data]));
      setOffset(next + data.length);
      setHasMore(data.length === PAGE);
    } catch (e: any) {
      if (seq !== reqSeq.current) return;
      if (e?.status === 401) setError("登录已过期或无效，请重新登录（右上角）。");
      else setError(e?.message || "加载失败");
    } finally {
      if (seq !== reqSeq.current) return;
      if (initial) setLoading(false);
      else setLoadingMore(false);
    }
  }

  async function loadFavorites(initial = true) {
    const seq = ++reqSeq.current;
    if (initial) setLoading(true);
    setError("");
    try {
      const data = await api.myFavorites();
      if (seq !== reqSeq.current) return; // 过期响应 → 丢弃
      setSongs(data);
      setOffset(data.length);
      setHasMore(false); // 收藏接口单次返回（服务端 limit=50），前端不分页
    } catch (e: any) {
      if (seq !== reqSeq.current) return;
      if (e?.status === 401)
        setError("请先登录后再查看收藏（右上角）。浏览广场无需登录。");
      else setError(e?.message || "加载收藏失败");
    } finally {
      if (seq !== reqSeq.current) return;
      if (initial) setLoading(false);
    }
  }

  useEffect(() => {
    if (view === "favorites") loadFavorites(true);
    else load(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q, sort, view]);

  // 登录/登出后刷新当前视图：登录态影响 liked_by_me/favorited_by_me 回显，
  // 也让「停在收藏视图的 401 错误态」在登录后自动恢复
  useEffect(() => {
    const onAuthChanged = () => {
      if (view === "favorites") loadFavorites(true);
      else load(true);
    };
    window.addEventListener("auth:changed", onAuthChanged);
    return () => window.removeEventListener("auth:changed", onAuthChanged);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q, sort, view]);

  /** 互动后局部替换该首，避免整页刷新（乐观体验 + 服务端为唯一真源） */
  function replace(s: SongView) {
    setSongs((prev) => {
      // 收藏视图下取消收藏 → 直接从列表移除（服务端为唯一真源）。
      // ⚠ 用 viewRef 而非闭包 view：响应可能在用户已切回广场后才返回，
      //   陈旧闭包会把「从收藏移除」误施于广场列表（广场本就包含未收藏的公开作品）。
      if (viewRef.current === "favorites" && !s.favorited_by_me) {
        return prev.filter((x) => x.id !== s.id);
      }
      return prev.map((x) => (x.id === s.id ? s : x));
    });
  }

  async function act(id: number, fn: () => Promise<SongView>) {
    setBusyId(id);
    try {
      replace(await fn());
    } catch (e: any) {
      // 广场可匿名浏览，但互动必须登录 — 401 은 "세션 만료" 가 아니라 "미로그인" 이다.
      setError(
        e?.status === 401
          ? "点赞 / 收藏 / 评分需要先登录（右上角）。浏览作品无需登录。"
          : e?.message || "操作失败"
      );
    } finally {
      setBusyId(null);
    }
  }

  return (
    <main className="mx-auto max-w-5xl px-4 py-8">
      {/* ── Hero ── */}
      <header className="mb-7">
        <div className="flex items-center gap-2.5">
          <span className="eq text-[var(--ink)]" aria-hidden="true">
            <span /><span /><span /><span /><span />
          </span>
          <h1 className="text-3xl font-bold tracking-tight text-[var(--ink)]">
            FLOW广场
          </h1>
        </div>
        <p className="mt-1 text-sm text-[var(--muted)]">
          FLOW Plaza · 聆听社区里每个人创作的音乐
        </p>
      </header>

      {/* ── 视图切换：广场 / 我的收藏 ── */}
      <div className="mb-5 flex flex-wrap gap-2">
        <button
          onClick={() => setView("feed")}
          className="chip inline-flex items-center gap-1.5 rounded-full px-3 py-1.5 text-sm"
          data-on={view === "feed"}
          aria-pressed={view === "feed"}
        >
          <IconPlaza />
          广场
        </button>
        <button
          onClick={() => setView("favorites")}
          className="chip inline-flex items-center gap-1.5 rounded-full px-3 py-1.5 text-sm"
          data-on={view === "favorites"}
          aria-pressed={view === "favorites"}
        >
          <IconFav />
          我的收藏
        </button>
      </div>

      {/* ── 控制条：排序 + 搜索（仅广场视图） ── */}
      {view === "feed" && (
      <div className="mb-6 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex flex-wrap gap-2">
          {SORTS.map((s) => {
            const Icon = s.Icon;
            return (
              <button
                key={s.key}
                onClick={() => setSort(s.key)}
                className="chip inline-flex items-center gap-1.5 rounded-full px-3 py-1.5 text-sm"
                data-on={sort === s.key}
                aria-pressed={sort === s.key}
              >
                <Icon />
                {s.label}
              </button>
            );
          })}
        </div>

        <div className="relative sm:w-72">
          <span className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-[var(--muted)]">
            <IconSearch />
          </span>
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="搜索标题或歌词…"
            className="input pl-9"
            aria-label="搜索作品"
          />
        </div>
      </div>
      )}

      {error && (
        <p className="mb-4 text-sm text-[var(--danger)]" role="alert">
          {error}
        </p>
      )}

      {/* ── 骨架屏（首屏加载） ── */}
      {loading && songs.length === 0 && (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          {[0, 1, 2, 3].map((i) => (
            <div key={i} className="card p-4">
              <div className="animate-pulse space-y-3">
                <div className="h-4 w-2/3 rounded bg-[var(--surface-2)]" />
                <div className="h-24 rounded bg-[var(--surface-2)]" />
                <div className="h-3 w-1/3 rounded bg-[var(--surface-2)]" />
              </div>
            </div>
          ))}
        </div>
      )}

      {/* ── 空态：我的收藏 ── */}
      {!loading && songs.length === 0 && !error && view === "favorites" && (
        <div className="card p-8 text-center">
          <p className="text-[var(--muted)]">
            你还没有收藏任何作品。去广场点亮 ☆ 收藏，它们会出现在这里。
          </p>
        </div>
      )}

      {/* ── 空态：广场 ── */}
      {!loading && songs.length === 0 && !error && view === "feed" && (
        <div className="card p-8 text-center">
          <p className="text-[var(--muted)]">
            {q
              ? "没有匹配的作品。"
              : "广场里还没有作品，去「我的作品」发布一首吧。"}
          </p>
        </div>
      )}

      {/* ── 作品网格 ── */}
      {songs.length > 0 && (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          {songs.map((s, i) => {
            const rank = view === "feed" && sort === "hot" ? i : -1;
            return (
              <article
                key={s.id}
                className="card lift enter p-4"
                aria-label={`社区作品《${s.title || "未命名"}》`}
                style={{ animationDelay: `${Math.min(i, 6) * 50}ms` }}
              >
                {rank >= 0 && rank < 3 && (
                  <div className="mb-2 flex items-center gap-2">
                    <span
                      className="inline-flex h-6 min-w-6 items-center justify-center rounded-full px-2 text-xs font-bold"
                      data-top={rank === 0}
                      style={
                        rank === 0
                          ? {
                              background: "var(--btn-solid-bg)",
                              color: "var(--btn-solid-fg)",
                            }
                          : {
                              border: "1px solid var(--border-strong)",
                              color: "var(--ink-soft)",
                            }
                      }
                    >
                      #{rank + 1}
                    </span>
                    <span className="text-xs text-[var(--muted)]">本周热门</span>
                  </div>
                )}

                <Player
                  song={s}
                  isOwner={s.is_owner ?? false}
                  onSpawn={(u) => replace(u)}
                  queue={songs}
                  onReplaceSong={replace}
                />

                <InteractRow
                  song={s}
                  busy={busyId === s.id}
                  onLike={() => act(s.id, () => api.likeSong(s.id))}
                  onFavorite={() => act(s.id, () => api.favoriteSong(s.id))}
                  onRate={(n) => act(s.id, () => api.rateSong(s.id, n))}
                />
              </article>
            );
          })}
        </div>
      )}

      {hasMore && songs.length > 0 && view === "feed" && (
        <button
          onClick={() => load(false)}
          disabled={loadingMore}
          className="btn btn-secondary mt-6 w-full"
          aria-busy={loadingMore}
        >
          {loadingMore ? "加载中…" : "加载更多"}
        </button>
      )}
    </main>
  );
}
