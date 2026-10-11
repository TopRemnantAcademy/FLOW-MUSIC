#!/usr/bin/env node
/**
 * FLOW 音乐 APK 정적 산출물 빌드 (PLAN_MUSIC_APK §5 단계 0: 1~3)
 *
 *   app/admin 격리 → next build(APK_BUILD=1) → <meta http-equiv> CSP 주입 → 산출물 자체 검증
 *
 * ── 왜 이 스크립트가 필요한가 ────────────────────────────────────────────────
 * 1) `output: 'export'` 는 next.config.js 의 **headers() 를 무시** 한다.
 *    APK 는 Caddy/Nginx 같은承载层 없이 Capacitor WebView 가 로컬 파일을 서빙하므로
 *    헤더로 CSP 를 내릴 수 없다 → **HTML 에 <meta http-equiv> 를 주입** 해야
 *    기존 CSP 강화가 APK 에서도 유지된다(PLAN §2-B "不可省略").
 *
 * 2) /admin 은 **절대 APK 에 들어가면 안 된다**(PLAN §3.3: 공격면 확대).
 *    ⚠️ 실측(2026-09-14): 완성 HTML 인 out/admin/ 만 삭제하는 것으로는 **불충분** 하다.
 *    `_next/static/chunks/app/admin/page-*.js` 등 **admin JS 청크가 그대로 남는다**.
 *    → 사후 삭제가 아니라 **빌드 시점에 app/admin 을 아예 격리(이동)** 해
 *      컴파일조차 되지 않게 한다(청크·RSC 페이로드까지 완전 배제).
 *
 * 3) 실기기에서 127.0.0.1 은 "기기 자신" 이므로, APK 산출물에 loopback API 가
 *    굽혀지면 100% 접속 실패한다 → 빌드 시점에 차단(조용한 실패 방지).
 *
 * 사용법:
 *   node scripts/build-apk.mjs --api=https://api.example.com [--music-host=music.example.com]
 *   node scripts/build-apk.mjs --restore-only        # 비정상 종료 후 app/admin 복구
 *
 * 산출물: frontend/out/  (Capacitor webDir 이 이 디렉터리를 가리킨다)
 */
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const FRONTEND = path.resolve(HERE, "..");
const APP_DIR = path.join(FRONTEND, "app");
const ADMIN_DIR = path.join(APP_DIR, "admin");
/** 격리 보관소(빌드 중에만 존재). app/ 밖이므로 Next 라우트로 컴파일되지 않는다. */
const STASH_DIR = path.join(FRONTEND, ".apk-build-stash");
const STASH_ADMIN = path.join(STASH_DIR, "admin");
const OUT = path.join(FRONTEND, "out");

// security-headers.js 는 CJS → 정책 문자열 단일 진실 공급원 재사용.
const require = createRequire(import.meta.url);
const { metaEquivalent } = require("../security-headers.js");

function die(msg) {
  console.error("[build-apk] ❌ " + msg);
  process.exit(1);
}

/**
 * `--name=value` 파싱.
 * ⚠️ 오프바이원 주의: 접두어는 "--" + name + "=" → 잘라낼 위치는 prefix.length.
 *    (+2 로 잘못 썼다가 값이 "=https://..." 로 잘려 검증을 통과해버린 적이 있다.)
 */
function arg(name) {
  const prefix = `--${name}=`;
  const hit = process.argv.find((a) => a.startsWith(prefix));
  return hit ? hit.slice(prefix.length) : null;
}

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

function rmrf(p) {
  fs.rmSync(p, { recursive: true, force: true });
}

function gitSha(cwd) {
  try {
    const r = spawnSync("git", ["rev-parse", "--short", "HEAD"], {
      cwd, encoding: "utf8", stdio: ["ignore", "pipe", "ignore"],
    });
    return (r.stdout || "").trim() || null;
  } catch { return null; }
}

