"use client";

// 主题引擎：system / light / dark。
// - system：跟随 OS prefers-color-scheme（不写 data-theme，交由 CSS 媒体查询）
// - light / dark：显式覆盖
// 选择持久化到 localStorage("theme")，首屏无闪烁由 layout 内联脚本保证。
import {
  createContext,
  useContext,
  useEffect,
  useState,
  useCallback,
  type ReactNode,
} from "react";

export type ThemeMode = "system" | "light" | "dark";
export type ResolvedTheme = "light" | "dark";

const STORAGE_KEY = "theme";
const ThemeContext = createContext<{
  theme: ThemeMode;
  resolvedTheme: ResolvedTheme;
  setTheme: (t: ThemeMode) => void;
  cycle: () => void;
} | null>(null);

function getSystemTheme(): ResolvedTheme {
  if (typeof window === "undefined") return "dark";
  return window.matchMedia("(prefers-color-scheme: dark)").matches
    ? "dark"
    : "light";
}

function applyTheme(mode: ThemeMode) {
  if (typeof document === "undefined") return;
  const root = document.documentElement;
  if (mode === "system") {
    root.removeAttribute("data-theme");
  } else {
    root.setAttribute("data-theme", mode);
  }
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<ThemeMode>("system");
  const [resolvedTheme, setResolvedTheme] = useState<ResolvedTheme>("dark");

  // 挂载时从 localStorage 读偏好
  useEffect(() => {
    const saved = (localStorage.getItem(STORAGE_KEY) as ThemeMode | null) || "system";
    setThemeState(saved);
    applyTheme(saved);
    setResolvedTheme(saved === "system" ? getSystemTheme() : saved);
  }, []);

  // system 模式下监听系统主题变化
  useEffect(() => {
    if (theme !== "system") return;
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => setResolvedTheme(getSystemTheme());
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, [theme]);

  const setTheme = useCallback((t: ThemeMode) => {
    setThemeState(t);
    applyTheme(t);
    setResolvedTheme(t === "system" ? getSystemTheme() : t);
    try {
      localStorage.setItem(STORAGE_KEY, t);
    } catch {
      /* localStorage 不可用时静默 */
    }
  }, []);

  // 切换顺序（2026-10-02 改）：原本 system → light → dark → system 三态循环，
  // 现在 UI 不再暴露"跟随系统"选项，只在亮/暗两态间切换。
  // 默认值仍是 system（首次安装即跟随 OS），系统媒体查询监听由上面的 useEffect 负责。
  const cycle = useCallback(() => {
    setTheme(resolvedTheme === "dark" ? "light" : "dark");
  }, [resolvedTheme, setTheme]);

  return (
    <ThemeContext.Provider value={{ theme, resolvedTheme, setTheme, cycle }}>
      {children}
    </ThemeContext.Provider>
  );
}

export function useTheme() {
  const ctx = useContext(ThemeContext);
  if (!ctx) throw new Error("useTheme 必须在 ThemeProvider 内使用");
  return ctx;
}

/** 首屏无闪烁脚本：在 <head> 中于首帧前应用 data-theme。 */
export const noFlashScript = `
(function(){
  try {
    var t = localStorage.getItem("${STORAGE_KEY}");
    if (t === "light" || t === "dark") {
      document.documentElement.setAttribute("data-theme", t);
    }
  } catch (e) {}
})();
`;
