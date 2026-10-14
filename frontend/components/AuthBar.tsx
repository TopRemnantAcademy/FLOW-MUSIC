"use client";

import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useT } from "@/lib/i18n";

export default function AuthBar() {
  const t = useT();
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
      setMsg(err?.message || t("auth.failed"));
    }
  }

  async function googleLogin() {
    setMsg("");
    try {
      const cfg = await api.googleConfig();
      if (!cfg.enabled) {
        setMsg(t("auth.googleDisabled"));
        return;
      }
      // Google Identity Services 로 ID 토큰 획득 → 백엔드 검증 → cookie 세팅
      const g = (window as any).google;
      await new Promise<void>((resolve, reject) => {
        if (!g || !g.accounts) {
          reject(new Error(t("auth.googleSdkMissing")));
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
            reject(new Error(t("auth.googlePopupBlocked")));
          }
        });
      });
    } catch (err: any) {
      setMsg(err?.message || t("auth.googleFailed"));
    }
  }

  if (isAuthed) {
    const name = email ? email.split("@")[0] : t("auth.signedIn");
    return (
      <div className="flex items-center gap-2">
        <span
          className="max-w-[10rem] truncate text-sm text-[var(--ink-soft)]"
          title={email ?? ""}
        >
          {name}
        </span>
        <button
          type="button"
          onClick={logout}
          className="btn btn-ghost btn-sm"
        >
          {t("auth.logout")}
        </button>
      </div>
    );
  }

  return (
    <div ref={wrapRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="btn btn-primary btn-sm"
        aria-expanded={open}
        aria-haspopup="dialog"
      >
        {open ? t("auth.close") : mode === "login" ? t("auth.login") : t("auth.register")}
      </button>
      {open && (
        <form
          onSubmit={submit}
          role="dialog"
          aria-label={mode === "login" ? t("auth.login") : t("auth.register")}
          className="card absolute right-0 z-10 mt-2 w-64 space-y-2 p-3 shadow-lg"
        >
          <input
            value={emailInput}
            onChange={(e) => setEmail(e.target.value)}
            type="email"
            placeholder={t("auth.email")}
            aria-label={t("auth.email")}
            className="input"
          />
          <input
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            type="password"
            placeholder={t("auth.password")}
            aria-label={t("auth.password")}
            className="input"
          />
          <button
            type="submit"
            className="btn btn-primary w-full"
          >
            {mode === "login" ? t("auth.login") : t("auth.register")}
          </button>
          {googleEnabled && (
            <>
              <div className="divider"><span>{t("auth.or")}</span></div>
              <button
                type="button"
                onClick={googleLogin}
                className="btn btn-secondary w-full"
              >
                {t("auth.googleLogin")}
              </button>
            </>
          )}
          <button
            type="button"
            onClick={() => setMode(mode === "login" ? "register" : "login")}
            className="btn btn-ghost w-full text-xs"
          >
            {mode === "login" ? t("auth.goRegister") : t("auth.goLogin")}
          </button>
          {msg && (
            <p className="text-xs text-[var(--danger)]" role="alert">
              {msg}
            </p>
          )}
        </form>
      )}
    </div>
  );
}
