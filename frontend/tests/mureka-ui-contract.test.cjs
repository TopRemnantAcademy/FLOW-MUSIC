#!/usr/bin/env node
/**
 * Mureka UI ↔ 后端 契约漂移 mock 测试（静态读取两端源码，不启动服务、不需要 node_modules）。
 *
 * 目的：阶段 2 前端 UI（创作模式 / 风格枚举 / 音色克隆 / 分轨 / 局部改写）与后端
 *       (routers/songs.py 路由 + orchestrator/mureka.py 枚举) 之间存在多处「跨边界约定」，
 *       任何一端单方面改动都会静默漂移（前端给了后端不认的风格 → 400；后端删了路由 →
 *       前端 404）。本测试把这些约定固化为可执行断言。
 *
 * 运行：node frontend/tests/mureka-ui-contract.test.cjs
 */
const fs = require("fs");
const path = require("path");

const ROOT = path.resolve(__dirname, "..", ".."); // ai-music-platform/
const F = {
  mureka: path.join(ROOT, "backend/app/orchestrator/mureka.py"),
  songs: path.join(ROOT, "backend/app/routers/songs.py"),
  api: path.join(ROOT, "frontend/lib/api.ts"),
  create: path.join(ROOT, "frontend/components/CreateForm.tsx"),
};

let pass = 0;
let fail = 0;
const failures = [];

function check(name, cond, detail) {
  if (cond) {
    pass++;
    console.log(`  \u2713 ${name}`);
  } else {
    fail++;
    failures.push(name);
    console.log(`  \u2717 ${name}${detail ? "  :: " + detail : ""}`);
  }
}

function section(title) {
  console.log(`\n${title}`);
}

function read(p) {
  return fs.readFileSync(p, "utf8");
}

const mureka = read(F.mureka);
const songs = read(F.songs);
const apiTs = read(F.api);
const createTsx = read(F.create);

