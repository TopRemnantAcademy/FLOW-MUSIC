"use client";

// 语言切换（GAP-017）：三语下拉。用原生 <select> 而不是自绘菜单——
// 键盘/读屏/移动端原生选择器全都免费正确，且不会有 focus 陷阱。
import { LOCALES, LOCALE_LABEL, useI18n, type Locale } from "@/lib/i18n";

export default function LocaleToggle() {
  const { locale, setLocale, t } = useI18n();
  return (
    <label className="flex items-center gap-1 text-xs text-[var(--muted)]">
      <span className="sr-only">{t("locale.label")}</span>
      <svg
        width="15"
        height="15"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden="true"
      >
        <circle cx="12" cy="12" r="9" />
        <path d="M3 12h18M12 3a15 15 0 0 1 0 18a15 15 0 0 1 0-18z" />
      </svg>
      <select
        className="chip cursor-pointer rounded-full px-2 py-1.5 text-xs"
        value={locale}
        // 每项都带上切换目标的本地化名称（“切换到 English”），
        // 因为当前界面语言的读屏用户未必认识别的语言的自称。
        title={t("locale.switchTo", { label: LOCALE_LABEL[locale] })}
        onChange={(e) => setLocale(e.target.value as Locale)}
      >
        {LOCALES.map((l) => (
          <option key={l} value={l}>
            {LOCALE_LABEL[l]}
          </option>
        ))}
      </select>
    </label>
  );
}
