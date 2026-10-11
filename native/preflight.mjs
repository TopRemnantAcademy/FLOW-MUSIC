#!/usr/bin/env node
/**
 * 패키징 전 사전 점검 — "나중에 포장" 할 때 막히는 조건을 **지금** 알려준다.
 *
 * 왜 필요한가
 * ----------
 * 실제 포장(APK / iOS)은 도메인·서명·macOS 등 외부 조건이 없으면 불가능하다.
 * 이 스크립트는 그 조건들을 **읽기 전용**으로 점검해, 무엇이 남았는지 목록으로 준다.
 * 절대 산출물을 만들지 않는다(포장 아님, 준비 상태 진단 전용).
 *
 * 사용법:
 *   node preflight.mjs            # 사람이 읽는 요약
 *   node preflight.mjs --json     # CI/자동화용
 *
 * 종료코드:
 *   0 = 블로커 없음(포장 진행 가능)
 *   1 = 블로커 있음(아직 포장 불가)
 */

import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const FRONTEND = path.resolve(HERE, "../frontend");
const OUT = path.join(FRONTEND, "out");

const results = [];
const add = (level, item, detail) => results.push({ level, item, detail });

// ── 1) Node 버전 ────────────────────────────────────────────────────────────
// Capacitor 8 要求 Node **22+**（6→8 升级时从 20 提高）。
const nodeMajor = Number(process.versions.node.split(".")[0]);
add(nodeMajor >= 22 ? "PASS" : "FAIL", "Node >= 22", `현재 v${process.versions.node}`);

// ── 2) 정적 산출물 out/ ─────────────────────────────────────────────────────
let outExists = false;
let htmlFiles = [];
if (!fs.existsSync(OUT)) {
  add("FAIL", "정적 산출물 frontend/out", "없음 — `cd ../frontend && APK_BUILD=1 next build` 필요");
} else {
  outExists = true;
  const walk = (d, acc = []) => {
    for (const e of fs.readdirSync(d, { withFileTypes: true })) {
      const p = path.join(d, e.name);
      if (e.isDirectory()) walk(p, acc);
      else acc.push(p);
    }
    return acc;
  };
  const files = walk(OUT);
  htmlFiles = files.filter((f) => f.endsWith(".html"));
  const hasIndex = fs.existsSync(path.join(OUT, "index.html"));
  add(hasIndex ? "PASS" : "FAIL", "out/index.html", hasIndex ? "존재" : "없음");
  add(htmlFiles.length > 0 ? "PASS" : "WARN", "HTML 페이지 수", `${htmlFiles.length}개`);
}

// ── 3) /admin 격리 (보안) ───────────────────────────────────────────────────
// build-apk.mjs 실측 근거: out/admin/ 만 지우는 것으로는 불충분하며
// _next/static/chunks/app/admin/*.js 가 남는다 → 둘 다 검사한다.
if (outExists) {
  const adminDir = fs.existsSync(path.join(OUT, "admin"));
  let adminChunk = false;
  const chunkDir = path.join(OUT, "_next/static/chunks/app");
  if (fs.existsSync(chunkDir)) {
    adminChunk = fs.existsSync(path.join(chunkDir, "admin"));
  }
  add(!adminDir && !adminChunk ? "PASS" : "FAIL", "/admin 완전 배제",
    adminDir || adminChunk
      ? `잔존: ${[adminDir && "out/admin/", adminChunk && "chunks/app/admin/"].filter(Boolean).join(", ")}`
      : "디렉터리·청크 모두 없음");
} else {
  add("WARN", "/admin 완전 배제", "out/ 없어 판정 불가");
}

