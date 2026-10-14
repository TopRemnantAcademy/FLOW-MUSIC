"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import AuthBar from "@/components/AuthBar";
import ThemeToggle from "@/components/ThemeToggle";
import LocaleToggle from "@/components/LocaleToggle";
import { useAdminReady } from "@/lib/adminAuth";
import { useT } from "@/lib/i18n";

// 2026-09-10: 「内容审核」「平台设置」是**仅管理员**功能，不再常驻公共导航栏
// （此前任何访客都能看到这两个入口）。它们已收进 /admin 控制台。
// label 用词典 key（GAP-017），渲染时再 t() 取当前语言。
const LINKS = [
  { href: "/create", key: "nav.create" },
  { href: "/plaza", key: "nav.plaza" },
  { href: "/library", key: "nav.library" },
  { href: "/account", key: "nav.account" },
  // GAP-040：更新日志是面向所有用户（含未登录）的功能说明，故常驻公共导航。
  { href: "/release-notes", key: "nav.releaseNotes" },
];

export default function NavBar() {
  const pathname = usePathname();
  const t = useT();
  // 仅在**管理密钥通过后端校验**后才显示；普通用户（含已登录普通用户）看不到。
  const adminReady = useAdminReady();

  return (
    <header className="sticky top-0 z-20 border-b border-[var(--border)] bg-[var(--bg)]/90 backdrop-blur">
      <nav
        aria-label={t("nav.ariaLabel")}
        className="mx-auto flex max-w-5xl flex-wrap items-center justify-between gap-x-3 gap-y-2 px-4 py-3"
      >
        <Link
          href="/"
          className="text-lg font-bold text-[var(--ink)] transition hover:text-[var(--ink-soft)]"
        >
          FLOW
        </Link>
        <div className="flex flex-wrap items-center gap-1 text-sm">
          {LINKS.map((l) => {
            const active = pathname === l.href;
            return (
              <Link
                key={l.href}
                href={l.href}
                className="chip rounded-full px-3 py-1.5"
                data-on={active}
                aria-current={active ? "page" : undefined}
              >
                {t(l.key)}
              </Link>
            );
          })}
          {/* 2026-09-14: APK 빌드에서는 관리 진입점까지 제거한다(관리 표면 축소).
              - APK 는 /admin 라우트를 빌드에서 격리하므로 이 링크가 404 가 된다.
              - NEXT_PUBLIC_* 는 빌드 시 인라이닝 → 프로덕션 번들에서 데드코드로 제거된다.
              - 관리 기능은 Web 전용(PLAN_MUSIC_APK §3.3). */}
          {adminReady && process.env.NEXT_PUBLIC_APK_BUILD !== "1" && (
            <Link
              href="/admin"
              className="chip rounded-full px-3 py-1.5"
              data-on={pathname.startsWith("/admin")}
              aria-current={pathname.startsWith("/admin") ? "page" : undefined}
              title={t("nav.adminOnlyTitle")}
            >
              {t("nav.admin")}
            </Link>
          )}
          <div className="ml-1 flex items-center gap-1">
            <LocaleToggle />
            <ThemeToggle />
            <AuthBar />
          </div>
        </div>
      </nav>
    </header>
  );
}
