"use client";

import { useEffect, useState } from "react";
import { api, AdminSongView, AdminStats, ModerationQueueItem, ReportRow } from "@/lib/api";
import { clearAdminKey, getAdminKey } from "@/lib/adminAuth";
import AdminGuard from "@/components/AdminGuard";

type Filter = "" | "pending" | "approved" | "rejected";

const MOD_LABEL: Record<string, string> = {
  pending: "待审核",
  approved: "已通过",
  rejected: "已驳回",
};
function modLabel(s: string) {
  return MOD_LABEL[s] || s;
}
function modColor(s: string) {
  if (s === "approved") return "bg-emerald-900 text-emerald-300";
  if (s === "rejected") return "bg-red-900 text-red-300";
  return "bg-amber-900 text-amber-300";
}

// 生成状态中文显示（后端 Song.status 枚举 pending|processing|completed|failed；未知值原样回退）
const GEN_STATUS_CN: Record<string, string> = {
  pending: "等待中",
  processing: "生成中",
  completed: "已完成",
  failed: "失败",
};
const genStatusCn = (s?: string | null) => (s ? GEN_STATUS_CN[s] ?? s : "");

// GAP-016 风险分级展示（等级由后端推导，前端只做映射 —— 避免两套口径）。
const RISK_LABEL: Record<string, string> = { high: "高风险", medium: "中风险", low: "低风险" };
function riskColor(level?: string) {
  if (level === "high") return "bg-red-900 text-red-300";
  if (level === "medium") return "bg-amber-900 text-amber-300";
  return "bg-emerald-900 text-emerald-300";
}

const REPORT_REASON_CN: Record<string, string> = {
  copyright: "版权 / DMCA",
  hate: "仇恨歧视",
  violence: "暴力",
  sexual: "色情低俗",
  spam: "垃圾信息",
  other: "其他",
};
const reportReasonCn = (r: string) => REPORT_REASON_CN[r] || r;

// 授权类型中文（GAP-016 内容权利）：随详情展示，便于判定可否被下游商用。
function licenseCn(l?: string | null) {
  return l === "commercial" ? "可商用" : "非商用";
}

function isAbsUrl(u?: string | null) {
  return typeof u === "string" && /^https?:\/\//i.test(u);
}

