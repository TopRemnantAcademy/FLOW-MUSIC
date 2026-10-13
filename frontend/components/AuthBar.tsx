"use client";

import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";

export default function AuthBar() {
  const { isAuthed, email, login, logout } = useAuth();
  const [emailInput, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [mode, setMode] = useState<"login" | "register">("login");
  const [open, setOpen] = useState(false);
  const [msg, setMsg] = useState("");
  const [googleEnabled, setGoogleEnabled] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function onDown(e: MouseEvent) {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  // Google 登录按钮 노출 여부: 백엔드 /api/auth/google/config.enabled 에 따라 결정
  useEffect(() => {
    if (!open) return;
    let alive = true;
    api.googleConfig().then((c) => { if (alive) setGoogleEnabled(!!c.enabled); }).catch(() => {});
    return () => { alive = false; };
  }, [open]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setMsg("");
    try {
      const r =
        mode === "login"
          ? await api.login(emailInput, password)
          : await api.register(emailInput, password);
      login(r.access_token);
      setOpen(false);
      setEmail("");
      setPassword("");
    } catch (err: any) {
      setMsg(err?.message || "失败");
    }
  }

  async function googleLogin() {
    setMsg("");
    try {
      const cfg = await api.googleConfig();
      if (!cfg.enabled) {
        setMsg("Google 登录未启用");
        return;
      }
      // Google Identity Services 로 ID 토큰 획득 → 백엔드 검증 → cookie 세팅
      const g = (window as any).google;
      await new Promise<void>((resolve, reject) => {
        if (!g || !g.accounts) {
          reject(new Error("Google SDK 未加载"));
          return;
        }
        g.accounts.id.initialize({
          client_id: cfg.client_id,
          callback: async (response: any) => {
            try {
              const r = await api.loginWithGoogle(response.credential);
              login(r.access_token);
              setOpen(false);
              resolve();
            } catch (e: any) {
              reject(e);
            }
          },
        });
        g.accounts.id.prompt((notification: any) => {
          if (notification.isNotDisplayed() || notification.isSkippedMoment()) {
            reject(new Error("Google 登录窗口未显示，请关闭弹窗拦截"));
          }
        });
      });
    } catch (err: any) {
      setMsg(err?.message || "Google 登录失败");
    }
  }

  if (isAuthed) {
    const name = email ? email.split("@")[0] : "已登录";
    return (
      <div className="flex items-center gap-2">
        <span
          className="max-w-[10rem] truncate text-sm text-[var(--ink-soft)]"
          title={email ?? ""}
        >
          {name}
        </span>
        <button
          onClick={logout}
          className="btn btn-ghost btn-sm"
        >
          退出
        </button>
      </div>
    );
  }

  return (
    <div ref={wrapRef} className="relative">
      <button
        onClick={() => setOpen((o) => !o)}
        className="btn btn-primary btn-sm"
      >
        {open ? "关闭" : mode === "login" ? "登录" : "注册"}
      </button>
      {open && (
        <form
          onSubmit={submit}
          className="card absolute right-0 z-10 mt-2 w-64 space-y-2 p-3 shadow-lg"
        >
          <input
            value={emailInput}
            onChange={(e) => setEmail(e.target.value)}
            type="email"
            placeholder="邮箱"
            aria-label="邮箱"
            className="input"
          />
          <input
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            type="password"
            placeholder="密码"
            aria-label="密码"
            className="input"
          />
          <button
            type="submit"
            className="btn btn-primary w-full"
          >
            {mode === "login" ? "登录" : "注册"}
          </button>
          {googleEnabled && (
            <>
              <div className="divider"><span>或</span></div>
              <button
                type="button"
                onClick={googleLogin}
                className="btn btn-secondary w-full"
              >
                使用 Google 登录
              </button>
            </>
          )}
          <button
            type="button"
            onClick={() => setMode(mode === "login" ? "register" : "login")}
            className="btn btn-ghost w-full text-xs"
          >
            {mode === "login" ? "去注册" : "去登录"}
          </button>
          {msg && <p className="text-xs text-[var(--danger)]">{msg}</p>}
        </form>
      )}
    </div>
  );
}
