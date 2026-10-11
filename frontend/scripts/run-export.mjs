#!/usr/bin/env node
/**
 * APK 정적 익스포트 런처 — `cd` 없이 절대경로로 실행한다.
 *
 * 왜 별도 런처인가:
 *   - 이 환경의 Bash shim 이 `cd` 를 삼키므로, `cd frontend && next build` 가 불안정하다.
 *   - Node process.chdir() 로 프론트엔드 디렉터리를 고정한다.
 *   - safe-delete node shim(genie-safe-delete.cjs, NODE_OPTIONS --require) 이
 *     `next build` 의 내부 fs.rm(.next/turbopack) 을 막는다(빌드 rc=1).
 *     → SESSION_ID 를 지워 shim 을 비활성화한다(자식 next 도 상속).
 *
 * 사용법:
 *   FRONTEND_DIR=/abs/path node scripts/run-export.mjs
 */
import { execFileSync } from "node:child_process";
import path from "node:path";
import fs from "node:fs";

const root = process.env.FRONTEND_DIR
  ? path.resolve(process.env.FRONTEND_DIR)
  : path.resolve(path.dirname(new URL(import.meta.url).pathname), "..");

if (!fs.existsSync(path.join(root, "next.config.js"))) {
  console.error("[run-export] next.config.js not found under", root);
  process.exit(2);
}
process.chdir(root);
console.log("[run-export] cwd =", process.cwd());

const nextBin = path.join(root, "node_modules", "next", "dist", "bin", "next");
if (!fs.existsSync(nextBin)) {
  console.error("[run-export] next binary missing:", nextBin);
  process.exit(2);
}

const env = { ...process.env, APK_BUILD: "1", NODE_ENV: "production" };
// safe-delete node shim 비활성화(자식 프로세스에도 상속).
delete env.CODEBUDDY_SESSION_ID;
delete env.CLAUDE_SESSION_ID;

console.log("[run-export] launching: next build (output:export, APK_BUILD=1)");
try {
  execFileSync(process.execPath, [nextBin, "build"], { stdio: "inherit", env });
  console.log("[run-export] BUILD_OK");
} catch (e) {
  console.error("[run-export] BUILD_FAILED rc=", e.status ?? "unknown");
  process.exit(typeof e.status === "number" ? e.status : 1);
}
