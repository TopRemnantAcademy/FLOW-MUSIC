"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import {
  api,
  sendContactMessage,
  type BillingOrder,
  type BillingPlan,
  type BillingProvider,
  type BillingSubscription,
  type MyBilling,
} from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { QRCodeSVG } from "qrcode.react";

// 네이버페이 JS SDK 를 동적으로 로드.
// ⚠ 공식 스크립트 주소는 **https://nsp.pay.naver.com/sdk/js/naverpay.min.js** 이다.
//   (공식 문서 명시: "script include 시 https://nsp.pay.naver.com/sdk/js/naverpay.min.js 는
//    반드시 해당 링크를 통해 서비스에 적용하여야 합니다.")
//   출처: https://docs.pay.naver.com/docs/onetime-payment/payment/payment-auth-window
//   pay.naver.com/sdk/... 는 공식 주소가 아니므로 SDK 로드가 실패한다.
const NAVER_SDK_URL = "https://nsp.pay.naver.com/sdk/js/naverpay.min.js";

// oPay.open(...) 이 호출되면 네이버가 결제창을 띄우고, 완료 시 returnUrl(landing) 으로
// `?resultCode=Success&paymentId=...` 를 붙여 리다이렉트한다(실패 시 resultCode/resultMessage/reserveId).
function loadNaverSdk(scriptUrl?: string): Promise<any> {
  return new Promise((resolve, reject) => {
    if (typeof window !== "undefined" && (window as any).NaverPay) {
      return resolve((window as any).NaverPay);
    }
    const s = document.createElement("script");
    s.src = scriptUrl || NAVER_SDK_URL;
    s.async = true;
    s.onload = () => {
      if ((window as any).NaverPay) resolve((window as any).NaverPay);
      else reject(new Error("支付组件初始化失败"));
    };
    s.onerror = () => reject(new Error("支付组件加载失败（网络或被拦截）"));
    document.body.appendChild(s);
  });
}

// 档位/支付方式/订单状态的中文显示名（UI 全中文规则；key 为内部值，不改动）
const TIER_CN: Record<string, string> = {
  free: "免费档", lite: "轻量档", standard: "标准档", sacred: "专业档", enterprise: "机构档",
};
const PROVIDER_CN: Record<string, string> = {
  manual: "人工确认", stripe: "国际卡支付", kakao: "韩国卡支付", naver: "韩国简易支付",
  wechat: "微信支付", alipay: "支付宝", paypal: "国际钱包",
};
const ORDER_STATUS_CN: Record<string, string> = {
  created: "已创建", pending: "处理中", paid: "已支付", failed: "已失败",
  expired: "已过期", canceled: "已取消", refunded: "已退款",
};
// 币种代码 → 中文（下拉框选项 key 不动）
const CURRENCY_CN: Record<string, string> = { CNY: "人民币", USD: "美元", KRW: "韩元" };
const tierCn = (t?: string | null) => (t ? TIER_CN[t] ?? t : "免费档");
const providerCn = (p?: string | null) => (p ? PROVIDER_CN[p] ?? p : p ?? "");
const statusCn = (s?: string | null) => (s ? ORDER_STATUS_CN[s] ?? s : s ?? "");