// ── 1. EASY_STYLES 枚举：前端 ⊂ 后端 且 后端 ⊆ 前端（完全一致）──────────────
section("① 风格枚举契约（灵感创作 styles）");
const mEasy = mureka.match(/EASY_STYLES[^=]*=\s*frozenset\(\s*\{([\s\S]*?)\}\s*\)/);
check("后端 mureka.py 可解析 EASY_STYLES", !!mEasy);
const backendStyles = new Set(mEasy ? [...mEasy[1].matchAll(/"([^"]+)"/g)].map((m) => m[1]) : []);

const cEasy = createTsx.match(/const EASY_STYLES[\s\S]*?=\s*\[([\s\S]*?)\];/);
check("前端 CreateForm.tsx 可解析 EASY_STYLES", !!cEasy);
const frontendStyles = new Set(
  cEasy ? [...cEasy[1].matchAll(/en:\s*"([^"]+)"/g)].map((m) => m[1]) : []
);

check("后端 EASY_STYLES 非空", backendStyles.size > 0, `size=${backendStyles.size}`);
check("前端 EASY_STYLES 非空", frontendStyles.size > 0, `size=${frontendStyles.size}`);
const onlyBackend = [...backendStyles].filter((s) => !frontendStyles.has(s));
const onlyFrontend = [...frontendStyles].filter((s) => !backendStyles.has(s));
check("前端未遗漏后端任何风格（否则用户点不到）", onlyBackend.length === 0, `缺: ${onlyBackend.join(",")}`);
check("前端未多出后端不认的风格（否则必然 400）", onlyFrontend.length === 0, `多: ${onlyFrontend.join(",")}`);
check("品牌关键风格 gospel 已内置", frontendStyles.has("gospel") && backendStyles.has("gospel"));

// ── 2. api.ts 包装方法与路径（前端网络层）─────────────────────────────────
section("② 前端 api.ts 包装方法");
const wrappers = [
  ["instrumental", "/api/songs/instrumental"],
  ["easyGenerate", "/api/songs/easy-generate"],
  ["regionEdit", "/api/songs/${id}/region-edit"],
  ["stem", "/api/songs/${id}/stem"],
  ["vocalClone", "/api/songs/vocal-clone"],
];
for (const [name, p] of wrappers) {
  check(`api.${name} 已包装`, new RegExp(`\\b${name}:`).test(apiTs));
  check(`api.${name} 路径 ${p}`, apiTs.includes(p));
}
check("vocalClone 使用 FormData 且不手写 Content-Type（boundary 由浏览器补）",
  apiTs.includes('fd.append("file"') && /body:\s*fd/.test(apiTs));
check("stem 返回类型含 expires_at_iso（后端已下发，前端可展示失效时间）",
  apiTs.includes("expires_at_iso"));

// ── 3. 后端路由存在性（songs.py）──────────────────────────────────────────
section("③ 后端路由存在性");
const routes = [
  ["POST /instrumental", '@router.post("/instrumental"'],
  ["POST /easy-generate", '@router.post("/easy-generate"'],
  ["POST /{song_id}/region-edit", '@router.post("/{song_id}/region-edit"'],
  ["POST /{song_id}/stem", '@router.post("/{song_id}/stem"'],
  ["POST /vocal-clone", '@router.post("/vocal-clone"'],
];
for (const [label, needle] of routes) {
  check(`songs.py 有路由 ${label}`, songs.includes(needle));
}

// ── 4. 前端三模式分发到正确端点 ───────────────────────────────────────────
section("④ 前端创作模式 → 端点分发");
check("模式枚举含 normal/instrumental/easy",
  /"normal"\s*\|\s*"instrumental"\s*\|\s*"easy"/.test(createTsx));
check("普通创作调 api.generate", /api\.generate\(/.test(createTsx));
check("纯器乐调 api.instrumental", /api\.instrumental\(/.test(createTsx));
check("灵感创作调 api.easyGenerate", /api\.easyGenerate\(/.test(createTsx));
check("音色克隆调 api.vocalClone", /api\.vocalClone\(/.test(createTsx));
check("普通创作把 vocal_id 一并提交", /vocal_id:\s*vocalId/.test(createTsx));

// ── 5. 模型约束（易踩 400 的端点差异）──────────────────────────────────────
section("⑤ 端点模型约束");
const mInst = mureka.match(/_INSTRUMENTAL_MODELS[^=]*=\s*frozenset\(\s*\{([\s\S]*?)\}\s*\)/);
const instSet = new Set(mInst ? [...mInst[1].matchAll(/"([^"]+)"/g)].map((m) => m[1]) : []);
check("instrumental 模型集排除 mureka-o2（否则 400）", instSet.size > 0 && !instSet.has("mureka-o2"),
  [...instSet].join(","));
const mExt = mureka.match(/_EXTEND_MODELS[^=]*=\s*frozenset\(\s*\{([\s\S]*?)\}\s*\)/);
const extSet = new Set(mExt ? [...mExt[1].matchAll(/"([^"]+)"/g)].map((m) => m[1]) : []);
check("extend 模型集仅 mureka-7.6 / mureka-8",
  extSet.size === 2 && extSet.has("mureka-7.6") && extSet.has("mureka-8"), [...extSet].join(","));

// ── 6. region-edit 区间守卫（前端 3 秒 == 后端 3000ms）────────────────────
section("⑥ region-edit 区间守卫一致性");
check("后端守卫 3000ms", songs.includes("< 3000"));
check("后端守卫 edit_end <= edit_start", songs.includes("结束时间须大于开始时间"));
check("前端以「秒」输入并按 ×1000 提交，且本地守卫 3 秒",
  /start \* 1000/.test(read(path.join(ROOT, "frontend/app/library/page.tsx"))) &&
  /end - start < 3/.test(read(path.join(ROOT, "frontend/app/library/page.tsx"))));

// ── 7. 收藏 / 管理后台 / 上传 契约（2026-09-12 新增 UI 的防漂移网）───────────
section("⑦ 收藏 / 管理 / 上传 契约");
const plazaPy = read(path.join(ROOT, "backend/app/routers/plaza.py"));
const adminPy = read(path.join(ROOT, "backend/app/routers/admin.py"));
const plazaTsx = read(path.join(ROOT, "frontend/app/plaza/page.tsx"));
const authTsx = read(path.join(ROOT, "frontend/lib/auth.tsx"));

check("api.myFavorites 已包装", /\bmyFavorites:/.test(apiTs));
check("api.myFavorites 路径 /api/plaza/favorites", apiTs.includes("/api/plaza/favorites"));
check("api.favoriteSong 路径 /api/plaza/songs/${id}/favorite",
  apiTs.includes("/api/plaza/songs/${id}/favorite"));
check("后端 plaza.py 有 GET /favorites 路由", plazaPy.includes('@router.get("/favorites"'));
check("plaza 页接线 api.myFavorites（收藏视图数据源）", /api\.myFavorites\(/.test(plazaTsx));

check("api.adminBilling 路径 /api/admin/billing", apiTs.includes("/api/admin/billing"));
check("api.adminAudit 路径 /api/admin/audit", apiTs.includes("/api/admin/audit"));
check("后端 admin.py 有 GET /billing 路由", adminPy.includes('@router.get("/billing"'));
check("后端 admin.py 有 GET /audit 路由", adminPy.includes('@router.get("/audit"'));

check("api.adminUserLinks 已包装", /\badminUserLinks:/.test(apiTs));
check("api.adminUserLinks 路径 /api/admin/user-links", apiTs.includes("/api/admin/user-links"));
check("后端 admin.py 有 GET /user-links 路由", adminPy.includes('@router.get("/user-links"'));
check("user-links 页接线 api.adminUserLinks", /api\.adminUserLinks\(/.test(
  read(path.join(ROOT, "frontend/app/admin/user-links/page.tsx"))));

check("uploadSong 返回类型含 custom_id（对齐 schemas.py UploadResp，误用 song_id 会在运行时拿空）",
  /uploadSong[\s\S]{0,220}custom_id/.test(apiTs));
check("后端 UploadResp 是 custom_id 单字段", /class UploadResp[\s\S]{0,80}custom_id/.test(
  read(path.join(ROOT, "backend/app/schemas.py"))));

check("auth.tsx login/logout 广播 auth:changed（收藏视图登录自动恢复依赖）",
  /auth:changed/.test(authTsx) && (authTsx.match(/auth:changed/g) || []).length >= 2);

// ── 汇总 ─────────────────────────────────────────────────────────────────
console.log(`\n${"=".repeat(56)}`);
console.log(`PASS=${pass}  FAIL=${fail}`);
if (fail > 0) {
  console.log("失败项：");
  for (const f of failures) console.log(`  - ${f}`);
  process.exit(1);
}
console.log("全部契约断言通过。");
