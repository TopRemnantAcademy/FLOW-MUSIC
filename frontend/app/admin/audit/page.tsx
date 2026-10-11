"use client";

/**
 * 审计日志：后台管理操作记录（最近 limit 条，倒序）。
 * 后端 GET /api/admin/audit（admin.py:211，limit 默认 200，上限 1000）。
 * 每条：状态码 / 方法 / 路径 / 操作者角色 / 客户端 IP / 时间 / 令牌前缀。
 */
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { clearAdminKey, getAdminKey } from "@/lib/adminAuth";
import AdminGuard from "@/components/AdminGuard";

type AuditRow = {
  id: number;
  method: string;
  path: string;
  status_code: number;
  actor_token_prefix: string | null;
  actor_role: string | null;
  client_ip: string | null;
  created_at: string | null;
};

const LIMITS = [100, 200, 500];

function statusClass(code: number): string {
  if (code >= 500) return "bg-red-900 text-red-300";
  if (code >= 400) return "bg-amber-900 text-amber-300";
  return "bg-emerald-900 text-emerald-300";
}

function fmtTime(iso: string | null): string {
  if (!iso) return "-";
  // 后端 created_at 来自 SQLite CURRENT_TIMESTAMP（naive UTC），isoformat 无时区后缀；
  // 不补 Z 会被浏览器按本地时区解析 → UTC+9 用户显示慢 9 小时。
  const normalized = /[zZ]|[+-]\d{2}:?\d{2}$/.test(iso) ? iso : `${iso}Z`;
  const d = new Date(normalized);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("zh-CN", { hour12: false });
}

export default function AdminAuditPage() {
  // null = 尚未读取 localStorage（首帧不渲染门禁，避免闪烁；与 moderation 页一致）
  const [token, setToken] = useState<string | null>(null);
  const [rows, setRows] = useState<AuditRow[]>([]);
  const [limit, setLimit] = useState(200);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    setToken(getAdminKey() || null);
  }, []);

  const load = useCallback(async (n: number) => {
    setError("");
    setLoading(true);
    try {
      setRows((await api.adminAudit(n)) as unknown as AuditRow[]);
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

  useEffect(() => {
    if (token) load(limit);
  }, [token, limit, load]);

  if (token === null) {
    return (
      <AdminGuard
        title="审计日志"
        desc="请输入管理密钥（后台密钥），查看后台管理操作审计记录。"
        onUnlocked={(k) => setToken(k)}
      />
    );
  }

  return (
    <div className="mx-auto max-w-3xl p-6">
      <div className="mb-5 flex items-center justify-between">
        <h1 className="text-2xl font-semibold">审计日志</h1>
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

      <div className="mb-4 flex flex-wrap items-center gap-2">
        {LIMITS.map((n) => (
          <button
            key={n}
            onClick={() => setLimit(n)}
            disabled={loading}
            className="chip rounded-full px-3 py-1.5 text-sm disabled:opacity-50"
            data-on={limit === n}
            aria-pressed={limit === n}
          >
            最近 {n} 条
          </button>
        ))}
        <button
          onClick={() => load(limit)}
          disabled={loading}
          className="btn btn-secondary ml-auto px-3 py-1.5 text-sm"
        >
          {loading ? "刷新中…" : "刷新"}
        </button>
      </div>

      {loading && rows.length === 0 && (
        <p className="card p-6 text-center text-[var(--muted)]">加载中…</p>
      )}

      {!loading && rows.length === 0 && !error && (
        <p className="card p-6 text-center text-[var(--muted)]">暂无审计记录</p>
      )}

      <div className="space-y-2">
        {rows.map((r) => (
          <div key={r.id} className="rounded-xl card p-3">
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <span
                className={`shrink-0 rounded-full px-2.5 py-0.5 text-xs font-semibold ${statusClass(r.status_code)}`}
              >
                {r.status_code}
              </span>
              <span className="shrink-0 font-mono text-xs text-[var(--ink-soft)]">{r.method}</span>
              <span
                className="min-w-0 flex-1 truncate font-mono text-xs text-[var(--ink)]"
                title={r.path}
              >
                {r.path}
              </span>
              {r.actor_role ? (
                <span className="shrink-0 rounded-full border border-[var(--border-strong)] px-2 py-0.5 text-xs text-[var(--muted)]">
                  {r.actor_role}
                </span>
              ) : null}
            </div>
            <div className="mt-1.5 flex flex-wrap gap-x-4 gap-y-1 text-xs text-[var(--muted)]">
              <span>{fmtTime(r.created_at)}</span>
              {r.client_ip ? <span>IP {r.client_ip}</span> : null}
              {r.actor_token_prefix ? <span>token {r.actor_token_prefix}…</span> : null}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
