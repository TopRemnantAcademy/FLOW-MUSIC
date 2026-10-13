"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { api, ApiError, PublicProfile, SongView } from "@/lib/api";
import Player from "@/components/Player";

const PAGE = 24;

/**
 * 公开资料页（GAP-015 · 只读部分）。
 *
 * 本轮只做**看得见、且服务端已确认安全**的那一半：
 *   公开作品列表 + 昵称占位 + 加入月份 + 获赞总数。
 *
 * ⚠ 关注 / 粉丝 / 留言**没有做**：后端没有 Follow / Comment 表（本批迁移槽被 #90
 * 占用），也就没有对应端点。这里不摆"关注"按钮、不显示 0 粉丝 ——
 * 一个按下去没反应的按钮比没有按钮更糟：用户会以为是自己网络的问题。
 * 数据层与端点草案见 PR 说明（Design: GAP-015），迁移槽放开后按草案落。
 */
export default function PublicProfilePage() {
  const params = useParams<{ id: string }>();
  const id = Number(params?.id);
  const [profile, setProfile] = useState<PublicProfile | null>(null);
  const [songs, setSongs] = useState<SongView[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [hasMore, setHasMore] = useState(false);
  const [error, setError] = useState("");
  const reqSeq = useRef(0);

  async function load(idNum: number, initial: boolean, offset = 0) {
    const seq = ++reqSeq.current;
    if (initial) {
      setLoading(true);
      setError("");
    } else setLoadingMore(true);
    try {
      if (initial) {
        const p = await api.publicProfile(idNum);
        if (seq !== reqSeq.current) return;
        setProfile(p);
      }
      const list = await api.profileSongs(idNum, { limit: PAGE, offset });
      if (seq !== reqSeq.current) return;
      setSongs((prev) => (initial ? list : [...prev, ...list]));
      setHasMore(list.length === PAGE);
    } catch (e: unknown) {
      if (seq !== reqSeq.current) return;
      const err = e as ApiError;
      setError(
        err?.status === 404
          ? "这个用户不存在。"
          : err?.message || "加载失败，请稍后重试。"
      );
    } finally {
      if (seq !== reqSeq.current) return;
      setLoading(false);
      setLoadingMore(false);
    }
  }

  useEffect(() => {
    if (!Number.isFinite(id) || id <= 0) {
      setLoading(false);
      setError("用户链接无效。");
      return;
    }
    load(id, true, 0);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  /** 互动后局部替换该首（服务端为唯一真源） */
  const replace = (s: SongView) =>
    setSongs((prev) => prev.map((x) => (x.id === s.id ? s : x)));

  if (loading) {
    return (
      <main className="mx-auto max-w-5xl px-4 py-8">
        <div className="card animate-pulse space-y-3 p-5">
          <div className="h-6 w-40 rounded bg-[var(--surface-2)]" />
          <div className="h-4 w-24 rounded bg-[var(--surface-2)]" />
        </div>
      </main>
    );
  }

  if (error || !profile) {
    return (
      <main className="mx-auto max-w-5xl px-4 py-8">
        <p className="text-sm text-[var(--danger)]" role="alert">
          {error || "加载失败"}
        </p>
        <Link href="/plaza" className="btn btn-secondary mt-4">
          返回广场
        </Link>
      </main>
    );
  }

  const showPlaceholderNote = profile.display_name_is_placeholder;

  return (
    <main className="mx-auto max-w-5xl px-4 py-8">
      {/* ── 资料头 ── */}
      <header className="card mb-6 p-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <h1 className="truncate text-2xl font-bold tracking-tight text-[var(--ink)]">
              {profile.display_name}
              {profile.is_me && (
                <span className="ml-2 align-middle text-xs font-normal text-[var(--muted)]">
                  这是你
                </span>
              )}
            </h1>
            <p className="mt-1 text-sm text-[var(--muted)]">
              {profile.member_since ? `${profile.member_since} 加入` : "成员"}
              <span aria-hidden="true"> · </span>
              公开作品 {profile.public_song_count} 首
              <span aria-hidden="true"> · </span>
              获得 {profile.likes_received} 个赞
            </p>
            {showPlaceholderNote && (
              <p className="mt-2 text-xs text-[var(--muted)]">
                昵称暂由账号邮箱前两位生成（资料完善功能尚未上线）。
              </p>
            )}
          </div>
          <Link href="/plaza" className="btn btn-secondary btn-sm shrink-0">
            去广场
          </Link>
        </div>
      </header>

      {/* ── 作品 ── */}
      <h2 className="mb-3 text-lg font-semibold text-[var(--ink)]">公开作品</h2>

      {songs.length === 0 ? (
        <div className="card p-8 text-center">
          <p className="text-[var(--muted)]">
            {profile.is_me
              ? "你还没有公开的作品。在「我的作品」里把满意的作品发布到广场吧。"
              : "这个人还没有公开的作品。"}
          </p>
          {profile.is_me && (
            <Link href="/library" className="btn btn-secondary mt-4">
              我的作品
            </Link>
          )}
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          {songs.map((s, i) => (
            <article
              key={s.id}
              className="card lift enter p-4"
              aria-label={`${profile.display_name} 的作品《${s.title || "未命名"}》`}
              style={{ animationDelay: `${Math.min(i, 6) * 50}ms` }}
            >
              <Player
                song={s}
                isOwner={s.is_owner ?? false}
                onSpawn={replace}
                queue={songs}
                onReplaceSong={replace}
              />
            </article>
          ))}
        </div>
      )}

      {hasMore && (
        <button
          onClick={() => load(id, false, songs.length)}
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
