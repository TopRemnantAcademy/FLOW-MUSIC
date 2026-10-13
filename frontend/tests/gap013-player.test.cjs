#!/usr/bin/env node
/**
 * GAP-013 播放器升级 契约测试（静态读取源码，不启动服务、不需要 node_modules）。
 *
 * 覆盖三件事，都是「跨文件约定」—— 任何一端单方面改动都会静默失配：
 *   1. 波形峰值：前端只能从**后端**拿（`/api/share/songs/{token}/peaks`），
 *      绝不能前端 `fetch(audio_url)`（会被页面 CSP `connect-src` 拦；见 Waveform.tsx 头注释）。
 *   2. AB 重复：`playback.ts` 的状态机谓词 + `audioBus.ts` 的回绕钩子必须同时存在，
 *      且 BottomPlayer 的按钮真的接到这些函数上。
 *   3. 分享链接：前端只能走 `/api/share/songs/{token}`，且分享页要区分 403/404。
 *
 * 运行：node frontend/tests/gap013-player.test.cjs
 */
const fs = require("fs");
const path = require("path");

const ROOT = path.resolve(__dirname, "..", "..");
const F = {
  waveform: path.join(ROOT, "frontend/components/Waveform.tsx"),
  bottom: path.join(ROOT, "frontend/components/BottomPlayer.tsx"),
  player: path.join(ROOT, "frontend/components/Player.tsx"),
  playback: path.join(ROOT, "frontend/lib/playback.ts"),
  audioBus: path.join(ROOT, "frontend/lib/audioBus.ts"),
  api: path.join(ROOT, "frontend/lib/api.ts"),
  sharePage: path.join(ROOT, "frontend/app/share/[token]/page.tsx"),
  sharePy: path.join(ROOT, "backend/app/routers/share.py"),
  mainPy: path.join(ROOT, "backend/app/main.py"),
  configPy: path.join(ROOT, "backend/app/config.py"),
  secHeaders: path.join(ROOT, "frontend/security-headers.js"),
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
    failures.push(`${name}${detail ? ` — ${detail}` : ""}`);
    console.log(`  \u2717 ${name}`);
  }
}

const read = (p) => fs.readFileSync(p, "utf8");
const src = {};
for (const [k, p] of Object.entries(F)) src[k] = read(p);

