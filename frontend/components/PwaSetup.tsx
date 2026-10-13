"use client";

import { useCallback, useEffect, useState } from "react";

/**
 * PWA 装配层（客户端）：SW 注册 + 更新接管 + A2HS 安装提示 + 离线提示。
 *
 * 为什么集中在一个组件：
 *   三者都依赖同一批浏览器事件（online/offline、beforeinstallprompt、controllerchange），
 *   分散到 NavBar / layout / 独立 hook 会各持一份 state，更新时序难以对齐。
 *   本组件只渲染「与 PWA 相关的浮层」，返回 null 时零 DOM 影响。
 *
 * ⚠️ Capacitor/APK 共存（硬约束）：
 *   APK 里 WebView 的 origin 是 capacitor://(iOS) / https://localhost(Android)，
 *   `navigator.serviceWorker` 并不总是可用，install prompt 更不会触发。
 *   因此**一切以 feature-detect 为准**：不支持就整体静默降级，绝不抛错、绝不阻塞首屏。
 */

const SW_URL = "/sw.js";
/** 用户忽略安装提示后，多久不再打扰（24h）。 */
const INSTALL_SNOOZE_MS = 24 * 60 * 60 * 1000;
const INSTALL_SNOOZE_KEY = "flow-pwa-install-snooze";

interface BeforeInstallPromptEvent extends Event {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed" }>;
}

function isStandalone(): boolean {
  if (typeof window === "undefined") return false;
  return (
    window.matchMedia?.("(display-mode: standalone)").matches ||
    // iOS Safari 专有（未标准化）
    (window.navigator as unknown as { standalone?: boolean }).standalone === true
  );
}

function snoozed(): boolean {
  try {
    const at = Number(window.localStorage.getItem(INSTALL_SNOOZE_KEY) || 0);
    return Number.isFinite(at) && Date.now() - at < INSTALL_SNOOZE_MS;
  } catch {
    return false;
  }
}

