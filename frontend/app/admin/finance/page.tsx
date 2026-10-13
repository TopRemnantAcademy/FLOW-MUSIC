"use client";

/**
 * 财务：结算 / 退款 / 发票导出（GAP-023）。
 *
 * 与「账单对账」(/admin/billing) 的分工必须说清，否则两个页面会被当成重复：
 *   · /admin/billing  = Mureka 实时账单 vs 本站估算**成本**（花钱，美元分）
 *   · /admin/finance  = 本站订单/订阅的**收入**与退款事实（收钱，人民币元）
 * 币种与口径都不同，合并展示只会让运维误读（后端也刻意分成两个端点）。
 */
import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { api, Settlement } from "@/lib/api";
import { clearAdminKey, getAdminKey } from "@/lib/adminAuth";
import AdminGuard from "@/components/AdminGuard";

const WINDOWS = [
  { label: "全部", days: 0 },
  { label: "近 7 天", days: 7 },
  { label: "近 30 天", days: 30 },
  { label: "近 90 天", days: 90 },
];

const STATUSES = ["", "paid", "refunded", "pending", "failed"];

export default function AdminFinancePage() {
  const [token, setToken] = useState<string | null>(null);
  const [data, setData] = useState<Settlement | null>(null);
  const [days, setDays] = useState(30);
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [refunding, setRefunding] = useState(false);

  useEffect(() => {
    setToken(getAdminKey() || null);
  }, []);

  const load = useCallback(async (d: number) => {
    setError("");
    setLoading(true);
    try {
      setData(await api.adminSettlement(d || undefined));
    } catch (e: any) {
      setError(e?.message || "加载失败");
      if (e?.status === 401 || e?.status === 403 || e?.status === 503) {
        clearAdminKey();
        setToken(null);
      }
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (token) load(days);
  }, [token, days, load]);

  if (token === null) {
    return (
      <AdminGuard
        title="财务结算"
        desc="请输入管理密钥（后台密钥），查看收入结算、导出对账明细与发票台账。"
        onUnlocked={(k) => setToken(k)}
      />
    );
  }

  const money = (n: number | undefined) => `¥${(n ?? 0).toFixed(2)}`;

  return (
    <div className="mx-auto max-w-4xl p-6">
      <div className="mb-5 flex items-center justify-between">
        <h1 className="text-2xl font-semibold">财务结算</h1>
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
        {WINDOWS.map((w) => (
          <button
            key={w.days}
            onClick={() => setDays(w.days)}
            disabled={loading}
            className="chip rounded-full px-3 py-1.5 text-sm disabled:opacity-50"
            data-on={days === w.days}
            aria-pressed={days === w.days}
          >
            {w.label}
          </button>
        ))}
      </div>

      <div className="mb-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Card label="已收款" value={money(data?.paid_amount_cny)} sub={`${data?.paid_count ?? 0} 笔`} />
        <Card label="已退款" value={money(data?.refunded_amount_cny)} sub={`${data?.refunded_count ?? 0} 笔`} />
        <Card label="净收入" value={money(data?.net_amount_cny)} sub="已收款 − 已退款" />
        <Card label="有效订阅" value={String(data?.active_subscriptions ?? 0)} sub="status=active" />
      </div>

      <div className="card mb-4 p-4">
        <div className="mb-2 text-sm text-[var(--muted)]">
          订单状态分布（共 {data?.order_count ?? 0} 笔：待付 {data?.pending_count ?? 0} · 失败{" "}
          {data?.failed_count ?? 0}）
        </div>
        <div className="grid gap-2 sm:grid-cols-2">
          <KV title="按支付渠道（已收款）" map={data?.by_provider} money={money} />
          <KV title="按套餐（已收款）" map={data?.by_tier} money={money} />
        </div>
      </div>

      <div className="card mb-4 p-4">
        <div className="mb-2 text-sm font-medium text-[var(--ink)]">成本 / 名义收入对照</div>
        <div className="grid grid-cols-3 gap-3 text-sm">
          <div>
            <div className="text-[var(--muted)]">供应商成本估算</div>
            <div className="text-lg">{money(data?.total_song_cost_cny)}</div>
          </div>
          <div>
            <div className="text-[var(--muted)]">名义收入（套餐标准价快照）</div>
            <div className="text-lg">{money(data?.total_song_revenue_cny)}</div>
          </div>
          <div>
            <div className="text-[var(--muted)]">毛利</div>
            <div className="text-lg">{money(data?.total_song_margin_cny)}</div>
          </div>
        </div>
        <p className="mt-2 text-xs text-[var(--muted)]">
          名义收入是「按套餐标准价算的应计额」，与上方「已收款」不是同一口径（未接支付时为 notional）。
          成本侧与 Mureka 实时账单的对照见{" "}
          <Link href="/admin/billing" className="underline">
            账单对账
          </Link>
          。
        </p>
      </div>

      <div className="card space-y-3 p-4">
        <div className="text-sm font-medium text-[var(--ink)]">导出（CSV，Excel 可直接打开）</div>
        <div className="flex flex-wrap items-center gap-2">
          <label className="text-xs text-[var(--muted)]">订单状态</label>
          <select value={status} onChange={(e) => setStatus(e.target.value)} className="input w-auto text-sm">
            {STATUSES.map((s) => (
              <option key={s} value={s}>
                {s || "全部"}
              </option>
            ))}
          </select>
          <a
            className="btn btn-secondary btn-sm"
            href={api.adminSettlementCsvUrl({ status: status || undefined, days: days || undefined })}
          >
            导出结算明细
          </a>
          <a className="btn btn-secondary btn-sm" href={api.adminInvoicesCsvUrl(days || undefined)}>
            导出发票台账
          </a>
        </div>
        <p className="text-xs text-[var(--muted)]">
          结算明细 = 全部订单（对账用）；发票台账 = 仅已收款订单，`invoice_status=to_issue`
          表示待开票 —— 本产品未接开票系统，**不伪造票号**。
        </p>
      </div>

      <div className="card mt-4 space-y-3 p-4">
        <div className="text-sm font-medium text-[var(--ink)]">退款（本地账务动作）</div>
        <div className="flex flex-wrap items-end gap-2">
          <label className="min-w-[140px]">
            <span className="mb-1 block text-xs text-[var(--muted)]">订单号</span>
            <input id="refund-order-id" inputMode="numeric" placeholder="order_id" className="input w-full" />
          </label>
          <label className="min-w-[200px] flex-1">
            <span className="mb-1 block text-xs text-[var(--muted)]">原因（可选，写入审计）</span>
            <input id="refund-reason" placeholder="如：用户申请 / 重复支付" className="input w-full" />
          </label>
          <button
            disabled={refunding}
            className="btn btn-danger btn-sm"
            onClick={async () => {
              const idEl = document.getElementById("refund-order-id") as HTMLInputElement | null;
              const reasonEl = document.getElementById("refund-reason") as HTMLInputElement | null;
              const oid = Number((idEl?.value || "").trim());
              if (!oid) {
                setError("请填写订单号");
                return;
              }
              const reason = (reasonEl?.value || "").trim();
              if (!window.confirm(`确认将订单 #${oid} 标记为已退款？该用户订阅将立即降回 free。`)) return;
              setError("");
              setRefunding(true);
              try {
                await api.adminRefundOrder(oid, reason);
                if (reasonEl) reasonEl.value = "";
                if (idEl) idEl.value = "";
                await load(days);
              } catch (e: any) {
                setError(e?.message || "退款标记失败");
              } finally {
                setRefunding(false);
              }
            }}
          >
            {refunding ? "处理中…" : "标记退款"}
          </button>
        </div>
        <p className="text-xs text-[var(--muted)]">
          ⚠ 这是**本地账务**动作：不向支付网关发起退款。真实退款请在供应商后台操作，或等其
          `charge.refunded` 回调打到 `/api/billing/webhook/&lt;provider&gt;`（两条路径幂等，不会互相打架）。
          标记后：订单 → refunded、订阅 → cancelled、套餐 → free（钱退了会员不能还在）。
        </p>
      </div>
    </div>
  );
}

function Card({ label, value, sub }: { label: string; value: string; sub: string }) {
  return (
    <div className="card rounded-2xl p-4">
      <div className="text-sm text-[var(--muted)]">{label}</div>
      <div className="text-2xl font-semibold text-[var(--ink)]">{value}</div>
      <div className="text-xs text-[var(--muted)]">{sub}</div>
    </div>
  );
}

function KV({
  title,
  map,
  money,
}: {
  title: string;
  map?: Record<string, number>;
  money: (n: number | undefined) => string;
}) {
  const entries = Object.entries(map || {});
  return (
    <div className="rounded-xl border border-[var(--border)] p-3">
      <div className="mb-1 text-xs text-[var(--muted)]">{title}</div>
      {entries.length === 0 ? (
        <div className="text-sm text-[var(--muted)]">暂无已收款</div>
      ) : (
        <div className="space-y-1">
          {entries.map(([k, v]) => (
            <div key={k} className="flex justify-between text-sm">
              <span className="text-[var(--ink-soft)]">{k}</span>
              <span className="font-mono">{money(v)}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
