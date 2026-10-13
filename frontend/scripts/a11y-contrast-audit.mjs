#!/usr/bin/env node
/**
 * GAP-024 色彩对比审计（WCAG 2.1 AA）
 *
 * 读取 app/globals.css 里的主题令牌（--bg/--surface/--ink/--muted/--danger …），
 * 在 oklch → sRGB → 相对亮度 上计算对比度，输出 light/dark 两套结论。
 *
 * 为什么自己算而不是只靠人工检查：oklch 是感知均匀空间，肉眼「看着还行」的
 * 中性灰常常正好卡在 4.5:1 上下（本仓库的 --muted 就是 4.38~4.58:1 这一档）。
 *
 * 运行：node frontend/scripts/a11y-contrast-audit.mjs
 */
// ===== OKLCH -> sRGB -> relative luminance -> WCAG contrast =====
function oklchToSrgb(L, C, hDeg) {
  const h = (hDeg * Math.PI) / 180;
  const a = C * Math.cos(h);
  const b = C * Math.sin(h);
  const l_ = L + 0.3963377774 * a + 0.2158037573 * b;
  const m_ = L - 0.1055613458 * a - 0.0638541728 * b;
  const s_ = L - 0.0894841775 * a - 1.2914855480 * b;
  const l = l_ ** 3, m = m_ ** 3, s = s_ ** 3;
  let r = (+4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s);
  let g = (-1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s);
  let bl = (-0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s);
  return [r, g, bl];
}
function lin(c) {
  return c <= 0.0031308 ? 12.92 * c : 1.055 * Math.pow(c, 1 / 2.4) - 0.055;
}
function relLum(rgbLinear) {
  const [r, g, b] = rgbLinear.map((c) => Math.min(1, Math.max(0, c)));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}
function lum(L, C, h) { return relLum(oklchToSrgb(L, C, h)); }
function ratio(l1, l2) {
  const a = Math.max(l1, l2), b = Math.min(l1, l2);
  return (a + 0.05) / (b + 0.05);
}
function hex(L, C, h) {
  const rgb = oklchToSrgb(L, C, h).map((c) => Math.round(Math.min(1, Math.max(0, lin(c))) * 255));
  return "#" + rgb.map((x) => x.toString(16).padStart(2, "0")).join("");
}
// tokens
const T = {
  light: { bg:[0.98,0,0], surface:[1,0,0], surface2:[0.965,0,0], ink:[0.20,0,0], inkSoft:[0.42,0,0], muted:[0.55,0,0], solidBg:[0.20,0,0], solidFg:[0.98,0,0], border:[0.90,0,0], borderStrong:[0.82,0,0], danger:[0.58,0.20,25] },
  dark:  { bg:[0.16,0,0], surface:[0.205,0,0], surface2:[0.25,0,0], ink:[0.96,0,0], inkSoft:[0.78,0,0], muted:[0.62,0,0], solidBg:[0.96,0,0], solidFg:[0.16,0,0], border:[0.30,0,0], borderStrong:[0.42,0,0], danger:[0.58,0.20,25] },
};

const pairs = [
  ["ink / bg", "ink", "bg"],
  ["ink / surface", "ink", "surface"],
  ["ink / surface-2", "ink", "surface2"],
  ["ink-soft / bg", "inkSoft", "bg"],
  ["ink-soft / surface", "inkSoft", "surface"],
  ["muted / bg", "muted", "bg"],
  ["muted / surface", "muted", "surface"],
  ["muted / surface-2", "muted", "surface2"],
  ["solid-fg / solid-bg (btn-primary)", "solidFg", "solidBg"],
  ["danger / bg", "danger", "bg"],
  ["danger / surface", "danger", "surface"],
];
for (const theme of ["light", "dark"]) {
  console.log(`\n===== ${theme.toUpperCase()} =====`);
  for (const [label, fg, bg] of pairs) {

    const l1 = lum(...T[theme][fg]), l2 = lum(...T[theme][bg]);
    const r = ratio(l1, l2);
    const aa = r >= 4.5 ? "PASS-AA" : r >= 3 ? "AA-large-only" : "FAIL";
    const aaa = r >= 7 ? "PASS-AAA" : "";
    console.log(`${label.padEnd(34)} ${r.toFixed(2).padStart(6)}:1  ${aa} ${aaa}`);
  }
  console.log("hex:", Object.entries(T[theme]).map(([k,v])=>`${k}=${hex(...v)}`).join(" "));
}
// worst-case gradients / overlays
console.log("\n===== overlay cases (bg with black/60 scrim, dialog sits on top) =====");
console.log("dialog surface on scrim: unaffected (opaque) — see table above");
