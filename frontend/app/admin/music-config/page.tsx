"use client";

import { useEffect, useState } from "react";
import {
  api,
  EngineConfig,
  RoutingRule,
  PricingTier,
  ConfigDashboard,
} from "@/lib/api";
import { clearAdminKey, getAdminKey } from "@/lib/adminAuth";
import AdminGuard from "@/components/AdminGuard";

type Tab = "overview" | "engines" | "routing" | "pricing";

// 分类中文名（未知分类原样显示）
const CAT_LABEL: Record<string, string> = {
  general: "通用歌曲",
  christian_worship: "基督教圣乐",
  instrumental: "纯音乐",
};
// 引擎一句话说明（键=provider 主键，仅运维用；显示一律走 display_name，不得出现第三方品牌名）
const ENGINE_HINT: Record<string, string> = {
  suno: "商业服务，质量高；对宗教词偶尔会误拦",
  mock: "本地测试用，不产生真实歌曲",
  musicgen: "开源模型，不可商用",
  ace_step_private: "合规通道：本地运行，宗教内容不会被误杀",
  tencent_mps: "云端引擎，按首计费",
  volcano: "云端引擎，按分钟计费",
  mureka: "主力商业引擎，按首计费，质量稳定",
};

type ModalState =
  | { kind: "engine"; item: Partial<EngineConfig> | null }
  | { kind: "routing"; item: Partial<RoutingRule> | null }
  | { kind: "pricing"; item: Partial<PricingTier> | null }
  | null;

function catLabel(c: string) {
  return CAT_LABEL[c] || c;
}
function engineHint(e: EngineConfig) {
  return ENGINE_HINT[e.provider] || e.notes || e.display_name || "";
}

