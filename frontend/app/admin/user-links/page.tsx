"use client";

/**
 * 跨域账号桥接：查看 FLOW 订阅者 ↔ 音乐用户的关联（user_links）。
 * 后端 GET /api/admin/user-links（admin.py，零 PII：email 仅以存在性布尔标记出现）。
 * 支持按 flow_subscriber_id / music_user_id 检索。
 */
import { useCallback, useEffect, useState } from "react";
import { api, UserLinkRow } from "@/lib/api";
import { clearAdminKey, getAdminKey } from "@/lib/adminAuth";
import AdminGuard from "@/components/AdminGuard";

const LIMITS = [50, 200, 500];

function subStatusClass(status: string): string {
  switch (status) {
    case "active":
      return "bg-emerald-900 text-emerald-300";
    case "past_due":
    case "unpaid":
      return "bg-amber-900 text-amber-300";
    case "cancelled":
    case "expired":
      return "bg-red-900 text-red-300";
    default:
      return "bg-zinc-800 text-zinc-300";
  }
}

function fmtTime(iso: string | null): string {
  if (!iso) return "-";
  // 后端 created_at / linked_at 来自 SQLite CURRENT_TIMESTAMP（naive UTC），isoformat 无时区后缀；
  // 不补 Z 会被浏览器按本地时区解析 → UTC+9 用户显示慢 9 小时（与审计页一致处理）。
  const normalized = /[zZ]|[+-]\d{2}:?\d{2}$/.test(iso) ? iso : `${iso}Z`;
  const d = new Date(normalized);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("zh-CN", { hour12: false });
}

export default function AdminUserLinksPage() {
  // null = 尚未读取 localStorage（首帧不渲染门禁，避免闪烁；与 moderation/audit 页一致）
  const [token, setToken] = useState<string | null>(null);
  const [rows, setRows] = useState<UserLinkRow[]>([]);
  const [subscriberId, setSubscriberId] = useState("");
  const [musicUserId, setMusicUserId] = useState("");
  const [limit, setLimit] = useState(200);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    setToken(getAdminKey() || null);
  }, []);

  const load = useCallback(async (params: { subscriberId?: string; musicUserId?: number; limit: number }) => {
    setError("");
    setLoading(true);
    try {
      setRows(
        await api.adminUserLinks({
          subscriberId: params.subscriberId || undefined,
          musicUserId: params.musicUserId,
          limit: params.limit,
        }),
      );
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
  }, []);

  // 初次进入：无条件列出最近 limit 条
  useEffect(() => {
    if (token) load({ limit });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  if (token === null) {
    return (
      <AdminGuard
        title="跨域账号桥接"
        desc="请输入管理密钥（后台密钥），查看 FLOW 订阅者与音乐用户的关联。"
        onUnlocked={(k) => setToken(k)}
      />
    );
  }

  function onSearch() {
    const uid = musicUserId.trim();
    load({
      subscriberId: subscriberId.trim(),
      musicUserId: uid ? Number(uid) : undefined,
      limit,
    });
  }

  return (
    <div className="mx-auto max-w-3xl p-6">
      <div className="mb-5 flex items-center justify-between">
        <h1 className="text-2xl font-semibold">跨域账号桥接</h1>
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

      <div className="card mb-4 space-y-3 p-4">
        <div className="flex flex-wrap gap-3">
          <label className="min-w-[200px] flex-1">
            <span className="mb-1 block text-xs text-[var(--muted)]">FLOW 订阅者 ID</span>
            <input
              value={subscriberId}
              onChange={(e) => setSubscriberId(e.target.value)}
              placeholder="flow_subscriber_id (UUID)"
              className="input w-full"
            />
          </label>
          <label className="min-w-[160px] flex-1">
            <span className="mb-1 block text-xs text-[var(--muted)]">音乐用户 ID</span>
            <input
              value={musicUserId}
              onChange={(e) => setMusicUserId(e.target.value)}
              placeholder="music_user_id (整数)"
              inputMode="numeric"
              className="input w-full"
            />
          </label>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {LIMITS.map((n) => (
            <button
              key={n}
              onClick={() => setLimit(n)}
              disabled={loading}
              className="chip rounded-full px-3 py-1.5 text-sm disabled:opacity-50"
              data-on={limit === n}
              aria-pressed={limit === n}
            >
              最多 {n} 条
            </button>
          ))}
          <button
            onClick={onSearch}
            disabled={loading}
            className="btn btn-secondary ml-auto px-4 py-1.5 text-sm"
          >
            {loading ? "查询中…" : "查询"}
          </button>
        </div>
        <p className="text-xs text-[var(--muted)]">
          仅展示桥接关系与订阅状态；不返回任何 PII（邮箱仅以「已登记 / 未登记」布尔标记出现）。
        </p>
      </div>

      {loading && rows.length === 0 && (
        <p className="card p-6 text-center text-[var(--muted)]">加载中…</p>
      )}

      {!loading && rows.length === 0 && !error && (
        <p className="card p-6 text-center text-[var(--muted)]">暂无关联记录</p>
      )}

      <div className="space-y-2">
        {rows.map((r, i) => (
          <div key={`${r.flow_subscriber_id}-${r.music_user_id}-${i}`} className="rounded-xl card p-3">
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <span className="shrink-0 rounded-full border border-[var(--border-strong)] px-2 py-0.5 text-xs text-[var(--muted)]">
                FLOW {r.flow_subscriber_id.slice(0, 8)}…
              </span>
              <span className="shrink-0 font-mono text-xs text-[var(--ink-soft)]">↔</span>
              <span className="shrink-0 rounded-full border border-[var(--border-strong)] px-2 py-0.5 text-xs text-[var(--muted)]">
                音乐 #{r.music_user_id}
              </span>
              <span
                className={`shrink-0 rounded-full px-2 py-0.5 text-xs font-semibold ${
                  r.music_email_present
                    ? "bg-emerald-900 text-emerald-300"
                    : "bg-zinc-800 text-zinc-400"
                }`}
              >
                {r.music_email_present ? "邮箱已登记" : "邮箱未登记"}
              </span>
              {r.music_tier_key ? (
                <span className="shrink-0 rounded-full border border-[var(--border-strong)] px-2 py-0.5 text-xs text-[var(--muted)]">
                  {r.music_tier_key}
                </span>
              ) : null}
            </div>

            <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-[var(--muted)]">
              <span>关联于 {fmtTime(r.linked_at)}</span>
              {r.subscription ? (
                <>
                  <span
                    className={`rounded-full px-2 py-0.5 text-xs font-semibold ${subStatusClass(r.subscription.status)}`}
                  >
                    {r.subscription.status}
                  </span>
                  <span>渠道 {r.subscription.provider}</span>
                  <span>档位 {r.subscription.tier_key}</span>
                  <span>到期 {fmtTime(r.subscription.current_period_end)}</span>
                </>
              ) : (
                <span>无有效订阅</span>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
