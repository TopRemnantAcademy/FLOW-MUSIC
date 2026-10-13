import type { Metadata, Viewport } from "next";
import Script from "next/script";
import "./globals.css";
import { AuthProvider } from "@/lib/auth";
import { ThemeProvider, noFlashScript } from "@/lib/theme";
import NavBar from "@/components/NavBar";
import AnalyticsTracker from "@/components/AnalyticsTracker";
import BottomPlayer from "@/components/BottomPlayer";
import PwaSetup from "@/components/PwaSetup";

export const metadata: Metadata = {
  title: "FLOW",
  description: "FLOW — 输入想法，自动生成完整带人声歌曲",
  // [GAP-018] PWA：可安装 + 独立窗口。文件位于 frontend/public/ → 原样落到站点根。
  manifest: "/manifest.webmanifest",
  applicationName: "FLOW",
  // iOS Safari 不读 manifest 的 icons：apple-touch-icon 必须显式声明，
  // 否则「添加到主屏幕」会用页面截图当图标。
  icons: {
    icon: [
      { url: "/icons/favicon-32.png", sizes: "32x32", type: "image/png" },
      { url: "/icons/icon.svg", type: "image/svg+xml" },
    ],
    apple: [{ url: "/icons/apple-touch-icon.png", sizes: "180x180", type: "image/png" }],
  },
  appleWebApp: {
    capable: true,
    title: "FLOW",
    // "default"：iOS 用系统默认状态栏配色。此处刻意不用 black-translucent ——
    // 那会要求整站手写 env(safe-area-inset-*) 内边距，属 #93/原生任务的范围。
    statusBarStyle: "default",
  },
  formatDetection: { telephone: false },
};

// [GAP-018] 移动端视口：viewport-fit=cover 是本任务对 iOS 全屏的最小让步，
// 不引入任何 safe-area 内边距改动（避免与并行任务冲突）。
export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
  // 与 public/manifest.webmanifest 的 theme_color 保持一致（单一来源：manifest）。
  themeColor: "#0f0f0f",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN" suppressHydrationWarning>
      <head>
        {/* 首屏无闪烁：于首帧前应用 data-theme */}
        <script dangerouslySetInnerHTML={{ __html: noFlashScript }} />
      </head>
      <body className="theme-transition min-h-screen">
        <ThemeProvider>
          <AuthProvider>
            {/* 跳转到主内容：键盘/读屏用户绕过导航栏（a11y，GAP-024） */}
            <a href="#main-content" className="skip-link">
              跳到主要内容
            </a>
            <NavBar />
            <AnalyticsTracker />
            <div id="main-content" tabIndex={-1} className="outline-none">
              {children}
            </div>
            <BottomPlayer />
          </AuthProvider>
          {/* [GAP-018] PWA 装配：SW 注册 / A2HS / 离线提示。
              内部全部 feature-detect —— Capacitor(APK) 环境自动静默跳过。 */}
          <PwaSetup />
        </ThemeProvider>
        {/* Google Identity Services (Google 로그인). AuthBar 의 버튼은
            백엔드 /api/auth/google/config.enabled 일 때만 노출되므로, 여기서는 항상 로드해도
            미설정 시 버튼이 보이지 않아 무해함. */}
        <Script src="https://accounts.google.com/gsi/client" strategy="afterInteractive" />
      </body>
    </html>
  );
}
