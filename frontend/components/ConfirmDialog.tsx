"use client";

import { useEffect } from "react";

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
  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onCancel();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onCancel]);

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      onClick={onCancel}
    >
      <div
        className="card w-full max-w-sm p-5 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="mb-2 text-base font-semibold text-[var(--ink)]">{title}</h3>
        <p className="mb-4 text-sm text-[var(--muted)]">{message}</p>
        <div className="flex justify-end gap-2">
          <button
            onClick={onCancel}
            className="btn btn-secondary btn-sm"
          >
            {cancelText}
          </button>
          <button
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
