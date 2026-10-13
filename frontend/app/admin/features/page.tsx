"use client";

/**
 * 特性开关（GAP-023）：灰度发布的最小机制。
 * 后端：GET/POST /api/admin/feature-flags、PUT/DELETE /{key}、GET /{key}/preview。
 *
 * 判定优先级（与后端 app/feature_flags.py 同一份语义，此处只做展示）：
 *   ① enabled=false → 对所有人关闭（总闸，连 allow 名单也不生效）
 *   ② deny 名单 → 关闭（压过 allow 与 rollout：灰度排障的第一动作必须立刻生效）
 *   ③ allow 名单 → 开启（手挑的账号不受档位/百分比限制）
 *   ④ 否则 enabled × min_tier × rollout 三层同时满足
 *
 * 「判定预览」不是装饰：靠读三个字段心算 hash 取模 + 名单 + 档位门槛几乎必错，
 * 而灰度期被问得最多的问题恰恰是「为什么这个用户没看到」。
 */
import { useCallback, useEffect, useState } from "react";
import { api, FeatureFlagRow, FeatureFlagPreview } from "@/lib/api";
import { clearAdminKey, getAdminKey } from "@/lib/adminAuth";
import AdminGuard from "@/components/AdminGuard";

const TIERS = ["", "free", "lite", "standard", "sacred", "enterprise"];

type Draft = {
  key: string;
  description: string;
  enabled: boolean;
  rollout: number;
  allow_user_ids: string;
  deny_user_ids: string;
  min_tier: string;
};

const EMPTY: Draft = {
  key: "",
  description: "",
  enabled: false,
  rollout: 0,
  allow_user_ids: "",
  deny_user_ids: "",
  min_tier: "",
};

