#!/usr/bin/env node
/**
 * GAP-017 硬编码中文扫描器 —— **只报告，不阻断**（退出码恒为 0）。
 *
 * 为什么先不阻断：
 *   词条抽取是分批进行的（本批次覆盖 create/library/plaza/account/player + 导航/主题），
 *   仓库里仍有大量未迁移的既有文案。若此刻让 CI 变红，只会逼出两种坏结果：
 *   ① 有人往扫描器里塞 ignore 大礼包，② 有人为了变绿删掉还在用的 UI 文案。
 *   因此本脚本先把**现状量化**出来，供后续按模块收敛（目标：ratio 单调下降）。
 *
 * 统计口径（刻意保守，避免噪音淹没信号）：
 *   - 只扫 frontend/app 与 frontend/components 下的 .ts/.tsx；
 *   - 跳过注释行（// …、/* … *​/）与整行 JSDoc —— 注释里的中文是设计说明，不是 UI 文案；
 *   - 只认**字符串字面量 / JSX 文本**里的中文，不看注释；
 *   - i18n 词典文件本身（messages/*.json）天然是中文，不在扫描范围内。
 *
 * 用法：node scripts/scan-hardcoded-i18n.mjs [--json]
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const FRONTEND = path.resolve(HERE, "..");
const ROOTS = [path.join(FRONTEND, "app"), path.join(FRONTEND, "components")];

// 中日韩统一表意文字（够用；扩展区罕见字不在此列）。
const CJK = /[\u4e00-\u9fff]/;

const asJson = process.argv.includes("--json");

function walk(dir, out = []) {
  if (!fs.existsSync(dir)) return out;
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) walk(p, out);
    else if (/\.(ts|tsx)$/.test(e.name)) out.push(p);
  }
  return out;
}

/** 去掉块注释与行注释后再找中文，避免把设计说明算成 UI 文案。 */
function stripComments(src) {
  return src
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/^\s*\/\/.*$/gm, "");
}

const files = ROOTS.flatMap((r) => walk(r));
const rows = [];
let totalCjkLines = 0;
let totalLines = 0;

for (const file of files) {
  const raw = fs.readFileSync(file, "utf8");
  const lines = raw.split("\n");
  totalLines += lines.length;
  let hits = [];
  // 先整体去块注释会影响行号，故逐行处理：行注释原地剔除，块注释用状态机跟踪。
  let inBlock = false;
  lines.forEach((line, i) => {
    let l = line;
    if (inBlock) {
      const end = l.indexOf("*/");
      if (end === -1) return;
      l = l.slice(end + 2);
      inBlock = false;
    }
    const open = l.indexOf("/*");
    if (open !== -1) {
      const close = l.indexOf("*/", open + 2);
      if (close === -1) {
        l = l.slice(0, open);
        inBlock = true;
      } else {
        l = l.slice(0, open) + l.slice(close + 2);
      }
    }
    const lineComment = l.indexOf("//");
    if (lineComment !== -1) l = l.slice(0, lineComment);
    // JSX 里 `{/* … */}` 已由块注释规则处理；剩余含中文即认定为待迁移文案。
    if (CJK.test(l)) hits.push({ line: i + 1, text: line.trim().slice(0, 100) });
  });
  if (hits.length) {
    totalCjkLines += hits.length;
    rows.push({ file: path.relative(FRONTEND, file), hits: hits.length, samples: hits.slice(0, 3) });
  }
}

rows.sort((a, b) => b.hits - a.hits);
const ratio = totalLines ? ((totalCjkLines / totalLines) * 100).toFixed(1) : "0.0";

if (asJson) {
  console.log(JSON.stringify({ totalLines, totalCjkLines, ratioPercent: ratio, files: rows }, null, 2));
} else {
  console.log("[scan-i18n] 硬编码中文（注释除外）—— 仅报告，不阻断");
  console.log(`  扫描文件: ${files.length}  总行数: ${totalLines}  含中文行: ${totalCjkLines} (${ratio}%)`);
  console.log("");
  for (const r of rows) {
    console.log(`  ${String(r.hits).padStart(4)}  ${r.file}`);
    for (const s of r.samples) console.log(`        L${s.line}: ${s.text}`);
  }
  console.log("");
  console.log("  目标：按模块逐步迁移到 t(\"…\")，让上面的比率单调下降。");
}

// 恒为 0：本脚本是报告工具，不是门禁。
process.exit(0);
