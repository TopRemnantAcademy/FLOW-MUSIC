#!/usr/bin/env node
/**
 * 更新日志一致性闸门（GAP-040）。
 *
 * 守着三件**已经出过事或迟早出事**的事：
 *   1. `package.json.version` 与 `lib/releaseNotes.ts` 的 `CURRENT_VERSION` 漂移
 *      —— 版本号两处不一致时，用户看到的「当前版本」就是个假数。
 *   2. 条目的 ref 为空 —— 空 ref 在 UI 上会渲染成「出处待补」，说明内容没核对。
 *      本脚本默认只 `warn`，`--strict` 时直接红（发布前用严格模式跑）。
 *   3. 日期写成未来 / 格式不对 —— 「预填发布日」是 changelog 最常见的失真方式。
 *
 * 运行：node scripts/check-release-notes.mjs [--strict]
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const strict = process.argv.includes("--strict");
const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, "..");

const pkg = JSON.parse(fs.readFileSync(path.join(root, "package.json"), "utf8"));
const src = fs.readFileSync(path.join(root, "lib/releaseNotes.ts"), "utf8");

const errors = [];
const warnings = [];

// ── 1. 版本号对齐 ───────────────────────────────────────────────────────────
const m = /CURRENT_VERSION\s*=\s*"([^"]+)"/.exec(src);
if (!m) {
  errors.push("lib/releaseNotes.ts 里找不到 CURRENT_VERSION");
} else if (m[1] !== pkg.version) {
  errors.push(`版本号不一致：package.json=${pkg.version}，releaseNotes.CURRENT_VERSION=${m[1]}`);
}

// ── 2. 条目出处 ────────────────────────────────────────────────────────────
// 结构化数据里的 ref 形如：{ kind: "...", text: "...", ref: "#82" }
const entries = [...src.matchAll(/\{\s*kind:\s*"[^"]+",\s*text:\s*"[^"]*",\s*ref:\s*"([^"]*)"/g)];
if (entries.length === 0) {
  errors.push("没解析到任何条目——检查 releaseNotes.ts 是否改了条目结构");
}
for (const e of entries) {
  if (!e[1]) {
    warnings.push(`有条目 ref 为空（出处待补）：${src.slice(e.index, e.index + 80).replace(/\s+/g, " ")}`);
  }
}

// ── 3. 日期合法性 ──────────────────────────────────────────────────────────
const today = new Date().toISOString().slice(0, 10);
for (const d of src.matchAll(/date:\s*(?:"(\d{4}-\d{2}-\d{2})"|null)/g)) {
  if (d[1] && d[1] > today) {
    errors.push(`日期 ${d[1]} 晚于今天 ${today}——更新日志不许预填未来日期`);
  }
}

// ── 报告 ──────────────────────────────────────────────────────────────────
console.log(`[check-release-notes] package.json=${pkg.version} 条目=${entries.length}`);
for (const w of warnings) console.log(`  warn: ${w}`);
for (const e of errors) console.log(`  ERROR: ${e}`);

if (errors.length) process.exit(1);
if (strict && warnings.length) {
  console.log("  --strict：存在待补出处，判失败。");
  process.exit(1);
}
console.log("一致性检查通过。");
