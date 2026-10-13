// 主题三态 + 持久化 + 首屏脚本 回归（纯静态源码断言，无需 React 运行时）
import fs from "node:fs";
import path from "node:path";

// 以本文件所在目录（tests/ 的上级 = frontend/）为根解析源码路径，
// 无论从哪个 cwd 调用都能运行。
const FE = path.resolve(import.meta.dirname, "..");
const read = (p) => fs.readFileSync(path.join(FE, p), "utf8");

const theme = read("lib/theme.tsx");
const toggle = read("components/ThemeToggle.tsx");
const css = read("app/globals.css");
const layout = read("app/layout.tsx");
const navbar = read("components/NavBar.tsx");

let pass = 0, fail = 0;
const ok = (n, c) => { c ? (pass++, console.log(`  ✓ ${n}`)) : (fail++, console.log(`  ✗ ${n}`)); };

console.log("① 三态定义与持久化");
ok("ThemeMode 含 system/light/dark 三态", /"system"\s*\|\s*"light"\s*\|\s*"dark"/.test(theme));
ok("STORAGE_KEY = localStorage('theme')", /STORAGE_KEY\s*=\s*"theme"/.test(theme));
ok("setTheme 写入 localStorage", /localStorage\.setItem\(STORAGE_KEY, t\)/.test(theme));
ok("挂载时从 localStorage 读回", /localStorage\.getItem\(STORAGE_KEY\)/.test(theme));
ok("localStorage 异常静默（隐私模式不崩）", /catch\s*\{[\s\S]{0,80}localStorage 不可用/.test(theme));

console.log("② system 模式 = 不写 data-theme，交媒体查询");
ok("applyTheme(system) removeAttribute", /mode === "system"[\s\S]{0,60}removeAttribute\("data-theme"\)/.test(theme));
ok("applyTheme(light/dark) setAttribute", /setAttribute\("data-theme", mode\)/.test(theme));
ok("监听 prefers-color-scheme 变化", /matchMedia\("\(prefers-color-scheme: dark\)"\)/.test(theme));
ok("卸载时移除监听", /removeEventListener\("change"/.test(theme));

console.log("③ 切换循环（两态）");
ok("cycle 在亮/暗间切换", /resolvedTheme === "dark" \? "light" : "dark"/.test(theme));

console.log("④ 首屏无闪烁脚本 + 挂载");
ok("noFlashScript 在首帧前设置 data-theme", /noFlashScript/.test(theme) && /setAttribute\("data-theme", t\)/.test(theme));
ok("layout 内联注入 noFlashScript（head 内）", /noFlashScript/.test(layout) && /dangerouslySetInnerHTML/.test(layout));
ok("html 有 suppressHydrationWarning", /suppressHydrationWarning/.test(layout));
ok("body 有 theme-transition（整页过渡）", /className="theme-transition/.test(layout));

console.log("⑤ 切换 UI 接入");
ok("ThemeToggle 用 useTheme", /useTheme\(\)/.test(toggle));
ok("ThemeToggle 接入 NavBar:61", /ThemeToggle/.test(navbar));
ok("按钮有 aria-label（可访问）", /aria-label=/.test(toggle));

console.log("⑥ CSS 变量主题：light/dark 全 token 覆盖");
const root = css.match(/:root\s*\{([\s\S]*?)\}/)[1];
const tokens = [...root.matchAll(/^\s*(--[a-z0-9-]+)\s*:/gm)].map(m => m[1]);
const darkMedia = css.match(/@media \(prefers-color-scheme: dark\)\s*\{[\s\S]*?:root:not\(\[data-theme="light"\]\)\s*\{([\s\S]*?)\n\s*\}/)[1];
const darkExplicit = css.match(/\[data-theme="dark"\]\s*\{([\s\S]*?)\n\}/)[1];
const lightExplicit = css.match(/\[data-theme="light"\]\s*\{([\s\S]*?)\n\}/)[1];
const keys = (b) => new Set([...b.matchAll(/(--[a-z0-9-]+)\s*:/g)].map(m => m[1]));

// 颜色类 token（含 --accent/--line），排除结构性（ease/radius）
const colorTokens = tokens.filter(t => !["--ease","--radius"].includes(t));
for (const [label, block] of [["@media dark", darkMedia], ["[data-theme=dark]", darkExplicit], ["[data-theme=light]", lightExplicit]]) {
  const k = keys(block);
  const missing = colorTokens.filter(t => !k.has(t));
  ok(`${label} 覆盖全部 ${colorTokens.length} 个颜色 token`, missing.length === 0);
  if (missing.length) console.log(`      缺: ${missing.join(", ")}`);
}
ok("--accent 已定义（修复未定义变量）", /--accent\s*:/.test(css));
ok("--line 已定义（修复未定义变量）", /--line\s*:/.test(css));
ok("color-scheme 随主题（light/dark）", /color-scheme:\s*light/.test(css) && /color-scheme:\s*dark/.test(css));

console.log(`\nPASS=${pass} FAIL=${fail}`);
process.exit(fail ? 1 : 0);
