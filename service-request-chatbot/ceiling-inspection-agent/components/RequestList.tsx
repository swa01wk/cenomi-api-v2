"use client";

import { useEffect, useState } from "react";
import { clearRecent, listRecent, type RecentRequest } from "@/lib/recent";

export function RequestList({
  refreshKey,
  onOpen,
}: {
  refreshKey: number;
  onOpen: (srId: string) => void;
}) {
  const [rows, setRows] = useState<RecentRequest[]>([]);
  useEffect(() => setRows(listRecent()), [refreshKey]);

  return (
    <div className="glass-canvas rounded-2xl h-full flex flex-col overflow-hidden">
      <div className="px-5 py-4 border-b border-white/10 flex items-center justify-between">
        <div>
          <div className="text-sm font-semibold">Ceiling Inspections</div>
          <div className="text-xs text-white/50">Raised in this browser — open one to act on it</div>
        </div>
        <button
          className="text-xs px-3 py-1.5 rounded-lg border border-white/20 hover:bg-white/10"
          onClick={() => {
            clearRecent();
            setRows([]);
          }}
        >
          Clear
        </button>
      </div>
      <div className="flex-1 overflow-y-auto p-4 space-y-3">
        {rows.length === 0 && (
          <p className="text-sm text-white/50 text-center mt-10">
            Nothing yet. Sign in as the Tenant and raise an inspection in the chat.
          </p>
        )}
        {rows.map((row) => (
          <div key={row.srId} className="glass rounded-xl p-4">
            <div className="text-sm">{row.summary}</div>
            <div className="text-xs text-white/50 mt-1 break-all">{row.srId}</div>
            <button
              className="mt-3 text-xs px-3 py-1.5 rounded-lg bg-[#C9A84C] text-[#1A1A2E] font-semibold"
              onClick={() => onOpen(row.srId)}
            >
              Open
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}
