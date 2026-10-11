"use client";

/**
 * 账单对账：Mureka 实时账单（GET /v1/account/billing，查询本身不计费）
 * vs 本站 songs 表累计估算成本（estimated_cost_cny）。
 * 后端 GET /api/admin/billing（admin.py:170）；
 * live.* 为美元分（cents），estimated_cost_cny 为人民币元 —— 币种不同仅量级参考（delta_note）。
 */
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { clearAdminKey, getAdminKey } from "@/lib/adminAuth";
import AdminGuard from "@/components/AdminGuard";

type LiveBilling = {
  account_id: string | number | null;
  balance: number | null;
  total_recharge: number | null;
  total_spending: number | null;
  concurrent_request_limit: number | null;
};

type BillingResp = {
  provider: string;
  configured: boolean;
  estimated_cost_cny: number;
  live: LiveBilling | null;
  delta_note?: string;
  live_error?: string;
};

/** 美元分 → "$1,234.56"（与后端注释一致：live.* 单位为美元分） */
function centsToUsd(c: number | null | undefined): string {
  if (c == null) return "-";
  return `$${(c / 100).toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

export default function AdminBillingPage() {
  // null = 尚未读取 localStorage（首帧不渲染门禁，避免闪烁；与 moderation 页一致）
  const [token, setToken] = useState<string | null>(null);
  const [data, setData] = useState<BillingResp | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    setToken(getAdminKey() || null);
  }, []);

  const load = useCallback(async () => {
    setError("");
    setLoading(true);
    try {
      setData((await api.adminBilling()) as unknown as BillingResp);
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
    if (token) load();
  }, [token, load]);

  if (token === null) {
    return (
      <AdminGuard
        title="账单对账"
        desc="请输入管理密钥（后台密钥），查看 Mureka 实时账单与本站估算成本对照。"
        onUnlocked={(k) => setToken(k)}
      />
    );
  }

  const live = data?.live ?? null;

  return (
    <div className="mx-auto max-w-3xl p-6">
      <div className="mb-5 flex items-center justify-between">
        <h1 className="text-2xl font-semibold">账单对账</h1>
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

      {!data && !error && (
        <p className="card p-6 text-center text-[var(--muted)]">加载中…</p>
      )}

      {data && (
        <>
          <div className="mb-3 flex flex-wrap items-center gap-2 text-sm text-[var(--muted)]">
            <span className="rounded-full border border-[var(--border-strong)] px-3 py-1 text-xs">
              {data.provider}
            </span>
            <span>
              {data.configured ? "✅ 已配置 API Key" : "⚠️ 未配置 MUREKA_API_KEY"}
            </span>
            <button
              onClick={load}
              disabled={loading}
              className="btn btn-secondary ml-auto px-3 py-1.5 text-sm"
            >
              {loading ? "刷新中…" : "刷新"}
            </button>
          </div>

          {!data.configured && (
            <p className="mb-4 rounded-xl bg-amber-950 px-4 py-3 text-sm text-amber-300">
              未配置 MUREKA_API_KEY，无法查询 Mureka 实时账单（仅显示本站估算成本）。
            </p>
          )}

          {data.live_error && (
            <p className="mb-4 rounded-xl bg-red-950 px-4 py-3 text-sm text-red-300">
              实时账单查询失败：{data.live_error}
            </p>
          )}

          {/* 实时账单（美元分） */}
          <div className="mb-5 grid grid-cols-2 gap-3 sm:grid-cols-4">
            <BigCard emoji="💳" title="余额" value={live ? centsToUsd(live.balance) : "-"} sub="Mureka 账户余额" />
            <BigCard emoji="🪙" title="累计充值" value={live ? centsToUsd(live.total_recharge) : "-"} sub="total_recharge" />
            <BigCard emoji="📉" title="累计消费" value={live ? centsToUsd(live.total_spending) : "-"} sub="total_spending" />
            <BigCard
              emoji="🔀"
              title="并发上限"
              value={live?.concurrent_request_limit != null ? String(live.concurrent_request_limit) : "-"}
              sub="concurrent_request_limit"
            />
          </div>

          {/* 本站估算（人民币元） */}
          <div className="card rounded-2xl p-4">
            <div className="text-sm text-[var(--muted)]">本站累计估算成本（songs.cost_cny 合计）</div>
            <div className="text-2xl font-semibold">¥{data.estimated_cost_cny.toFixed(2)}</div>
          </div>

          {data.delta_note && (
            <p className="mt-3 rounded-xl border border-[var(--border)] px-4 py-3 text-xs text-[var(--muted)]">
              {data.delta_note}
            </p>
          )}
        </>
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
