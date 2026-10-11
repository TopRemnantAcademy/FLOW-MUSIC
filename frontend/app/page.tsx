import Link from "next/link";
import HomeFeed from "@/components/HomeFeed";

export default function Home() {
  return (
    <>
      {/* 首屏：品牌 + 主行动，占满一屏并留出下拉空间 */}
      <main className="mx-auto flex min-h-[78vh] max-w-3xl flex-col items-center justify-center gap-10 px-4 text-center">
        {/* 装饰：单色均衡器，呼吸式律动（黑白、尊重 reduced-motion） */}
        <div
          className="eq text-[var(--ink-soft)] enter"
          style={{ height: "3.5rem", gap: "6px" }}
          aria-hidden="true"
        >
          {Array.from({ length: 9 }).map((_, i) => (
            <span
              key={i}
              style={{ animationDelay: `${i * 0.12}s`, opacity: 0.35 + (i % 3) * 0.2 }}
            />
          ))}
        </div>

        <div className="space-y-4">
          <h1
            className="enter enter-delay-1 font-bold tracking-tight text-[var(--ink)]"
            style={{ fontSize: "clamp(2.5rem, 8vw, 4.5rem)", lineHeight: 1.05 }}
          >
            FLOW
          </h1>
          <p
            className="enter enter-delay-2 mx-auto max-w-xl text-[var(--muted)]"
            style={{ fontSize: "clamp(1rem, 2.5vw, 1.25rem)" }}
          >
            输入想法，自动生成完整带人声歌曲 —— 敬拜、灵修、生活的歌。
          </p>
        </div>

        <div className="enter enter-delay-3 flex flex-col items-center gap-4">
          <Link href="/create" className="btn btn-primary btn-lg px-8">
            开始创作
          </Link>
          <div className="flex items-center gap-6 text-sm">
            <Link
              href="/plaza"
              className="text-[var(--ink-soft)] underline-offset-4 transition hover:text-[var(--ink)] hover:underline"
            >
              FLOW广场
            </Link>
            <Link
              href="/library"
              className="text-[var(--ink-soft)] underline-offset-4 transition hover:text-[var(--ink)] hover:underline"
            >
              我的作品
            </Link>
          </div>
        </div>

        {/* 脉冲下滑提示：引导用户看到「其他人的作品」 */}
        <Link
          href="#others"
          aria-label="向下查看其他人的作品"
          className="scroll-hint mt-2 flex flex-col items-center gap-1 text-[var(--muted)] transition hover:text-[var(--ink)]"
        >
          <span className="text-xs tracking-[0.2em]">其他人的作品</span>
          <svg
            width="20"
            height="20"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden="true"
          >
            <path d="M12 5v14M19 12l-7 7-7-7" />
          </svg>
        </Link>
      </main>

      {/* 下拉区：其他人的作品（广场精选流） */}
      <div id="others">
        <HomeFeed />
      </div>
    </>
  );
}
