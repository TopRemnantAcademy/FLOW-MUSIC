"use client";

/**
 * 产品分析看板（GAP-020）。
 *
 * 后端：GET /api/admin/analytics/summary?days=7|30（只读聚合，无新表/无迁移）。
 *
 * ## 为什么图表是手写 SVG
 * 本仓库图表依赖只有 `wavesurfer.js`（音频波形，不是图表库）。为了一个运营看板
 * 引入 recharts/echarts 是拿 ~100KB+ 的包体换两根柱子 —— 而这里要画的东西
 * （日趋势折线、分布条形、漏斗）用十几行 SVG 就能表达清楚。
 *
 * ## 为什么每个数字旁边都挂「口径」
 * 本产品**没有事件表**（禁新增迁移），所以后端给的全是代理指标：
 * 活跃用户只统计「产出过作品/互动」的人，播放量是全量累计不可切窗。
 * 这些限制必须**贴着脸显示**——把代理值当精确 DAU 汇报给业务方，
 * 比没有看板更危险。后端在响应里带 basis/caveat，这里原样渲染，不在前端重写措辞
 * （前端改写只会让两处说法漂移）。
 */
import { useCallback, useEffect, useState } from "react";
import { api, AnalyticsSummary } from "@/lib/api";
import { clearAdminKey, getAdminKey } from "@/lib/adminAuth";
import AdminGuard from "@/components/AdminGuard";

const WINDOWS: { label: string; days: 7 | 30 }[] = [
  { label: "近 7 天", days: 7 },
  { label: "近 30 天", days: 30 },
];

const STATUS_LABEL: Record<string, string> = {
  completed: "已完成",
  failed: "失败",
  processing: "生成中",
  pending: "排队中",
};

const TASK_LABEL: Record<string, string> = {
  generate: "生成",
  custom: "自定义",
  extend: "续写",
  cover: "翻唱",
  remix: "混音",
  instrumental: "纯音乐",
  vocal_clone: "音色克隆",
};

function pct(n: number): string {
  return `${(n * 100).toFixed(1)}%`;
}

/** 单行条形图（横向）。分布类指标用它而不是饼图：分布项少、要读**具体数**，
 *  横向条形把「名称 + 数字」并排，不需要在图例和饼块之间来回找。 */
function BarRow({
  label,
  value,
  max,
  suffix,
}: {
  label: string;
  value: number;
  max: number;
  suffix?: string;
}) {
  const w = max > 0 ? Math.max((value / max) * 100, value > 0 ? 2 : 0) : 0;
  return (
    <div className="mb-2">
      <div className="mb-1 flex items-baseline justify-between text-sm">
        <span className="text-[var(--ink-soft)]">{label}</span>
        <span className="tabular-nums text-[var(--ink)]">
          {value.toLocaleString("zh-CN")}
          {suffix || ""}
        </span>
      </div>
      <div
        className="h-1.5 w-full overflow-hidden rounded-full bg-[var(--line)]"
        role="img"
        aria-label={`${label}：${value}${suffix || ""}`}
      >
        <div className="h-full rounded-full bg-[var(--ink)]" style={{ width: `${w}%` }} />
      </div>
    </div>
  );
}

/** 日趋势折线（纯 SVG）。
 *  横轴日期、纵轴计数；只有一条序列 → 不需要图例，标题即图例。 */
function TrendChart({
  title,
  data,
  unit,
}: {
  title: string;
  data: Record<string, number>;
  unit: string;
}) {
  const entries = Object.entries(data).sort(([a], [b]) => a.localeCompare(b));
  if (entries.length === 0) {
    return (
      <section className="card p-4">
        <h3 className="mb-2 text-base font-medium">{title}</h3>
        <p className="text-sm text-[var(--muted)]">该窗口内无数据</p>
      </section>
    );
  }
  const W = 560;
  const H = 140;
  const PAD = { l: 8, r: 8, t: 10, b: 22 };
  const max = Math.max(...entries.map(([, v]) => v), 1);
  const iw = W - PAD.l - PAD.r;
  const ih = H - PAD.t - PAD.b;
  const x = (i: number) =>
    PAD.l + (entries.length === 1 ? iw / 2 : (i / (entries.length - 1)) * iw);
  const y = (v: number) => PAD.t + ih - (v / max) * ih;
  const pts = entries.map(([, v], i) => `${x(i)},${y(v)}`).join(" ");
  const area = `${PAD.l},${PAD.t + ih} ${pts} ${PAD.l + iw},${PAD.t + ih}`;

  return (
    <section className="card p-4">
      <h3 className="mb-1 text-base font-medium">
        {title}
        <span className="ml-2 text-sm font-normal text-[var(--muted)]">
          峰值 {max.toLocaleString("zh-CN")} {unit}
        </span>
      </h3>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="h-36 w-full"
        role="img"
        aria-label={`${title}：${entries.map(([d, v]) => `${d} ${v}`).join("，")}`}
      >
        <polygon points={area} fill="var(--line)" opacity="0.7" />
        <polyline
          points={pts}
          fill="none"
          stroke="var(--ink)"
          strokeWidth="2"
          strokeLinejoin="round"
          strokeLinecap="round"
        />
        {entries.map(([d, v], i) => (
          <circle key={d} cx={x(i)} cy={y(v)} r="2.5" fill="var(--ink)" />
        ))}
        <text x={PAD.l} y={H - 6} fontSize="10" fill="var(--muted)">
          {entries[0][0]}
        </text>
        {entries.length > 1 && (
          <text x={W - PAD.r} y={H - 6} fontSize="10" fill="var(--muted)" textAnchor="end">
            {entries[entries.length - 1][0]}
          </text>
        )}
      </svg>
    </section>
  );
}

