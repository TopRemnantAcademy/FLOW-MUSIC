/**
 * 应用内更新日志的内容源（GAP-040）。
 *
 * 这里**刻意**不写 HTML/Markdown，只写结构化数据。原因有二：
 *  1. 渲染安全 —— update notes 天生是「服务端可能会改的内容」，如果内容是
 *     Markdown/HTML，迟早会有人 `dangerouslySetInnerHTML`，那就是一个 XSS 入口。
 *     结构化数据 + React 渲染，从根上没有这个面。
 *  2. 可校验 —— `scripts/check-release-notes.mjs` 能直接读这个文件，
 *     校验「版本号与 package.json 一致」「条目都有出处」「日期不晚于今天」。
 *
 * ⚠ 内容必须与根 `CHANGELOG.md` 同源（真实提交/PR）。详见 `docs/RELEASE_PROCESS.md`。
 */

export type ReleaseEntryKind = "新增" | "变更" | "修复" | "安全" | "文档";

export type ReleaseEntry = {
  kind: ReleaseEntryKind;
  /** 面向用户的一句话。写「能做什么」，不写「哪个文件改了」。 */
  text: string;
  /** 出处：真实 PR 号或提交短 hash。空字符串表示「待补」—— 发布前必须补上。 */
  ref: string;
  /** 尚未核对出处的条目，UI 上会标记出来，避免读者误以为是已发布内容。 */
  unverified?: boolean;
};

export type ReleaseVersion = {
  version: string;
  /** 发布日 YYYY-MM-DD；未发布版本用 null。 */
  date: string | null;
  released: boolean;
  entries: ReleaseEntry[];
};

/**
 * 各版本要点。`CURRENT_VERSION` 必须与 `package.json` 的 `version` 一致，
 * 否则 `npm run check:release` 会红。
 */
export const CURRENT_VERSION = "0.2.0";

export const RELEASES: ReleaseVersion[] = [
  {
    version: "0.2.0",
    date: "2026-10-12",
    released: true,
    entries: [
      { kind: "新增", text: "搜索与发现：标签分类浏览、相似作品推荐、7 天 / 30 天趋势榜", ref: "#82" },
      { kind: "新增", text: "下载可选 mp3 / wav / flac，无无损源时如实提示而不是悄悄降级", ref: "#105" },
      { kind: "新增", text: "播放列表、文件夹、批量操作与回收站（删除后 30 天内可恢复）", ref: "#108" },
      { kind: "新增", text: "邮箱验证、密码重置、两步验证与登录失败锁定", ref: "#70" },
      { kind: "新增", text: "支持 Google / Kakao / Naver 账号登录", ref: "#99" },
      { kind: "新增", text: "作品授权类型标注（商用 / 非商用）与内容审核队列", ref: "#84" },
      { kind: "新增", text: "音频优先走 CDN 分发，播放更快、更省流量", ref: "#87" },
      { kind: "新增", text: "应用内更新日志（本页）", ref: "#117" },
      { kind: "变更", text: "后端支持 PostgreSQL，数据库迁移在真实 PG 上可从零建表", ref: "#106" },
      { kind: "变更", text: "续写歌曲时可选择从开头或结尾衔接", ref: "fd463fe" },
      { kind: "修复", text: "修复数据库迁移链冲突导致的全新环境部署失败", ref: "#104" },
      { kind: "修复", text: "修复续写方向选择不生效", ref: "fd463fe" },
      { kind: "安全", text: "登录凭证改为 HttpOnly Cookie 保存，页面脚本无法读取", ref: "d003c81" },
    ],
  },
];

/** 取最新一版（数组首项约定为最新，不按日期排序 —— 日期可能是 null）。 */
export function latestRelease(): ReleaseVersion {
  return RELEASES[0];
}

/**
 * 比较两个 `x.y.z` 版本号。
 * 返回 >0 表示 a 更新，<0 表示 b 更新，0 表示相等。
 * **解析失败返回 null** —— 版本号格式不对时「不比大小」比瞎猜安全：
 * 曾有上游把 `1.0` / `v1.0` / `1.0.0-rc1` 混着下发，任何兜底解析都会误报「有更新」。
 */
export function compareVersion(a: string, b: string): number | null {
  const pa = parseVersion(a);
  const pb = parseVersion(b);
  if (!pa || !pb) return null;
  for (let i = 0; i < 3; i++) {
    if (pa[i] !== pb[i]) return pa[i] - pb[i];
  }
  return 0;
}

function parseVersion(v: string): [number, number, number] | null {
  const m = /^(\d+)\.(\d+)\.(\d+)$/.exec(String(v || "").trim());
  if (!m) return null;
  return [Number(m[1]), Number(m[2]), Number(m[3])];
}
