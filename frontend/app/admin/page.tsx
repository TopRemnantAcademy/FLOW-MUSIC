"use client";

/**
 * 管理员控制台（唯一入口）。
 *
 * 2026-09-10 产品调整：内容审核与平台设置**只对管理员可见**，
 * 因此从公共导航栏移除，改为收进本控制台：
 *   /admin                 → 控制台首页（密钥门禁 + 全部入口）
 *   /admin/users           → 用户管理（搜索/分页/封禁/改套餐，GAP-023）
 *   /admin/finance         → 财务结算（收入侧 + 退款 + CSV 导出，GAP-023）
 *   /admin/features        → 特性开关（灰度发布基础，GAP-023）
 *   /admin/moderation      → 内容审核
 *   /admin/music-config    → FLOW 设置
 *   /admin/billing         → 成本对账（Mureka 实时账单 vs 本站估算成本）
 *   /admin/audit           → 审计日志（后台管理操作记录）
 *   /admin/user-links      → 跨域账号桥接（FLOW 订阅者 ↔ 音乐用户，零 PII）
 */
import Link from "next/link";
import { useEffect, useState } from "react";
import { api, AdminStats } from "@/lib/api";
import { clearAdminKey, getAdminKey } from "@/lib/adminAuth";
import AdminGuard from "@/components/AdminGuard";

const ENTRIES = [
  {
    href: "/admin/users",
    emoji: "👥",
    title: "用户管理",
    desc: "搜索用户，封禁/解封、调整套餐（动作全部入审计）",
  },
  {
    href: "/admin/finance",
    emoji: "💰",
    title: "财务结算",
    desc: "收入与退款事实、结算/发票 CSV 导出（与成本对账互补）",
  },
  {
    href: "/admin/features",
    emoji: "🚦",
    title: "特性开关",
    desc: "灰度发布：按比例/名单/套餐放量，带判定预览",
  },
  {
    href: "/admin/moderation",
    emoji: "🛡️",
    title: "内容审核",
    desc: "审核用户作品，通过 / 驳回并填写理由",
  },
  {
    href: "/admin/music-config",
    emoji: "⚙️",
    title: "FLOW 设置",
    desc: "生成引擎、内容分配规则、收费方案",
  },
  {
    href: "/admin/billing",
    emoji: "💰",
    title: "成本对账",
    desc: "Mureka 实时账单 vs 本站估算成本（花钱侧）",
  },
  {
    href: "/admin/audit",
    emoji: "📋",
    title: "审计日志",
    desc: "后台管理操作审计记录",
  },
  {
    href: "/admin/user-links",
    emoji: "🔗",
    title: "跨域账号桥接",
    desc: "FLOW 订阅者 ↔ 音乐用户关联（零 PII）",
  },
];

export default function AdminHomePage() {
  const [key, setKey] = useState<string | null>(null);
  const [stats, setStats] = useState<AdminStats | null>(null);
  const [error, setError] = useState("");

  // null = 尚未读取 localStorage（避免 SSR/首帧把门禁闪出来）
  useEffect(() => {
    setKey(getAdminKey() || null);
  }, []);

  async function load() {
    setError("");
    try {
      setStats(await api.adminStats());
    } catch (e: any) {
      setError(e?.message || "加载失败");
      // 密钥无效 → 回到门禁，避免停留在"能点但全都报错"的状态
      if (e?.status === 401 || e?.status === 403 || e?.status === 503) {
        clearAdminKey();
        setKey(null);
      }
    }
  }

  useEffect(() => {
    if (key) load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  function logout() {
    clearAdminKey();
    setKey(null);
    setStats(null);
  }

  if (key === null) {
    return (
      <AdminGuard
        title="管理员控制台"
        desc="请输入管理密钥（后台密钥），进入后可管理内容审核与平台设置。"
        onUnlocked={(k) => setKey(k)}
      />
    );
  }

  const pending = stats?.by_moderation?.pending || 0;
  const handled =
    (stats?.by_moderation?.approved || 0) + (stats?.by_moderation?.rejected || 0);

  return (
    <div className="mx-auto max-w-3xl p-6">
      <div className="mb-5 flex items-center justify-between">
        <h1 className="text-2xl font-semibold">管理员控制台</h1>
        <button
          onClick={logout}
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

      <div className="grid gap-3 sm:grid-cols-2">
        {ENTRIES.map((e) => (
          <Link
            key={e.href}
            href={e.href}
            className="card p-5 transition hover:border-[var(--ink-soft)]"
          >
            <div className="mb-2 text-2xl">{e.emoji}</div>
            <div className="mb-1 text-lg font-semibold text-[var(--ink)]">{e.title}</div>
            <div className="text-sm text-[var(--muted)]">{e.desc}</div>
          </Link>
        ))}
      </div>
    </div>
  );
}

function BigCard({
  emoji,
  title,
  value,
  sub,
}: {
  emoji: string;
  title: string;
  value: string;
  sub: string;
}) {
  return (
    <div className="card rounded-2xl p-4">
      <div className="text-2xl">{emoji}</div>
      <div className="mt-1 text-sm text-[var(--muted)]">{title}</div>
      <div className="text-2xl font-semibold text-[var(--ink)]">{value}</div>
      <div className="text-xs text-[var(--muted)]">{sub}</div>
    </div>
  );
}