export default function PwaSetup() {
  const [offline, setOffline] = useState(false);
  const [deferred, setDeferred] = useState<BeforeInstallPromptEvent | null>(null);
  const [showInstall, setShowInstall] = useState(false);
  const [iosHint, setIosHint] = useState(false);

  // ── SW 注册 + 更新接管 ────────────────────────────────────────────────────
  useEffect(() => {
    if (typeof navigator === "undefined" || !("serviceWorker" in navigator)) return;
    // ⚠️ 不在 Capacitor(APK) 里注册：WebView 本地 origin 下 SW 生命周期不可靠，
    //    且 APK 已有 Capacitor 自身的资源缓存策略。APK 侧用 window.Capacitor 标记。
    if ((window as unknown as { Capacitor?: unknown }).Capacitor) return;

    let reloaded = false;
    const onControllerChange = () => {
      // 新 SW 已接管：整页 reload 一次以对齐资源版本（防「旧壳 + 新 chunk」错配）。
      if (reloaded) return;
      reloaded = true;
      window.location.reload();
    };

    const register = async () => {
      try {
        const reg = await navigator.serviceWorker.register(SW_URL, { scope: "/" });
        // 有 waiting worker（新版本已就绪）→ 直接促其接管。
        if (reg.waiting) reg.waiting.postMessage({ type: "FLW_SW_ACTIVATE" });
        reg.addEventListener("updatefound", () => {
          const sw = reg.installing;
          if (!sw) return;
          sw.addEventListener("statechange", () => {
            if (sw.state === "installed" && navigator.serviceWorker.controller) {
              sw.postMessage({ type: "FLW_SW_ACTIVATE" });
            }
          });
        });
      } catch (e) {
        // 注册失败不能打断应用（HTTP 非安全上下文 / 私密模式 / WebView）。
        console.warn("[pwa] service worker registration failed:", e);
      }
    };

    navigator.serviceWorker.addEventListener("controllerchange", onControllerChange);
    void register();
    return () => navigator.serviceWorker.removeEventListener("controllerchange", onControllerChange);
  }, []);

  // ── 离线状态提示 ──────────────────────────────────────────────────────────
  useEffect(() => {
    if (typeof navigator === "undefined") return;
    const sync = () => setOffline(!navigator.onLine);
    sync();
    window.addEventListener("online", sync);
    window.addEventListener("offline", sync);
    return () => {
      window.removeEventListener("online", sync);
      window.removeEventListener("offline", sync);
    };
  }, []);

  // ── A2HS：Chromium beforeinstallprompt ────────────────────────────────────
  useEffect(() => {
    if (isStandalone() || snoozed()) return;
    const onPrompt = (e: Event) => {
      e.preventDefault(); // 拦下浏览器自带的迷你信息条，改由我们在合适的时机展示
      setDeferred(e as BeforeInstallPromptEvent);
      setShowInstall(true);
    };
    window.addEventListener("beforeinstallprompt", onPrompt);
    const onInstalled = () => {
      setShowInstall(false);
      setDeferred(null);
    };
    window.addEventListener("appinstalled", onInstalled);
    return () => {
      window.removeEventListener("beforeinstallprompt", onPrompt);
      window.removeEventListener("appinstalled", onInstalled);
    };
  }, []);

  // ── A2HS：iOS Safari 无 beforeinstallprompt → 给「分享→添加到主屏幕」文案 ──
  useEffect(() => {
    if (isStandalone() || snoozed()) return;
    const ua = navigator.userAgent || "";
    const isIOS = /iPad|iPhone|iPod/.test(ua) && !/CriOS|FxiOS|EdgiOS/.test(ua);
    if (!isIOS) return;
    const t = setTimeout(() => setIosHint(true), 2500);
    return () => clearTimeout(t);
  }, []);

  const dismiss = useCallback(() => {
    setShowInstall(false);
    setIosHint(false);
    try {
      window.localStorage.setItem(INSTALL_SNOOZE_KEY, String(Date.now()));
    } catch {
      /* 私密模式下 localStorage 不可写 —— 忽略即可 */
    }
  }, []);

  const install = useCallback(async () => {
    if (!deferred) return;
    await deferred.prompt();
    const choice = await deferred.userChoice.catch(() => null);
    if (choice && choice.outcome === "accepted") dismiss();
    else dismiss();
  }, [deferred, dismiss]);

  const visible = showInstall || iosHint;

  return (
    <>
      {visible && (
        <div
          role="dialog"
          aria-label="安装 FLOW 到主屏幕"
          className="fixed inset-x-3 bottom-3 z-40 mx-auto flex max-w-xl items-center gap-3 rounded-2xl border border-[var(--border-strong)] bg-[var(--surface)] px-4 py-3 text-sm shadow-[var(--shadow)] sm:inset-x-auto sm:right-4"
        >
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src="/icons/icon-192.png" alt="" width={36} height={36} className="rounded-lg" />
          <div className="min-w-0 flex-1">
            <p className="font-medium text-[var(--ink)]">安装 FLOW 到主屏幕</p>
            <p className="text-[var(--ink-soft)]">
              {iosHint && !showInstall
                ? "点击 Safari 的「分享」→「添加到主屏幕」，即可像 App 一样打开。"
                : "离线也能打开已缓存的歌曲，启动更快。"}
            </p>
          </div>
          {showInstall && (
            <button type="button" onClick={install} className="btn-primary shrink-0 rounded-full px-3 py-1.5">
              安装
            </button>
          )}
          <button
            type="button"
            onClick={dismiss}
            aria-label="关闭"
            className="chip shrink-0 rounded-full px-2.5 py-1.5"
          >
            稍后
          </button>
        </div>
      )}

      {offline && (
        <div
          role="status"
          aria-live="polite"
          className="fixed inset-x-3 top-3 z-40 mx-auto max-w-md rounded-full border border-[var(--border-strong)] bg-[var(--surface)] px-4 py-2 text-center text-xs text-[var(--ink-soft)] shadow-[var(--shadow)]"
        >
          当前处于离线状态 · 仅可播放已缓存的歌曲
        </div>
      )}
    </>
  );
}
