"use client";

import { useTheme, type ResolvedTheme } from "@/lib/theme";

const ICONS: Record<ResolvedTheme, JSX.Element> = {
  light: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
    </svg>
  ),
  dark: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" />
    </svg>
  ),
};

const LABEL: Record<ResolvedTheme, string> = {
  light: "亮色",
  dark: "暗色",
};

export default function ThemeToggle() {
  const { theme, resolvedTheme, cycle } = useTheme();
  // theme === "system"（默认，未手动选择）时显示"当前实际呈现"的模式，
  // 这样界面上永远只有亮/暗两种可选状态，不再出现"跟随系统"入口。
  const shown: ResolvedTheme = theme === "system" ? resolvedTheme : theme;
  return (
    <button
      type="button"
      onClick={cycle}
      className="btn btn-ghost btn-sm"
      title={`主题：${LABEL[shown]}（点击切换）`}
      aria-label={`主题：${LABEL[shown]}，点击切换`}
    >
      {ICONS[shown]}
      <span className="hidden sm:inline">{LABEL[shown]}</span>
    </button>
  );
}
