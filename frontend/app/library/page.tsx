"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api, SongView } from "@/lib/api";
import Player from "@/components/Player";
import ConfirmDialog from "@/components/ConfirmDialog";

const PAGE = 20;

export default function LibraryPage() {
  const [songs, setSongs] = useState<SongView[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [offset, setOffset] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [pendingDelete, setPendingDelete] = useState<SongView | null>(null);

  // 分轨导出（stem）：同步接口，结果含会过期的 zip 链接。
  const [stemBusy, setStemBusy] = useState<number | null>(null);
  const [stemResults, setStemResults] = useState<
    Record<number, { zipUrl: string | null; midiUrl: string | null; expiresAt?: string | null }>
  >({});

  // 局部改写（region-edit）：在站内歌曲上开一段区间重写歌词，生成子歌曲。
  const [regionEditFor, setRegionEditFor] = useState<number | null>(null);
  const [regionLyrics, setRegionLyrics] = useState("");
  const [regionStart, setRegionStart] = useState("");
  const [regionEnd, setRegionEnd] = useState("");
  const [regionBusy, setRegionBusy] = useState(false);
  const [regionError, setRegionError] = useState("");

  async function load(initial = false) {
    if (initial) setLoading(true);
    else setLoadingMore(true);
    setError("");
    try {
      const next = initial ? 0 : offset;
      const data = await api.listSongs(PAGE, next);
      setSongs((prev) => (initial ? data : [...prev, ...data]));
      setOffset(next + data.length);
      setHasMore(data.length === PAGE);
    } catch (e: any) {
      if (e?.status === 401) setError("登录已过期或无效，请重新登录（右上角）。");
      else setError(e?.message || "加载失败");
    } finally {
      if (initial) setLoading(false);
      else setLoadingMore(false);
    }
  }

  useEffect(() => {
    load(true);
  }, []);

  async function onTogglePublic(s: SongView) {
    try {
      const updated = await api.updateVisibility(s.id, !s.is_public);
      setSongs((prev) => prev.map((x) => (x.id === s.id ? updated : x)));
    } catch (e: any) {
      setError(e?.message || "更新失败");
    }
  }

  async function confirmDelete() {
    const s = pendingDelete;
    if (!s) return;
    setPendingDelete(null);
    try {
      await api.deleteSong(s.id);
      // O6：本地移除即可，避免整页 load(true) 重新拉取列表造成的闪烁/重载
      setSongs((prev) => prev.filter((x) => x.id !== s.id));
    } catch (e: any) {
      setError(e?.message || "删除失败");
    }
  }

  // ── 分轨导出（stem，同步接口）──
  async function onStem(s: SongView) {
    setStemBusy(s.id);
    setError("");
    try {
      const r = await api.stem(s.id);
      if (r.zip_url || r.midi_zip_url) {
        setStemResults((prev) => ({
          ...prev,
          [s.id]: { zipUrl: r.zip_url, midiUrl: r.midi_zip_url, expiresAt: r.expires_at_iso ?? null },
        }));
      } else {
        setError("分轨导出暂不可用（该歌曲可能不支持分轨）");
      }
    } catch (e: any) {
      setError(e?.message || "分轨导出失败");
    } finally {
      setStemBusy(null);
    }
  }

  // ── 局部改写（region-edit）──
  function openRegionEdit(s: SongView) {
    setRegionEditFor(s.id);
    setRegionLyrics("");
    setRegionStart("");
    setRegionEnd("");
    setRegionError("");
  }

  async function submitRegionEdit(s: SongView) {
    const start = Number(regionStart);
    const end = Number(regionEnd);
    if (!regionLyrics.trim()) {
      setRegionError("请填写改写后的歌词");
      return;
    }
    if (!(end > start)) {
      setRegionError("结束时间须大于开始时间");
      return;
    }
    if (end - start < 3) {
      setRegionError("改写区间至少 3 秒（Mureka 官方约束 ≥3000ms）");
      return;
    }
    if (regionLyrics.length > 3000) {
      setRegionError("改写歌词超过 3000 字上限");
      return;
    }
    setRegionBusy(true);
    setRegionError("");
    try {
      await api.regionEdit(s.id, {
        lyrics: regionLyrics.trim(),
        edit_start: Math.round(start * 1000),
        edit_end: Math.round(end * 1000),
      });
      setRegionEditFor(null);
      // 改写会创建子歌曲并进入生成流程，刷新列表让其出现（状态 preparing/running）。
      await load(true);
    } catch (e: any) {
      setRegionError(e?.message || "局部改写失败");
    } finally {
      setRegionBusy(false);
    }
  }

  return (
    <main className="mx-auto max-w-3xl px-4 py-8">
      <h1 className="mb-6 text-2xl font-bold text-[var(--ink)]">我的作品</h1>

      {loading && <p className="text-[var(--muted)]">加载中…</p>}
      {error && <p className="text-sm text-[var(--danger)]">{error}</p>}
      {!loading && songs.length === 0 && (
        <div className="card p-6 text-center">
          <p className="mb-3 text-[var(--muted)]">还没有作品。</p>
          <div className="flex flex-wrap items-center justify-center gap-2">
            <Link href="/create" className="btn btn-primary">
              创作第一首
            </Link>
            <Link href="/plaza" className="btn btn-secondary">
              去FLOW广场
            </Link>
          </div>
        </div>
      )}

      <div className="space-y-4">
        {songs.map((s) => (
          <div key={s.id} className="card p-4">
            <div className="mb-2 flex items-start justify-between gap-3">
              <h3 className="font-semibold">{s.title || "未命名"}</h3>
              <span className="rounded-full bg-[var(--surface-2)] px-2 py-0.5 text-xs text-[var(--muted)]">
                {s.status}
              </span>
            </div>
            <Player
              song={s}
              isOwner={s.is_owner ?? false}
              onSpawn={(u) => setSongs((prev) => prev.map((x) => (x.id === s.id ? u : x)))}
              queue={songs}
              onReplaceSong={(u) => setSongs((prev) => prev.map((x) => (x.id === s.id ? u : x)))}
            />
            <div className="mt-2 flex flex-wrap items-center gap-3 text-sm text-[var(--muted)]">
              <label
                className={`flex items-center gap-1 ${s.status === "completed" ? "" : "opacity-50"}`}
                title={
                  s.status === "completed"
                    ? "公开后所有人可在广场看到"
                    : "生成完成后才能发布到广场"
                }
              >
                <input
                  type="checkbox"
                  checked={!!s.is_public}
                  disabled={s.status !== "completed"}
                  onChange={() => onTogglePublic(s)}
                />
                发布到广场
              </label>
              <button
                onClick={() => setPendingDelete(s)}
                className="btn btn-danger btn-sm"
              >
                删除
              </button>
              {s.status === "completed" && (
                <>
                  <button
                    onClick={() => onStem(s)}
                    disabled={stemBusy === s.id}
                    className="btn btn-secondary btn-sm"
                  >
                    {stemBusy === s.id ? "导出中…" : "分轨导出"}
                  </button>
                  <button
                    onClick={() => openRegionEdit(s)}
                    className="btn btn-secondary btn-sm"
                  >
                    局部改写
                  </button>
                </>
              )}
            </div>

            {s.status === "completed" && stemResults[s.id] && (
              <div className="mt-2 rounded-lg bg-[var(--surface-2)] p-3 text-xs">
                <p className="mb-1 text-[var(--muted)]">分轨下载（链接会过期，请尽快保存）：</p>
                {stemResults[s.id].zipUrl && (
                  <a
                    href={stemResults[s.id].zipUrl!}
                    target="_blank"
                    rel="noreferrer"
                    className="mr-3 text-[var(--ink)] underline"
                  >
                    人声/乐器分轨包
                  </a>
                )}
                {stemResults[s.id].midiUrl && (
                  <a
                    href={stemResults[s.id].midiUrl!}
                    target="_blank"
                    rel="noreferrer"
                    className="text-[var(--ink)] underline"
                  >
                    MIDI 包
                  </a>
                )}
                {stemResults[s.id].expiresAt && (
                  <p className="mt-1 text-[var(--muted)]">失效时间：{stemResults[s.id].expiresAt}</p>
                )}
              </div>
            )}

            {s.status === "completed" && regionEditFor === s.id && (
              <div className="mt-2 rounded-lg bg-[var(--surface-2)] p-3">
                <p className="mb-1 text-xs text-[var(--muted)]">
                  局部改写：填写新歌词与改写区间（秒，区间至少 3 秒）。
                </p>
                <textarea
                  value={regionLyrics}
                  onChange={(e) => setRegionLyrics(e.target.value)}
                  className="input mb-2"
                  rows={3}
                  placeholder="改写后的歌词（≤3000 字）"
                />
                <div className="mb-2 flex gap-2">
                  <input
                    value={regionStart}
                    onChange={(e) => setRegionStart(e.target.value)}
                    className="input w-24"
                    type="number"
                    min={0}
                    step="0.1"
                    placeholder="开始秒"
                  />
                  <input
                    value={regionEnd}
                    onChange={(e) => setRegionEnd(e.target.value)}
                    className="input w-24"
                    type="number"
                    min={0}
                    step="0.1"
                    placeholder="结束秒"
                  />
                </div>
                {regionError && <p className="mb-2 text-xs text-[var(--danger)]">{regionError}</p>}
                <div className="flex gap-2">
                  <button
                    onClick={() => submitRegionEdit(s)}
                    disabled={regionBusy}
                    className="btn btn-primary btn-sm"
                  >
                    {regionBusy ? "改写中…" : "提交改写"}
                  </button>
                  <button
                    onClick={() => setRegionEditFor(null)}
                    className="btn btn-secondary btn-sm"
                  >
                    取消
                  </button>
                </div>
              </div>
            )}
          </div>
        ))}
      </div>

      {hasMore && (
        <button
          onClick={() => load(false)}
          disabled={loadingMore}
          className="btn btn-secondary mt-4 w-full"
        >
          {loadingMore ? "加载中…" : "加载更多"}
        </button>
      )}

      <ConfirmDialog
        open={!!pendingDelete}
        title="删除作品"
        message={
          pendingDelete
            ? `确定删除《${pendingDelete.title || "未命名"}》？此操作不可恢复。`
            : ""
        }
        confirmText="删除"
        danger
        onConfirm={confirmDelete}
        onCancel={() => setPendingDelete(null)}
      />
    </main>
  );
}
