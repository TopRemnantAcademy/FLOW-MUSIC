"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api, ExportResult, FolderView, PlaylistView, SongView, TrashItem } from "@/lib/api";
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

  // ── GAP-011 库组织 ──
  // tab: songs = 我的作品（含多选/歌单/分组入口）；trash = 回收站
  const [tab, setTab] = useState<"songs" | "trash">("songs");
  const [selected, setSelected] = useState<number[]>([]); // 多选集合（批量操作对象）
  const [batchBusy, setBatchBusy] = useState(false);
  const [notice, setNotice] = useState(""); // 批量结果回执（含 skipped 明细）
  const [playlists, setPlaylists] = useState<PlaylistView[]>([]);
  const [folders, setFolders] = useState<FolderView[]>([]);
  const [newPlaylist, setNewPlaylist] = useState("");
  const [folderFor, setFolderFor] = useState<SongView | null>(null);
  const [folderName, setFolderName] = useState("");
  const [trash, setTrash] = useState<TrashItem[]>([]);
  const [trashLoading, setTrashLoading] = useState(false);
  const [pendingPurge, setPendingPurge] = useState<TrashItem | null>(null);

  // ── GAP-028 批量导出 ──
  // 与 GAP-011 的 `selected` 复用同一份多选集合：库页只有一个「选中」概念
  // （勾选 → 批量公开/下架/删除/加歌单/导出），再开一个 exportSelected 会立刻分叉出
  // 「导出的是哪一批」的歧义。区别只在**已完成的曲目**才能进导出（后端 400 会把
  // 未完成的一并拒绝），故这里显式过滤并在 UI 上说明。
  const [exportBusy, setExportBusy] = useState(false);
  const [exportCsvBusy, setExportCsvBusy] = useState(false);
  const [exportResult, setExportResult] = useState<ExportResult | null>(null);

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

  async function loadLibrary() {
    try {
      const [pls, fds] = await Promise.all([api.playlists(), api.folders()]);
      setPlaylists(pls);
      setFolders(fds);
    } catch (e: any) {
      if (e?.status !== 401) setError(e?.message || "歌单/分组加载失败");
    }
  }

  async function loadTrash() {
    setTrashLoading(true);
    try {
      setTrash(await api.trash());
    } catch (e: any) {
      setError(e?.message || "回收站加载失败");
    } finally {
      setTrashLoading(false);
    }
  }

  useEffect(() => {
    load(true);
    loadLibrary();
  }, []);

  useEffect(() => {
    if (tab === "trash") loadTrash();
  }, [tab]);

  // ── 批量操作 ──
  const allSelected = songs.length > 0 && selected.length === songs.length;

  function toggleSelect(id: number) {
    setSelected((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));
  }

  function toggleSelectAll() {
    setSelected(allSelected ? [] : songs.map((s) => s.id));
  }

  function describeSkip(skipped: (number | { id: number; reason: string })[] = []) {
    if (skipped.length === 0) return "";
    const withReason = skipped.filter((x): x is { id: number; reason: string } => typeof x !== "number");
    return withReason.length
      ? `，跳过 ${skipped.length} 首（${withReason.map((x) => x.reason).join("；")}）`
      : `，跳过 ${skipped.length} 首`;
  }

  async function runBatch(fn: (ids: number[]) => Promise<any>, label: string, dropFromList = false) {
    if (selected.length === 0) return;
    setBatchBusy(true);
    setNotice("");
    try {
      const r = await fn(selected);
      const n =
        r.deleted?.length ?? r.updated?.length ?? r.added?.length ?? 0;
      setNotice(`${label}：${n} 首${describeSkip(r.skipped)}`);
      if (dropFromList) setSongs((prev) => prev.filter((s) => !selected.includes(s.id)));
      setSelected([]);
      if (tab === "trash") await loadTrash();
    } catch (e: any) {
      setError(e?.message || `${label}失败`);
    } finally {
      setBatchBusy(false);
    }
  }

  async function onBatchAddToPlaylist(playlistId: number) {
    if (!playlistId) return;
    await runBatch((ids) => api.batchAddToPlaylist(ids, playlistId), "加入播放列表");
    await loadLibrary();
  }

  async function onCreatePlaylist() {
    const name = newPlaylist.trim();
    if (!name) return;
    try {
      const pl = await api.createPlaylist(name);
      setNewPlaylist("");
      setPlaylists((prev) => [pl, ...prev]);
      setNotice(`已建立播放列表「${pl.name}」，可勾选歌曲后加入`);
    } catch (e: any) {
      setError(e?.message || "建立播放列表失败");
    }
  }

  async function onPutInFolder() {
    const s = folderFor;
    const name = folderName.trim();
    if (!s || !name) return;
    try {
      await api.putInFolder(name, "song", s.id);
      setFolderFor(null);
      setFolderName("");
      setFolders(await api.folders());
      setNotice(`已把《${s.title || "未命名"}》归入「${name}」`);
    } catch (e: any) {
      setError(e?.message || "归入分组失败");
    }
  }

  async function onRestore(item: TrashItem) {
    try {
      await api.restoreSong(item.id);
      setTrash((prev) => prev.filter((x) => x.id !== item.id));
      await load(true); // 恢复的歌要回到「我的作品」
      setNotice(`已恢复《${item.title || "未命名"}》`);
    } catch (e: any) {
      setError(e?.message || "恢复失败");
    }
  }

  async function confirmPurge() {
    const item = pendingPurge;
    if (!item) return;
    setPendingPurge(null);
    try {
      await api.purgeSong(item.id);
      setTrash((prev) => prev.filter((x) => x.id !== item.id));
      setNotice("已彻底删除（不可恢复）");
    } catch (e: any) {
      setError(e?.message || "彻底删除失败");
    }
  }

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
      setNotice(`《${s.title || "未命名"}》已移入回收站，30 天内可恢复`);
    } catch (e: any) {
      setError(e?.message || "删除失败");
    }
  }

  // ── GAP-028 批量导出 ──
  /** 勾选里**可导出**的曲目：只导已完成的（后端对未完成的作品返回 400）。 */
  const exportableIds = songs.filter((s) => selected.includes(s.id) && s.status === "completed").map((s) => s.id);
  const selectedUnfinished = selected.length - exportableIds.length;

  function formatBytes(n: number): string {
    if (!n) return "0 B";
    const units = ["B", "KB", "MB", "GB"];
    const i = Math.min(units.length - 1, Math.floor(Math.log(n) / Math.log(1024)));
    return `${(n / Math.pow(1024, i)).toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
  }

  /** 触发浏览器下载：签名链接(绝对)与新窗口打开，同源 token 链接也走 href，
   *  但**必须**带 cookie —— 用 <a> 而非 fetch，浏览器会自动带上同源 cookie。 */
  function triggerDownload(url: string) {
    const a = document.createElement("a");
    a.href = url.startsWith("/") ? `${process.env.NEXT_PUBLIC_API_BASE || "http://localhost:8000"}${url}` : url;
    a.rel = "noreferrer";
    a.click();
  }

  async function onExportZip() {
    if (exportableIds.length === 0) return;
    setExportBusy(true);
    setError("");
    setExportResult(null);
    setNotice("");
    try {
      const r = await api.exportSongs(exportableIds);
      setExportResult(r);
      const skipped = r.skipped?.length ? `，跳过 ${r.skipped.length} 首` : "";
      setNotice(
        `导出包已就绪：${r.song_count} 首、${formatBytes(r.total_bytes)}${skipped}。链接会过期，请尽快下载。`
      );
      // 自动触发一次下载（同时保留链接供再次领取）
      if (r.url) triggerDownload(r.url);
    } catch (e: any) {
      setError(e?.message || "批量导出失败");
    } finally {
      setExportBusy(false);
    }
  }

  async function onExportCsv(all = false) {
    setExportCsvBusy(true);
    setError("");
    try {
      const blob = await api.exportCsv(all ? undefined : exportableIds);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "my-songs.csv";
      a.click();
      URL.revokeObjectURL(url);
      setNotice(all ? "已导出全部作品的元数据 CSV" : `已导出 ${exportableIds.length} 首的元数据 CSV`);
    } catch (e: any) {
      setError(e?.message || "导出 CSV 失败");
    } finally {
      setExportCsvBusy(false);
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
      <div className="mb-6 flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-bold text-[var(--ink)]">
          {tab === "songs" ? "我的作品" : "回收站"}
        </h1>
        <div className="flex gap-2">
          <button
            onClick={() => setTab("songs")}
            className={`btn btn-sm ${tab === "songs" ? "btn-primary" : "btn-secondary"}`}
          >
            我的作品
          </button>
          <button
            onClick={() => setTab("trash")}
            className={`btn btn-sm ${tab === "trash" ? "btn-primary" : "btn-secondary"}`}
          >
            回收站{trash.length > 0 && tab === "trash" ? `（${trash.length}）` : ""}
          </button>
        </div>
      </div>

      {loading && <p className="text-[var(--muted)]">加载中…</p>}
      {error && (
        <p className="mb-3 text-sm text-[var(--danger)]" role="alert">
          {error}
        </p>
      )}
      {notice && (
        <p
          className="mb-3 rounded-lg bg-[var(--surface-2)] px-3 py-2 text-sm text-[var(--ink)]"
          role="status"
          aria-live="polite"
        >
          {notice}
          <button onClick={() => setNotice("")} className="ml-2 text-xs text-[var(--muted)] underline">
            知道了
          </button>
        </p>
      )}

      {tab === "trash" && (
        <section className="space-y-3">
          <p className="text-sm text-[var(--muted)]">
            删除的作品会在这里保留 30 天，到期后自动清理。恢复后歌单与收藏原样回来。
          </p>
          {trashLoading && <p className="text-[var(--muted)]">加载中…</p>}
          {!trashLoading && trash.length === 0 && (
            <div className="card p-6 text-center text-[var(--muted)]">回收站是空的。</div>
          )}
          {trash.map((t) => (
            <article
              key={t.id}
              className="card flex flex-wrap items-center justify-between gap-3 p-4"
              aria-label={`回收站作品《${t.title || "未命名"}》`}
            >
              <div>
                <p className="font-semibold text-[var(--ink)]">{t.title || "未命名"}</p>
                <p className="text-xs text-[var(--muted)]">
                  剩余 {t.days_left ?? 0} 天（保留 {t.keep_days ?? 30} 天）
                  {t.expires_at ? `，将于 ${t.expires_at.slice(0, 10)} 清理` : ""}
                </p>
              </div>
              <div className="flex gap-2">
                <button onClick={() => onRestore(t)} className="btn btn-secondary btn-sm">
                  恢复
                </button>
                <button onClick={() => setPendingPurge(t)} className="btn btn-danger btn-sm">
                  彻底删除
                </button>
              </div>
            </article>
          ))}
          {trash.length > 0 && (
            <button
              onClick={async () => {
                try {
                  const r = await api.emptyTrash();
                  setTrash([]);
                  setNotice(`已清空回收站（${r.count} 首永久删除）`);
                } catch (e: any) {
                  setError(e?.message || "清空回收站失败");
                }
              }}
              className="btn btn-danger w-full"
            >
              清空回收站
            </button>
          )}
        </section>
      )}

      {tab === "songs" && (
        <>
          <PlaylistPanel
            playlists={playlists}
            folders={folders}
            selectedCount={selected.length}
            newPlaylist={newPlaylist}
            onNewPlaylistChange={setNewPlaylist}
            onCreatePlaylist={onCreatePlaylist}
            onAddToPlaylist={onBatchAddToPlaylist}
            busy={batchBusy}
            onDeletePlaylist={async (id) => {
              try {
                await api.deletePlaylist(id);
                setPlaylists((prev) => prev.filter((x) => x.id !== id));
              } catch (e: any) {
                setError(e?.message || "删除播放列表失败");
              }
            }}
            onDeleteFolder={async (id) => {
              try {
                await api.deleteFolder(id);
                setFolders(await api.folders());
              } catch (e: any) {
                setError(e?.message || "解散分组失败");
              }
            }}
          />

          {songs.length > 0 && (
            <div
              className="card mb-4 flex flex-wrap items-center gap-2 p-3 text-sm"
              role="group"
              aria-label="批量操作"
            >
              <label className="flex items-center gap-1">
                <input
                  type="checkbox"
                  checked={allSelected}
                  onChange={toggleSelectAll}
                  aria-label={`全选本页作品（已选 ${selected.length}/${songs.length}）`}
                />
                全选（{selected.length}/{songs.length}）
              </label>
              <span className="flex-1" />
              <button
                disabled={selected.length === 0 || batchBusy}
                onClick={() => runBatch((ids) => api.batchPublic(ids, true), "批量公开")}
                className="btn btn-secondary btn-sm"
              >
                批量公开
              </button>
              <button
                disabled={selected.length === 0 || batchBusy}
                onClick={() => runBatch((ids) => api.batchPublic(ids, false), "批量下架")}
                className="btn btn-secondary btn-sm"
              >
                批量下架
              </button>
              <button
                disabled={selected.length === 0 || batchBusy}
                onClick={() => runBatch(api.batchDelete, "批量删除", true)}
                className="btn btn-danger btn-sm"
              >
                批量删除
              </button>
              <span className="mx-1 h-4 w-px bg-[var(--line)]" aria-hidden />
              <button
                disabled={exportableIds.length === 0 || exportBusy}
                onClick={onExportZip}
                className="btn btn-primary btn-sm"
                title={
                  exportableIds.length === 0
                    ? "先勾选已完成的作品"
                    : `打包 ${exportableIds.length} 首（音频 + metadata.csv）`
                }
              >
                {exportBusy ? "打包中…" : `导出 ZIP（${exportableIds.length}）`}
              </button>
              <button
                disabled={(selected.length === 0 && songs.length === 0) || exportCsvBusy}
                onClick={() => onExportCsv(false)}
                className="btn btn-secondary btn-sm"
                title="导出已勾选曲目的元数据（CSV，Excel 可直接打开）"
              >
                {exportCsvBusy ? "导出中…" : "导出 CSV"}
              </button>
              <button
                disabled={songs.length === 0 || exportCsvBusy}
                onClick={() => onExportCsv(true)}
                className="btn btn-secondary btn-sm"
                title="导出我的全部作品元数据（最多 200 首）"
              >
                全部 CSV
              </button>
            </div>
          )}

          {selected.length > 0 && (
            <p className="mb-3 text-xs text-[var(--muted)]">
              已选 {selected.length} 首
              {selectedUnfinished > 0 && (
                <>
                  ，其中 {selectedUnfinished} 首未生成完成
                  <span className="text-[var(--warning,inherit)]">不会</span>进入导出包
                </>
              )}
              ：勾选既用于批量公开/下架/删除/加歌单，也用于批量导出。
            </p>
          )}

          {exportResult && (
            <div className="card mb-4 p-3 text-sm">
              <p className="mb-1 font-medium text-[var(--ink)]">
                导出包已就绪（{exportResult.song_count} 首 / {formatBytes(exportResult.total_bytes)}）
              </p>
              <p className="mb-2 text-xs text-[var(--muted)]">
                包含每首音频 + <code>metadata.csv</code>（UTF-8 BOM，Excel 可直接打开）。
                {exportResult.expires_at_iso
                  ? `链接失效时间：${exportResult.expires_at_iso.replace("T", " ").slice(0, 16)} UTC。`
                  : "链接会过期，请尽快保存。"}
              </p>
              {exportResult.skipped?.length > 0 && (
                <ul className="mb-2 list-inside list-disc text-xs text-[var(--muted)]">
                  {exportResult.skipped.slice(0, 5).map((r) => (
                    <li key={r}>{r}</li>
                  ))}
                  {exportResult.skipped.length > 5 && <li>…共 {exportResult.skipped.length} 条</li>}
                </ul>
              )}
              <div className="flex flex-wrap gap-2">
                <button
                  onClick={() => triggerDownload(exportResult.url)}
                  className="btn btn-primary btn-sm"
                >
                  再次下载
                </button>
                <button
                  onClick={() => setExportResult(null)}
                  className="btn btn-secondary btn-sm"
                >
                  收起
                </button>
              </div>
              <p className="mt-2 text-xs text-[var(--muted)]">
                {exportResult.storage === "local"
                  ? "当前未配置对象存储，导出包保存在服务器临时目录，领取一次后即失效。"
                  : "导出包存放在对象存储，凭签名链接一次性下载。"}
              </p>
            </div>
          )}

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
          <article key={s.id} className="card p-4" aria-label={`作品《${s.title || "未命名"}》`}>
            <div className="mb-2 flex items-start justify-between gap-3">
              <div className="flex items-start gap-2">
                <input
                  type="checkbox"
                  className="mt-1"
                  checked={selected.includes(s.id)}
                  onChange={() => toggleSelect(s.id)}
                  aria-label={`选择 ${s.title || "未命名"}`}
                />
                <h3 className="font-semibold">{s.title || "未命名"}</h3>
              </div>
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
                  aria-label={`把《${s.title || "未命名"}》发布到广场`}
                />
                发布到广场
              </label>
              <button
                onClick={() => {
                  setFolderFor(s);
                  setFolderName("");
                }}
                className="btn btn-secondary btn-sm"
              >
                归入分组
              </button>
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
                  {/* GAP-012：歌词编辑器独立页（段落结构 / 改词后重排时轴 / LRC 导入导出）。
                      做成导航而非弹层：逐行对时点是长任务，卡片里塞不下；且独立 URL 可刷新。 */}
                  <Link href={`/songs/${s.id}/lyrics`} className="btn btn-secondary btn-sm">
                    编辑歌词
                  </Link>
                </>
              )}
            </div>

            {folderFor?.id === s.id && (
              <div className="mt-2 flex flex-wrap items-center gap-2 rounded-lg bg-[var(--surface-2)] p-3">
                <input
                  value={folderName}
                  onChange={(e) => setFolderName(e.target.value)}
                  className="input"
                  placeholder="分组名称（如：电子 / 客户 A）"
                  aria-label="分组名称"
                />
                <button onClick={onPutInFolder} className="btn btn-primary btn-sm">
                  存入
                </button>
                <button onClick={() => setFolderFor(null)} className="btn btn-secondary btn-sm">
                  取消
                </button>
              </div>
            )}

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
                  aria-label="局部改写：改写后的歌词"
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
                    aria-label="局部改写：开始时间（秒）"
                  />
                  <input
                    value={regionEnd}
                    onChange={(e) => setRegionEnd(e.target.value)}
                    className="input w-24"
                    type="number"
                    min={0}
                    step="0.1"
                    placeholder="结束秒"
                    aria-label="局部改写：结束时间（秒）"
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
          </article>
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
            ? `确定把《${pendingDelete.title || "未命名"}》移入回收站？30 天内可以恢复。`
            : ""
        }
        confirmText="移入回收站"
        danger
        onConfirm={confirmDelete}
        onCancel={() => setPendingDelete(null)}
      />

      <ConfirmDialog
        open={!!pendingPurge}
        title="彻底删除"
        message={
          pendingPurge
            ? `确定彻底删除《${pendingPurge.title || "未命名"}》？此操作不可恢复，收藏与歌单中的记录也会一并清除。`
            : ""
        }
        confirmText="彻底删除"
        danger
        onConfirm={confirmPurge}
        onCancel={() => setPendingPurge(null)}
      />
        </>
      )}
    </main>
  );
}

/** 播放列表 / 分组面板：建立歌单、把勾选歌曲加入、解散分组。 */
function PlaylistPanel({
  playlists,
  folders,
  selectedCount,
  newPlaylist,
  onNewPlaylistChange,
  onCreatePlaylist,
  onAddToPlaylist,
  onDeletePlaylist,
  onDeleteFolder,
  busy,
}: {
  playlists: PlaylistView[];
  folders: FolderView[];
  selectedCount: number;
  newPlaylist: string;
  onNewPlaylistChange: (v: string) => void;
  onCreatePlaylist: () => void;
  onAddToPlaylist: (playlistId: number) => void;
  onDeletePlaylist: (id: number) => void;
  onDeleteFolder: (id: number) => void;
  busy: boolean;
}) {
  const [target, setTarget] = useState<string>("");

  return (
    <section className="card mb-4 p-4">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <input
          value={newPlaylist}
          onChange={(e) => onNewPlaylistChange(e.target.value)}
          className="input flex-1"
          placeholder="新建播放列表名称"
          aria-label="新建播放列表名称"
        />
        <button onClick={onCreatePlaylist} className="btn btn-primary btn-sm">
          新建
        </button>
      </div>

      {playlists.length === 0 ? (
        <p className="text-sm text-[var(--muted)]">还没有播放列表。</p>
      ) : (
        <ul className="space-y-2">
          {playlists.map((pl) => (
            <li key={pl.id} className="flex flex-wrap items-center gap-2 text-sm">
              <span className="font-medium text-[var(--ink)]">{pl.name}</span>
              <span className="text-xs text-[var(--muted)]">{pl.song_count ?? 0} 首</span>
              <span className="flex-1" />
              <button
                disabled={selectedCount === 0 || busy}
                onClick={() => onAddToPlaylist(pl.id)}
                className="btn btn-secondary btn-sm"
                title={selectedCount === 0 ? "先勾选歌曲" : `把已勾选的 ${selectedCount} 首加入`}
              >
                加入已勾选
              </button>
              <button onClick={() => onDeletePlaylist(pl.id)} className="btn btn-danger btn-sm">
                删除歌单
              </button>
            </li>
          ))}
        </ul>
      )}

      {folders.length > 0 && (
        <div className="mt-4 border-t border-[var(--line)] pt-3">
          <p className="mb-2 text-xs text-[var(--muted)]">分组</p>
          <ul className="space-y-1 text-sm">
            {folders.map((f) => (
              <li key={f.id} className="flex items-center gap-2">
                <span className="text-[var(--ink)]">
                  {f.name}（{f.count}）
                </span>
                <span className="flex-1 text-xs text-[var(--muted)]">
                  {f.items
                    .map((i) => i.title ?? (i.item_type === "playlist" ? "播放列表" : `#${i.item_id}`))
                    .join("、")}
                </span>
                <button onClick={() => onDeleteFolder(f.id)} className="btn btn-danger btn-sm">
                  解散
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