console.log("── 1. 波形：后端给峰值，前端不自取音频 ──");
check(
  "share.py 提供 /peaks 端点",
  /@router\.get\("\/songs\/\{token\}\/peaks"\)/.test(src.sharePy)
);
check("share.py 用 ffmpeg 抽峰值", /ffmpeg/.test(src.sharePy) && /_extract_peaks/.test(src.sharePy));
check("share.py 抽峰值前过同一道内容闸", /def share_song_peaks[\s\S]{0,900}_share_visible_song\(/.test(src.sharePy));
check("share.py 抽峰值前验 token", /def share_song_peaks[\s\S]{0,1200}verify_share_token\(/.test(src.sharePy));
check("peaks 失败 fail-closed（404，不给假波形）", /波形不可用/.test(src.sharePy) && /status_code=404/.test(src.sharePy));
check("api.ts 有 sharePeaksPath", /sharePeaksPath:/.test(src.api));
check("Waveform 从 peaksUrl 拉（同源）", /fetch\(`\$\{API_BASE\}\$\{peaksUrl\}`/.test(src.waveform));
check(
  "Waveform **不**直接 fetch 音频 src（CSP 会拦）",
  !/fetch\(\s*src\b/.test(src.waveform) && !/fetch\(src,/.test(src.waveform),
  "发现 fetch(src) —— 跨源音频会被 connect-src 拦"
);
check("Waveform 不再 import wavesurfer（改为自绘）", !/from "wavesurfer\.js"/.test(src.waveform) && !/import\("wavesurfer\.js"\)/.test(src.waveform));
check("CSP 确实限制 connect-src（解释了为何不能前端取音频）", /connect-src/.test(src.secHeaders));
check("CSP 放行 media-src（所以 <audio> 能播）", /media-src/.test(src.secHeaders));
check("分享页把 peaksUrl 传给 Waveform", /peaksUrl=\{api\.sharePeaksPath\(token\)\}/.test(src.sharePage));

console.log("── 2. 波形 seek：比例→秒数自算（不是 ws.getDuration） ──");
check("Waveform 用 pointerdown 起 seek", /onPointerDown/.test(src.waveform));
check("Waveform 用 pointermove 支持拖动 seek", /onPointerMove/.test(src.waveform));
check("seek 走 busSeek（复用全局音频总线）", /busSeek\(/.test(src.waveform));
check("比例换算用自身 duration 的 ref（防闭包捕获 0）", /durationRef/.test(src.waveform));
check("换算为 relativeX * duration", /ratio \* d|ratio \* duration/.test(src.waveform) || /busSeek\(ratio \* d\)/.test(src.waveform));

console.log("── 3. AB 重复：状态机 + 回绕钩子 + UI 接线 ──");
check("playback.ts 定义 AbLoop", /export interface AbLoop/.test(src.playback));
check("playback.ts 有 AB_MIN_SPAN 常量", /export const AB_MIN_SPAN/.test(src.playback));
check("playback.ts setAbA", /export function setAbA/.test(src.playback));
check("playback.ts setAbB（返回 bool 供 UI 提示）", /export function setAbB\(seconds: number\): boolean/.test(src.playback));
check("playback.ts clearAb", /export function clearAb/.test(src.playback));
check("playback.ts activeAbLoop（B 未设/区间过短都不生效）", /export function activeAbLoop/.test(src.playback));
check("AB 非法组合**丢弃**而不是静默交换", /b <= ab\.a \+ AB_MIN_SPAN\) return false/.test(src.playback));
check("AB 区间持久化（刷新不丢）", /AB_KEY/.test(src.playback) && /localStorage\.setItem\(AB_KEY/.test(src.playback));
check("AB 持久化有形状校验（坏数据丢弃）", /isValidAb/.test(src.playback));
check("audioBus 有 busSetAbLoop（回绕注册）", /export function busSetAbLoop/.test(src.audioBus));
check("audioBus 在 timeupdate 里做回绕", /maybeWrapAb\(a\)/.test(src.audioBus));
check("回绕判定为 currentTime >= b", /a\.currentTime < abLoop\.b/.test(src.audioBus));
check("回绕跳回 a", /a\.currentTime = abLoop\.a/.test(src.audioBus));
check("BottomPlayer 有 A 按钮接线 setAbA", /setAbA\(t\)/.test(src.bottom));
check("BottomPlayer 有 B 按钮接线 setAbB", /setAbB\(t\)/.test(src.bottom));
check("BottomPlayer 有清除按钮接线 clearAb", /clearAb\(\)/.test(src.bottom));
check("BottomPlayer 把区间推给 audioBus", /busSetAbLoop\(loop\)/.test(src.bottom));
check("BottomPlayer 同步 AB 区间的 effect 依赖 a/b", /\[loop\?\.a, loop\?\.b\]/.test(src.bottom));
check("BottomPlayer 编辑 AB 后清理订阅", /return \(\) => busSetAbLoop\(null\)/.test(src.bottom));

console.log("── 4. 分享链接：无状态 HMAC + 前端只走 /api/share ──");
check("share.py 用 HMAC-SHA256", /hmac\.new\(/.test(src.sharePy) && /hashlib\.sha256/.test(src.sharePy));
check("share.py 恒定时间比较签名", /hmac\.compare_digest/.test(src.sharePy));
check("share.py 有 token 版本前缀（可平滑演进）", /_TOKEN_V1/.test(src.sharePy));
check("share.py 校验过期 exp", /exp <= ts/.test(src.sharePy));
check("share.py 密钥只从 env 读（config）", /share_secret/.test(src.configPy));
check("share.py 渲染用 share_secret 或回落 jwt_secret", /settings\.share_secret or ""\)\.strip\(\) or settings\.jwt_secret/.test(src.sharePy));
check("share.py **无新表/无迁移**", !/Column\(|Base\.metadata|__tablename__/.test(src.sharePy));
check("share.py 内容闸要求 is_public", /if not song\.is_public or song\.status != "completed"/.test(src.sharePy));
check("share.py 内容闸拦 rejected", /moderation_status == "rejected"/.test(src.sharePy));
check("share.py 内容闸拦软删", /deleted_at is not None/.test(src.sharePy));
check("share.py 内容闸在开审核时要求 approved", /enable_moderation and song\.moderation_status != "approved"/.test(src.sharePy));
check("share.py 未公开/未过审返回 404（不泄露存在性）", /作品不存在或已下架/.test(src.sharePy));
check("share.py 签发要求登录（get_current_user）", /def create_share_link[\s\S]{0,400}Depends\(get_current_user\)/.test(src.sharePy));
check("share.py 解析允许匿名（get_optional_user）", /def resolve_share_link[\s\S]{0,400}Depends\(get_optional_user\)/.test(src.sharePy));
check("分享不绕开试听门（can_play_full 决定整曲/片段）", /can_play_full\(user\)/.test(src.sharePy));
check("片段缺失 fail-closed（不给整曲）", /preview_audio_url or None/.test(src.sharePy));
// 只看**代码**里有没有下发 cost_cny —— 注释里提到它是为了说明「刻意不下发」。
{
  const codeOnly = src.sharePy
    .split("\n")
    .filter((l) => !/^\s*#/.test(l) && !/^\s*\*/.test(l))
    .join("\n")
    .replace(/"""[\s\S]*?"""/g, ""); // 去掉 docstring
  check("分享视图不下发 cost_cny（匿名面收口）", !/cost_cny/.test(codeOnly));
  check("分享视图不下发 is_owner", !/is_owner/.test(codeOnly));
}
check("share router 注册到 main.py", /include_router\(share\.router\)/.test(src.mainPy));
check("api.ts createShareLink", /createShareLink:/.test(src.api));
check("api.ts resolveShareLink", /resolveShareLink:/.test(src.api));
check("分享页区分 403（链接坏）与 404（作品没了）", /status === 403/.test(src.sharePage) && /status === 404/.test(src.sharePage));
check("Player 分享入口只在已公开时可用", /const canShare = completed && !!local\.is_public/.test(src.player));
check("Player 分享失败按状态码给提示", /status === 400/.test(src.player));

console.log("── 5. AB 纯逻辑（从源码提取常量后验证谓词） ──");
// 从 playback.ts 源码里抠出 AB_MIN_SPAN 的真实值，避免测试与实现各写一份而漂移。
const spanMatch = src.playback.match(/export const AB_MIN_SPAN = ([\d.]+);/);
check("能读到 AB_MIN_SPAN 的真实值", !!spanMatch);
const MIN = spanMatch ? Number(spanMatch[1]) : 0.5;

// 用源码里的**真实判定形式**重演：b <= a + MIN → 丢弃
const setAbB_ok = (ab, seconds) => {
  if (!ab) return false;
  const b = Math.max(0, seconds);
  if (b <= ab.a + MIN) return false;
  return true;
};
check(`MIN=${MIN}: B=A+MIN 拒绝（严格大于）`, setAbB_ok({ a: 10, b: null }, 10 + MIN) === false);
check(`MIN=${MIN}: B=A+MIN+0.1 接受`, setAbB_ok({ a: 10, b: null }, 10 + MIN + 0.1) === true);
check("无 A 时打 B 无效", setAbB_ok(null, 99) === false);
check("B 在 A 之前不被静默交换", setAbB_ok({ a: 10, b: null }, 5) === false);

// activeAbLoop 的真实判定：b <= a + MIN → null
const loop_ok = (ab) => {
  if (!ab || ab.b == null) return null;
  if (ab.b <= ab.a + MIN) return null;
  return { a: ab.a, b: ab.b };
};
check("A 点单独存在不循环", loop_ok({ a: 5, b: null }) === null);
check("区间过短不循环", loop_ok({ a: 5, b: 5 + MIN }) === null);
check("合法区间可循环", JSON.stringify(loop_ok({ a: 5, b: 5 + MIN + 0.1 })) === `{"a":5,"b":${5 + MIN + 0.1}}`);

// 回绕判定与 audioBus.ts 一致：ct >= b → 跳 a
const wrap = (loop, ct) => (loop && ct >= loop.b ? loop.a : ct);
check("未到 b 不回绕", wrap({ a: 2, b: 5 }, 4.99) === 4.99);
check("到达 b 回绕到 a", wrap({ a: 2, b: 5 }, 5) === 2);
check("超过 b 也回绕到 a", wrap({ a: 2, b: 5 }, 8) === 2);
check("未启用 AB 不干预", wrap(null, 9) === 9);

console.log(`\n${"=".repeat(56)}`);
console.log(`PASS=${pass}  FAIL=${fail}`);
if (fail > 0) {
  console.log("失败项：");
  for (const f of failures) console.log(`  - ${f}`);
  process.exit(1);
}
console.log("全部 GAP-013 契约断言通过。");
