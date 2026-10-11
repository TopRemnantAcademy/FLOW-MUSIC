"use client";

/**
 * 管理后台统一门禁。
 *
 * 内容审核 / 平台设置 / 控制台首页 三处共用同一套「输入管理密钥」界面，
 * 避免三个页面各写一份（此前确实各写了一份，文案与退出行为都不一致）。
 *
 * ⚠ 这是 **UI 门禁，不是安全边界**。真正的边界在后端
 * `require_admin`(backend/app/routers/auth.py:168) —— 没有有效密钥时
 * 所有 `/api/admin/*` 直接 401/503。
 */
import { useState } from "react";
import { setAdminKey } from "@/lib/adminAuth";

export default function AdminGuard({
  title,
  desc,
  onUnlocked,
}: {
  title: string;
  desc: string;
  /** 保存密钥后触发（父组件据此重新加载数据）。 */
  onUnlocked: (key: string) => void;
}) {
  const [value, setValue] = useState("");
  const [error, setError] = useState("");

  async function submit() {
    const key = value.trim();
    if (!key) {
      setError("请输入管理密钥");
      return;
    }
    setError("");
    try {
      // [SEC] 2026-09-13：经后端校验并写入 flow_admin HttpOnly cookie，不再存 localStorage。
      await setAdminKey(key);
      onUnlocked(key);
    } catch {
      setError("管理密钥无效或后台未启用");
    }
  }

  return (
    <div className="mx-auto max-w-md p-8">
      <h1 className="mb-4 text-2xl font-semibold text-[var(--ink)]">{title}</h1>
      <p className="mb-4 text-base text-[var(--muted)]">{desc}</p>
      <input
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") submit();
        }}
        type="password"
        placeholder="管理密钥"
        aria-label="管理密钥"
        className="input mb-4"
      />
      {error && <p className="mb-3 text-sm text-[var(--danger)]">{error}</p>}
      <button
        onClick={submit}
        className="btn btn-primary w-full"
      >
        进入
      </button>
    </div>
  );
}