// ── 4) CSP <meta http-equiv> 주입 ───────────────────────────────────────────
// output:'export' 에선 next.config.js 의 headers() 가 무시되므로
// HTML 의 meta 로만 CSP 가 유지된다.
if (htmlFiles.length > 0) {
  const withCsp = htmlFiles.filter((f) => {
    try {
      return /http-equiv=["']?Content-Security-Policy/i.test(fs.readFileSync(f, "utf8"));
    } catch {
      return false;
    }
  });
  add(withCsp.length === htmlFiles.length ? "PASS" : "FAIL", "CSP meta 주입",
    `${withCsp.length}/${htmlFiles.length} HTML`);
} else {
  add("WARN", "CSP meta 주입", "HTML 없어 판정 불가");
}

// ── 5) loopback API 굽힘 금지 ───────────────────────────────────────────────
// 실기기에서 127.0.0.1 은 "기기 자신" → 100% 접속 실패.
if (outExists) {
  const scan = htmlFiles.concat(
    fs.existsSync(path.join(OUT, "_next"))
      ? (function walkJs(d, acc = []) {
          for (const e of fs.readdirSync(d, { withFileTypes: true })) {
            const p = path.join(d, e.name);
            if (e.isDirectory()) walkJs(p, acc);
            else if (p.endsWith(".js")) acc.push(p);
          }
          return acc;
        })(path.join(OUT, "_next"))
      : []
  );
  const hits = [];
  for (const f of scan.slice(0, 400)) {
    try {
      const s = fs.readFileSync(f, "utf8");
      if (/(127\.0\.0\.1|localhost:\d{2,5})/.test(s)) hits.push(path.relative(OUT, f));
    } catch { /* ignore */ }
  }
  add(hits.length === 0 ? "PASS" : "FAIL", "loopback API 미포함",
    hits.length === 0 ? "없음" : `${hits.length}개 파일: ${hits.slice(0, 3).join(", ")}`);
} else {
  add("WARN", "loopback API 미포함", "out/ 없어 판정 불가");
}

// ── 6) Capacitor 설치 여부 ──────────────────────────────────────────────────
const capCli = fs.existsSync(path.join(HERE, "node_modules/@capacitor/cli/package.json"));
add(capCli ? "PASS" : "WARN", "@capacitor/cli 설치",
  capCli ? "설치됨" : "미설치 — `npm ci` 필요(네트워크)");

// ── 7) 네이티브 플랫폼 ──────────────────────────────────────────────────────
const hasAndroid = fs.existsSync(path.join(HERE, "android"));
const hasIos = fs.existsSync(path.join(HERE, "ios"));
add(hasAndroid ? "PASS" : "WARN", "android 플랫폼",
  hasAndroid ? "존재" : "없음 — `npx cap add android`");
add(hasIos ? "PASS" : "WARN", "ios 플랫폼",
  hasIos ? "존재" : "없음 — `npx cap add ios`(macOS 필요)");

// ── 8) iOS 는 macOS 필요 ────────────────────────────────────────────────────
const isMac = process.platform === "darwin";
add(isMac ? "PASS" : "WARN", "iOS 빌드 환경(macOS+Xcode)",
  isMac ? "이 환경에서 가능" : `현재 ${process.platform} — macOS runner/클라우드/Mac 필요`);

// ── 9) 공개 API base (P0) ───────────────────────────────────────────────────
// 포장 자체는 가능해도, 도메인이 없으면 --api 를 줄 수 없어 산출물이 무의미하다.
const envApi = process.env.NEXT_PUBLIC_API_BASE || "";
add(/^https:\/\//.test(envApi) ? "PASS" : "FAIL", "공개 HTTPS API base",
  envApi ? envApi : "NEXT_PUBLIC_API_BASE 미설정 — 도메인 확보가 최우선(P0)");

// ── 10) Android SDK platform 36 ─────────────────────────────────────────────
// Capacitor 8 = compileSdk/targetSdk 36. Play 는 2026-08-31 부터 신규/업데이트에
// target API 36 을 요구하므로, platform 36 부재 시 릴리스 빌드가 성립하지 않는다.
const sdkRoot = process.env.ANDROID_HOME || process.env.ANDROID_SDK_ROOT ||
  path.join(os.homedir(), "AppData/Local/Android/Sdk");
const platDir = path.join(sdkRoot, "platforms");
let hasApi36 = false;
try {
  hasApi36 = fs.readdirSync(platDir).some((d) => d === "android-36" || d.startsWith("android-36"));
} catch { /* SDK 미설치 */ }
add(hasApi36 ? "PASS" : "FAIL", "Android SDK platform 36",
  hasApi36 ? "설치됨" : `없음 — sdkmanager "platforms;android-36" (SDK: ${sdkRoot})`);

// ── 11) JDK 21 ──────────────────────────────────────────────────────────────
// Capacitor 8 / AGP 8.13 은 Java 21 로 컴파일한다. 시스템 JDK 가 17 이면
// "error: invalid source release: 21" 로 빌드 실패 →
// scripts/build-android.sh 가 CCM_JDK21 로 -Dorg.gradle.java.home 을 덮어쓴다.
const jdk21 = process.env.CCM_JDK21 ||
  "C:/Users/piaoy/.workbuddy/binaries/java/jdk21/jdk-21.0.12.1+1";
const jdkOk = fs.existsSync(path.join(jdk21, "bin", "javac.exe")) ||
  fs.existsSync(path.join(jdk21, "bin", "javac"));
add(jdkOk ? "PASS" : "FAIL", "JDK 21 (빌드용)",
  jdkOk ? jdk21 : `없음: ${jdk21} — scripts/build-android.sh 가 실패한다`);

// ── 12) 릴리스 서명 키 ──────────────────────────────────────────────────────
// android/local.properties (gitignore 대상) 에 storeFile 등이 있어야
// bundleRelease / assembleRelease 가 서명된 산출물을 낸다.
const lpPath = path.join(HERE, "android", "local.properties");
let signOk = false;
try {
  const lp = fs.readFileSync(lpPath, "utf8");
  signOk = /storeFile=/.test(lp) && /keyAlias=/.test(lp) &&
    fs.existsSync(path.join(HERE, "android", "ccm-release.keystore"));
} catch { /* 미설정 */ }
add(signOk ? "PASS" : "FAIL", "릴리스 서명 키",
  signOk ? "local.properties + ccm-release.keystore 확인" :
    "없음 — 릴리스 산출물이 unsigned 로 나온다(Play 업로드 불가)");

// ── 출력 ────────────────────────────────────────────────────────────────────
const asJson = process.argv.includes("--json");
if (asJson) {
  console.log(JSON.stringify(results, null, 2));
} else {
  const icon = { PASS: "✅", WARN: "⚠️ ", FAIL: "❌" };
  console.log("\n=== FLOW CCM 패키징 사전 점검 ===\n");
  for (const r of results) console.log(`${icon[r.level]} ${r.item.padEnd(28)} ${r.detail}`);
  const blockers = results.filter((r) => r.level === "FAIL").length;
  const warns = results.filter((r) => r.level === "WARN").length;
  console.log(`\n블로커 ${blockers}건 / 경고 ${warns}건`);
  if (blockers > 0) console.log("→ 아직 포장 불가. 위 ❌ 항목부터 해결.");
  else console.log("→ 포장 진행 가능.");
}

process.exit(results.some((r) => r.level === "FAIL") ? 1 : 0);
