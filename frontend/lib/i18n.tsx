"use client";

// 客户端 i18n 运行时（GAP-017）：Context + Provider + useT()。
// 词典与纯函数翻译核心在 lib/i18n-catalog.ts（服务端也可用）——拆开是为了让
// layout.tsx 能在服务端调 translate() 而不触发「服务端调用客户端函数」错误。
//
// ── 为什么不用 next-intl（选型结论，详见 PR 评论）────────────────────────────
// Wave 1 硬验收要求 `APK_BUILD=1` 下 `output: 'export'` 必须通过。实测 next-intl 4.x
// 在 App Router + 静态导出组合下会取请求头（headers()）来推断 locale：
//   Error: Route /[locale] with `dynamic = "error"` couldn't be rendered statically
//          because it used `headers()`.
// 直接把导出打挂。故改用轻量自建：**服务端**在 layout 里根据 cookie 定 locale 并设
// <html lang>，**客户端**用 Context 读同一份词典 —— 首帧两边同值，天然无 hydration
// mismatch。不读 Accept-Language 是刻意的：依赖请求头就会毁掉静态导出。
import {
  createContext,
  useContext,
  useEffect,
  useState,
  useCallback,
  type ReactNode,
} from "react";
import {
  COOKIE_NAME,
  DEFAULT_LOCALE,
  HTML_LANG,
  STORAGE_KEY,
  isLocale,
  translate,
  type Locale,
} from "@/lib/i18n-catalog";

// 重新导出，让调用方只认 lib/i18n 一个入口。
export {
  LOCALES,
  LOCALE_LABEL,
  HTML_LANG,
  DEFAULT_LOCALE,
  translate,
  interpolate,
  isLocale,
} from "@/lib/i18n-catalog";
export type { Locale } from "@/lib/i18n-catalog";

const LocaleContext = createContext<{
  locale: Locale;
  setLocale: (l: Locale) => void;
  t: (path: string, vars?: Record<string, string | number>) => string;
} | null>(null);

/**
 * @param initialLocale 服务端从 cookie 解析出的 locale。客户端首帧直接用它，
 *   因此 SSR 与 hydration 的 DOM 完全一致（不会 mismatch），之后再读
 *   localStorage 做纠偏（cookie 与 localStorage 不同步的极端情况）。
 */
export function LocaleProvider({
  initialLocale = DEFAULT_LOCALE,
  children,
}: {
  initialLocale?: Locale;
  children: ReactNode;
}) {
  const [locale, setLocaleState] = useState<Locale>(initialLocale);

  // 挂载后与 localStorage 对齐：cookie 可能被禁用/被清，localStorage 是第二来源。
  useEffect(() => {
    let saved: string | null = null;
    try {
      saved = localStorage.getItem(STORAGE_KEY);
    } catch {
      /* localStorage 不可用时静默 */
    }
    if (isLocale(saved) && saved !== initialLocale) {
      setLocaleState(saved);
      // localStorage 与 cookie 不一致 → 回写 cookie，保证下次 SSR 也对。
      try {
        document.cookie = `${COOKIE_NAME}=${saved};path=/;max-age=31536000;samesite=lax`;
      } catch {
        /* cookie 不可写时静默 */
      }
    }
  }, [initialLocale]);

  const setLocale = useCallback((l: Locale) => {
    setLocaleState(l);
    try {
      localStorage.setItem(STORAGE_KEY, l);
    } catch {
      /* 静默 */
    }
    try {
      document.cookie = `${COOKIE_NAME}=${l};path=/;max-age=31536000;samesite=lax`;
    } catch {
      /* 静默 */
    }
    // <html lang> 立即同步（SSR 已在服务端设过，这里覆盖客户端切换后的值）。
    if (typeof document !== "undefined") {
      document.documentElement.lang = HTML_LANG[l];
    }
  }, []);

  const t = useCallback(
    (path: string, vars?: Record<string, string | number>) =>
      translate(locale, path, vars),
    [locale]
  );

  return (
    <LocaleContext.Provider value={{ locale, setLocale, t }}>
      {children}
    </LocaleContext.Provider>
  );
}

export function useI18n() {
  const ctx = useContext(LocaleContext);
  if (!ctx) throw new Error("useI18n 必须在 LocaleProvider 内使用");
  return ctx;
}

/** 只要 t 的便捷钩子。 */
export function useT() {
  return useI18n().t;
}
