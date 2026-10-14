#!/usr/bin/env node
/**
 * GAP-017 i18n 自检 —— 纯静态校验，不启动服务、不需要浏览器。
 *
 * 为什么需要（每一条都对应一类「静默失败」）：
 *   1) 词典 key 漂移：ko 少一个 key 时页面**不会报错**，只会静默回退到中文
 *      → 韩语用户看到中文字；只有逐 key 比对才能发现。
 *   2) `output:'export'` 是 Wave 1 硬验收，且历史上真的被动态路由打挂过
 *      （#80/#81 引入动态路由时漏 generateStaticParams）。这里钉死：
 *      每个动态路由目录都必须有能提供 generateStaticParams 的 layout/route 文件。
 *   3) locale 持久化的机制是「cookie → SSR 的 <html lang>」。一旦有人把
 *      lib/i18n-server 改成读 headers()/Accept-Language，静态导出会立刻失败；
 *      这里断言它只依赖 cookies()。
 *
 * 用法：node scripts/verify-i18n.mjs
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const FRONTEND = path.resolve(HERE, "..");
const MESSAGES = path.join(FRONTEND, "messages");

let pass = 0;
const failures = [];
function check(name, cond, detail = "") {
  if (cond) { pass += 1; console.log("  \u2713 " + name); }
  else { failures.push(name + (detail ? " — " + detail : "")); console.log("  \u2717 " + name + (detail ? " — " + detail : "")); }
}

const LOCALES = ["zh", "en", "ko"];
const read = (p) => fs.readFileSync(p, "utf8");

console.log("[verify-i18n] 1. messages/*.json 存在且可解析");
const dicts = {};
for (const l of LOCALES) {
  const p = path.join(MESSAGES, `${l}.json`);
  check(`${l}.json 存在`, fs.existsSync(p));
  try {
    dicts[l] = JSON.parse(read(p));
    check(`${l}.json 可解析`, true);
  } catch (e) {
    check(`${l}.json 可解析`, false, e.message);
  }
}

function flatten(obj, prefix = "", out = {}) {
  for (const [k, v] of Object.entries(obj)) {
    const key = prefix ? `${prefix}.${k}` : k;
    if (v && typeof v === "object" && !Array.isArray(v)) flatten(v, key, out);
    else out[key] = v;
  }
  return out;
}

console.log("[verify-i18n] 2. 三语 key 集完全一致（防静默回退）");
if (dicts.zh && dicts.en && dicts.ko) {
  const fz = flatten(dicts.zh);
  const fe = flatten(dicts.en);
  const fk = flatten(dicts.ko);
  const kz = Object.keys(fz).sort();
  check("zh/en key 数一致", kz.length === Object.keys(fe).length,
    `zh=${kz.length} en=${Object.keys(fe).length}`);
  check("zh/ko key 数一致", kz.length === Object.keys(fk).length,
    `zh=${kz.length} ko=${Object.keys(fk).length}`);
  const missEn = kz.filter((k) => !(k in fe));
  const missKo = kz.filter((k) => !(k in fk));
  check("en 无缺失 key", missEn.length === 0, missEn.slice(0, 5).join(", "));
  check("ko 无缺失 key", missKo.length === 0, missKo.slice(0, 5).join(", "));
  // 空值会让 UI 出现空白而不是回退，属于隐性缺陷。
  const empties = [];
  for (const [l, f] of [["zh", fz], ["en", fe], ["ko", fk]]) {
    for (const [k, v] of Object.entries(f)) {
      if (typeof v !== "string" || v.trim() === "") empties.push(`${l}:${k}`);
    }
  }
  check("无空词条", empties.length === 0, empties.slice(0, 5).join(", "));
  // 占位符集合必须跨语言一致：en 少了 {count} 就会出现「Selected 」
  const badPh = [];
  for (const k of kz) {
    const ph = (s) => new Set([...String(s).matchAll(/\{(\w+)\}/g)].map((m) => m[1]));
    const a = ph(fz[k]), b = ph(fe[k]), c = ph(fk[k]);
    const eq = (x, y) => x.size === y.size && [...x].every((v) => y.has(v));
    if (!eq(a, b) || !eq(a, c)) badPh.push(k);
  }
  check("占位符集合跨语言一致", badPh.length === 0, badPh.slice(0, 5).join(", "));
}

console.log("[verify-i18n] 3. layout 用 cookie 决定 <html lang>（不读 Accept-Language）");
const layout = read(path.join(FRONTEND, "app", "layout.tsx"));
check("layout 引用 resolveLocale", layout.includes("resolveLocale"));
check("layout 用 HTML_LANG 设 lang", /lang=\{HTML_LANG\[/.test(layout));
check("layout 不含硬编码 lang=\"zh-CN\"", !/lang="zh-CN"/.test(layout));
const srv = read(path.join(FRONTEND, "lib", "i18n-server.ts"));
check("i18n-server 只依赖 cookies()", srv.includes("cookies()"));
// 去注释后再查：注释里出现 headers() 是解释，不是调用。
const srvCode = srv.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
check("i18n-server 不读 headers()（否则静态导出会挂）", !/\bheaders\(\)/.test(srvCode));

console.log("[verify-i18n] 4. 静态导出：动态路由均可枚举");
// 找出 app 下所有含 [param] 的目录，要求其自身或父级链上有 generateStaticParams。
function findDynamicDirs(dir, out = []) {
  if (!fs.existsSync(dir)) return out;
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    if (!e.isDirectory()) continue;
    const p = path.join(dir, e.name);
    if (/^\[.+\]$/.test(e.name)) out.push(p);
    findDynamicDirs(p, out);
  }
  return out;
}
for (const d of findDynamicDirs(path.join(FRONTEND, "app"))) {
  const rel = path.relative(FRONTEND, d);
  let cur = d;
  let found = false;
  // 参数可以定义在该段或任意祖先段的 layout/route 里。
  while (cur.startsWith(path.join(FRONTEND, "app"))) {
    for (const f of fs.readdirSync(cur)) {
      if (/^(layout|route)\.(ts|tsx|js|jsx)$/.test(f) && read(path.join(cur, f)).includes("generateStaticParams")) {
        found = true;
      }
    }
    if (found) break;
    // 也认「子段」提供参数：Next 允许参数在更深一层的 segment 里枚举
    // （如 songs/[id]/lyrics/layout.tsx 覆盖到 [id] 段下实际存在的页面）。
    const sub = fs.readdirSync(cur, { withFileTypes: true }).filter((e) => e.isDirectory());
    for (const d of sub) {
      const sp = path.join(cur, d.name);
      for (const f of fs.readdirSync(sp)) {
        if (/^(layout|route)\.(ts|tsx|js|jsx)$/.test(f) && read(path.join(sp, f)).includes("generateStaticParams")) {
          found = true;
        }
      }
    }
    if (found) break;
    const parent = path.dirname(cur);
    if (parent === cur) break;
    cur = parent;
  }
  check(`${rel} 可静态枚举`, found, "缺 generateStaticParams（APK_BUILD=1 会失败）");
}

console.log("[verify-i18n] 5. 切换 UI 与持久化接线");
const nav = read(path.join(FRONTEND, "components", "NavBar.tsx"));
check("NavBar 含 LocaleToggle", nav.includes("LocaleToggle"));
const catalog = read(path.join(FRONTEND, "lib", "i18n-catalog.ts"));
check("cookie 名 = flow_locale", catalog.includes('COOKIE_NAME = "flow_locale"'));
check("localStorage key = locale", catalog.includes('STORAGE_KEY = "locale"'));
check("默认 locale = zh", /DEFAULT_LOCALE: Locale = "zh"/.test(catalog));

console.log("");
if (failures.length) {
  console.error("[verify-i18n] ❌ " + failures.length + " 项失败");
  for (const f of failures) console.error("   - " + f);
  process.exit(1);
}
console.log(`[verify-i18n] ✅ ${pass} passed, 0 failed`);
