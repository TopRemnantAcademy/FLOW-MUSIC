"use client";

/**
 * 管理后台鉴权（前端侧）。
 *
 * ⚠ 아키텍처 사실(2026-09-10 실측): 백엔드 관리자 인증은 **공유 키(X-Admin-Token)** 방식이며
 *   `users` 테이블에 `is_admin`/`role` 컬럼이 **없다**(backend/app/models.py:User =
 *   id/email/hashed_password/tier_key/created_at). 즉 "관리자" = "유효한 관리 키를 가진 사람"이며
 *   사용자 계정과는 무관하다. 따라서 프런트에서의 "관리자에게만 보이기" 는
 *   **로컬에 관리 키가 있고 + 백엔드 검증을 통과했는가** 로만 판정할 수 있다.
 *
 * 서버가 진짜 문지기다: 모든 /api/admin/* 는 require_admin 이 지킨다(backend/app/routers/auth.py:168).
 * 이 모듈은 **UI 노출 여부** 만 다루며, 보안 경계가 아니다(숨겨도 URL 직접 접근은 가능하고,
 * 서버가 거부한다).
 */
import { useEffect, useState } from "react";
import { api } from "@/lib/api";

/** 管理密钥变更事件（同标签页内同步用；跨标签页由原生 storage 事件覆盖）。 */
export const ADMIN_AUTH_CHANGED = "admin-auth-changed";

export function getAdminKey(): string {
  // [SEC] 2026-09-13：管理员密钥已改为后端写入 flow_admin HttpOnly cookie，JS 不可读。
  // 保留签名以兼容可能引用；恒返回空字符串。
  return "";
}

export async function setAdminKey(value: string): Promise<void> {
  // [SEC] 2026-09-13：不再存 localStorage；改为调后端校验并写入 flow_admin HttpOnly cookie。
  await api.adminLogin(value);
  window.dispatchEvent(new Event(ADMIN_AUTH_CHANGED));
}

export async function clearAdminKey(): Promise<void> {
  try {
    await api.adminLogout();
  } catch {
    /* 后端未启用管理后台等情况忽略 */
  }
  window.dispatchEvent(new Event(ADMIN_AUTH_CHANGED));
}

/**
 * 当前是否持有**通过后端校验**的管理密钥。
 *
 * - `null` = 校验中（避免首帧闪烁把入口先渲染出来再隐藏）
 * - `false` = 没有密钥，或密钥无效/过期 → **不显示任何管理入口**
 * - `true`  = 有效
 *
 * 本地没有密钥时**不发请求**（普通用户零额外开销）。
 */
export function useAdminReady(): boolean | null {
  const [ready, setReady] = useState<boolean | null>(null);

  useEffect(() => {
    let alive = true;

    async function check() {
      try {
        // [SEC] 2026-09-13：不再依赖 localStorage 密钥；由后端经 flow_admin cookie 校验有效性。
        // 只读端点，viewer/super 两种令牌都可用 → 仅用于连通性+有效性校验
        await api.adminStats();
        if (alive) setReady(true);
      } catch {
        if (alive) setReady(false);
      }
    }

    check();
    window.addEventListener(ADMIN_AUTH_CHANGED, check);
    window.addEventListener("storage", check);
    return () => {
      alive = false;
      window.removeEventListener(ADMIN_AUTH_CHANGED, check);
      window.removeEventListener("storage", check);
    };
  }, []);

  return ready;
}
