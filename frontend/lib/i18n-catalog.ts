// 词典与纯函数翻译核心（GAP-017）——**不含任何 React / "use client"**，
// 因此 Server Component（layout.tsx）与 Client Component 都能安全引用。
//
// 之所以拆成两片：
//   - 本文件：LOCALES / 词典 / translate()（纯函数，可服务端调用）
//   - lib/i18n.tsx：Context + Provider + useT()（客户端）
// 合在一起的话，服务端调 translate() 会被 Next 判成「服务端调用了客户端函数」而报错。
import zh from "@/messages/zh.json";
import en from "@/messages/en.json";
import ko from "@/messages/ko.json";

export const LOCALES = ["ko", "zh", "en"] as const;
export type Locale = (typeof LOCALES)[number];

/** 默认 locale：与 <html lang> 的静态默认值、词典兜底保持一致。 */
export const DEFAULT_LOCALE: Locale = "zh";

/** <html lang> 用的 BCP-47 标签（词典 key 是简写）。 */
export const HTML_LANG: Record<Locale, string> = {
  ko: "ko-KR",
  zh: "zh-CN",
  en: "en",
};

export const LOCALE_LABEL: Record<Locale, string> = {
  ko: "한국어",
  zh: "中文",
  en: "English",
};

export const STORAGE_KEY = "locale";
/** cookie 名：服务端 layout 靠它决定 SSR 的 <html lang>，避免刷新后跳回默认。 */
export const COOKIE_NAME = "flow_locale";

type Dict = Record<string, unknown>;
const DICTS: Record<Locale, Dict> = {
  zh: zh as Dict,
  en: en as Dict,
  ko: ko as Dict,
};

export function isLocale(v: unknown): v is Locale {
  return typeof v === "string" && (LOCALES as readonly string[]).includes(v);
}

/**
 * 把 `hello {name}` 里的占位符替换掉。缺失的变量原样保留占位符（可见即可修），
 * 不做静默吞掉 —— 少一个参数应该看得见，而不是渲染成空串。
 */
export function interpolate(
  template: string,
  vars?: Record<string, string | number>
): string {
  if (!vars) return template;
  return template.replace(/\{(\w+)\}/g, (m, key: string) =>
    key in vars ? String(vars[key]) : m
  );
}

function lookup(dict: Dict, path: string): string | undefined {
  const parts = path.split(".");
  let cur: unknown = dict;
  for (const p of parts) {
    if (typeof cur !== "object" || cur === null) return undefined;
    cur = (cur as Dict)[p];
  }
  return typeof cur === "string" ? cur : undefined;
}

/**
 * 取值：`translate(locale, "nav.create")`。找不到时回退到 zh 词典；再找不到则返回
 * key 本身（开发期一眼可见，生产也不会崩）。绝不抛异常 —— 词条缺失不该让页面白屏。
 */
export function translate(
  locale: Locale,
  path: string,
  vars?: Record<string, string | number>
): string {
  const hit = lookup(DICTS[locale], path) ?? lookup(DICTS[DEFAULT_LOCALE], path);
  if (hit === undefined) return path;
  return interpolate(hit, vars);
}
