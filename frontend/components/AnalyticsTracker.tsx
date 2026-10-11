"use client";

import { useEffect } from "react";
import { usePathname } from "next/navigation";
import { trackEvent, trackPageView } from "@/lib/eventReporter";

// 挂载于 RootLayout：每次路由变化上报 session_start + page_view（跨产品 DAU/平台/时间段分析）。
export default function AnalyticsTracker() {
  const pathname = usePathname();
  useEffect(() => {
    trackEvent("session_start", { screen: pathname || "/" });
    trackPageView(pathname || "/");
  }, [pathname]);
  return null;
}