export default function AdminFeaturesPage() {
  const [token, setToken] = useState<string | null>(null);
  const [rows, setRows] = useState<FeatureFlagRow[]>([]);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [loading, setLoading] = useState(false);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [previewKey, setPreviewKey] = useState("");
  const [previewUser, setPreviewUser] = useState("");
  const [preview, setPreview] = useState<FeatureFlagPreview | null>(null);

  useEffect(() => {
    setToken(getAdminKey() || null);
  }, []);

  const load = useCallback(async () => {
    setError("");
    setLoading(true);
    try {
      setRows(await api.adminFeatureFlags());
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
    if (token) load();
  }, [token, load]);

  if (token === null) {
    return (
      <AdminGuard
        title="特性开关"
        desc="请输入管理密钥（后台密钥），管理灰度开关与放量比例。"
        onUnlocked={(k) => setToken(k)}
      />
    );
  }

  async function save() {
    if (!draft) return;
    setError("");
    setNotice("");
    try {
      if (rows.some((r) => r.key === draft.key)) {
        // 更新：key 不可改（改了就是另一个开关，灰度结果无从解释）
        await api.adminUpdateFeatureFlag(draft.key, {
          description: draft.description,
          enabled: draft.enabled,
          rollout: draft.rollout,
          allow_user_ids: draft.allow_user_ids,
          deny_user_ids: draft.deny_user_ids,
          min_tier: draft.min_tier,
        });
        setNotice(`已更新 ${draft.key}`);
      } else {
        await api.adminCreateFeatureFlag({ ...draft });
        setNotice(`已创建 ${draft.key}（默认关闭）`);
      }
      setDraft(null);
      await load();
    } catch (e: any) {
      setError(e?.message || "保存失败");
    }
  }

  return (
    <div className="mx-auto max-w-4xl p-6">
      <div className="mb-5 flex items-center justify-between">
        <h1 className="text-2xl font-semibold">特性开关</h1>
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

      <div className="mb-4 flex items-center gap-2">
        <button onClick={() => setDraft({ ...EMPTY })} className="btn btn-primary btn-sm">
          新建开关
        </button>
        <button onClick={load} disabled={loading} className="btn btn-secondary btn-sm disabled:opacity-50">
          {loading ? "刷新中…" : "刷新"}
        </button>
      </div>

      <div className="space-y-2">
        {rows.map((f) => (
          <div key={f.key} className="card p-3">
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <span className="font-mono text-[var(--ink)]">{f.key}</span>
              <span
                className={`rounded-full px-2 py-0.5 text-xs font-semibold ${
                  f.enabled ? "bg-emerald-900 text-emerald-300" : "bg-zinc-800 text-zinc-400"
                }`}
              >
                {f.enabled ? "已启用" : "已关闭"}
              </span>
              <span className="rounded-full border border-[var(--border-strong)] px-2 py-0.5 text-xs text-[var(--muted)]">
                灰度 {f.rollout}%
              </span>
              {f.min_tier && (
                <span className="rounded-full border border-[var(--border-strong)] px-2 py-0.5 text-xs text-[var(--muted)]">
                  ≥ {f.min_tier}
                </span>
              )}
              {f.allow_user_ids && (
                <span className="rounded-full bg-sky-950 px-2 py-0.5 text-xs text-sky-300">
                  名单开 {f.allow_user_ids.split(",").filter(Boolean).length}
                </span>
              )}
              {f.deny_user_ids && (
                <span className="rounded-full bg-red-950 px-2 py-0.5 text-xs text-red-300">
                  名单关 {f.deny_user_ids.split(",").filter(Boolean).length}
                </span>
              )}
            </div>
            {f.description && <div className="mt-1 text-xs text-[var(--muted)]">{f.description}</div>}

            <div className="mt-2 flex flex-wrap items-center gap-2">
              {/* 启停是最高频动作 → 单独一个按钮，不必进弹窗 */}
              <button
                className="btn btn-secondary btn-sm"
                onClick={async () => {
                  setError("");
                  setNotice("");
                  try {
                    await api.adminUpdateFeatureFlag(f.key, { enabled: !f.enabled });
                    setNotice(`${f.key} → ${!f.enabled ? "启用" : "关闭"}`);
                    await load();
                  } catch (e: any) {
                    setError(e?.message || "切换失败");
                  }
                }}
              >
                {f.enabled ? "关闭" : "启用"}
              </button>
              <button
                className="btn btn-secondary btn-sm"
                onClick={() =>
                  setDraft({
                    key: f.key,
                    description: f.description || "",
                    enabled: f.enabled,
                    rollout: f.rollout,
                    allow_user_ids: f.allow_user_ids || "",
                    deny_user_ids: f.deny_user_ids || "",
                    min_tier: f.min_tier || "",
                  })
                }
              >
                修改
              </button>
              <button
                className="btn btn-ghost btn-sm"
                onClick={() => {
                  setPreviewKey(f.key);
                  setPreview(null);
                }}
              >
                判定预览
              </button>
              <button
                className="btn btn-danger btn-sm"
                onClick={async () => {
                  if (!window.confirm(`删除开关 ${f.key}？删除 = 对所有人生效地关闭。`)) return;
                  setError("");
                  try {
                    await api.adminDeleteFeatureFlag(f.key);
                    setNotice(`已删除 ${f.key}`);
                    await load();
                  } catch (e: any) {
                    setError(e?.message || "删除失败");
                  }
                }}
              >
                删除
              </button>
            </div>

            {previewKey === f.key && (
              <div className="mt-3 rounded-xl border border-[var(--border)] p-3">
                <div className="flex flex-wrap items-end gap-2">
                  <label className="min-w-[120px]">
                    <span className="mb-1 block text-xs text-[var(--muted)]">user_id</span>
                    <input
                      value={previewUser}
                      onChange={(e) => setPreviewUser(e.target.value)}
                      inputMode="numeric"
                      className="input w-full"
                    />
                  </label>
                  <button
                    className="btn btn-secondary btn-sm"
                    onClick={async () => {
                      setError("");
                      try {
                        setPreview(
                          await api.adminPreviewFeatureFlag(
                            f.key,
                            previewUser.trim() ? Number(previewUser.trim()) : undefined,
                          ),
                        );
                      } catch (e: any) {
                        setError(e?.message || "预览失败");
                      }
                    }}
                  >
                    判定
                  </button>
                </div>
                {preview && (
                  <div className="mt-2 text-sm">
                    <div>
                      结果：
                      <span className={preview.enabled ? "text-emerald-300" : "text-red-300"}>
                        {preview.enabled ? "可见" : "不可见"}
                      </span>
                    </div>
                    <div className="mt-1 space-y-0.5 text-xs text-[var(--muted)]">
                      {Object.entries(preview.steps).map(([k, v]) => (
                        <div key={k}>
                          {k}: {String(v)}
                        </div>
                      ))}
                    </div>
                    <div className="mt-1 text-xs text-[var(--muted)]">优先级：{preview.rule}</div>
                  </div>
                )}
              </div>
            )}
          </div>
        ))}
      </div>

      {!loading && rows.length === 0 && !error && (
        <p className="card p-6 text-center text-[var(--muted)]">还没有开关（新建后默认关闭）</p>
      )}

      {draft && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
          <div className="card max-h-[90vh] w-full max-w-lg overflow-auto rounded-2xl p-5">
            <h2 className="mb-3 text-lg font-semibold">
              {rows.some((r) => r.key === draft.key) ? `修改 ${draft.key}` : "新建开关"}
            </h2>
            <div className="space-y-3">
              <FText
                label="开关代号（小写字母/数字/下划线）"
                value={draft.key}
                disabled={rows.some((r) => r.key === draft.key)}
                onChange={(v) => setDraft({ ...draft, key: v })}
              />
              <FText
                label="说明（这个开关控制什么）"
                value={draft.description}
                onChange={(v) => setDraft({ ...draft, description: v })}
              />
              <FCheck
                label="启用（总闸：关闭时对所有人不可见，含下方名单）"
                checked={draft.enabled}
                onChange={(v) => setDraft({ ...draft, enabled: v })}
              />
              <FNum
                label="灰度比例 0–100（确定性：同一用户恒定命中，不是随机）"
                value={draft.rollout}
                onChange={(v) => setDraft({ ...draft, rollout: v })}
              />
              <FText
                label="强制开启名单（逗号分隔 user_id）"
                value={draft.allow_user_ids}
                onChange={(v) => setDraft({ ...draft, allow_user_ids: v })}
              />
              <FText
                label="强制关闭名单（逗号分隔 user_id，压过开启名单）"
                value={draft.deny_user_ids}
                onChange={(v) => setDraft({ ...draft, deny_user_ids: v })}
              />
              <label className="block">
                <span className="text-sm text-[var(--muted)]">最低套餐门槛（空 = 不限）</span>
                <select
                  value={draft.min_tier}
                  onChange={(e) => setDraft({ ...draft, min_tier: e.target.value })}
                  className="input"
                >
                  {TIERS.map((t) => (
                    <option key={t} value={t}>
                      {t || "不限"}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <div className="mt-5 flex justify-end gap-2">
              <button onClick={() => setDraft(null)} className="btn btn-ghost">
                取消
              </button>
              <button onClick={save} className="btn btn-primary">
                保存
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function FText({
  label,
  value,
  onChange,
  disabled,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  disabled?: boolean;
}) {
  return (
    <label className="block">
      <span className="text-sm text-[var(--muted)]">{label}</span>
      <input
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
        className="input disabled:opacity-50"
      />
    </label>
  );
}

function FNum({
  label,
  value,
  onChange,
}: {
  label: string;
  value: number;
  onChange: (v: number) => void;
}) {
  return (
    <label className="block">
      <span className="text-sm text-[var(--muted)]">{label}</span>
      <input
        type="number"
        min={0}
        max={100}
        value={value}
        onChange={(e) => onChange(e.target.value === "" ? 0 : Number(e.target.value))}
        className="input"
      />
    </label>
  );
}

function FCheck({
  label,
  checked,
  onChange,
}: {
  label: string;
  checked: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <label className="flex items-center gap-2 text-base">
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      <span className="text-[var(--muted)]">{label}</span>
    </label>
  );
}
