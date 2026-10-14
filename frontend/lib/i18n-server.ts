// 服务端 i18n 辅助（GAP-017）。与 lib/i18n.tsx 分开，是为了让 **Server Component**
// 能在不把 Context/Client 逻辑拉进服务端 bundle 的前提下读取 locale。
//
// locale 的确定顺序（全部是**确定性**输入，故 SSR 与 CSR 首帧一致 → 无 hydration mismatch）：
//   1. cookie `flow_locale`（客户端切换时写入；刷新后服务端据此渲染正确的 <html lang>）
//   2. 都没有 → DEFAULT_LOCALE
// 刻意**不**读 Accept-Language / headers()：一旦依赖请求头，`output: 'export'`
// 静态导出就会把它判成动态渲染并直接失败（这正是 next-intl 被否掉的原因）。
import { cookies } from "next/headers";
import { DEFAULT_LOCALE, isLocale, type Locale } from "@/lib/i18n-catalog";

export async function resolveLocale(): Promise<Locale> {
  try {
    const store = await cookies();
    const v = store.get("flow_locale")?.value;
    return isLocale(v) ? v : DEFAULT_LOCALE;
  } catch {
    // 静态导出（APK）编译期没有请求上下文 → 直接用默认 locale。
    // 客户端挂载后再根据 cookie/localStorage 纠偏，不会白屏。
    return DEFAULT_LOCALE;
  }
}
