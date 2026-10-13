"use client";

/**
 * 应用内更新日志（GAP-040）。
 *
 * 设计取舍：
 *  - **静态优先**：要点随包发布，断网 / 接口挂了也能看。后端接口只用来补
 *    「服务端认为的最新版本」，拿不到就静默降级，绝不因为接口失败白屏。
 *  - 内容渲染走结构化数据（见 `lib/releaseNotes.ts`），不碰 dangerouslySetInnerHTML。
 */

import { useEffect, useState } from "react";
import { api, type ReleaseNotesResponse } from "@/lib/api";
import {
  CURRENT_VERSION,
  RELEASES,
  compareVersion,
  type ReleaseEntryKind,
} from "@/lib/releaseNotes";

// 语义色只用功能色（错误红 / 后台正负绿红），主体保持黑白反相 —— 与全站调性一致。
const KIND_STYLE: Record<ReleaseEntryKind, string> = {
  新增: "border-[var(--ink)] text-[var(--ink)]",
  变更: "border-[var(--border-strong)] text-[var(--ink-soft)]",
  修复: "border-[var(--border-strong)] text-[var(--ink-soft)]",
  安全: "border-[var(--danger)] text-[var(--danger)]",
  文档: "border-[var(--border-strong)] text-[var(--muted)]",
};

export default function ReleaseNotesPage() {
  const [server, setServer] = useState<ReleaseNotesResponse | null>(null);

  useEffect(() => {
    let alive = true;
    api
      .releaseNotes()
      .then((r) => {
        if (alive) setServer(r);
      })
      .catch(() => {
        /* 降级为纯静态内容：更新日志不该因为接口挂了就看不到 */
      });
    return () => {
      alive = false;
    };
  }, []);

  // 判定「有新版本」只在两端都能解析成 x.y.z 时进行。
  const cmp =
    server?.latest_version && server?.app_version
      ? compareVersion(server.latest_version, server.app_version)
      : null;
  const hasUpdate = cmp !== null && cmp > 0;

  return (
    <div className="mx-auto max-w-3xl p-6">
      <h1 className="text-2xl font-semibold text-[var(--ink)]">更新日志</h1>
      <p className="mt-2 text-sm text-[var(--muted)]">
        当前版本 {server?.app_version || CURRENT_VERSION}
        {server?.latest_version && ` · 线上最新 ${server.latest_version}`}
      </p>

      {hasUpdate && (
        <p className="mt-4 rounded-xl border border-[var(--ink)] px-4 py-3 text-sm text-[var(--ink)]">
          有新版本可用：{server?.latest_version}。刷新页面即可获取最新功能。
        </p>
      )}

      <div className="mt-6 space-y-6">
        {RELEASES.map((rel) => (
          <section key={rel.version} className="card p-5">
            <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
              <h2 className="text-lg font-semibold text-[var(--ink)]">{rel.version}</h2>
              <span className="text-sm text-[var(--muted)]">
                {rel.date || "未发布"}
                {!rel.released && " · 开发中"}
              </span>
            </div>

            <ul className="mt-4 space-y-3">
              {rel.entries.map((e, i) => (
                <li key={i} className="flex items-start gap-3">
                  <span
                    className={`chip mt-0.5 shrink-0 px-2 py-0.5 text-xs ${KIND_STYLE[e.kind]}`}
                  >
                    {e.kind}
                  </span>
                  <span className="text-sm leading-relaxed text-[var(--ink-soft)]">
                    {e.text}
                    {e.ref && <span className="ml-2 text-xs text-[var(--muted)]">{e.ref}</span>}
                    {e.unverified && (
                      <span className="ml-2 text-xs text-[var(--danger)]">出处待补</span>
                    )}
                  </span>
                </li>
              ))}
            </ul>
          </section>
        ))}
      </div>

      <p className="mt-6 text-xs leading-relaxed text-[var(--muted)]">
        完整变更与出处见仓库根目录 <code>CHANGELOG.md</code>；发布与版本号约定见{" "}
        <code>docs/RELEASE_PROCESS.md</code>。
      </p>
    </div>
  );
}