// ── 복구 전용 모드: 비정상 종료로 app/admin 이 보관소에 남은 경우 ─────────────
if (process.argv.includes("--restore-only")) {
  if (!fs.existsSync(STASH_ADMIN)) {
    console.log("[build-apk] 복구할 격리 보관소가 없습니다(.apk-build-stash/admin 없음).");
    process.exit(0);
  }
  if (fs.existsSync(ADMIN_DIR)) {
    die("app/admin 과 보관소가 **둘 다** 존재합니다. 수동으로 확인 후 하나만 남기세요.");
  }
  fs.mkdirSync(APP_DIR, { recursive: true });
  fs.renameSync(STASH_ADMIN, ADMIN_DIR);
  rmrf(STASH_DIR);
  console.log("[build-apk] ✅ app/admin 복구 완료");
  process.exit(0);
}

// ── 0. 인자/환경 검증 ───────────────────────────────────────────────────────
const apiBase = arg("api") || process.env.NEXT_PUBLIC_API_BASE || "";
const musicHost = arg("music-host") || process.env.MUSIC_WEB_HOST || "";

if (!apiBase) {
  die("--api=<https://api.도메인> 또는 NEXT_PUBLIC_API_BASE 가 필요합니다.\n" +
      "    (정적 익스포트는 **빌드 시점** 에 API 주소가 굽혀집니다 — 런타임 변경 불가)");
}
let parsed;
try {
  parsed = new URL(apiBase);
} catch {
  die(`API 주소를 URL 로 파싱할 수 없습니다: ${JSON.stringify(apiBase)}`);
}
// URL 정규화는 pathname 에 "/" 를 보태므로 양쪽을 동일하게 정규화해 비교.
const stripSlash = (s) => s.replace(/\/+$/, "");
if (/\s/.test(apiBase) || stripSlash(parsed.toString()) !== stripSlash(apiBase)) {
  die(`API 주소에 예상치 못한 문자가 있습니다: ${JSON.stringify(apiBase)}`);
}
if (!parsed.hostname) die(`API 주소에 호스트가 없습니다: ${JSON.stringify(apiBase)}`);
if (/^(127\.0\.0\.1|localhost)(:|$)/i.test(parsed.hostname)) {
  die(`APK 산출물에 loopback API(${apiBase})를 구울 수 없습니다.\n` +
      "    실기기에서 127.0.0.1 은 기기 자신을 가리킵니다. 공개 HTTPS 도메인을 지정하세요.");
}
if (parsed.protocol !== "https:" && !process.argv.includes("--allow-insecure-http")) {
  die(`API 가 HTTPS 가 아닙니다(${apiBase}).\n` +
      "    APK WebView origin 은 https:// 이므로 mixed-content 로 차단됩니다.\n" +
      "    (로컬 http 검증이 정말 필요하면 --allow-insecure-http 를 붙이세요.)");
}
console.log("[build-apk] API = " + apiBase + (musicHost ? "  (web host: " + musicHost + ")" : ""));

// ── 1. app/admin 격리 (빌드에서 완전 배제) ──────────────────────────────────
// 이전 실행이 비정상 종료해 보관소가 남아있으면 먼저 복구(유실 방지).
if (fs.existsSync(STASH_ADMIN) && !fs.existsSync(ADMIN_DIR)) {
  console.warn("[build-apk] ⚠️  이전 실행의 격리 보관소를 발견 — app/admin 복구 후 진행합니다.");
  fs.mkdirSync(APP_DIR, { recursive: true });
  fs.renameSync(STASH_ADMIN, ADMIN_DIR);
  rmrf(STASH_DIR);
}
if (fs.existsSync(STASH_ADMIN) && fs.existsSync(ADMIN_DIR)) {
  die("app/admin 과 격리 보관소가 둘 다 있습니다. `node scripts/build-apk.mjs --restore-only` 또는 수동 정리 후 재시도하세요.");
}

