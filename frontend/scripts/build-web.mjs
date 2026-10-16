#!/usr/bin/env node
/**
 * FLOW 音乐 **Web 정적 산출물** 빌드 (deploy/hk/bootstrap.sh 가 호출).
 *
 *   공개 상수 fail-fast → next build(output:export) → <meta http-equiv> CSP 주입 → 자체 검증
 *
 * ── 왜 별도 스크립트인가(P1-3, 2026-10-15) ──────────────────────────────────
 * 1) 운영 Web 은 `APK_BUILD=1 next build`(정적 익스포트) 산출물을 Caddy file_server 로
 *    서빙한다. 정적 익스포트는 next.config.js 의 headers() 를 **무시** 한다 →
 *    CSP 가 **아예 안 내려간다**(build-apk 는 meta 로 보완했지만 Web 은 누락이었다).
 *    그리고 connect-src 가 없으니 교차 출처 fetch(문의/埋点)가 조용히 차단된다.
 * 2) NEXT_PUBLIC_* 는 **빌드 시점에 굽힌다** → 값이 없으면 소스의 fallback(anistar/localhost)이
 *    그대로 박힌다. build-apk 처럼 **빌드 시작 전에 검증하고 없으면 죽는다**(fail-fast).
 *
 * 산출물: frontend/out/  (Caddy 가 /srv 로 마운트)
 *
 * 사용법:
 *   NEXT_PUBLIC_API_BASE=https://music.example.com \
 *   NEXT_PUBLIC_GOSPEL_API_BASE=https://anistar.studio \
 *   NEXT_PUBLIC_FLOW_EVENTS_BASE=https://anistar.studio \
 *   node scripts/build-web.mjs
 */
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";
import { resolvePublicConsts, connectOriginsFrom } from "./public-consts.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const FRONTEND = path.resolve(HERE, "..");
const OUT = path.join(FRONTEND, "out");

const require = createRequire(import.meta.url);
const { metaEquivalent } = require("../security-headers.js");

function die(msg) {
  console.error("[build-web] ❌ " + msg);
  process.exit(1);
}

// ── 1. 빌드 시점 공개 상수 검증(fail-fast) ───────────────────────────────────
let consts;
try {
  consts = resolvePublicConsts(process.env, { target: "web" });
} catch (e) {
  die(e.message);
}
const apiBase = consts.NEXT_PUBLIC_API_BASE;
console.log("[build-web] API = " + apiBase);

const nextBin = path.join(FRONTEND, "node_modules", "next", "dist", "bin", "next");
if (!fs.existsSync(nextBin)) die("next 바이너리를 찾을 수 없습니다: " + nextBin);

// ── 2. 정적 익스포트 ────────────────────────────────────────────────────────
fs.rmSync(OUT, { recursive: true, force: true }); // 잔존 산출물 혼입 방지
console.log("[build-web] next build (APK_BUILD=1, 정적 익스포트) 실행…");
const build = spawnSync(process.execPath, [nextBin, "build"], {
  cwd: FRONTEND,
  stdio: "inherit",
  env: {
    ...process.env,
    APK_BUILD: "1",
    NEXT_PUBLIC_API_BASE: apiBase,
    NEXT_PUBLIC_GOSPEL_API_BASE: consts.NEXT_PUBLIC_GOSPEL_API_BASE,
    NEXT_PUBLIC_FLOW_EVENTS_BASE: consts.NEXT_PUBLIC_FLOW_EVENTS_BASE,
    // Web 산출물에도 admin 을 포함할지는 별도 정책. APK 는 배제하지만 Web 은
    // Caddy 뒤에 있고 기존 동작을 바꾸지 않기 위해 NEXT_PUBLIC_APK_BUILD 는
    // 설정하지 않는다(admin 진입점 유지).
    NODE_ENV: "production",
  },
});
if (build.status !== 0) die(`next build 실패 (exit=${build.status})`);
if (!fs.existsSync(OUT)) die(`정적 산출물이 없습니다: ${OUT} (output: 'export' 미적용?)`);

// ── 3. <meta http-equiv> CSP 주입(headers() 가 무효하므로 필수) ──────────────
function walk(dir) {
  const out = [];
  if (!fs.existsSync(dir)) return out;
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) out.push(...walk(p));
    else out.push(p);
  }
  return out;
}

const cdnBase = process.env.NEXT_PUBLIC_STORAGE_PUBLIC_BASE || "";
const metas = metaEquivalent(apiBase, connectOriginsFrom(consts), cdnBase);
const htmls = walk(OUT).filter((f) => f.toLowerCase().endsWith(".html"));
if (htmls.length === 0) die("HTML 산출물이 0개입니다 — 사이트에 인터페이스가 없습니다");

let injected = 0;
for (const file of htmls) {
  let html = fs.readFileSync(file, "utf8");
  if (html.includes('http-equiv="Content-Security-Policy"')) continue; // 멱등
  if (!/<head[^>]*>/i.test(html)) die(`<head> 를 찾을 수 없습니다: ${file} — CSP 주입 불가`);
  const tags = metas.map((m) => `<meta http-equiv="${m.httpEquiv}" content="${m.content}">`).join("\n");
  fs.writeFileSync(file, html.replace(/(<head[^>]*>)/i, `$1\n${tags}`), "utf8");
  injected += 1;
}
console.log(`[build-web] CSP <meta> 주입: ${injected}개 HTML`);

// ── 4. 산출물 자체 검증(거짓 성공 방지) ─────────────────────────────────────
const problems = [];
const extraOrigins = connectOriginsFrom(consts);
for (const file of htmls) {
  const rel = path.relative(OUT, file).replace(/\\/g, "/");
  const t = fs.readFileSync(file, "utf8");
  if (!t.includes('http-equiv="Content-Security-Policy"')) problems.push(`CSP 미주입: ${rel}`);
  for (const o of extraOrigins) {
    if (!t.includes(o)) problems.push(`connect-src 에 ${o} 누락: ${rel}`);
  }
}
// 번들에 남은 loopback 잔존 검사(Web 은 도메인 배포 — localhost 는 100% 오류).
for (const f of walk(OUT)) {
  const rel = path.relative(OUT, f).replace(/\\/g, "/");
  if (!/\.(js|html|txt|json)$/i.test(rel)) continue;
  if (rel === "web-build-manifest.json") continue;
  const t = fs.readFileSync(f, "utf8");
  if (/(127\.0\.0\.1|localhost:\d{2,5})/.test(t)) problems.push(`loopback 잔존: ${rel}`);
}
if (problems.length) {
  for (const p of problems) console.error("[build-web] ❌ " + p);
  die(`산출물 검증 실패 ${problems.length}건`);
}

const manifest = {
  builtAt: new Date().toISOString(),
  target: "music-web",
  apiBase,
  gospelApiBase: consts.NEXT_PUBLIC_GOSPEL_API_BASE,
  flowEventsBase: consts.NEXT_PUBLIC_FLOW_EVENTS_BASE,
  fileCount: walk(OUT).length,
  htmlCount: htmls.length,
  cspInjected: injected,
  note: "Caddy file_server 정적 산출물. headers() 무효 → CSP 는 <meta http-equiv> 로 주입.",
};
fs.writeFileSync(path.join(OUT, "web-build-manifest.json"), JSON.stringify(manifest, null, 2) + "\n", "utf8");

console.log("\n[build-web] ✅ 완료");
console.log("  HTML      : " + htmls.length);
console.log("  CSP 주입  : " + injected);
console.log("  manifest  : out/web-build-manifest.json");