export default function AccountPage() {
  const { isAuthed, email } = useAuth();
  const [data, setData] = useState<MyBilling | null>(null);
  const [sub, setSub] = useState<BillingSubscription | null>(null);
  const [orders, setOrders] = useState<BillingOrder[]>([]);
  const [plans, setPlans] = useState<BillingPlan[]>([]);
  const [providers, setProviders] = useState<BillingProvider[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [tier, setTier] = useState("standard");
  const [currency, setCurrency] = useState("CNY");
  const [upgrading, setUpgrading] = useState(false);
  const [approving, setApproving] = useState(false);
  const [qr, setQr] = useState<{ url: string; orderId: number } | null>(null);
  const [naverParams, setNaverParams] = useState<Record<string, unknown> | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // 单一 active provider(settings.billing_provider)가 결정하는 결제/币种硬约束.
  // kakao/naver 는 KRW 단일, stripe/alipay/wechat 은 다币种. backend require_currency 와 동일 source of truth.
  const defaultProvider = providers.find((p) => p.is_default) || null;
  const allowedCurrency = defaultProvider?.currency || null; // null = 多币种(请求에서 결정)

  async function refreshAll() {
    const [billing, subscription, myOrders, plansResp] = await Promise.all([
      api.meBilling(),
      api.mySubscription(),
      api.myOrders(),
      api.plans(),
    ]);
    setData(billing);
    setSub(subscription);
    setOrders(myOrders);
    setPlans(plansResp);
  }

  function clearPoll() {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }

  // webhook 型(stripe/alipay/wechat): 跳转/扫码后 서버가 비동기 승격 → 프론트는 구독 상태를 폴링.
  async function pollUntilPaid(orderId: number) {
    clearPoll();
    let attempts = 0;
    pollRef.current = setInterval(async () => {
      attempts += 1;
      try {
        const s = await api.mySubscription();
        setSub(s);
        if (s.active) {
          clearPoll();
          setQr(null);
          await refreshAll();
        } else if (attempts >= 40) {
          // 약 2분(3s×40) 내 미도착: 무한 폴링 방지 + 사용자 안내.
          clearPoll();
          setError("支付结果尚未到账，请稍后刷新账户页查看；若已扣款仍未升级，请联系客服。");
        }
      } catch {
        // 轮询실패는 조용히 재시도(일시적 네트워크 등).
      }
    }, 3000);
  }

  // approve 型(kakao/naver): 回跳 쿼리의 pg_token / paymentId 를 서버 approve 엔드포인트로 전달.
  async function finalizeApprove(kind: "kakao" | "naver", orderId: number, token: string) {
    setApproving(true);
    setError("");
    try {
      if (kind === "kakao") {
        await api.approveKakao(orderId, token);
      } else {
        await api.approveNaver(orderId, token);
      }
      await refreshAll();
      setNaverParams(null);
      setQr(null);
    } catch (e: any) {
      setError(e?.message || "支付确认失败");
    } finally {
      setApproving(false);
    }
  }

  // 挂载时: kakao/naver 回跳 처리(approve 型은 webhook 이 없으므로 프론트가 토큰을 서버로 넘겨야 승격됨).
  // BILLING_RETURN_URL 이 이 계정 페이지(/account) 를 가리켜야 함(后端 kakao/naver 가 approval/cancel/fail/return 로 쿼리 붙여 리다이렉트).
  useEffect(() => {
    if (!isAuthed) return;
    const q = new URLSearchParams(window.location.search);
    const prov = q.get("provider");
    const orderId = Number(q.get("order_id") || "");
    const cleanUrl = () => window.history.replaceState({}, "", window.location.pathname);

    if (q.get("canceled") === "1" || q.get("failed") === "1") {
      setError("支付已取消或失败，请重试。");
      cleanUrl();
      return;
    }
    // --- 공식 회귀 파라미터(실증됨) ---
    //  kakao : approval_url 뒤에 **pg_token** 이 query string 으로 붙어 온다.
    //          (공식: "approval_url로 redirection 해줄 때 pg_token을 query string으로 전달")
    //  naver : returnUrl 로 `?resultCode=Success&paymentId=...` 가 온다.
    //          실패/취소는 `?resultCode=UserCancel&resultMessage=...&reserveId=...` 이므로
    //          **resultCode 를 먼저 검증하지 않으면 paymentId 없이 승인을 시도하게 된다.**
    if (prov === "naver" && orderId) {
      const resultCode = q.get("resultCode");
      const paymentId = q.get("paymentId") || "";
      cleanUrl();
      if (resultCode && resultCode !== "Success") {
        const reason = q.get("resultMessage") || resultCode;
        setError(
          resultCode === "UserCancel"
            ? "支付已取消，请重试。"
            : `支付失败（${reason}），请重试`
        );
        setNaverParams(null);
        return;
      }
      if (paymentId) {
        finalizeApprove("naver", orderId, paymentId);
        return;
      }
      // resultCode 도 paymentId 도 없다 → 정상 진입(회귀 아님). 아래 공통 처리를 타지 않게 종료.
      return;
    }

    if (prov === "kakao" && orderId && q.get("pg_token")) {
      cleanUrl();
      finalizeApprove("kakao", orderId, q.get("pg_token") as string);
      return;
    }

    // stripe/alipay(webhook 型): 回跳带 order_id(无 token) → 启动轮询, 待 서버 승격后自动반영.
    // (kakao/naver 는 위에서 approve 를 처리하므로 제외)
    if (orderId && prov !== "kakao" && prov !== "naver") {
      cleanUrl();
      pollUntilPaid(orderId);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isAuthed]);

  // 초기 로드 + providers(币种/결제방식 제약 source of truth)
  useEffect(() => {
    if (!isAuthed) return;
    let cancelled = false;
    setLoading(true);
    setError("");
    Promise.all([
      api.meBilling(),
      api.mySubscription(),
      api.myOrders(),
      api.plans(),
      // providers 는 선택적: 실패해도 나머지 로드는 유지.
      api.providers().catch(() => ({ providers: [], default: "", mode: "" })),
    ])
      .then(([billing, subscription, myOrders, plansResp, provResp]) => {
        if (cancelled) return;
        setData(billing);
        setSub(subscription);
        setOrders(myOrders);
        setPlans(plansResp);
        setProviders(provResp.providers as BillingProvider[]);
      })
      .catch((e: any) => {
        if (!cancelled) setError(e?.message || "加载失败");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
      clearPoll();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isAuthed]);

  // 단일 provider 가 KRW 전용이면 통화 선택기를 해당 통화로 고정(后端 require_currency 400 방지).
  useEffect(() => {
    if (allowedCurrency) setCurrency(allowedCurrency);
  }, [allowedCurrency]);

  async function upgrade() {
    setUpgrading(true);
    setError("");
    setQr(null);
    setNaverParams(null);
    try {
      if (tier === "free") {
        setError("免费档为默认档，无需支付。请选择轻量档 / 标准档 / 专业档 / 机构档升级。");
        return;
      }
      // 단일 active provider 가 KRW 전용이면 강제 KRW(다른 통화 선택 시 backend 400).
      const cur = allowedCurrency || currency;
      const co = await api.checkout(tier, undefined, cur);
      const prov = co.provider;

      if (prov === "manual") {
        // 线下/对账模式: 属主 본인 confirm 이 승격 근거(step-1).
        await api.confirmOrder(co.order_id);
        await refreshAll();
        return;
      }

      if (prov === "wechat") {
        // Native QR: checkout_url = weixin://..., 프론트가 QR 로 렌더. 서버 webhook(验签) 승격 후 폴링으로 반영.
        const url =
          (co.provider_params?.qr_code_url as string | undefined) || co.checkout_url;
        setQr({ url, orderId: co.order_id });
        await pollUntilPaid(co.order_id);
        return;
      }

      if (prov === "naver") {
        // SDK handoff: 이 페이지(landing)에서 provider_params 로 oPay.open 호출.
        // 결제 후 네이버가 returnUrl(landing) 로 paymentId 를 붙여 리다이렉트 → 挂载 핸들러가 approve 호출.
        const params = (co.provider_params as Record<string, unknown>) || {};
        setNaverParams(params);
        return;
      }

      // kakao / stripe / alipay: 收银台 redirect.
      //  - kakao: 승인형 → 리다이렉트 후 approval_url(pg_token) 로 회귀 → approve 호출.
      //  - stripe/alipay: webhook 형 → 서버 비동기 승격 → 폴링으로 반영.
      window.location.href = co.checkout_url;
    } catch (e: any) {
      setError(e?.message || "升级失败");
    } finally {
      setUpgrading(false);
    }
  }

  async function openNaver() {
    if (!naverParams) return;
    setUpgrading(true);
    setError("");
    try {
      // 공식 스크립트 주소는 backend 가 내려준 값을 우선(없으면 상수) — 오타 방지.
      const NaverPay: any = await loadNaverSdk(naverParams.sdk_script as string | undefined);
      const oPay = NaverPay.create({
        mode: naverParams.mode,
        // 공식: 일반결제 SDK 는 payType 을 "normal" 로 설정한다.
        payType: (naverParams.payType as string) || "normal",
        clientId: naverParams.clientId,
        chainId: naverParams.chainId,
      });
      oPay.open({
        merchantUserKey: naverParams.merchantUserKey,
        merchantPayKey: naverParams.merchantPayKey,
        productName: naverParams.productName,
        totalPayAmount: naverParams.totalPayAmount,
        taxScopeAmount: naverParams.taxScopeAmount,
        taxExScopeAmount: naverParams.taxExScopeAmount,
        returnUrl: naverParams.returnUrl,
        // 공식 필수 파라미터(누락 시 결제창이 열리지 않는다).
        productCount: naverParams.productCount ?? 1,
        productItems: naverParams.productItems ?? [],
      });
    } catch (e: any) {
      setError(e?.message || "支付组件异常");
    } finally {
      setUpgrading(false);
    }
  }

  function planPrice(tierKey: string, cur: string): string | null {
    const p = plans.find((x) => x.tier_key === tierKey);
    if (!p) return null;
    const val = cur === "USD" ? p.price_usd : cur === "KRW" ? p.price_krw : p.price_cny;
    const sym = cur === "USD" ? "$" : cur === "KRW" ? "₩" : "¥";
    // 価格이 0(데이터 미입력/무료)이면 "—" 표시(¥0.00 오표기 방지), 실제 0원 결제는 backend 에서 차단.
    if (val === 0) return null;
    return `${sym}${val.toFixed(2)}`;
  }

  async function cancelSub() {
    setUpgrading(true);
    setError("");
    try {
      await api.cancelSubscription();
      await refreshAll();
    } catch (e: any) {
      setError(e?.message || "取消订阅失败");
    } finally {
      setUpgrading(false);
    }
  }

  async function reactivateSub() {
    setUpgrading(true);
    setError("");
    try {
      await api.reactivateSubscription();
      await refreshAll();
    } catch (e: any) {
      setError(e?.message || "恢复订阅失败");
    } finally {
      setUpgrading(false);
    }
  }

  if (!isAuthed) {
    // 2026-10-04: 未登录也要能留下 문의 —— 백엔드가 익명을 지원하고 화면 文案도
    // 「无需登录」라고 안내하므로, 로그인 게이트 **뒤에** 두면 文案과 behaviour 가 어긋난다.
    return (
      <main className="mx-auto max-w-3xl px-4 py-10">
        <h1 className="mb-1 text-2xl font-bold">账户</h1>
        <p className="text-[var(--muted)]">请先在右上角登录或注册。</p>
        <ContactSection />
      </main>
    );
  }

  const fmt = (n: number) => `¥${n.toFixed(4)}`;
  // 결제 진행 중(wechat QR / naver SDK)이면 업그레이드 버튼 대신 해당 플로우 UI 를 노출.
  const inPaymentFlow = !!qr || !!naverParams;

  return (
    <main className="mx-auto max-w-3xl px-4 py-10">
      <h1 className="mb-1 text-2xl font-bold">账户 · 用量与账单</h1>
      <p className="mb-6 text-sm text-[var(--muted)]">
        {email} · 当前套餐：<span className="text-[var(--ink-soft)]">{tierCn(data?.tier_key)}</span>
      </p>

      {loading && <p className="text-[var(--muted)]">加载中…</p>}
      {error && (
        <p className="text-sm text-[var(--danger)]" role="alert">
          {error}
        </p>
      )}

      {data && (
        <>
          {/* 今日生成额度（此前后端已返回 daily_quota_used/limit，前端从未渲染） */}
          {data.daily_quota_limit > 0 && (
            <div className="mb-4 card p-4">
              <div className="mb-1 flex items-center justify-between text-sm">
                <span className="text-[var(--ink-soft)]">今日生成额度</span>
                <span className="text-[var(--ink-soft)]">
                  {data.daily_quota_used} / {data.daily_quota_limit}
                </span>
              </div>
              <div className="h-1.5 overflow-hidden rounded-full bg-[var(--surface-2)]">
                <div
                  className={
                    data.daily_quota_used >= data.daily_quota_limit
                      ? "h-full bg-[var(--danger)]"
                      : "h-full bg-[var(--ink-soft)]"
                  }
                  style={{
                    width: `${Math.min(100, (data.daily_quota_used / data.daily_quota_limit) * 100)}%`,
                  }}
                />
              </div>
              {data.daily_quota_used >= data.daily_quota_limit && (
                <p className="mt-2 text-xs text-[var(--danger)]">
                  今日免费额度已用完，订阅后可继续生成。
                </p>
              )}
            </div>
          )}
          <div className="mb-8 grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Card label="歌曲总数" value={String(data.total_songs)} />
            <Card label="总成本" value={fmt(data.total_cost_cny)} />
            <Card label="总营收(名义)" value={fmt(data.total_revenue_cny)} />
            <Card
              label="总毛利"
              value={fmt(data.total_margin_cny)}
              danger={data.total_margin_cny < 0}
            />
          </div>

          <div className="mb-8 rounded-xl card p-4">
            <div className="mb-2 flex items-center justify-between text-sm">
              <span className="text-[var(--ink-soft)]">订阅与支付</span>
              {sub && (
                <span
                  className={`rounded-full px-2 py-0.5 text-xs ${
                    sub.active
                      ? "bg-emerald-500/15 text-emerald-400"
                      : sub.status && ["past_due", "unpaid"].includes(sub.status)
                        ? "bg-[var(--danger)]/15 text-[var(--danger)]"
                        : "bg-[var(--surface-2)] text-[var(--ink-soft)]"
                  }`}
                >
                  {sub.active
                    ? `已订阅 · ${tierCn(sub.tier_key)}`
                    : sub.status === "trialing"
                      ? "试用中"
                      : sub.status && ["past_due", "unpaid"].includes(sub.status)
                        ? `订阅异常（${sub.status}）· 请更新支付方式`
                        : `未订阅 · ${tierCn(sub.tier_key ?? "free")}`}
                </span>
              )}
            </div>
            {sub?.current_period_end && (
              <p className="mb-3 text-xs text-[var(--muted)]">
                当前订阅周期至：{sub.current_period_end}
              </p>
            )}
            {sub?.cancel_at_period_end && (
              <p className="mb-3 text-xs text-[var(--muted)]">
                已申请取消，当前周期内仍可使用，到期后不再续费。
              </p>
            )}
            {sub?.status && ["past_due", "unpaid"].includes(sub.status) && (
              <p className="mb-3 text-xs text-[var(--danger)]">
                订阅处于异常状态（{sub.status}）—— 请更新支付方式或重新订阅，否则权益将失效。
              </p>
            )}

            {/* wechat Native QR 플로우 */}
            {qr && (
              <div className="mb-4 flex flex-col items-center gap-3 rounded-lg border-[var(--border)] bg-[var(--surface)] p-4">
                <p className="text-sm text-[var(--ink-soft)]">请用微信扫描二维码完成支付</p>
                <div className="rounded-lg bg-white p-3">
                  <QRCodeSVG value={qr.url} size={200} />
                </div>
                <p className="text-xs text-[var(--muted)]">
                  支付成功后本页会自动更新（无需手动刷新）。
                </p>
                <button
                  onClick={() => {
                    clearPoll();
                    setQr(null);
                  }}
                  className="text-xs text-[var(--muted)] underline"
                >
                  取消二维码
                </button>
              </div>
            )}

            {/* naver SDK handoff 플로우 */}
            {naverParams && (
              <div className="mb-4 flex flex-col items-center gap-3 rounded-lg border-[var(--border)] bg-[var(--surface)] p-4">
                <p className="text-sm text-[var(--ink-soft)]">点击下方按钮完成支付</p>
                <button
                  onClick={openNaver}
                  disabled={upgrading || approving}
                  className="btn btn-primary"
                >
                  {upgrading ? "支付窗口打开中…" : "去支付"}
                </button>
                <p className="text-xs text-[var(--muted)]">
                  支付完成后，订阅自动生效。
                </p>
              </div>
            )}

            {!inPaymentFlow && (
              <div className="flex flex-wrap items-center gap-2">
                <select
                  value={tier}
                  onChange={(e) => setTier(e.target.value)}
                  className="input"
                  aria-label="订阅档位"
                >
                  <option value="free">免费档</option>
                  {/* 低档位：绑定 mureka-7.6，成本更低，故价格低于 standard */}
                  <option value="lite">轻量档（8/日 · 轻量模型）</option>
                  <option value="standard">标准档（20/日）</option>
                  <option value="sacred">专业档（50/日）</option>
                  <option value="enterprise">机构档（999/日）</option>
                </select>
                <select
                  value={currency}
                  onChange={(e) => setCurrency(e.target.value)}
                  disabled={!!allowedCurrency}
                  title={allowedCurrency ? `当前支付方式仅支持 ${CURRENCY_CN[allowedCurrency] ?? allowedCurrency}` : undefined}
                  className="input disabled:opacity-50"
                  aria-label="支付币种"
                >
                  <option value="CNY">人民币 ¥</option>
                  <option value="USD">美元 $</option>
                  <option value="KRW">韩元 ₩</option>
                </select>
                {tier !== "free" && plans.length > 0 && (
                  <span className="text-xs text-[var(--muted)]">
                    {planPrice(tier, currency) ?? "—"} / 月
                  </span>
                )}
                <button
                  onClick={upgrade}
                  disabled={upgrading || approving}
                  className="btn btn-primary"
                >
                  {upgrading ? "处理中…" : "升级 / 切换套餐"}
                </button>
                {sub?.active &&
                  (sub.cancel_at_period_end ? (
                    <button
                      onClick={reactivateSub}
                      disabled={upgrading}
                      className="btn btn-secondary disabled:opacity-50"
                    >
                      恢复订阅
                    </button>
                  ) : (
                    <button
                      onClick={cancelSub}
                      disabled={upgrading}
                      className="btn btn-secondary disabled:opacity-50"
                    >
                      取消订阅
                    </button>
                  ))}
              </div>
            )}
          </div>

          {orders.length > 0 && (
            <div className="mb-8 rounded-xl card p-4">
              <p className="mb-2 text-sm text-[var(--ink-soft)]">支付记录</p>
              <div className="space-y-2">
                {orders.map((o) => (
                  <div
                    key={o.order_id}
                    className="flex items-center justify-between text-xs"
                  >
                    <span className="text-[var(--muted)]">
                      #{o.order_id} · {tierCn(o.tier_key)} · {providerCn(o.provider)}
                    </span>
                    <span className="text-[var(--ink-soft)]">
                      {o.currency === "CNY" ? "¥" : o.currency === "USD" ? "$" : "₩"}
                      {o.amount.toFixed(2)} · {statusCn(o.status)}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}

          <h2 className="mb-3 text-lg font-semibold">最近歌曲</h2>
          {data.recent.length === 0 ? (
            <p className="text-sm text-[var(--muted)]">还没有生成歌曲。</p>
          ) : (
            <div className="space-y-2">
              {data.recent.map((s) => (
                <div
                  key={s.id}
                  className="flex items-center justify-between rounded-lg card px-3 py-2 text-sm"
                >
                  <div className="min-w-0">
                    <p className="truncate font-medium text-[var(--ink)]">{s.title}</p>
                    <p className="text-xs text-[var(--muted)]">
                      {s.status} · {s.created_at ?? ""}
                    </p>
                  </div>
                  <div className="flex shrink-0 gap-3 text-right text-xs">
                    <span className="text-[var(--muted)]">成本 {fmt(s.cost_cny)}</span>
                    <span className="text-[var(--ink-soft)]">营收 {fmt(s.price_cny)}</span>
                    <span className={s.margin_cny < 0 ? "text-[var(--danger)]" : "text-[var(--ink-soft)]"}>
                      毛利 {fmt(s.margin_cny)}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </>
      )}

      <ContactSection />

      {/* 2026-09-10: 管理入口已从公共导航栏移除（仅管理员可见）。
          这里保留一个不显眼的入口，方便管理员首次进入 /admin 输入管理密钥。
          2026-09-14: **APK 빌드에서는 완전히 제거**한다(관리자 표면 축소).
            - APK 는 /admin 정적 자산을 빌드에서 제외하므로 이 링크가 404 가 된다.
            - NEXT_PUBLIC_* 는 빌드 시 인라이닝 → 프로덕션 번들에서 이 분기는
              데드코드로 제거되어 관리자 진입점 자체가 산출물에 남지 않는다.
            - 관리 기능은 Web 으로만 제공(PLAN_MUSIC_APK §3.3). */}
      {process.env.NEXT_PUBLIC_APK_BUILD !== "1" && (
        <footer className="mt-10 border-t border-[var(--border)] pt-4 text-xs text-[var(--muted)]">
          <Link href="/admin" className="transition hover:text-[var(--ink-soft)]">
            管理员入口
          </Link>
        </footer>
      )}
    </main>
  );
}

/* ── 📨 联系我们(2026-10-04) ────────────────────────────────────────────────
   主站 support_ticket(type=contact)으로 보낸다. 独立 컴포넌트로 뺀 이유:
   로그인 게이트 앞/뒤 **두 경로 모두**에서 렌더되어야 "无需登录" 안내가 사실이 된다. */
function ContactSection() {
  const [open, setOpen] = useState(false);
  const [body, setBody] = useState("");
  const [reply, setReply] = useState("");
  const [state, setState] = useState<"idle" | "sending" | "ok" | "err">("idle");
  const [err, setErr] = useState("");

  return (
    <section className="mt-10 border-t border-[var(--border)] pt-4">
      <button
        type="button"
        onClick={() => {
          setOpen((v) => !v);
          setState("idle");
          setErr("");
        }}
        aria-expanded={open}
        aria-controls="contact-form"
        className="flex w-full items-center justify-between text-sm text-[var(--ink-soft)] transition hover:text-[var(--ink)]"
      >
        <span>联系我们</span>
        <span aria-hidden>{open ? "\u2212" : "+"}</span>
      </button>

      {open && (
        <div
          id="contact-form"
          className="mt-3 flex flex-col gap-2"
          role="group"
          aria-label="联系我们表单"
        >
          <p className="text-xs text-[var(--muted)]">
            无需登录即可留言。我们会阅读每一条消息。
          </p>
          <textarea
            value={body}
            onChange={(e) => setBody(e.target.value)}
            maxLength={5000}
            rows={5}
            placeholder="请输入内容…"
            aria-label="留言内容"
            className="w-full rounded-xl border border-[var(--border)] bg-transparent p-3 text-sm outline-none focus:border-[var(--ink-soft)]"
          />
          <input
            value={reply}
            onChange={(e) => setReply(e.target.value)}
            maxLength={200}
            placeholder="联系方式（选填）：邮箱 / Telegram"
            aria-label="联系方式"
            className="w-full rounded-xl border border-[var(--border)] bg-transparent p-3 text-sm outline-none focus:border-[var(--ink-soft)]"
          />
          <button
            type="button"
            disabled={state === "sending"}
            onClick={async () => {
              if (!body.trim()) {
                setState("err");
                setErr("请先填写留言内容。");
                return;
              }
              setState("sending");
              setErr("");
              try {
                // contact 는 서버에서 dedup 하지 않으므로 duplicate 응답은 오지 않는다.
                // contact 는 서버에서 dedup 하지 않으므로 duplicate 응답은 오지 않는다.
                await sendContactMessage(body, reply);
                setState("ok");
                setBody("");
                setReply("");
              } catch (e) {
                setState("err");
                setErr(e instanceof Error ? e.message : "发送失败，请稍后重试。");
              }
            }}
            className="rounded-xl bg-[var(--btn-solid-bg)] px-4 py-2 text-sm font-semibold text-[var(--btn-solid-fg)] disabled:opacity-60"
          >
            {state === "sending" ? "发送中…" : "发送"}
          </button>
          {state === "ok" && (
            <p className="text-xs text-[var(--ink-soft)]" role="status" aria-live="polite">
              留言已送达，谢谢。
            </p>
          )}
          {state === "err" && (
            <p className="text-xs text-[var(--danger)]" role="alert">
              {err || "发送失败，请稍后重试。"}
            </p>
          )}
        </div>
      )}
    </section>
  );
}

function Card({
  label,
  value,
  danger,
}: {
  label: string;
  value: string;
  danger?: boolean;
}) {
  return (
    <div className="rounded-xl card p-3">
      <p className="text-xs text-[var(--muted)]">{label}</p>
      <p className={`mt-1 text-lg font-semibold ${danger ? "text-[var(--danger)]" : "text-[var(--ink)]"}`}>
        {value}
      </p>
    </div>
  );
}