let stashed = false;
if (fs.existsSync(ADMIN_DIR)) {
  rmrf(STASH_DIR);
  fs.mkdirSync(STASH_DIR, { recursive: true });
  fs.renameSync(ADMIN_DIR, STASH_ADMIN);
  stashed = true;
  console.log("[build-apk] app/admin 격리 완료(빌드에서 제외) → .apk-build-stash/admin");
}
if (fs.existsSync(STASH_DIR) && !stashed) rmrf(STASH_DIR);

// 정상 종료/예외 모두 반드시 복구(소스 유실 방지).
function restoreAdmin() {
  if (!stashed) return;
  if (!fs.existsSync(STASH_ADMIN)) return; // 이미 복구됨
  if (fs.existsSync(ADMIN_DIR)) return;    // 수동 복구됨
  fs.mkdirSync(APP_DIR, { recursive: true });
  fs.renameSync(STASH_ADMIN, ADMIN_DIR);
  rmrf(STASH_DIR);
  console.log("[build-apk] app/admin 원위치 복구 완료");
}
process.on("exit", restoreAdmin);
process.on("SIGINT", () => { restoreAdmin(); process.exit(130); });

// ── 2. 정적 익스포트 ────────────────────────────────────────────────────────
rmrf(OUT); // 이전 산출물 제거(남은 admin 자산 혼입 방지)

const nextBin = path.join(FRONTEND, "node_modules", "next", "dist", "bin", "next");
if (!fs.existsSync(nextBin)) { restoreAdmin(); die("next 바이너리를 찾을 수 없습니다: " + nextBin); }

console.log("[build-apk] next build (APK_BUILD=1) 실행…");
const build = spawnSync(process.execPath, [nextBin, "build"], {
  cwd: FRONTEND,
  stdio: "inherit",
  env: {
    ...process.env,
    APK_BUILD: "1",
    NEXT_PUBLIC_APK_BUILD: "1", // account 페이지의 /admin 진입점 데드코드 제거
    NEXT_PUBLIC_API_BASE: apiBase,
    // 2026-10-04: 询单写入 FLOW 主站 support_ticket. 未设置时 lib/api.ts 가
    // https://anistar.studio 로 기본값을 쓰므로 빌드는 계속 성공한다(치명적이지 않음).
    NEXT_PUBLIC_GOSPEL_API_BASE: process.env.NEXT_PUBLIC_GOSPEL_API_BASE || "https://anistar.studio",
    NODE_ENV: "production",
  },
});
if (build.status !== 0) { restoreAdmin(); die(`next build 실패 (exit=${build.status})`); }
restoreAdmin();

if (!fs.existsSync(OUT)) die(`정적 산출물이 없습니다: ${OUT} (output: 'export' 미적용?)`);

// 방어적 삭제: 어떤 경로로든 admin 산출물이 생겼다면 제거.
for (const p of [path.join(OUT, "admin"), path.join(OUT, "admin.html")]) {
  if (fs.existsSync(p)) { rmrf(p); console.log("[build-apk] admin 산출물 추가 제거: " + path.relative(OUT, p)); }
}

// ── 3. <meta http-equiv> CSP 주입 (정적 익스포트가 잃어버린 보안 헤더 보완) ──
const htmls0 = walk(OUT).filter((f) => f.toLowerCase().endsWith(".html"));
const metas = metaEquivalent(apiBase);
let injected = 0;
for (const file of htmls0) {
  let html = fs.readFileSync(file, "utf8");
  if (html.includes('http-equiv="Content-Security-Policy"')) continue; // 멱등
  const tags = metas.map((m) => `<meta http-equiv="${m.httpEquiv}" content="${m.content}">`).join("\n");
  if (!/<head[^>]*>/i.test(html)) die(`<head> 를 찾을 수 없습니다: ${file} — CSP 주입 불가(방치 금지)`);
  fs.writeFileSync(file, html.replace(/(<head[^>]*>)/i, `$1\n${tags}`), "utf8");
  injected += 1;
}
console.log(`[build-apk] CSP <meta> 주입: ${injected}개 HTML`);

