"use client";

// 全局登录态：统一管理登录态的读取/写入/登出。
// [SEC] 2026-09-13：认证 JWT 已改为后端写入 HttpOnly cookie（flow_auth），JS 不可读，
// 故登录态只能由 /api/auth/me 推导，而不再依赖 localStorage。
import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { api } from "./api";

type AuthState = {
  token: string | null;
  isAuthed: boolean;
  email: string | null;
  login: (token?: string) => void;
  logout: () => void;
};

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [isAuthed, setIsAuthed] = useState(false);
  const [email, setEmail] = useState<string | null>(null);
  const [ready, setReady] = useState(false);

  // [SEC] 2026-09-13：cookie 不可被 JS 读取 → 登录态只能由 /api/auth/me 推导。
  const refresh = useCallback(async () => {
    try {
      const m = await api.me();
      setEmail(m.email);
      setIsAuthed(true);
    } catch {
      setEmail(null);
      setIsAuthed(false);
    } finally {
      setReady(true);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  // 全局 401 广播（api.request 在默认令牌失效时发出）：回到未登录态，并清后端 cookie。
  useEffect(() => {
    const onUnauthorized = () => {
      setEmail(null);
      setIsAuthed(false);
      api.logout().catch(() => {});
    };
    window.addEventListener("auth:unauthorized", onUnauthorized);
    return () => window.removeEventListener("auth:unauthorized", onUnauthorized);
  }, []);

  function login(_t?: string) {
    // 后端已在登录响应里种下 cookie；这里仅重新拉取用户信息以刷新 UI 态。
    refresh();
    window.dispatchEvent(new Event("auth:changed"));
  }

  function logout() {
    api
      .logout()
      .catch(() => {})
      .finally(() => {
        setEmail(null);
        setIsAuthed(false);
        window.dispatchEvent(new Event("auth:changed"));
      });
  }

  return (
    <AuthContext.Provider value={{ token: null, isAuthed, email, login, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth 必须在 AuthProvider 内使用");
  return ctx;
}
