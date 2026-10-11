import type { Metadata } from "next";
import Script from "next/script";
import "./globals.css";
import { AuthProvider } from "@/lib/auth";
import { ThemeProvider, noFlashScript } from "@/lib/theme";
import NavBar from "@/components/NavBar";
import AnalyticsTracker from "@/components/AnalyticsTracker";
import BottomPlayer from "@/components/BottomPlayer";

export const metadata: Metadata = {
  title: "FLOW",
  description: "FLOW — 输入想法，自动生成完整带人声歌曲",
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
            <NavBar />
            <AnalyticsTracker />
            {children}
            <BottomPlayer />
          </AuthProvider>
        </ThemeProvider>
        {/* Google Identity Services (Google 로그인). AuthBar 의 버튼은
            백엔드 /api/auth/google/config.enabled 일 때만 노출되므로, 여기서는 항상 로드해도
            미설정 시 버튼이 보이지 않아 무해함. */}
        <Script src="https://accounts.google.com/gsi/client" strategy="afterInteractive" />
      </body>
    </html>
  );
}
