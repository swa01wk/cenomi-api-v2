"use client";

import { useEffect, useRef, useState } from "react";
import { SalesDataDocumentModal } from "@/components/SalesDataDocumentModal";

export type AdminRowAction =
  | "accept"
  | "ask_sales_data"
  | "ask_supporting"
  | "ask_audited";

export function adminActionMessage(
  leaseCode: string,
  action: AdminRowAction,
  period?: { start?: string | null; end?: string | null },
): string {
  const periodBit =
    period?.start && period?.end ? ` (${period.start} to ${period.end})` : "";
  switch (action) {
    case "accept":
      return `Approve ${leaseCode}${periodBit}`;
    case "ask_sales_data":
      return `Request reupload for ${leaseCode}${periodBit}`;
    case "ask_supporting":
      return `Request supporting document for ${leaseCode}${periodBit}`;
    case "ask_audited":
      return `Request audited sales for ${leaseCode}${periodBit}`;
  }
}

interface AdminRowMenuProps {
  leaseCode: string;
  documentId?: string | null;
  disabled?: boolean;
  onAction: (action: AdminRowAction) => void;
  compact?: boolean;
}

export function AdminRowMenu({
  leaseCode,
  documentId,
  disabled = false,
  onAction,
  compact = false,
}: AdminRowMenuProps) {
  const [open, setOpen] = useState(false);
  const [askOpen, setAskOpen] = useState(false);
  const [viewOpen, setViewOpen] = useState(false);
  const [menuPos, setMenuPos] = useState({ top: 0, right: 0 });
  const rootRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const hasDocument = Boolean(documentId && String(documentId).trim());

  useEffect(() => {
    if (!open) return;
    function onDocClick(e: MouseEvent) {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
        setOpen(false);
        setAskOpen(false);
      }
    }
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, [open]);

  function toggle() {
    if (!open && buttonRef.current) {
      const rect = buttonRef.current.getBoundingClientRect();
      setMenuPos({
        top: rect.bottom + 6,
        right: window.innerWidth - rect.right,
      });
    }
    setOpen((v) => !v);
    setAskOpen(false);
  }

  function pick(action: AdminRowAction) {
    setOpen(false);
    setAskOpen(false);
    onAction(action);
  }

  return (
    <div className="relative inline-block" ref={rootRef}>
      <button
        ref={buttonRef}
        type="button"
        aria-label={`Actions for ${leaseCode}`}
        aria-expanded={open}
        disabled={disabled}
        onClick={toggle}
        className={`inline-flex items-center justify-center rounded-lg border border-white/15 text-white/80 hover:bg-white/10 hover:text-white disabled:opacity-40 ${
          compact ? "w-7 h-7" : "w-8 h-8"
        }`}
      >
        <svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor" aria-hidden>
          <circle cx="8" cy="3" r="1.4" />
          <circle cx="8" cy="8" r="1.4" />
          <circle cx="8" cy="13" r="1.4" />
        </svg>
      </button>
      {open && (
        <div
          className="fixed z-50 min-w-[13.5rem] rounded-xl border border-white/15 bg-[#12141f]/95 backdrop-blur-xl shadow-[0_16px_40px_rgba(0,0,0,0.45)] overflow-hidden"
          style={{ top: menuPos.top, right: menuPos.right }}
        >
          <button
            type="button"
            onClick={() => setAskOpen((v) => !v)}
            className="w-full flex items-center justify-between px-3 py-2 text-left text-xs text-white/90 hover:bg-white/10"
          >
            <span>Ask upload</span>
            <span className="text-white/40">{askOpen ? "▾" : "▸"}</span>
          </button>
          {askOpen && (
            <div className="border-y border-white/10 bg-white/[0.03]">
              {(
                [
                  ["ask_sales_data", "Sales data"],
                  ["ask_supporting", "Supporting document"],
                  ["ask_audited", "Audited sales data"],
                ] as const
              ).map(([action, label]) => (
                <button
                  key={action}
                  type="button"
                  onClick={() => pick(action)}
                  className="w-full px-4 py-2 text-left text-[11px] text-white/75 hover:bg-[#C9A84C]/15 hover:text-[#E4C063]"
                >
                  {label}
                </button>
              ))}
            </div>
          )}
          <button
            type="button"
            onClick={() => pick("accept")}
            className="w-full px-3 py-2 text-left text-xs text-white/90 hover:bg-white/10"
          >
            Accept
          </button>
          {hasDocument && (
            <button
              type="button"
              onClick={() => {
                setOpen(false);
                setAskOpen(false);
                setViewOpen(true);
              }}
              className="w-full px-3 py-2 text-left text-xs text-white/90 hover:bg-white/10 border-t border-white/10"
            >
              Download Supporting Document
            </button>
          )}
        </div>
      )}
      {viewOpen && documentId && (
        <SalesDataDocumentModal
          documentId={documentId}
          onClose={() => setViewOpen(false)}
        />
      )}
    </div>
  );
}