/** 口径说明块（basis 已足够解释时只显示 basis，避免同义重复占屏）。 */
function Basis({ basis, caveat }: { basis: string; caveat: string }) {
  return (
    <details className="mt-2 text-sm">
      <summary className="cursor-pointer text-[var(--muted)] hover:text-[var(--ink-soft)]">
        口径与局限
      </summary>
      <p className="mt-1 text-[var(--ink-soft)]">口径：{basis}</p>
      <p className="mt-1 text-[var(--ink-soft)]">局限：{caveat}</p>
    </details>
  );
}

function Stat({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="card p-4">
      <p className="text-sm text-[var(--muted)]">{label}</p>
      <p className="mt-1 text-2xl font-semibold tabular-nums">{value}</p>
      {sub && <p className="mt-1 text-sm text-[var(--ink-soft)]">{sub}</p>}
    </div>
  );
}

export default function AdminAnalyticsPage() {
  const [token, setToken] = useState<string | null>(null);
  const [days, setDays] = useState<7 | 30>(7);
  const [data, setData] = useState<AnalyticsSummary | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    setToken(getAdminKey() || null);
  }, []);

  const load = useCallback(async (d: 7 | 30) => {
    setError("");
    setLoading(true);
    try {
      setData(await api.adminAnalyticsSummary(d));
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
        title="产品分析"
        desc="请输入管理密钥（后台密钥），查看生成量、完成率、转化漏斗与失败原因。"
        onUnlocked={(k) => setToken(k)}
      />
    );
  }

  const ov = data?.overview;
  const pv = data?.providers;
  const fl = data?.failures;
  const fn = data?.funnel;
  const en = data?.engagement;

  const maxStatus = ov ? Math.max(...Object.values(ov.by_status), 0) : 0;
  const maxEngine = pv ? Math.max(...Object.values(pv.by_engine), 0) : 0;
  const maxTask = pv ? Math.max(...Object.values(pv.by_task_type), 0) : 0;
  const maxReason = fl ? Math.max(...fl.top.map((r) => r.count), 0) : 0;
  const maxStep = fn ? Math.max(...fn.steps.map((s) => s.users), 0) : 0;

  return (
    <div className="mx-auto max-w-4xl p-6">
      <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-semibold">产品分析</h1>
        <div className="flex items-center gap-2">
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
      </div>

      {error && (
        <p className="mb-4 rounded-xl bg-red-950 px-4 py-3 text-base text-red-300" role="alert">
          {error}
        </p>
      )}

      {data && (
        <p className="mb-4 rounded-xl border border-[var(--line)] px-4 py-3 text-sm text-[var(--ink-soft)]">
          ⚠️ {data.disclaimer}
        </p>
      )}

      {loading && !data && <p className="text-sm text-[var(--muted)]">加载中…</p>}

      {ov && (
        <>
          <div className="mb-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Stat
              label="生成总数"
              value={ov.generated_total.toLocaleString("zh-CN")}
              sub={`成功 ${ov.completed} · 失败 ${ov.failed}`}
            />
            <Stat
              label="完成率"
              value={pct(ov.completion_rate)}
              sub={`分母排除在途 ${ov.inflight} 首`}
            />
            <Stat
              label="新增注册"
              value={ov.new_users.toLocaleString("zh-CN")}
              sub={`近 ${ov.window_days} 天`}
            />
            <Stat
              label="活跃用户（代理）"
              value={ov.active_users.value.toLocaleString("zh-CN")}
              sub={`单日峰值 ${ov.active_users.peak_daily}`}
            />
          </div>

          <section className="card mb-4 p-4">
            <h3 className="mb-2 text-base font-medium">生成状态分布</h3>
            {Object.keys(ov.by_status).length === 0 ? (
              <p className="text-sm text-[var(--muted)]">该窗口内无数据</p>
            ) : (
              // 按计数降序，未知状态原样显示（后端将来加状态时这里不会静默吞掉）
              Object.entries(ov.by_status)
                .sort(([, a], [, b]) => b - a)
                .map(([k, v]) => (
                  <BarRow
                    key={k}
                    label={STATUS_LABEL[k] || k}
                    value={v}
                    max={maxStatus}
                    suffix=" 首"
                  />
                ))
            )}
            <Basis basis={ov.active_users.basis} caveat={ov.active_users.caveat} />
          </section>

          <div className="mb-4 grid gap-3 sm:grid-cols-2">
            <TrendChart title="每日生成数" data={ov.daily_generated} unit="首" />
            <TrendChart
              title="每日活跃用户（代理）"
              data={ov.active_users.daily}
              unit="人"
            />
          </div>
        </>
      )}

      {pv && (
        <section className="card mb-4 p-4">
          <h3 className="mb-2 text-base font-medium">
            引擎分布
            <span className="ml-2 text-sm font-normal text-[var(--muted)]">
              共 {pv.total} 首
            </span>
          </h3>
          {Object.keys(pv.by_engine).length === 0 ? (
            <p className="text-sm text-[var(--muted)]">该窗口内无数据</p>
          ) : (
            Object.entries(pv.by_engine)
              .sort(([, a], [, b]) => b - a)
              .map(([k, v]) => (
                <BarRow key={k} label={k} value={v} max={maxEngine} suffix=" 首" />
              ))
          )}
          <h3 className="mb-2 mt-4 text-base font-medium">任务类型分布</h3>
          {Object.keys(pv.by_task_type).length === 0 ? (
            <p className="text-sm text-[var(--muted)]">该窗口内无数据</p>
          ) : (
            Object.entries(pv.by_task_type)
              .sort(([, a], [, b]) => b - a)
              .map(([k, v]) => (
                <BarRow
                  key={k}
                  label={TASK_LABEL[k] || k}
                  value={v}
                  max={maxTask}
                  suffix=" 首"
                />
              ))
          )}
          <Basis basis={pv.basis} caveat={pv.caveat} />
        </section>
      )}

      {fl && (
        <section className="card mb-4 p-4">
          <h3 className="mb-2 text-base font-medium">
            失败原因 TOP
            <span className="ml-2 text-sm font-normal text-[var(--muted)]">
              共 {fl.total_failed} 次失败
            </span>
          </h3>
          {fl.top.length === 0 ? (
            <p className="text-sm text-[var(--muted)]">该窗口内无失败记录 🎉</p>
          ) : (
            fl.top.map((r) => (
              <BarRow
                key={r.reason}
                label={`${r.reason}（${pct(r.share)}）`}
                value={r.count}
                max={maxReason}
                suffix=" 次"
              />
            ))
          )}
          <Basis basis={fl.basis} caveat={fl.caveat} />
        </section>
      )}

      {fn && (
        <section className="card mb-4 p-4">
          <h3 className="mb-3 text-base font-medium">转化漏斗</h3>
          <div className="mb-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
            {fn.steps.map((s) => (
              <div key={s.key}>
                <p className="text-sm text-[var(--muted)]">{s.label}</p>
                <p className="text-xl font-semibold tabular-nums">
                  {s.users.toLocaleString("zh-CN")}
                </p>
                <p className="text-sm text-[var(--ink-soft)]">
                  上一步 {pct(s.from_prev)} · 距注册 {pct(s.from_top)}
                </p>
              </div>
            ))}
          </div>
          {fn.steps.map((s) => (
            <BarRow key={`bar-${s.key}`} label={s.label} value={s.users} max={maxStep} suffix=" 人" />
          ))}
          <p className="mt-2 text-sm text-[var(--ink-soft)]">
            窗口内付费用户 {fn.paid_users} 人 · 实收 ¥{fn.paid_revenue_cny.toFixed(2)} · ARPPU ¥
            {fn.arppu_cny.toFixed(2)}
          </p>
          <Basis basis={fn.basis} caveat={fn.caveat} />
        </section>
      )}

      {en && (
        <>
          <div className="mb-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Stat label="累计播放（全量）" value={en.play_count_all_time.toLocaleString("zh-CN")} />
            <Stat label="点赞" value={en.likes_in_window.toLocaleString("zh-CN")} sub="窗口内" />
            <Stat label="收藏" value={en.favorites_in_window.toLocaleString("zh-CN")} sub="窗口内" />
            <Stat label="评分" value={en.ratings_in_window.toLocaleString("zh-CN")} sub="窗口内" />
          </div>

          <section className="card mb-4 p-4">
            <h3 className="mb-2 text-base font-medium">播放 TOP（累计）</h3>
            {en.top_played.length === 0 ? (
              <p className="text-sm text-[var(--muted)]">暂无播放记录</p>
            ) : (
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-[var(--muted)]">
                    <th className="py-1 font-normal">曲目</th>
                    <th className="py-1 font-normal">公开</th>
                    <th className="py-1 text-right font-normal">播放</th>
                  </tr>
                </thead>
                <tbody>
                  {en.top_played.map((t) => (
                    <tr key={t.song_id} className="border-t border-[var(--line)]">
                      <td className="py-1.5 text-[var(--ink)]">{t.title}</td>
                      <td className="py-1.5 text-[var(--ink-soft)]">{t.is_public ? "是" : "否"}</td>
                      <td className="py-1.5 text-right tabular-nums">
                        {t.play_count.toLocaleString("zh-CN")}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            <Basis basis={en.basis} caveat={en.caveat} />
          </section>
        </>
      )}

      {data?.generated_at && (
        <p className="text-sm text-[var(--muted)]">
          数据生成于 {data.generated_at}（UTC）· 窗口 {data.window_days} 天
        </p>
      )}
    </div>
  );
}
