"use client";

/**
 * 分享落地页（GAP-013）
 *
 * 无状态：页面拿到的就是 URL 里的 token，不查任何「分享记录」。
 * 一切可见性判定在后端 `/api/share/songs/{token}` 实时做（签名 + 过期 + is_public + 审核），
 * 前端只负责把 403/404 的区别讲清楚 —— 这是分享功能最容易出「用户以为是 bug」的地方：
 *   · 403 = 链接本身坏了（过期 / 被改过）→ 让朋友找作者重发
 *   · 404 = 链接是好的，但作品已下架 / 未过审 → 不必重发，作品没了
 * 把这两种混成一句「链接无效」，用户只能反复找作者要新链接，却永远修不好。
 */

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { ApiError, api, type SharedSongView } from "@/lib/api";
import Waveform from "@/components/Waveform";

type State =
  | { kind: "loading" }
  | { kind: "ok"; song: SharedSongView; url: string | null }
  | { kind: "expired" }
  | { kind: "gone" }
  | { kind: "error"; message: string };

export default function SharePage() {
  const params = useParams();
  const token = typeof params?.token === "string" ? params.token : "";
  const [state, setState] = useState<State>({ kind: "loading" });

  useEffect(() => {
    if (!token) {
      setState({ kind: "expired" });
      return;
    }
    let canceled = false;
    (async () => {
      try {
        const song = await api.resolveShareLink(token);
        if (canceled) return;
        const url = song.audio_url ?? null;
        setState({ kind: "ok", song, url });
        // 刻意**不自动起播**：分享页由链接直达，没有任何用户手势，浏览器的自动播放
        // 策略必然拦掉 play()（无手势 + 带声音）。与其做一个注定失败还会在控制台
        // 刷一条 rejection 的尝试，不如把「播放」交给页面上的 <audio controls>。
      } catch (e: any) {
        if (canceled) return;
        const status = e instanceof ApiError ? e.status : 0;
        if (status === 403) setState({ kind: "expired" });
        else if (status === 404) setState({ kind: "gone" });
        else setState({ kind: "error", message: e?.message || "加载失败" });
      }
    })();
    return () => {
      canceled = true;
    };
  }, [token]);

  return (
    <main className="mx-auto max-w-2xl px-4 py-10">
      <h1 className="mb-1 text-lg font-semibold">分享的作品</h1>
      <p className="mb-6 text-xs text-[var(--muted)]">
        {state.kind === "ok"
          ? `有效期至 ${new Date(state.song.expires_at * 1000).toLocaleString()}`
          : "来自 FLOW MUSIC 的分享链接"}
      </p>

      {state.kind === "loading" && <p className="text-sm text-[var(--muted)]">加载中…</p>}

      {state.kind === "expired" && (
        <div className="card p-4">
          <p className="text-sm">链接已过期或已失效。</p>
          <p className="mt-1 text-xs text-[var(--muted)]">
            分享链接有有效期限制（最长 30 天）。请让作者重新生成一条。
          </p>
        </div>
      )}

      {state.kind === "gone" && (
        <div className="card p-4">
          <p className="text-sm">该作品已下架或不可对外分享。</p>
          <p className="mt-1 text-xs text-[var(--muted)]">
            链接本身有效，是作品当前不可见（作者取消公开 / 未通过内容审核 / 已删除）。
            重发链接也不行，请联系作者。
          </p>
        </div>
      )}

      {state.kind === "error" && (
        <div className="card p-4">
          <p className="text-sm">加载失败：{state.message}</p>
        </div>
      )}

      {state.kind === "ok" && (
        <div className="card p-4">
          <div className="mb-3 flex items-center gap-3">
            {state.song.cover_url && (
              <img
                src={state.song.cover_url}
                alt=""
                loading="lazy"
                onError={(e) => {
                  (e.currentTarget as HTMLImageElement).style.display = "none";
                }}
                className="h-20 w-20 rounded-lg border border-[var(--border)] object-cover"
              />
            )}
            <div className="min-w-0">
              <h2 className="truncate font-semibold">{state.song.title}</h2>
              <p className="mt-0.5 text-xs text-[var(--muted)]">
                {state.song.license === "commercial" ? "可商用授权" : "非商用授权"}
                {state.song.can_play_full === false && state.song.preview_seconds
                  ? ` · 试听前 ${state.song.preview_seconds} 秒`
                  : ""}
              </p>
            </div>
          </div>

          {state.url ? (
            <>
              <audio
                src={state.url}
                controls
                preload="metadata"
                className="w-full"
                aria-label={`${state.song.title} 试听`}
              />
              <div className="mt-3">
                <Waveform
                  src={state.url}
                  duration={state.song.duration}
                  height={44}
                  label={`${state.song.title} 波形`}
                  peaksUrl={api.sharePeaksPath(token)}
                />
              </div>
            </>
          ) : (
            <p className="text-sm text-[var(--muted)]">
              ⚠ 试听片段暂不可用（后端 fail-closed，不会回退整曲）。请稍后再试。
            </p>
          )}

          {state.song.can_play_full === false && (
            <p className="mt-3 text-xs text-[var(--muted)]">
              分享链接不改变试听规则：未订阅只能听前 {state.song.preview_seconds ?? 60} 秒。
            </p>
          )}
        </div>
      )}
    </main>
  );
}
