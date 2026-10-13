#!/usr/bin/env node
/**
 * PWA(GAP-018) 资产自检 —— 不启动服务、不需要浏览器，纯静态校验。
 *
 * 为什么需要：
 *   1) manifest 是 JSON，一个多余逗号就会让浏览器静默忽略「可安装」资格，
 *      而构建完全不报错 → 必须有独立断言。
 *   2) sw.js 必须语法合法（SW 解析失败是「完全静默」的失败模式）。
 *   3) 「不得破坏 APK_BUILD=1」是硬验收：断言 next.config.js 的 APK 分支仍在。
 *   4) meta 注入必须真的出现在 layout.tsx（改了组件但忘了导出的经典漂移）。
 *
 * 运行：node frontend/scripts/verify-pwa.mjs
 */
import fs from "node:fs";
import path from "node:path";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const FRONTEND = path.resolve(HERE, "..");
const PUBLIC = path.join(FRONTEND, "public");

let pass = 0;
const failures = [];

function check(name, cond, detail = "") {
  if (cond) { pass += 1; console.log("  \u2713 " + name); }
  else { failures.push(name + (detail ? " — " + detail : "")); console.log("  \u2717 " + name + (detail ? " — " + detail : "")); }
}

const read = (p) => fs.readFileSync(p, "utf8");
const exists = (p) => fs.existsSync(p);

console.log("[verify-pwa] 1. manifest");
const manifestPath = path.join(PUBLIC, "manifest.webmanifest");
check("manifest.webmanifest 存在", exists(manifestPath));
let manifest = null;
if (exists(manifestPath)) {
  try { manifest = JSON.parse(read(manifestPath)); check("manifest 可解析为 JSON", true); }
  catch (e) { check("manifest 可解析为 JSON", false, e.message); }
}
if (manifest) {
  check("name / short_name 非空", !!manifest.name && !!manifest.short_name);
  check("start_url 存在", !!manifest.start_url);
  check("scope 存在", !!manifest.scope);
  check("display === standalone", manifest.display === "standalone", "实际 " + manifest.display);
  check("theme_color 存在", !!manifest.theme_color, "实际 " + manifest.theme_color);
  check("background_color 存在", !!manifest.background_color);
  const icons = manifest.icons || [];
  const sizes = icons.map((i) => i.sizes);
  check("含 192x192 图标", sizes.includes("192x192"));
  check("含 512x512 图标", sizes.includes("512x512"));
  check("含 maskable 图标", icons.some((i) => (i.purpose || "").includes("maskable")));
  // 每个声明的图标必须真实存在（404 图标 → Chrome 判不可安装）
  for (const ic of icons) {
    if (ic.sizes === "any" && String(ic.src).endsWith(".svg")) continue;
    const p = path.join(PUBLIC, ic.src.replace(/^\//, ""));
    check("图标文件存在: " + ic.src, exists(p));
  }
}

console.log("[verify-pwa] 2. service worker");
const swPath = path.join(PUBLIC, "sw.js");
check("sw.js 存在", exists(swPath));
if (exists(swPath)) {
  try { execFileSync(process.execPath, ["--check", swPath], { stdio: "pipe" }); check("sw.js 语法合法", true); }
  catch (e) { check("sw.js 语法合法", false, String(e.stderr || e.message).slice(0, 200)); }
  const sw = read(swPath);
  check("含 audio 缓存策略", /cacheFirstAudio/.test(sw));
  check("含音频上限常量", /FLW_AUDIO_MAX_ENTRIES\s*=\s*\d+/.test(sw));
  check("含 SW 版本常量(更新策略)", /SW_VERSION\s*=\s*"/.test(sw));
  check("拒绝 206/Range 落盘", /status\s*!==\s*200/.test(sw));
  check("拒绝 opaque 响应", /type\s*===\s*"opaque"/.test(sw));
  check("离线音频 503 兜底", /offlineAudioResponse/.test(sw));
  check("导航离线回退壳", /networkFirstShell/.test(sw));
  check("activate 清理旧版本缓存", /caches\.delete/.test(sw));
}

console.log("[verify-pwa] 3. apple-touch-icon（iOS meta）");
check("apple-touch-icon.png 存在", exists(path.join(PUBLIC, "icons", "apple-touch-icon.png")));

console.log("[verify-pwa] 4. layout.tsx meta 注入");
const layout = read(path.join(FRONTEND, "app", "layout.tsx"));
check("声明 manifest", /manifest:\s*"\/manifest\.webmanifest"/.test(layout));
check("声明 apple 图标", /apple-touch-icon\.png/.test(layout));
check("声明 appleWebApp", /appleWebApp\s*:\s*\{/.test(layout));
check("声明 viewport(viewport-fit)", /viewportFit\s*:\s*"cover"/.test(layout));
check("渲染 PwaSetup", /<PwaSetup\s*\/>/.test(layout));

console.log("[verify-pwa] 5. APK 分支未被破坏（硬验收）");
const cfg = read(path.join(FRONTEND, "next.config.js"));
check("APK_BUILD 开关仍在", /APK_BUILD\s*===\s*"1"/.test(cfg));
check("output:'export' 分支仍在", /output\s*=\s*"export"/.test(cfg));
check("images.unoptimized 仍在", /unoptimized\s*:\s*true/.test(cfg));
check("trailingSlash 分支仍在", /trailingSlash\s*=\s*true/.test(cfg));
check("turbopack.root 钉定仍在", /turbopack\s*=\s*\{\s*root/.test(cfg));

console.log("[verify-pwa] 6. PwaSetup 的 Capacitor 守卫");
const pwa = read(path.join(FRONTEND, "components", "PwaSetup.tsx"));
check("feature-detect serviceWorker", /"serviceWorker"\s*in\s*navigator/.test(pwa));
check("显式跳过 Capacitor(window.Capacitor)", /window[^\n]*\.Capacitor|Capacitor\s*\}\)/.test(pwa));
check("注册失败不抛出(仅 warn)", /console\.warn/.test(pwa));

console.log("\n[verify-pwa] " + pass + " passed, " + failures.length + " failed");
if (failures.length) {
  for (const f of failures) console.error("  ✗ " + f);
  process.exit(1);
}
console.log("[verify-pwa] ✅ PWA 自检通过");
