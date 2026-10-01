"use client";

/**
 * Header bell — the signed-in party's action-required ceiling inspections.
 *
 * Rows come from GET /api/v1/notifications/inbox. In mock mode the backend
 * records one each time an SR moves to a new party (create → RDD PM,
 * level 1 → FM Manager, level 2 → tenant), standing in for the platform's
 * notifyActionOnHandoverInspectionSR. Opening a row loads the SR into both
 * the form and the chat through the notification agent's Open path.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { getCeilingInbox } from "@/lib/api";
import type { InboxRow } from "@/lib/types";

const POLL_MS = 15_000;

export function NotificationBell({
  refreshKey,
  onOpen,
}: {
  refreshKey: number;
  onOpen: (srId: string, subCategory: string) => void;
}) {
  const [rows, setRows] = useState<InboxRow[]>([]);
  const [open, setOpen] = useState(false);
  const [seen, setSeen] = useState<Set<string>>(new Set());
  const [error, setError] = useState<string | null>(null);
  const root = useRef<HTMLDivElement>(null);

  const load = useCallback(async () => {
    try {
      const groups = await getCeilingInbox();
      setRows(groups.flatMap((g) => g.notifications).filter((r) => r.sr_id));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    void load();
    const timer = setInterval(() => void load(), POLL_MS);
    return () => clearInterval(timer);
  }, [load, refreshKey]);

  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => {
      if (!root.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, [open]);

  const unread = rows.filter((r) => !r.read && !seen.has(r.notification_id)).length;

  return (
    <div ref={root} className="relative">
      <button
        aria-label={`Notifications${unread ? ` (${unread} new)` : ""}`}
        className="relative w-9 h-9 rounded-lg border border-white/20 hover:bg-white/10 flex items-center justify-center"
        onClick={() => setOpen((o) => !o)}
      >
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
          <path d="M6 8a6 6 0 1 1 12 0c0 7 3 9 3 9H3s3-2 3-9" />
          <path d="M10.3 21a1.94 1.94 0 0 0 3.4 0" />
        </svg>
        {unread > 0 && (
          <span className="absolute -top-1.5 -right-1.5 min-w-[18px] h-[18px] px-1 rounded-full bg-[#C9A84C] text-[#1A1A2E] text-[11px] font-bold flex items-center justify-center">
            {unread}
          </span>
        )}
      </button>

      {open && (
        <div className="absolute right-0 mt-2 w-[380px] max-h-[420px] overflow-y-auto rounded-xl glass-light z-50 shadow-2xl">
          <div className="px-4 py-3 border-b border-slate-200 text-sm font-semibold">Needs your action</div>
          {error && <div className="px-4 py-3 text-xs text-red-600">{error}</div>}
          {!error && rows.length === 0 && (
            <div className="px-4 py-8 text-center text-sm text-slate-500">You’re all caught up.</div>
          )}
          {rows.map((row) => {
            const isNew = !row.read && !seen.has(row.notification_id);
            return (
              <button
                key={row.notification_id}
                className="w-full text-left px-4 py-3 border-b border-slate-100 hover:bg-slate-50 flex gap-3"
                onClick={() => {
                  setSeen((s) => new Set(s).add(row.notification_id));
                  setOpen(false);
                  onOpen(row.sr_id as string, row.sub_category);
                }}
              >
                <span
                  className={`mt-1.5 w-2 h-2 rounded-full flex-shrink-0 ${isNew ? "bg-[#C9A84C]" : "bg-transparent"}`}
                />
                <span className="min-w-0">
                  <span className="block text-sm text-slate-800">{row.message}</span>
                  <span className="block text-xs text-slate-400 mt-0.5">
                    {row.sub_category === "OPEN_CEILING_INSPECTION" ? "Open Ceiling" : "Close Ceiling"} ·{" "}
                    {row.created_at ? new Date(row.created_at).toLocaleString() : ""}
                  </span>
                </span>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