export default function AdminConfigPage() {
  // null = 尚未读取 localStorage（首帧不渲染门禁，避免闪烁）
  const [token, setToken] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("overview");
  const [engines, setEngines] = useState<EngineConfig[]>([]);
  const [routing, setRouting] = useState<RoutingRule[]>([]);
  const [tiers, setTiers] = useState<PricingTier[]>([]);
  const [dash, setDash] = useState<ConfigDashboard | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [modal, setModal] = useState<ModalState>(null);

  useEffect(() => {
    setToken(getAdminKey() || null);
  }, []);

  async function load() {
    setError("");
    try {
      const d = await api.adminDashboard();
      setEngines(d.engines);
      setRouting(d.routing_rules);
      setTiers(d.pricing_tiers);
      setDash(d);
    } catch (e: any) {
      setError(e?.message || "加载失败");
      // 密钥无效/过期 → 退回门禁，不停留在"能点但全报错"的状态
      if (e?.status === 401 || e?.status === 403 || e?.status === 503) {
        clearAdminKey();
        setToken(null);
      }
    }
  }

  useEffect(() => {
    if (token) load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  async function reseed() {
    if (!confirm("恢复初始设置？你手动改过的内容会被保留，只补齐缺失的默认值。")) return;
    setBusy(true);
    try {
      await api.adminSeedConfig();
      await load();
    } catch (e: any) {
      setError(e?.message || "恢复失败");
    } finally {
      setBusy(false);
    }
  }

  // 当前默认生成方式（is_default 且启用，否则首个启用）
  const defaultEngine = engines.find((e) => e.is_default && e.enabled) || engines.find((e) => e.enabled);
  // 圣乐合规通道状态：christian_worship 规则指向的引擎是否启用
  const sacredRule = routing.find((r) => r.category === "christian_worship");
  const sacredEngine = sacredRule ? engines.find((e) => e.provider === sacredRule.provider) : undefined;
  const sacredOn = !!sacredRule && !!sacredEngine && sacredRule.enabled && sacredEngine.enabled;

  if (token === null) {
    return (
      <AdminGuard
        title="FLOW 设置"
        desc="请输入管理密钥（后台密钥），即可在这里调整音乐生成方式与收费。"
        onUnlocked={(k) => {
          setToken(k);
          load();
        }}
      />
    );
  }

  const TABS: { key: Tab; label: string }[] = [
    { key: "overview", label: "概览" },
    { key: "engines", label: "生成方式" },
    { key: "routing", label: "内容分配" },
    { key: "pricing", label: "收费方案" },
  ];

  return (
    <div className="mx-auto max-w-3xl p-6">
      <div className="mb-5 flex items-center justify-between">
        <h1 className="text-2xl font-semibold">FLOW 设置</h1>
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

      <div className="mb-5 flex flex-wrap gap-2 text-base">
        {TABS.map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            className="chip rounded-full px-4 py-2"
            data-on={tab === t.key}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === "overview" && (
        <OverviewTab
          defaultEngine={defaultEngine}
          sacredOn={sacredOn}
          dash={dash}
          onReseed={reseed}
          busy={busy}
        />
      )}
      {tab === "engines" && (
        <EnginesTab
          items={engines}
          busy={busy}
          setBusy={setBusy}
          setError={setError}
          reload={load}
          onEdit={(it) => setModal({ kind: "engine", item: it })}
          onNew={() => setModal({ kind: "engine", item: null })}
        />
      )}
      {tab === "routing" && (
        <RoutingTab
          items={routing}
          engines={engines}
          busy={busy}
          setBusy={setBusy}
          setError={setError}
          reload={load}
          onNew={() => setModal({ kind: "routing", item: null })}
        />
      )}
      {tab === "pricing" && (
        <PricingTab
          items={tiers}
          busy={busy}
          setBusy={setBusy}
          setError={setError}
          reload={load}
          onEdit={(it) => setModal({ kind: "pricing", item: it })}
          onNew={() => setModal({ kind: "pricing", item: null })}
        />
      )}

      {modal && (
        <EditModal
          modal={modal}
          engines={engines}
          setModal={setModal}
          reload={load}
          setError={setError}
          setBusy={setBusy}
        />
      )}
    </div>
  );
}

// ---------------- 概览 ----------------
function OverviewTab({
  defaultEngine,
  sacredOn,
  dash,
  onReseed,
  busy,
}: {
  defaultEngine?: EngineConfig;
  sacredOn: boolean;
  dash: ConfigDashboard | null;
  onReseed: () => void;
  busy: boolean;
}) {
  return (
    <div className="space-y-4">
      <p className="text-base text-[var(--muted)]">
        在这里选择用哪种方式生成音乐、怎么收费，改完立刻生效，不用重启。
      </p>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <BigCard
          emoji="🎼"
          title="默认生成方式"
          value={defaultEngine ? defaultEngine.display_name || defaultEngine.provider : "未设置"}
          sub="大多数歌曲用它生成"
        />
        <BigCard
          emoji={sacredOn ? "✅" : "⚠️"}
          title="圣乐合规通道"
          value={sacredOn ? "已开启" : "未开启"}
          sub="基督教圣乐是否走合规引擎"
        />
        <BigCard
          emoji="⏳"
          title="待审核歌曲"
          value={dash ? String(dash.pending_moderation) : "-"}
          sub="需要你过目的内容"
        />
      </div>

      <div className="rounded-2xl card p-4">
        <div className="mb-3 text-base font-medium">单位经济（累计）</div>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <div>
            <div className="text-xs text-[var(--muted)]">总成本</div>
            <div className="text-xl font-bold">¥{dash ? dash.total_cost_cny.toFixed(2) : "0.00"}</div>
          </div>
          <div>
            <div className="text-xs text-[var(--muted)]">总营收（名义）</div>
            <div className="text-xl font-bold text-emerald-400">¥{dash ? dash.total_revenue_cny.toFixed(2) : "0.00"}</div>
          </div>
          <div>
            <div className="text-xs text-[var(--muted)]">总毛利</div>
            <div className={`text-xl font-bold ${(dash?.total_margin_cny ?? 0) >= 0 ? "text-emerald-400" : "text-red-400"}`}>
              ¥{dash ? dash.total_margin_cny.toFixed(2) : "0.00"}
            </div>
          </div>
          <div>
            <div className="text-xs text-[var(--muted)]">毛利率</div>
            <div className="text-xl font-bold">{(dash ? (dash.margin_rate * 100).toFixed(1) : "0.0")}%</div>
          </div>
        </div>

        <div className="mt-3 space-y-1 text-sm">
          <div className="text-sm text-[var(--muted)]">按分类（成本 / 营收 / 毛利）</div>
          {dash &&
            Object.entries(dash.cost_by_category).map(([k, v]) => (
              <div key={k} className="flex justify-between gap-2">
                <span>{catLabel(k)}</span>
                <span className="text-[var(--muted)]">
                  ¥{(dash.revenue_by_category[k] ?? 0).toFixed(2)} / ¥{v.toFixed(2)} /{" "}
                  <span className={(dash.margin_by_category[k] ?? 0) >= 0 ? "text-emerald-400" : "text-red-400"}>
                    ¥{(dash.margin_by_category[k] ?? 0).toFixed(2)}
                  </span>
                </span>
              </div>
            ))}
          {(!dash || Object.keys(dash.cost_by_category).length === 0) && (
            <div className="text-[var(--muted)]">还没有生成记录</div>
          )}
        </div>

        {dash && Object.keys(dash.revenue_by_tier).length > 0 && (
          <div className="mt-3 space-y-1 text-sm">
            <div className="text-sm text-[var(--muted)]">按套餐（营收 / 毛利）</div>
            {Object.entries(dash.revenue_by_tier).map(([k, v]) => (
              <div key={k} className="flex justify-between gap-2">
                <span>{k || "free"}</span>
                <span className="text-[var(--muted)]">
                  ¥{v.toFixed(2)} /{" "}
                  <span className={(dash.margin_by_tier[k] ?? 0) >= 0 ? "text-emerald-400" : "text-red-400"}>
                    ¥{(dash.margin_by_tier[k] ?? 0).toFixed(2)}
                  </span>
                </span>
              </div>
            ))}
          </div>
        )}

        {dash && Object.keys(dash.cost_by_engine).length > 0 && (
          <>
            <div className="mt-4 text-sm text-[var(--muted)]">按生成方式（成本）</div>
            <div className="mt-2 space-y-1 text-sm">
              {Object.entries(dash.cost_by_engine).map(([k, v]) => (
                <div key={k} className="flex justify-between">
                  <span>{k}</span>
                  <span>¥{v.toFixed(2)}</span>
                </div>
              ))}
            </div>
          </>
        )}
      </div>

      <div className="card p-4">
        <div className="text-base font-medium">💡 小提示</div>
        <p className="mt-1 text-sm text-[var(--ink-soft)]">
          想让基督教圣乐用合规引擎、不被通用引擎误杀？到「内容分配」确认基督教圣乐已开启，并到「生成方式」打开
          <b> 合规私有引擎</b> 即可。
        </p>
      </div>

      <div className="flex justify-end">
        <button
          onClick={onReseed}
          disabled={busy}
          className="btn btn-secondary"
        >
          恢复初始设置（改乱了点这里）
        </button>
      </div>
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

// ---------------- 生成方式（引擎） ----------------
function EnginesTab({
  items,
  busy,
  setBusy,
  setError,
  reload,
  onEdit,
  onNew,
}: {
  items: EngineConfig[];
  busy: boolean;
  setBusy: (b: boolean) => void;
  setError: (s: string) => void;
  reload: () => void;
  onEdit: (it: EngineConfig) => void;
  onNew: () => void;
}) {
  async function toggle(e: EngineConfig) {
    setBusy(true);
    try {
      await api.adminUpdateEngine(e.provider, { enabled: !e.enabled });
      await reload();
    } catch (err: any) {
      setError(err?.message || "操作失败");
    } finally {
      setBusy(false);
    }
  }
  async function del(e: EngineConfig) {
    if (!confirm(`确定关闭并删除「${e.display_name || e.provider}」吗？`)) return;
    setBusy(true);
    try {
      await api.adminDeleteEngine(e.provider);
      await reload();
    } catch (err: any) {
      setError(err?.message || "删除失败");
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="space-y-3">
      <p className="text-sm text-[var(--muted)]">打开开关即可启用该生成方式；关闭则不再使用（歌曲会自动改用默认方式）。</p>
      {items.map((e) => (
        <div key={e.provider} className="rounded-2xl card p-4">
          <div className="flex items-start justify-between gap-3">
            <div>
              <div className="text-lg font-semibold">{e.display_name || e.provider}</div>
              <div className="text-sm text-[var(--muted)]">{engineHint(e)}</div>
              <div className="mt-1 text-sm text-[var(--muted)]">约 ¥{e.cost_per_song_cny}/首</div>
              <div className="mt-1 text-xs">
                {e.api_key_set ? (
                  <span className="rounded-full bg-emerald-900 px-2 py-0.5 text-emerald-300">已配密钥</span>
                ) : (
                  <span className="rounded-full bg-[var(--surface-2)] px-2 py-0.5 text-[var(--muted)]">未配密钥</span>
                )}
              </div>
            </div>
            <div className="flex flex-col items-end gap-2">
              <BigToggle on={e.enabled} onChange={() => toggle(e)} label="启用" />
              <span className="text-xs text-[var(--muted)]">{e.enabled ? "已启用" : "已关闭"}</span>
            </div>
          </div>
          <div className="mt-3 flex gap-2">
            <button onClick={() => onEdit(e)} className="btn btn-secondary btn-sm">修改</button>
            <button onClick={() => del(e)} className="btn btn-danger btn-sm">删除</button>
          </div>
        </div>
      ))}
      {items.length === 0 && <p className="text-[var(--muted)]">还没有生成方式</p>}
      <button onClick={onNew} className="btn btn-primary w-full">
        ＋ 新增生成方式
      </button>
    </div>
  );
}

// ---------------- 内容分配（路由） ----------------
function RoutingTab({
  items,
  engines,
  busy,
  setBusy,
  setError,
  reload,
  onNew,
}: {
  items: RoutingRule[];
  engines: EngineConfig[];
  busy: boolean;
  setBusy: (b: boolean) => void;
  setError: (s: string) => void;
  reload: () => void;
  onNew: () => void;
}) {
  async function changeEngine(r: RoutingRule, provider: string) {
    setBusy(true);
    try {
      await api.adminUpdateRoutingRule(r.id, { provider });
      await reload();
    } catch (err: any) {
      setError(err?.message || "操作失败");
    } finally {
      setBusy(false);
    }
  }
  async function toggle(r: RoutingRule) {
    setBusy(true);
    try {
      await api.adminUpdateRoutingRule(r.id, { enabled: !r.enabled });
      await reload();
    } catch (err: any) {
      setError(err?.message || "操作失败");
    } finally {
      setBusy(false);
    }
  }
  async function del(r: RoutingRule) {
    if (!confirm(`确定删除「${catLabel(r.category)}」的分配吗？`)) return;
    setBusy(true);
    try {
      await api.adminDeleteRoutingRule(r.id);
      await reload();
    } catch (err: any) {
      setError(err?.message || "删除失败");
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="space-y-3">
      <p className="text-sm text-[var(--muted)]">
        这里决定「哪类内容用哪种方式生成」。例如把基督教圣乐分配到合规引擎，就不会被通用引擎误杀。
      </p>
      {items.map((r) => {
        const target = engines.find((e) => e.provider === r.provider);
        const off = target && !target.enabled;
        return (
          <div key={r.id} className="rounded-2xl card p-4">
            <div className="flex items-center justify-between">
              <div className="text-lg font-semibold">{catLabel(r.category)}</div>
              <div className="flex items-center gap-2">
                <BigToggle on={r.enabled} onChange={() => toggle(r)} label="启用" />
              </div>
            </div>
            <div className="mt-3 flex items-center gap-2 text-base">
              <span className="text-[var(--muted)]">用</span>
              <select
                value={r.provider}
                disabled={busy}
                onChange={(e) => changeEngine(r, e.target.value)}
                className="input"
              >
                {engines.map((e) => (
                  <option key={e.provider} value={e.provider}>
                    {e.display_name || e.provider}
                    {e.enabled ? "" : "（已关闭）"}
                  </option>
                ))}
              </select>
              <span className="text-[var(--muted)]">生成</span>
            </div>
            {off && (
              <p className="mt-2 text-sm text-amber-400">
                ⚠️ 该方式已关闭，这类内容会改用默认方式生成。
              </p>
            )}
            <div className="mt-3">
              <button onClick={() => del(r)} className="btn btn-danger btn-sm">删除</button>
            </div>
          </div>
        );
      })}
      {items.length === 0 && <p className="text-[var(--muted)]">还没有分配规则</p>}
      <button onClick={onNew} className="btn btn-primary w-full">
        ＋ 新增内容分配
      </button>
    </div>
  );
}

// ---------------- 收费方案（计费） ----------------
function PricingTab({
  items,
  busy,
  setBusy,
  setError,
  reload,
  onEdit,
  onNew,
}: {
  items: PricingTier[];
  busy: boolean;
  setBusy: (b: boolean) => void;
  setError: (s: string) => void;
  reload: () => void;
  onEdit: (it: PricingTier) => void;
  onNew: () => void;
}) {
  async function toggle(t: PricingTier) {
    setBusy(true);
    try {
      await api.adminUpdatePricingTier(t.tier_key, { enabled: !t.enabled });
      await reload();
    } catch (err: any) {
      setError(err?.message || "操作失败");
    } finally {
      setBusy(false);
    }
  }
  async function del(t: PricingTier) {
    if (!confirm(`确定删除方案「${t.tier_name || t.tier_key}」吗？`)) return;
    setBusy(true);
    try {
      await api.adminDeletePricingTier(t.tier_key);
      await reload();
    } catch (err: any) {
      setError(err?.message || "删除失败");
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="space-y-3">
      <p className="text-sm text-[var(--muted)]">设置不同方案的收费标准，开关控制该方案是否可用。</p>
      {items.map((t) => (
        <div key={t.tier_key} className="rounded-2xl card p-4">
          <div className="flex items-start justify-between gap-3">
            <div>
              <div className="text-lg font-semibold">{t.tier_name || t.tier_key}</div>
              <div className="text-2xl font-bold">¥{t.price_cny}<span className="text-sm font-normal text-[var(--muted)]"> /首</span></div>
              <div className="mt-1 text-sm text-[var(--muted)]">{t.description || "—"}</div>
            </div>
            <div className="flex flex-col items-end gap-2">
              <BigToggle on={t.enabled} onChange={() => toggle(t)} label="启用" />
              <span className="text-xs text-[var(--muted)]">{t.enabled ? "可用" : "已停用"}</span>
            </div>
          </div>
          <div className="mt-3 flex gap-2">
            <button onClick={() => onEdit(t)} className="btn btn-secondary btn-sm">修改</button>
            <button onClick={() => del(t)} className="btn btn-danger btn-sm">删除</button>
          </div>
        </div>
      ))}
      {items.length === 0 && <p className="text-[var(--muted)]">还没有收费方案</p>}
      <button onClick={onNew} className="btn btn-primary w-full">
        ＋ 新增收费方案
      </button>
    </div>
  );
}

// ---------------- 大号开关 ----------------
function BigToggle({ on, onChange, label }: { on: boolean; onChange: (v: boolean) => void; label?: string }) {
  return (
    <button
      type="button"
      onClick={() => onChange(!on)}
      aria-label={label}
      className={`relative inline-flex h-8 w-14 shrink-0 items-center rounded-full transition ${
        on ? "bg-[var(--btn-solid-bg)]" : "bg-[var(--border-strong)]"
      }`}
    >
      <span
        className={`inline-block h-6 w-6 transform rounded-full bg-[var(--btn-solid-fg)] transition ${
          on ? "translate-x-7" : "translate-x-1"
        }`}
      />
    </button>
  );
}

// ---------------- 编辑/新建弹窗 ----------------
function EditModal({
  modal,
  engines,
  setModal,
  reload,
  setError,
  setBusy,
}: {
  modal: NonNullable<ModalState>;
  engines: EngineConfig[];
  setModal: (m: ModalState) => void;
  reload: () => void;
  setError: (s: string) => void;
  setBusy: (b: boolean) => void;
}) {
  const isEdit =
    modal.item != null &&
    ("provider" in (modal.item ?? {}) || "id" in (modal.item ?? {}) || "tier_key" in (modal.item ?? {}));
  const [form, setForm] = useState<Record<string, any>>({ ...(modal.item ?? {}) });

  function set(k: string, v: any) {
    setForm((f) => ({ ...f, [k]: v }));
  }

  async function save() {
    setBusy(true);
    try {
      if (modal.kind === "engine") {
        const body: Record<string, unknown> = { ...form };
        if (isEdit) await api.adminUpdateEngine(form.provider, body);
        else await api.adminCreateEngine(body);
      } else if (modal.kind === "routing") {
        if (isEdit) await api.adminUpdateRoutingRule(form.id, { ...form });
        else
          await api.adminCreateRoutingRule({
            category: form.category,
            provider: form.provider,
            priority: Number(form.priority) || 0,
            enabled: true,
          });
      } else {
        if (isEdit) await api.adminUpdatePricingTier(form.tier_key, { ...form });
        else await api.adminCreatePricingTier({ ...form });
      }
      setModal(null);
      await reload();
    } catch (e: any) {
      setError(e?.message || "保存失败");
    } finally {
      setBusy(false);
    }
  }

  const title =
    modal.kind === "engine" ? "生成方式" : modal.kind === "routing" ? "内容分配" : "收费方案";

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
      <div className="w-full max-w-md rounded-2xl card p-5">
        <h3 className="mb-4 text-lg font-semibold">
          {isEdit ? "修改" : "新增"} {title}
        </h3>
        <div className="max-h-[65vh] space-y-3 overflow-y-auto">
          {modal.kind === "engine" && (
            <>
              <FText label="名称（方式代号，新建后不可改）" value={form.provider ?? ""} disabled={isEdit} onChange={(v) => set("provider", v)} />
              <FText label="显示名" value={form.display_name ?? ""} onChange={(v) => set("display_name", v)} />
              <FNum label="每首成本（元）" value={form.cost_per_song_cny ?? 0} onChange={(v) => set("cost_per_song_cny", v)} />
              <details className="rounded-lg border-[var(--border)] p-2">
                <summary className="cursor-pointer text-sm text-[var(--muted)]">高级设置（一般不用改）</summary>
                <div className="mt-2 space-y-3">
                  <FText label="接口地址" value={form.api_base ?? ""} onChange={(v) => set("api_base", v)} />
                  <FText label="密钥（留空=不改）" value={form.api_key ?? ""} type="password" onChange={(v) => set("api_key", v)} />
                  <FNum label="优先级" value={form.priority ?? 0} onChange={(v) => set("priority", v)} />
                  <FCheck label="设为默认方式" checked={!!form.is_default} onChange={(v) => set("is_default", v)} />
                  <FText label="备注" value={form.notes ?? ""} onChange={(v) => set("notes", v)} />
                </div>
              </details>
            </>
          )}
          {modal.kind === "routing" && (
            <>
              <FText label="内容类型（系统键值：general / christian_worship / instrumental）" value={form.category ?? ""} disabled={isEdit} onChange={(v) => set("category", v)} />
              <label className="block">
                <span className="text-sm text-[var(--muted)]">用哪种方式生成</span>
                <select
                  value={form.provider ?? ""}
                  onChange={(e) => set("provider", e.target.value)}
                  className="input"
                >
                  <option value="">— 请选择 —</option>
                  {engines.map((e) => (
                    <option key={e.provider} value={e.provider}>
                      {e.display_name || e.provider}
                    </option>
                  ))}
                </select>
              </label>
              <FNum label="优先级（数字越大越优先）" value={form.priority ?? 0} onChange={(v) => set("priority", v)} />
            </>
          )}
          {modal.kind === "pricing" && (
            <>
              <FText label="方案代号（新建后不可改）" value={form.tier_key ?? ""} disabled={isEdit} onChange={(v) => set("tier_key", v)} />
              <FText label="方案名称" value={form.tier_name ?? ""} onChange={(v) => set("tier_name", v)} />
              <FNum label="每首价格（元）" value={form.price_cny ?? 0} onChange={(v) => set("price_cny", v)} />
              <FText label="一句话说明" value={form.description ?? ""} onChange={(v) => set("description", v)} />
              <details className="rounded-lg border-[var(--border)] p-2">
                <summary className="cursor-pointer text-sm text-[var(--muted)]">高级设置（一般不用改）</summary>
                <div className="mt-2 space-y-3">
                  <FText label="指定引擎（填系统键值）" value={form.engine_provider ?? ""} onChange={(v) => set("engine_provider", v)} />
                  <FNum label="每首积分" value={form.credits_per_song ?? 1} onChange={(v) => set("credits_per_song", v)} />
                </div>
              </details>
            </>
          )}
        </div>
        <div className="mt-5 flex justify-end gap-2">
          <button onClick={() => setModal(null)} className="btn btn-ghost">
            取消
          </button>
          <button onClick={save} className="btn btn-primary">
            保存
          </button>
        </div>
      </div>
    </div>
  );
}

function FText({ label, value, onChange, disabled, type }: { label: string; value: string; onChange: (v: string) => void; disabled?: boolean; type?: string }) {
  return (
    <label className="block">
      <span className="text-sm text-[var(--muted)]">{label}</span>
      <input
        type={type || "text"}
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
        className="input disabled:opacity-50"
      />
    </label>
  );
}

function FNum({ label, value, onChange }: { label: string; value: number; onChange: (v: number) => void }) {
  return (
    <label className="block">
      <span className="text-sm text-[var(--muted)]">{label}</span>
      <input
        type="number"
        value={value}
        onChange={(e) => onChange(e.target.value === "" ? 0 : Number(e.target.value))}
        className="input"
      />
    </label>
  );
}

function FCheck({ label, checked, onChange }: { label: string; checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <label className="flex items-center gap-2 text-base">
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      <span className="text-[var(--muted)]">{label}</span>
    </label>
  );
}
