"use client";

import { useParams } from "next/navigation";
import Link from "next/link";
import LyricsEditor from "@/components/LyricsEditor";

// GAP-012：歌词编辑器独立页面。
//
// 为什么是独立路由而不是塞进作品卡片或播放器弹层：
//   · 编辑是**长任务**（多行、逐行对时点），弹层里做必然被挤压；
//   · 作品列表页的卡片已经承载了生成/发布/分组/分轨/局部改写，再挂编辑器会让
//     卡片变成「什么都能干但什么都挤」的一坨；
//   · 独立 URL 可分享/可刷新，「我到哪首曲子的歌词页」有稳定地址。
export default function LyricsEditPage() {
  const params = useParams<{ id: string }>();
  const songId = Number(params?.id);

  if (!songId || Number.isNaN(songId)) {
    return (
      <main className="mx-auto max-w-3xl px-4 py-8">
        <div className="card p-6 text-sm text-[var(--muted)]">
          作品 ID 无效。
          <Link href="/library" className="btn btn-secondary btn-sm ml-3 inline-block">
            返回我的作品
          </Link>
        </div>
      </main>
    );
  }

  return (
    <main className="mx-auto max-w-3xl px-4 py-8">
      <div className="mb-6">
        <h1 className="text-2xl font-bold">歌词编辑器</h1>
        <p className="mt-1 text-sm text-[var(--muted)]">
          段落结构化编辑 · 改词后按原时间轴重排 · LRC 导入导出（标准 [mm:ss.xx]，第三方播放器可直接解析）
        </p>
      </div>
      <LyricsEditor songId={songId} />
    </main>
  );
}
