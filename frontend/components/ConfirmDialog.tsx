"use client";

import { useEffect, useId, useRef } from "react";

/**
 * 确认对话框（模态）
 *
 * a11y（GAP-024）：
 *  - role="dialog" + aria-modal + aria-labelledby/aria-describedby：读屏能报出标题与正文；
 *  - 打开时焦点进入「取消」按钮，关闭后**归还**给打开前的元素（否则焦点掉回 <body>）；
 *  - Tab / Shift+Tab 焦点陷阱，循环在这两个按钮之间，不会跑到背后页面；
 *  - Esc 关闭（保留原有行为，并改为在捕获阶段监听，避免被内部控件吞掉）。
 *
 * ⚠ 只改可访问性，交互与样式与原实现保持一致。
 */
export default function ConfirmDialog({
  open,
  title = "确认操作",
  message,
  confirmText = "确定",
  cancelText = "取消",
  danger = false,
  onConfirm,
  onCancel,
}: {
  open: boolean;
  title?: string;
  message: string;
  confirmText?: string;
  cancelText?: string;
  danger?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const titleId = useId();
  const descId = useId();
  const cardRef = useRef<HTMLDivElement>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);
  // 打开前的活动元素：关闭时归还焦点
  const restoreRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!open) return;
    restoreRef.current =
      document.activeElement instanceof HTMLElement ? document.activeElement : null;

    // 初始焦点：落在「取消」上（破坏性操作不应把焦点默认给确认按钮）
    cancelRef.current?.focus();

    function focusables(): HTMLElement[] {
      const root = cardRef.current;
      if (!root) return [];
      return Array.from(
        root.querySelectorAll<HTMLElement>(
          'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
        ),
      );
    }

    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") {
        e.preventDefault();
        onCancel();
        return;
      }
      if (e.key !== "Tab") return;
      const list = focusables();
      if (list.length === 0) {
        e.preventDefault();
        return;
      }
      const first = list[0];
      const last = list[list.length - 1];
      const active = document.activeElement as HTMLElement | null;
      const inDialog = !!cardRef.current?.contains(active);
      // 焦点跑到了对话框之外 → 拽回头（或尾）
      if (!inDialog) {
        e.preventDefault();
        (e.shiftKey ? last : first).focus();
        return;
      }
      const i = list.indexOf(active as HTMLElement);
      if (i === -1) return;
      // 在对话框内自己移动焦点，形成闭环：不依赖浏览器默认 Tab 顺序；
      // 单元素时不抢默认行为（用户仍可用 Shift+Tab 回到浏览器 chrome）。
      if (list.length > 1) {
        e.preventDefault();
        list[(i + (e.shiftKey ? -1 : 1) + list.length) % list.length].focus();
      }
    }

    document.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("keydown", onKey, true);
      restoreRef.current?.focus?.();
    };
  }, [open, onCancel]);

  if (!open) return null;

  return (
    // 点遮罩关闭属于增强的鼠标便捷操作；键盘路径由 Esc + 两个按钮承担（已实现），
    // 故此处对 jsx-a11y 的"交互元素需可聚焦"规则做有据豁免。
    /* eslint-disable-next-line jsx-a11y/click-events-have-key-events, jsx-a11y/no-static-element-interactions */
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      onClick={onCancel}
    >
      {/* 遮罩本身是装饰性的：点击关闭由外层承载，这里不参与可访问性树 */}
      {/* eslint-disable-next-line jsx-a11y/click-events-have-key-events, jsx-a11y/no-noninteractive-element-interactions, jsx-a11y/no-static-element-interactions */}
      <div
        ref={cardRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={descId}
        className="card w-full max-w-sm p-5 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 id={titleId} className="mb-2 text-base font-semibold text-[var(--ink)]">
          {title}
        </h3>
        <p id={descId} className="mb-4 text-sm text-[var(--muted)]">
          {message}
        </p>
        <div className="flex justify-end gap-2">
          <button
            ref={cancelRef}
            type="button"
            onClick={onCancel}
            className="btn btn-secondary btn-sm"
          >
            {cancelText}
          </button>
          <button
            type="button"
            onClick={onConfirm}
            className={danger ? "btn btn-danger btn-sm" : "btn btn-primary btn-sm"}
          >
            {confirmText}
          </button>
        </div>
      </div>
    </div>
  );
}