// ── 4. 산출물 자체 검증 (거짓 성공 방지) ────────────────────────────────────
const problems = [];
const allFiles = walk(OUT);
const htmls = allFiles.filter((f) => f.toLowerCase().endsWith(".html"));
if (htmls.length === 0) problems.push("HTML 산출물이 0개입니다");

for (const f of allFiles) {
  const rel = path.relative(OUT, f).replace(/\\/g, "/");
  // HTML 문서 경로뿐 아니라 **JS 청크/RSC 페이로드 경로** 까지 검사한다.
  // (HTML 만 지우면 chunks/app/admin/page-*.js 가 남는다 — 2026-09-14 실측)
  if (/(^|\/)admin(\/|\.|$)/.test(rel)) problems.push(`admin 산출물 잔존: ${rel}`);
}
for (const f of htmls) {
  const rel = path.relative(OUT, f).replace(/\\/g, "/");
  const t = fs.readFileSync(f, "utf8");
  if (!t.includes('http-equiv="Content-Security-Policy"')) problems.push(`CSP 미주입: ${rel}`);
  if (/href="\/admin"/.test(t)) problems.push(`/admin 링크 잔존(HTML): ${rel}`);
}
// 컴파일된 JSX 형태(href:"/admin") 도 검사한다.
// HTML 만 보면 **JS 청크에 남은 관리 진입점** 을 놓친다 — 실제로 NavBar.tsx 의
// 관리 chip 이 layout 청크에 남아 있었고(2026-09-14 실측) HTML 검사만으로는 통과했다.
for (const f of allFiles) {
  const rel = path.relative(OUT, f).replace(/\\/g, "/");
  if (rel === "apk-build-manifest.json") continue; // 자기 자신(설명 문구) 제외
  if (!/\.(js|html|txt|json)$/i.test(rel)) continue;
  const t = fs.readFileSync(f, "utf8");
  if (/href:"\/admin"/.test(t)) problems.push(`/admin 링크 잔존(JSX): ${rel}`);
  // [APK] loopback API 잔존 검사 — 실기기에서 127.0.0.1 은 기기 자신 → 접속 실패.
  // preflight.mjs #5 와 동일 기준. 빌드 자체가 이를 막아야 한다(방치 금지).
  if (/(127\.0\.0\.1|localhost:\d{2,5})/.test(t)) problems.push(`loopback API 잔존: ${rel}`);
}
if (problems.length) {
  for (const p of problems) console.error("[build-apk] ❌ " + p);
  die(`산출물 검증 실패 ${problems.length}건`);
}

// ── 5. 빌드 추적 매니페스트 ─────────────────────────────────────────────────
const manifest = {
  builtAt: new Date().toISOString(),
  target: "music-apk",
  apiBase,
  musicHost: musicHost || null,
  gitSha: gitSha(FRONTEND),
  fileCount: allFiles.length,
  htmlCount: htmls.length,
  adminExcludedFromBuild: stashed,
  cspInjected: injected,
  note: "정적 익스포트 산출물. headers() 가 무효하므로 CSP 는 <meta http-equiv> 로 주입됨. app/admin 은 빌드에서 격리되어 청크까지 배제됨.",
};
fs.writeFileSync(path.join(OUT, "apk-build-manifest.json"), JSON.stringify(manifest, null, 2) + "\n", "utf8");

console.log("\n[build-apk] ✅ 완료");
console.log("  파일 수        : " + allFiles.length);
console.log("  HTML           : " + htmls.length);
console.log("  admin 빌드배제 : " + (stashed ? "yes" : "no(라우트 없음)"));
console.log("  CSP 주입       : " + injected);
console.log("  manifest       : out/apk-build-manifest.json");
console.log("  next step      : node ../../scripts/build-music-config.js (Capacitor 동기화)");
