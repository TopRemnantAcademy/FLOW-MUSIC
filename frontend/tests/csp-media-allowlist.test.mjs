#!/usr/bin/env node
/**
 * CSP img-src / media-src 白名单回归（#153）。
 *
 * 钉死三件事，任一被改回去都要当场变红：
 *   1. **不许再出现通配 `https:`** —— 它是「任意 https 域可播音频/图片」，
 *      与 connect-src 辛苦收窄的白名单直接矛盾（一个指令放行 = 另一个等于没写）。
 *   2. data: / blob: **必须留着** —— 预览片段与下载走 `URL.createObjectURL`
 *      （lib/api.ts `previewObjectUrl` / `downloadSong`），删了就是「能播的播不了」。
 *   3. Web(headers()) 与 APK(<meta http-equiv>) 两条下发路径**同一份字符串**，
 *      仅差 meta 明确不支持的两条指令（frame-ancestors）。从前是「一处加强、另一处没跟」。
 *
 * 顺带钉住「两个域清单不是一回事」：CORS 白名单是**请求来源**，媒体白名单是
 * **资源来源**，二者只应经过各自的实际消费点，不该互相抄。
 *
 * 运行：node tests/csp-media-allowlist.test.mjs
 */
import { createRequire } from "node:module";
import fs from "node:fs";
import path from "node:path";

const FE = path.resolve(import.meta.dirname, "..");
const require = createRequire(import.meta.url);
const { contentSecurityPolicy, mediaHosts, META_UNSUPPORTED } = require("../security-headers.js");

const read = (p) => fs.readFileSync(path.join(FE, p), "utf8");

let pass = 0, fail = 0;
const ok = (n, c, detail) => {
  if (c) { pass++; console.log(`  ✓ ${n}`); }
  else { fail++; console.log(`  ✗ ${n}${detail ? ` — ${detail}` : ""}`); }
};

/** 取出某条指令的 token 列表（`img-src a b` → ["a","b"]）。 */
const directive = (policy, name) => {
  const part = policy.split(";").map((s) => s.trim()).find((s) => s.startsWith(name + " "));
  return part ? part.slice(name.length + 1).trim().split(/\s+/) : null;
};

const API = "https://music.anistar.studio";
const policy = contentSecurityPolicy(API);
const img = directive(policy, "img-src");
const media = directive(policy, "media-src");

console.log("① 通配 https: 必须消失（本条是 #153 的全部意义）");
for (const [name, tokens] of [["img-src", img], ["media-src", media]]) {
  ok(`${name} 不含裸 https:`, tokens && !tokens.includes("https:"), JSON.stringify(tokens));
  ok(`${name} 不含 *`, tokens && !tokens.includes("*"));
  // 前缀通配（https://*.example.com）同理放行一整片域，这里也不接受。
  ok(`${name} 无域名通配`, tokens && !tokens.some((t) => t.includes("*")));
}

console.log("② 必须保留的：'self' / data: / blob:");
ok("img-src 含 'self'", img?.includes("'self'"));
ok("img-src 含 data:（内联图标/首屏占位）", img?.includes("data:"));
ok("img-src 含 blob:（本地预览 URL）", img?.includes("blob:"));
ok("media-src 含 'self'", media?.includes("'self'"));
ok("media-src 含 blob:（previewObjectUrl / downloadSong）", media?.includes("blob:"));
ok(
  "media-src 不给 data:（音频没有 data: 用例，能少一条是一条）",
  !media?.includes("data:")
);

console.log("③ 本项目实际用到的媒体域（可实证，非臆造）");
// apiBase 同时是 connect-src 与媒体域：storage 未配时 audio_url 与 API 同源。
ok("img-src 含 apiBase", img?.includes(API));
ok("media-src 含 apiBase", media?.includes(API));
// 主引擎 Mureka 的媒体 CDN（PRO_MUSIC_PLATFORM_GAP_PLAN.md:325 明写 cdn.mureka.ai）。
ok("media-src 含 cdn.mureka.ai（Mureka 上游音频）", media?.includes("https://cdn.mureka.ai"));
ok("img-src 含 cdn.mureka.ai（Mureka 封面同域）", img?.includes("https://cdn.mureka.ai"));
// 自研 CDN 只在显式传入时出现（避免把「没有的域」写进生产头）。
ok("未配 cdnBase 时不出现虚构 CDN 域", !policy.includes("cdn.example.com"));
ok(
  "显式传入 cdnBase 时才进白名单",
  contentSecurityPolicy(API, [], "https://cdn.mysite.example").includes("https://cdn.mysite.example")
);

console.log("④ 与 api.ts / CORS 的关系：两个清单**不该互相抄**");
const apiTs = read("lib/api.ts");
ok("api.ts 的 GOSPEL_API_BASE 兜底仍是 anistar.studio", /anistar\.studio/.test(apiTs));
// 主站是 fetch 目标（connect-src 的事），不该因为它出现在 api.ts 就塞进 media-src。
ok(
  "media-src 不含主站 anistar.studio 根域（那是 connect-src 的职责）",
  !media?.includes("https://anistar.studio")
);
// CORS 白名单在后端 config.py：那是**请求来源**，与资源来源是两套，只做存在性核对。
const cfg = read("../backend/app/config.py");
ok("后端 CORS 白名单仍在（未因本改动被动到）", /cors_origins/.test(cfg));

console.log("⑤ 两条下发路径同一份字符串");
const meta = require("../security-headers.js").metaEquivalent(API)[0];
ok("meta 只有一条 CSP", require("../security-headers.js").metaEquivalent(API).length === 1);
// headers 侧去掉 meta 不支持的指令后，必须与 meta 逐字相同 —— 两条路径共享同一份
// 生成逻辑，差异只允许来自这处显式的过滤，不允许「某天只加强了一边」。
const expectMeta = policy
  .split(";")
  .map((s) => s.trim())
  .filter((s) => s && !META_UNSUPPORTED.some((d) => s.startsWith(d + " ")))
  .join("; ");
ok("meta == headers 去掉 meta 不支持指令后的结果", meta.content === expectMeta,
  `meta="${meta.content}"\nexpected="${expectMeta}"`);
ok("meta 不含 frame-ancestors（浏览器会忽略）", !meta.content.includes("frame-ancestors"));

console.log("⑥ 接线：next.config.js / build-apk.mjs 都传 cdnBase");
ok("next.config.js 读 NEXT_PUBLIC_STORAGE_PUBLIC_BASE", /NEXT_PUBLIC_STORAGE_PUBLIC_BASE/.test(read("next.config.js")));
ok("build-apk.mjs 读 NEXT_PUBLIC_STORAGE_PUBLIC_BASE", /NEXT_PUBLIC_STORAGE_PUBLIC_BASE/.test(read("scripts/build-apk.mjs")));
ok("build-apk.mjs 把 cdnBase 传给 metaEquivalent", /metaEquivalent\(apiBase,[\s\S]*?cdnBase\)/.test(read("scripts/build-apk.mjs")));

console.log("⑦ 源码里不再留旧写法（防止改回来）");
ok("security-headers.js 无 \"img-src 'self' data: blob: https:\"", !/img-src 'self' data: blob: https:"/.test(read("security-headers.js")));
ok("security-headers.js 无 \"media-src 'self' blob: https:\"", !/media-src 'self' blob: https:"/.test(read("security-headers.js")));

console.log(`\nPASS=${pass} FAIL=${fail}`);
process.exit(fail ? 1 : 0);
