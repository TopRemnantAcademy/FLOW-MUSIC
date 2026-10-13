"use client";

/**
 * 用户管理（GAP-023）。
 * 后端：GET /api/admin/users（搜索/筛选/分页）、/users/{id}、POST ban|unban|tier|role。
 *
 * 三条与后端同源的规矩（不要在前端"顺手放宽"）：
 *   · 封禁必须填理由（后端 400）——无理由封禁在申诉时无法自证。
 *   · 角色调整后端 501（本架构的管理员是共享密钥，不是"用户角色"），
 *     前端如实展示后端给的解释，不假装成功。
 *   · 邮箱可显示，但页面上任何地方都不请求/展示凭据材料。
 */
import { useCallback, useEffect, useState } from "react";
import { api, AdminUserRow } from "@/lib/api";
import { clearAdminKey, getAdminKey } from "@/lib/adminAuth";
import AdminGuard from "@/components/AdminGuard";

const PAGE_SIZE = 25;
const TIERS = ["free", "lite", "standard", "sacred", "enterprise"];

function fmtTime(iso: string | null): string {
  if (!iso) return "-";
  // 后端时间来自 SQLite CURRENT_TIMESTAMP（naive UTC），不补 Z 会被按本地时区解析
  // → UTC+9 用户看到的时间慢 9 小时（与审计/桥接页同一处理）。
  const normalized = /[zZ]|[+-]\d{2}:?\d{2}$/.test(iso) ? iso : `${iso}Z`;
  const d = new Date(normalized);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString("zh-CN", { hour12: false });
}