export default function AdminPage() {
  // null = 尚未读取 localStorage（首帧不渲染门禁，避免闪烁）
  const [token, setToken] = useState<string | null>(null);
  const [filter, setFilter] = useState<Filter>("");
  const [statusFilter, setStatusFilter] = useState<string>("");
  const [songs, setSongs] = useState<AdminSongView[]>([]);
  const [stats, setStats] = useState<AdminStats | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [rejectTarget, setRejectTarget] = useState<number | null>(null);
  const [rejectNote, setRejectNote] = useState("");
  // GAP-016：视图切换（审核队列 / 举报）、批量选择、风险过滤
  const [view, setView] = useState<"queue" | "reports">("queue");
  const [riskFilter, setRiskFilter] = useState<string>("");
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [riskBySong, setRiskBySong] = useState<Record<number, string>>({});
  const [reports, setReports] = useState<ReportRow[]>([]);
  const [batchNote, setBatchNote] = useState("");
  const [batchRejectOpen, setBatchRejectOpen] = useState(false);
  const PAGE_SIZE = 50;
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    setToken(getAdminKey() || null);
  }, []);

  async function loadReports() {
    setError("");
    setLoading(true);
    try {
      setReports(await api.adminReports("open"));
    } catch (e: any) {
      setError(e?.message || "加载失败");
      if (e?.status === 401 || e?.status === 403 || e?.status === 503) {
        clearAdminKey();
        setToken(null);
      }
    } finally {
      setLoading(false);
    }
  }

  async function load(reset = true) {
    setError("");
    setLoading(true);
    const off = reset ? 0 : songs.length;
    try {
      const [s, st] = await Promise.all([
        api.adminSongs(filter || undefined, statusFilter || undefined, PAGE_SIZE, off),
        api.adminStats(),
      ]);
      setSongs(reset ? s : [...songs, ...s]);
      setStats(st);
      setHasMore(s.length >= PAGE_SIZE);
      // 风险分级只对「待审核」有意义：拉一次队列建立 id → risk_level 映射，
      // 用于列表里展示风险徽章（等级由后端推导，前端不重算）。
      api
        .adminModerationQueue(undefined, 200, 0)
        .then((q: ModerationQueueItem[]) => {
          const m: Record<number, string> = {};
          q.forEach((it) => (m[it.id] = it.risk_level));
          setRiskBySong(m);
        })
        .catch(() => {
          /* 风险映射是增强项，失败不影响主列表 */
        });
    } catch (e: any) {
      setError(e?.message || "加载失败");
      // 密钥无效/过期 → 退回门禁，不停留在"能点但全报错"的状态
      if (e?.status === 401 || e?.status === 403 || e?.status === 503) {
        clearAdminKey();
        setToken(null);
      }
    } finally {
      setLoading(false);
    }
  }

  async function refreshStats() {
    try {
      setStats(await api.adminStats());
    } catch {
      // 统计刷新失败不影响主流程
    }
  }

  function patchModeration(id: number, status: string, note: string) {
    setSongs((prev) => {
      if (filter && filter !== status) {
        // 当前按审核状态筛选时，处置后该条不再属于此筛选，移除以反映最新视图
        return prev.filter((s) => s.id !== id);
      }
      return prev.map((s) =>
        s.id === id ? { ...s, moderation_status: status, moderation_note: note } : s,
      );
    });
  }

  function loadMore() {
    load(false);
  }

  useEffect(() => {
    if (!token) return;
    if (view === "reports") {
      loadReports();
    } else {
      load(true);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, filter, statusFilter, view]);

  async function approve(id: number) {
    setBusy(true);
    try {
      await api.adminApprove(id);
      patchModeration(id, "approved", "");
      refreshStats();
    } catch (e: any) {
      setError(e?.message || "操作失败");
    } finally {
      setBusy(false);
    }
  }

  async function confirmReject() {
    if (rejectTarget == null) return;
    const note = rejectNote;
    setBusy(true);
    try {
      await api.adminReject(rejectTarget, note);
      patchModeration(rejectTarget, "rejected", note);
      setRejectTarget(null);
      setRejectNote("");
      refreshStats();
    } catch (e: any) {
      setError(e?.message || "操作失败");
    } finally {
      setBusy(false);
    }
  }

  // —— GAP-016 批量处置 ——
  function toggleSelect(id: number) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function batchApprove() {
    const ids = Array.from(selected);
    if (ids.length === 0) return;
    setBusy(true);
    try {
      await api.adminBatchModerate(ids, "approve");
      ids.forEach((id) => patchModeration(id, "approved", ""));
      setSelected(new Set());
      refreshStats();
    } catch (e: any) {
      setError(e?.message || "批量操作失败");
    } finally {
      setBusy(false);
    }
  }

  async function batchReject() {
    const ids = Array.from(selected);
    if (ids.length === 0) return;
    const note = batchNote.trim();
    if (!note) {
      setError("批量驳回必须填写原因");
      return;
    }
    setBusy(true);
    try {
      await api.adminBatchModerate(ids, "reject", note);
      ids.forEach((id) => patchModeration(id, "rejected", note));
      setSelected(new Set());
      setBatchNote("");
      setBatchRejectOpen(false);
      refreshStats();
    } catch (e: any) {
      setError(e?.message || "批量操作失败");
    } finally {
      setBusy(false);
    }
  }

  async function resolveReport(id: number, resolution: "takedown" | "rejected") {
    setBusy(true);
    try {
      await api.adminResolveReport(id, resolution);
      setReports((prev) => prev.filter((r) => r.id !== id));
      refreshStats();
    } catch (e: any) {
      setError(e?.message || "举报处置失败");
    } finally {
      setBusy(false);
    }
  }

  if (token === null) {
    return (
      <AdminGuard
        title="内容审核"
        desc="请输入管理密钥（后台密钥），即可审核用户上传的歌曲。"
        onUnlocked={(k) => {
          setToken(k);
          load();
        }}
      />
    );
  }

  const FILTERS: { key: Filter; label: string }[] = [
    { key: "", label: "全部" },
    { key: "pending", label: "待审核" },
    { key: "approved", label: "已通过" },
    { key: "rejected", label: "已驳回" },
  ];

  const STATUS_FILTERS: { key: string; label: string }[] = [
    { key: "", label: "全部状态" },
    { key: "completed", label: "已完成" },
    { key: "processing", label: "生成中" },
    { key: "failed", label: "失败" },
  ];

  const pending = stats?.by_moderation?.pending || 0;
  const handled =
    (stats?.by_moderation?.approved || 0) + (stats?.by_moderation?.rejected || 0);

  return (
    <div className="mx-auto max-w-3xl p-6">
      <div className="mb-5 flex items-center justify-between">
        <h1 className="text-2xl font-semibold">内容审核</h1>
        <button
          onClick={() => {
            clearAdminKey();
            setToken(null);
          }}
          className="text-sm text-[var(--muted)] hover:text-[var(--ink-soft)]"
        >
          退出
        </button>
      </div>

      {error && (
        <p className="mb-4 rounded-xl bg-red-950 px-4 py-3 text-base text-red-300">{error}</p>
      )}

      <div className="mb-5 grid grid-cols-3 gap-3">
        <BigCard emoji="🎵" title="歌曲总数" value={stats ? String(stats.total) : "-"} sub="平台全部歌曲" />
        <BigCard emoji="⏳" title="待审核" value={String(pending)} sub="需要你过目" />
        <BigCard emoji="✅" title="已处理" value={String(handled)} sub="通过 + 驳回" />
      </div>

      {/* 视图切换：审核队列 / 举报（GAP-016） */}
      <div className="mb-5 flex flex-wrap gap-2 text-base">
        <button
          onClick={() => setView("queue")}
          className="chip rounded-full px-4 py-2"
          data-on={view === "queue"}
        >
          审核队列
        </button>
        <button
          onClick={() => setView("reports")}
          className="chip rounded-full px-4 py-2"
          data-on={view === "reports"}
        >
          举报{stats && (stats as any).reports_open ? `(${(stats as any).reports_open})` : ""}
        </button>
      </div>

      {view === "reports" ? (
        <div className="space-y-3">
          {reports.map((r) => (
            <div key={r.id} className="rounded-2xl card p-4">
              <div className="flex items-start justify-between gap-3">
                <div>
                  <div className="text-lg font-semibold">{r.song_title || `曲目 #${r.song_id}`}</div>
                  <div className="mt-1 text-sm text-[var(--muted)]">
                    {reportReasonCn(r.reason)}
                    {r.reporter_email ? ` · ${r.reporter_email}` : ""}
                  </div>
                  {r.detail ? (
                    <p className="mt-2 whitespace-pre-wrap text-sm text-[var(--ink-soft)]">{r.detail}</p>
                  ) : null}
                </div>
                <span className="shrink-0 rounded-full bg-amber-900 px-3 py-1 text-sm text-amber-300">
                  待处理
                </span>
              </div>
              <div className="mt-3 flex gap-2">
                <button
                  onClick={() => resolveReport(r.id, "takedown")}
                  disabled={busy}
                  className="btn btn-danger"
                >
                  下架
                </button>
                <button
                  onClick={() => resolveReport(r.id, "rejected")}
                  disabled={busy}
                  className="btn btn-secondary"
                >
                  举报不成立
                </button>
              </div>
            </div>
          ))}
          {reports.length === 0 && (
            <p className="rounded-2xl card p-6 text-center text-[var(--muted)]">暂无待处理举报</p>
          )}
        </div>
      ) : (
      <>
      {/* 风险筛选（GAP-016） */}
      <div className="mb-5 flex flex-wrap gap-2 text-base">
        {[{ key: "", label: "全部风险" }, { key: "high", label: "高风险" }, { key: "medium", label: "中风险" }, { key: "low", label: "低风险" }].map((f) => (
          <button
            key={f.key || "all-risk"}
            onClick={() => setRiskFilter(f.key)}
            className="chip rounded-full px-4 py-2"
            data-on={riskFilter === f.key}
          >
            {f.label}
          </button>
        ))}
      </div>

      <div className="mb-5 flex flex-wrap gap-2 text-base">
        {FILTERS.map((f) => (
          <button
            key={f.key || "all"}
            onClick={() => setFilter(f.key)}
            className="chip rounded-full px-4 py-2"
            data-on={filter === f.key}
          >
            {f.label}
          </button>
        ))}
      </div>

      <div className="mb-5 flex flex-wrap gap-2 text-base">
        {STATUS_FILTERS.map((f) => (
          <button
            key={f.key || "all"}
            onClick={() => setStatusFilter(f.key)}
            className="chip rounded-full px-4 py-2"
            data-on={statusFilter === f.key}
          >
            {f.label}
          </button>
        ))}
      </div>

      <div className="space-y-3">
        {songs
          .filter((s) => !riskFilter || riskBySong[s.id] === riskFilter)
          .map((s) => {
          const ms = s.moderation_status || "pending";
          const risk = riskBySong[s.id];
          return (
            <div key={s.id} className="rounded-2xl card p-4">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    {/* 批量选择：仅待审核项可勾选（已终态不会被批量覆盖） */}
                    {ms === "pending" && (
                      <input
                        type="checkbox"
                        checked={selected.has(s.id)}
                        onChange={() => toggleSelect(s.id)}
                        className="h-4 w-4 shrink-0"
                        aria-label="选择本曲"
                      />
                    )}
                    <div className="text-lg font-semibold">{s.title}</div>
                    {risk ? (
                      <span className={`shrink-0 rounded-full px-2 py-0.5 text-xs ${riskColor(risk)}`}>
                        {RISK_LABEL[risk] || risk}
                      </span>
                    ) : null}
                  </div>
                  <div className="mt-1 text-sm text-[var(--muted)]">
                    {s.user_email || "未知用户"}
                    {s.duration ? ` · ${Math.floor(s.duration / 60)}分${s.duration % 60}秒` : ""}
                    {s.status ? ` · 生成：${genStatusCn(s.status)}` : ""}
                  </div>
                  <div className="mt-1 flex flex-wrap gap-2 text-xs text-[var(--muted)]">
                    {s.content_category ? <span>分类：{s.content_category}</span> : null}
                    <span>授权：{licenseCn(s.license)}</span>
                    {s.engine_name ? <span>引擎：{s.engine_name}</span> : null}
                    {s.tier_key ? <span>套餐：{s.tier_key}</span> : null}
                    {s.cost_cny != null ? <span>成本：¥{s.cost_cny.toFixed(2)}</span> : null}
                    {s.price_cny != null ? <span>营收：¥{s.price_cny.toFixed(2)}</span> : null}
                    {s.margin_cny != null ? (
                      <span className={s.margin_cny >= 0 ? "text-emerald-400" : "text-red-400"}>
                        毛利：¥{s.margin_cny.toFixed(2)}
                      </span>
                    ) : null}
                  </div>
                </div>
                <span className={`shrink-0 rounded-full px-3 py-1 text-sm ${modColor(ms)}`}>
                  {modLabel(ms)}
                </span>
              </div>

              {s.cover_url && isAbsUrl(s.cover_url) ? (
                <img src={s.cover_url} alt="封面" className="mt-3 max-h-40 rounded-xl" />
              ) : null}

              {s.audio_url && isAbsUrl(s.audio_url) ? (
                // 审核试听：歌曲音频无对白/字幕轨，track 只是一个空的占位（jsx-a11y/media-has-caption）
                <audio controls src={s.audio_url} className="mt-3 w-full" aria-label="审核试听音频">
                  <track kind="captions" srcLang="zh" label="无字幕（歌曲音频）" />
                  你的浏览器不支持音频播放
                </audio>
              ) : null}

              {(s.lyric || s.prompt || s.style) && (
                <details className="mt-3 rounded-lg border-[var(--border)] p-2">
                  <summary className="cursor-pointer text-sm text-[var(--ink-soft)]">查看内容（歌词 / 提示词 / 风格）</summary>
                  <div className="mt-2 space-y-2 text-sm text-[var(--ink-soft)]">
                    {s.style ? <div><b>风格：</b>{s.style}</div> : null}
                    {s.prompt ? <div><b>提示词：</b>{s.prompt}</div> : null}
                    {s.lyric ? <div className="whitespace-pre-wrap"><b>歌词：</b>{s.lyric}</div> : null}
                  </div>
                </details>
              )}

              {ms === "rejected" && s.moderation_note ? (
                <p className="mt-3 rounded-lg bg-red-950 px-3 py-2 text-sm text-red-300">
                  驳回原因：{s.moderation_note}
                </p>
              ) : null}

              <div className="mt-3 flex gap-2">
                {ms !== "approved" && (
                  <button
                    onClick={() => approve(s.id)}
                    disabled={busy}
                    className="btn btn-primary"
                  >
                    通过
                  </button>
                )}
                {ms !== "rejected" && (
                  <button
                    onClick={() => setRejectTarget(s.id)}
                    disabled={busy}
                    className="btn btn-danger"
                  >
                    驳回
                  </button>
                )}
              </div>
            </div>
          );
        })}
        {songs.length === 0 && (
          <p className="rounded-2xl card p-6 text-center text-[var(--muted)]">
            这里没有歌曲
          </p>
        )}
      </div>

      {/* GAP-016 批量操作条：选中 ≥1 项时出现 */}
      {selected.size > 0 && (
        <div className="sticky bottom-4 mt-4 flex items-center justify-between gap-3 rounded-2xl card p-3">
          <span className="text-sm text-[var(--muted)]">已选 {selected.size} 首</span>
          <div className="flex gap-2">
            <button
              onClick={() => setSelected(new Set())}
              className="btn btn-ghost"
              disabled={busy}
            >
              清除
            </button>
            <button onClick={batchApprove} disabled={busy} className="btn btn-primary">
              批量通过
            </button>
            <button
              onClick={() => setBatchRejectOpen(true)}
              disabled={busy}
              className="btn btn-danger"
            >
              批量驳回
            </button>
          </div>
        </div>
      )}

      {hasMore && (
        <button
          onClick={loadMore}
          disabled={busy || loading}
          className="btn btn-secondary w-full"
        >
          {loading ? "加载中…" : "加载更多"}
        </button>
      )}
      </>
      )}

      {/* 批量驳回：必须填原因（与后端契约一致：无理由 400） */}
      {batchRejectOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
          <div className="w-full max-w-sm rounded-2xl card p-5">
            <h3 className="mb-3 text-lg font-semibold">批量驳回（{selected.size} 首）</h3>
            <textarea
              value={batchNote}
              onChange={(e) => setBatchNote(e.target.value)}
              rows={4}
              aria-label="批量驳回原因（必填）"
              placeholder="请填写驳回原因（必填，供作者申诉与追溯）"
              className="mb-4 w-full rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 py-2 text-base text-[var(--ink)] outline-none transition focus:border-[var(--ink-soft)]"
            />
            <div className="flex justify-end gap-2">
              <button
                onClick={() => {
                  setBatchRejectOpen(false);
                  setBatchNote("");
                }}
                className="btn btn-ghost"
              >
                取消
              </button>
              <button onClick={batchReject} disabled={busy} className="btn btn-danger">
                确认驳回
              </button>
            </div>
          </div>
        </div>
      )}

      {rejectTarget != null && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
          <div className="w-full max-w-sm rounded-2xl card p-5">
            <h3 className="mb-3 text-lg font-semibold">驳回原因（可选）</h3>
            <textarea
              value={rejectNote}
              onChange={(e) => setRejectNote(e.target.value)}
              rows={4}
              aria-label="驳回原因（可选）"
              placeholder="如不填将记录为「管理员驳回」"
              className="mb-4 w-full rounded-lg border border-[var(--border)] bg-[var(--surface)] px-3 py-2 text-base text-[var(--ink)] outline-none transition focus:border-[var(--ink-soft)]"
            />
            <div className="flex justify-end gap-2">
              <button
                onClick={() => {
                  setRejectTarget(null);
                  setRejectNote("");
                }}
                className="btn btn-ghost"
              >
                取消
              </button>
              <button
                onClick={confirmReject}
                disabled={busy}
                className="btn btn-danger"
              >
                确认驳回
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function BigCard({ emoji, title, value, sub }: { emoji: string; title: string; value: string; sub: string }) {
  return (
    <div className="rounded-2xl card p-4">
      <div className="text-2xl">{emoji}</div>
      <div className="mt-1 text-xs text-[var(--muted)]">{title}</div>
      <div className="text-lg font-semibold">{value}</div>
      <div className="text-xs text-[var(--muted)]">{sub}</div>
    </div>
  );
}
