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

// ── 6b. extend_type 四层存在性（2026-10-11 缺陷修复的防漂移网）────────────
// 本次缺陷的本质就是「四层里缺三层」：GenerateRequest 有 → Song 无列、SpawnReq 无字段、
// 前端无从传入 → 该值在 spawn→持久化→重建 链路中被静默丢弃（一律按 tail）。
// 单看任何一层都「正常」，所以这里把四层一起断言 —— 缺任一层，回归立即失败。
section("⑥b extend_type 四层存在性（模型/schema/路由/前端）");
const playerTsx = read(path.join(ROOT, "frontend/components/Player.tsx"));
const modelsPy = read(path.join(ROOT, "backend/app/models.py"));
const schemasPy = read(path.join(ROOT, "backend/app/schemas.py"));
const spawnTestPy = read(path.join(ROOT, "backend/tests/test_spawn_router.py"));
const migrationDir = path.join(ROOT, "backend/migrations/versions");

// 层① 模型：Song 列
check("模型层：Song 有 extend_type 列（否则后台从 DB 重建时必然丢失）",
  /extend_type\s*=\s*Column\(/.test(modelsPy));
// 层② schema：SpawnReq + GenerateReq + SongOut（缺 SpawnReq 则前端传了被 pydantic 拒）
check("schema 层：GenerateReq 有 extend_type（provider 请求契约）",
  /class GenerateReq[\s\S]*?extend_type:\s*Optional\[str\]/.test(schemasPy));
check("schema 层：SpawnReq 有 extend_type（缺则前端传了必然被拒）",
  /class SpawnReq[\s\S]*?extend_type:\s*Optional\[str\]/.test(schemasPy));
check("schema 层：SongOut 有 extend_type（前端需回显）",
  /class SongOut[\s\S]*?extend_type:\s*Optional\[str\]/.test(schemasPy));
// 层③ 路由：spawn→持久化→重建 三处 + 白名单 fail-closed
check("路由层：_create_song 持久化 extend_type",
  /extend_type=_normalize_extend_type\(req\.extend_type\)/.test(songs));
check("路由层：_process_song 重建请求时带回 song.extend_type（根因修复点）",
  /extend_type=song\.extend_type/.test(songs));
check("路由层：extend 路由透传 body.extend_type（仅 extend 生效）",
  /extend_type\s*=\s*_normalize_extend_type\(body\.extend_type\)\s*if task == "extend"/.test(songs));
check("路由层：非法值 400 fail-closed（不静默改写为 tail）",
  /_EXTEND_TYPES\s*=\s*frozenset\(\{"tail",\s*"head"\}\)/.test(songs) &&
  /status_code=400/.test(songs));
// 层④ 前端：api 类型 + 选择器
check("前端：api.ts 有 SpawnParams 且含 extend_type",
  /export type SpawnParams[\s\S]*?extend_type\?:\s*"tail"\s*\|\s*"head"/.test(apiTs));
check("前端：extendSong 使用 SpawnParams（不再是任意 Record）",
  /extendSong:\s*\(id: number, body: SpawnParams/.test(apiTs));
check("前端：Player.tsx 有「从尾部衔接 / 从头部衔接」选择器",
  playerTsx.includes("从尾部衔接") && playerTsx.includes("从头部衔接"));
check("前端：extend 请求真的带上 extend_type（而不是仍然发 {}）",
  /task === "extend"\s*\?\s*\{\s*extend_type:\s*extendType\s*\}/.test(playerTsx));
// 迁移存在性
check("迁移：存在 songs.extend_type 的 alembic 迁移",
  fs.readdirSync(migrationDir).some((f) => {
    if (!f.endsWith(".py")) return false;
    const body = read(path.join(migrationDir, f));
    return /add_column\(\s*"songs"[\s\S]{0,120}extend_type/.test(body);
  }));
// 回归用例存在性
check("测试：test_spawn_router.py 存在且覆盖三处链路",
  /test_extend_type_reaches_provider_after_db_rebuild/.test(spawnTestPy) &&
  /test_extend_type_illegal_value_400_fail_closed/.test(spawnTestPy));
check("测试：机审父歌回退绕过路径已锁死（_moderate_spawn_text）",
  songs.includes("_moderate_spawn_text") &&
  /test_moderation_blocks_unmoderated_parent_via_cover/.test(spawnTestPy));

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

// ── GAP-010 音频格式（下载侧可选）契约 ─────────────────────────────────────
section("GAP-010 音频格式 / 下载格式契约");

// 后端：三列存在，且不得出现臆造的 quality/bitrate 列（Mureka 生成入参无码率控制）
check("models.py Song 有 audio_format 列", /audio_format = Column/.test(modelsPy));
check("models.py Song 有 audio_wav_url 列", /audio_wav_url = Column/.test(modelsPy));
check("models.py Song 有 audio_flac_url 列", /audio_flac_url = Column/.test(modelsPy));
check("models.py 无臆造质量列（quality/bitrate/kbps）",
  !/Column\([^)]*(quality|bitrate|kbps)/i.test(modelsPy));

// 后端：下载端点支持 ?format= 且白名单 = mp3|wav|flac
check("songs.py 下载端点接受 format 参数", /def download_song\([\s\S]{0,120}format/.test(songs));
check("songs.py 格式白名单含 mp3/wav/flac（且不含 stem）",
  /_AUDIO_FORMATS[\s\S]{0,300}"mp3"[\s\S]{0,300}"wav"[\s\S]{0,300}"flac"/.test(songs));
check("songs.py 无损缺失返回 409（不静默降级）", /status_code=409/.test(songs));
check("songs.py 生成请求体不夹带 format（上游不支持）",
  !/payload\["(format|quality|bitrate)"\]/.test(mureka));

// 前端：api 与 UI 接线
check("api.ts downloadSong 接受 format 参数", /downloadSong: async \(id: number, format\?/.test(apiTs));
check("api.ts downloadSong 拼接 ?format=", apiTs.includes("/download${qs}"));
check("SongView 含 audio_format", /audio_format\?: string/.test(apiTs));
check("SongView 含 available_formats", /available_formats\?: string\[\]/.test(apiTs));
check("CreateForm 有下载格式控件（AUDIO_FORMATS）", /AUDIO_FORMATS/.test(createTsx));
check("CreateForm 无损受权益约束（canDownload 禁用）", /locked = f\.lossless && !canDownload/.test(createTsx));
check("CreateForm 提交携带 audio_format", /audio_format: audioFormat/.test(createTsx));
check("Player 下载按钮按格式调用 api.downloadSong(id, format)",
  /api\.downloadSong\(local\.id, downloadFormat\)/.test(playerTsx));

// ── 汇总 ─────────────────────────────────────────────────────────────────
console.log(`\n${"=".repeat(56)}`);
console.log(`PASS=${pass}  FAIL=${fail}`);
if (fail > 0) {
  console.log("失败项：");
  for (const f of failures) console.log(`  - ${f}`);
  process.exit(1);
}
console.log("全部契约断言通过。");
