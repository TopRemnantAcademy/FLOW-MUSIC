// 跨产品活动上报：复用 FLOW 后端 /mobile/events/batch 端点（ALLOWED_EVENT_TYPES 同源）。
//
// 设计要点：
//  - 仅做匿名聚合分析（DAU/平台分布/功能使用/时间段），不携带任何 PII。
//  - FLOW 服务端忽略客户端上报的 subscriber_id，统一按 device_id 归属性（anonymous aggregation）；
//    跨产品身份桥接（FLOW subscriber ↔ 音乐 user）由后端 user_links 负责，此处不处理。
//  - 上报失败静默忽略，绝不影响主流程。
//  - 端点为 guest 允许（无需登录令牌），CORS 需在 FLOW 侧将音乐前端 origin 加入 ALLOWED_ORIGINS。
// [APK] lib/api.ts 의 API_BASE 와 동일 게이팅: APK 빌드에서 loopback 리터럴 잔존 방지.
const FLOW_EVENTS_BASE =
  process.env.NEXT_PUBLIC_FLOW_EVENTS_BASE ||
  (process.env.NEXT_PUBLIC_APK_BUILD === "1" ? "" : "http://localhost:8000");
const PLATFORM = "music-web";

function _anonId(): string {
  if (typeof window === "undefined") return "ssr";
  try {
    let id = window.localStorage.getItem("flow_anon_id");
    if (!id) {
      id = "m_" + Math.random().toString(36).slice(2) + Date.now().toString(36);
      window.localStorage.setItem("flow_anon_id", id);
    }
    return id;
  } catch {
    return "anon";
  }
}

function _sessionId(): string {
  if (typeof window === "undefined") return "ssr";
  const w = window as unknown as { __flowSession?: string };
  if (!w.__flowSession) {
    w.__flowSession = "s_" + Math.random().toString(36).slice(2) + Date.now().toString(36);
  }
  return w.__flowSession;
}

export type MusicEventType =
  | "session_start"
  | "session_end"
  | "page_view"
  | "media_play_start"
  | "media_play_progress"
  | "media_play_complete"
  | "search"
  | "share"
  | "button_click"
  | "chat_send"
  | "identify"
  | "js_error";

type PendingEvent = { event_type: MusicEventType; payload?: Record<string, unknown> };

function _flush(events: PendingEvent[]) {
  if (typeof window === "undefined") return;
  if (!events.length) return;
  const body = {
    device_id: _anonId(),
    session_id: _sessionId(),
    platform: PLATFORM,
    app_version: process.env.NEXT_PUBLIC_APP_VERSION || "unknown",
    events: events.map((e) => ({
      event_type: e.event_type,
      ts: Date.now() / 1000,
      payload: e.payload || {},
    })),
  };
  try {
    // keepalive: true → 页面卸载时仍可发出（近似 sendBeacon，但显式声明 JSON Content-Type）
    fetch(`${FLOW_EVENTS_BASE}/mobile/events/batch`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      keepalive: true,
    }).catch(() => {});
  } catch {
    /* 上报失败静默忽略 */
  }
}

export function trackEvent(type: MusicEventType, payload?: Record<string, unknown>) {
  _flush([{ event_type: type, payload }]);
}

export function trackPageView(path: string) {
  _flush([{ event_type: "page_view", payload: { screen: path } }]);
}

export function trackGenerate() {
  _flush([{ event_type: "button_click", payload: { cta: "generate" } }]);
}

export function trackSearch(query: string) {
  _flush([{ event_type: "search", payload: { query, result_count: 0 } }]);
}