export default function AdminUsersPage() {
  const [token, setToken] = useState<string | null>(null);
  const [rows, setRows] = useState<AdminUserRow[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(0);
  const [q, setQ] = useState("");
  const [tier, setTier] = useState("");
  const [banned, setBanned] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(0); // 正在操作的用户 id（按钮禁用，防双击重复提交）

  useEffect(() => {
    setToken(getAdminKey() || null);
  }, []);

  const load = useCallback(
    async (p: number, query: { q: string; tier: string; banned: string }) => {
      setError("");
      setLoading(true);
      try {
        const data = await api.adminUsers({
          q: query.q || undefined,
          tierKey: query.tier || undefined,
          banned: query.banned === "" ? undefined : query.banned === "1",
          limit: PAGE_SIZE,
          offset: p * PAGE_SIZE,
        });
        setRows(data.items);
        setTotal(data.total);
        setPage(p);
      } catch (e: any) {
        setError(e?.message || "加载失败");
        if (e?.status === 401 || e?.status === 403 || e?.status === 503) {
          clearAdminKey();
          setToken(null);
        }
      } finally {
        setLoading(false);
      }
    },
    [],
  );

  useEffect(() => {
    if (token) load(0, { q: "", tier: "", banned: "" });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  async function act(userId: number, fn: () => Promise<AdminUserRow>, okMsg: string) {
    setError("");
    setNotice("");
    setBusy(userId);
    try {
      const updated = await fn();
      setRows((prev) => prev.map((r) => (r.id === updated.id ? updated : r)));
      setNotice(okMsg);
    } catch (e: any) {
      setError(e?.message || "操作失败");
    } finally {
      setBusy(0);
    }
  }

  if (token === null) {
    return (
      <AdminGuard
        title="用户管理"
        desc="请输入管理密钥（后台密钥），管理用户套餐、封禁与状态。"
        onUnlocked={(k) => setToken(k)}
      />
    );
  }

  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <div className="mx-auto max-w-5xl p-6">
      <div className="mb-5 flex items-center justify-between">
        <h1 className="text-2xl font-semibold">用户管理</h1>
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
        <p className="mb-3 rounded-xl bg-red-950 px-4 py-3 text-base text-red-300">{error}</p>
      )}
      {notice && (
        <p className="mb-3 rounded-xl bg-emerald-950 px-4 py-3 text-base text-emerald-300">{notice}</p>
      )}

      <div className="card mb-4 space-y-3 p-4">
        <div className="flex flex-wrap gap-3">
          <label className="min-w-[200px] flex-1">
            <span className="mb-1 block text-xs text-[var(--muted)]">搜索（邮箱或用户 ID）</span>
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && load(0, { q, tier, banned })}
              placeholder="user@example.com 或 123"
              className="input w-full"
            />
          </label>
          <label className="min-w-[140px]">
            <span className="mb-1 block text-xs text-[var(--muted)]">套餐</span>
            <select value={tier} onChange={(e) => setTier(e.target.value)} className="input w-full">
              <option value="">全部</option>
              {TIERS.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          </label>
          <label className="min-w-[140px]">
            <span className="mb-1 block text-xs text-[var(--muted)]">状态</span>
            <select value={banned} onChange={(e) => setBanned(e.target.value)} className="input w-full">
              <option value="">全部</option>
              <option value="0">正常</option>
              <option value="1">已封禁</option>
            </select>
          </label>
          <button
            onClick={() => load(0, { q, tier, banned })}
            disabled={loading}
            className="btn btn-secondary self-end px-4 py-1.5 text-sm"
          >
            {loading ? "查询中…" : "查询"}
          </button>
        </div>
        <p className="text-xs text-[var(--muted)]">
          共 {total} 位用户 · 第 {page + 1} / {pages} 页
        </p>
      </div>

      <div className="space-y-2">
        {rows.map((u) => (
          <div key={u.id} className="card p-3">
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <span className="font-mono text-xs text-[var(--muted)]">#{u.id}</span>
              <span className="font-medium text-[var(--ink)]">{u.email}</span>
              <span className="rounded-full border border-[var(--border-strong)] px-2 py-0.5 text-xs text-[var(--muted)]">
                {u.tier_key || "free"}
              </span>
              {u.is_banned ? (
                <span className="rounded-full bg-red-900 px-2 py-0.5 text-xs font-semibold text-red-300">
                  已封禁
                </span>
              ) : (
                <span className="rounded-full bg-emerald-900 px-2 py-0.5 text-xs font-semibold text-emerald-300">
                  正常
                </span>
              )}
              {u.mfa_enabled && (
                <span className="rounded-full bg-zinc-800 px-2 py-0.5 text-xs text-zinc-300">MFA</span>
              )}
              {u.email_verified && (
                <span className="rounded-full bg-zinc-800 px-2 py-0.5 text-xs text-zinc-300">邮箱已验证</span>
              )}
              {u.locked_until && (
                <span className="rounded-full bg-amber-900 px-2 py-0.5 text-xs text-amber-300">
                  锁定至 {fmtTime(u.locked_until)}
                </span>
              )}
            </div>

            <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-[var(--muted)]">
              <span>生成 {u.song_count} 首</span>
              <span>实付 ¥{u.paid_cny.toFixed(2)}</span>
              <span>注册 {fmtTime(u.created_at)}</span>
              {u.failed_logins > 0 && <span>连续登录失败 {u.failed_logins} 次</span>}
              {u.ban_reason && <span className="text-red-300">封禁理由：{u.ban_reason}</span>}
            </div>

            <div className="mt-2 flex flex-wrap items-center gap-2">
              <select
                value={u.tier_key || "free"}
                disabled={busy === u.id}
                onChange={(e) =>
                  act(
                    u.id,
                    () => api.adminSetUserTier(u.id, e.target.value),
                    `已将 #${u.id} 套餐改为 ${e.target.value}`,
                  )
                }
                className="input w-auto text-sm disabled:opacity-50"
                aria-label={`修改 #${u.id} 套餐`}
              >
                {TIERS.map((t) => (
                  <option key={t} value={t}>
                    套餐：{t}
                  </option>
                ))}
              </select>

              {u.is_banned ? (
                <button
                  onClick={() => act(u.id, () => api.adminUnbanUser(u.id), `已解封 #${u.id}`)}
                  disabled={busy === u.id}
                  className="btn btn-secondary btn-sm"
                >
                  解封
                </button>
              ) : (
                <button
                  onClick={() => {
                    const reason = window.prompt(`封禁 #${u.id} 的理由（必填，将写入审计）：`);
                    if (reason && reason.trim()) {
                      act(u.id, () => api.adminBanUser(u.id, reason.trim()), `已封禁 #${u.id}`);
                    }
                  }}
                  disabled={busy === u.id}
                  className="btn btn-danger btn-sm"
                >
                  封禁
                </button>
              )}

              <button
                onClick={async () => {
                  // 角色调整：后端**明确** 501（本架构无用户角色，管理员是共享密钥）。
                  // 这里把后端的解释原样显示，而不是吞掉错误假装成功。
                  setError("");
                  setNotice("");
                  try {
                    await api.adminSetUserTier(u.id, u.tier_key || "free");
                  } catch {
                    /* 幂等检查（同档位 409）不该拦截下面的说明 */
                  }
                  try {
                    const res = await fetch(
                      `${process.env.NEXT_PUBLIC_API_BASE || ""}/api/admin/users/${u.id}/role`,
                      {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        credentials: "include",
                        body: JSON.stringify({ role: "admin" }),
                      },
                    );
                    const body = await res.json().catch(() => ({}));
                    setError(
                      `角色调整未生效（HTTP ${res.status}）：${body?.detail || "无说明"}`,
                    );
                  } catch (e: any) {
                    setError(e?.message || "角色调整请求失败");
                  }
                }}
                disabled={busy === u.id}
                className="btn btn-ghost btn-sm"
                title="本架构的管理员身份是共享密钥，不是用户角色"
              >
                角色说明
              </button>
            </div>
          </div>
        ))}
      </div>

      {!loading && rows.length === 0 && !error && (
        <p className="card p-6 text-center text-[var(--muted)]">没有匹配的用户</p>
      )}

      <div className="mt-4 flex items-center justify-center gap-3">
        <button
          onClick={() => load(page - 1, { q, tier, banned })}
          disabled={page <= 0 || loading}
          className="btn btn-secondary btn-sm disabled:opacity-50"
        >
          上一页
        </button>
        <span className="text-sm text-[var(--muted)]">
          {page + 1} / {pages}
        </span>
        <button
          onClick={() => load(page + 1, { q, tier, banned })}
          disabled={page + 1 >= pages || loading}
          className="btn btn-secondary btn-sm disabled:opacity-50"
        >
          下一页
        </button>
      </div>

      <p className="mt-4 text-xs text-[var(--muted)]">
        封禁 = 立即挡登录 + 下次访问回收付费档（已签发令牌最长 24h 内仍有效，属既有架构边界）；
        所有动作写入审计日志，可在「审计日志」页查看。
      </p>
    </div>
  );
}
