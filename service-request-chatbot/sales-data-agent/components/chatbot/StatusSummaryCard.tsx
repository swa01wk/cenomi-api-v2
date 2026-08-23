"use client";

import type { StatusSummaryUI, SalesDataRow } from "@/lib/types/chat";

const STATUS_CONFIG: Record<string, { label: string; icon: string; cls: string }> = {
  PENDING:                        { label: "Pending",                icon: "⏳", cls: "bg-yellow-50 border-yellow-200 text-yellow-800" },
  NEED_REUPLOAD:                  { label: "Needs Re-upload",        icon: "🔄", cls: "bg-orange-50 border-orange-200 text-orange-800" },
  SUBMITTED:                      { label: "Submitted",              icon: "✅", cls: "bg-blue-50 border-blue-200 text-blue-800" },
  APPROVED:                       { label: "Approved",               icon: "🎉", cls: "bg-green-50 border-green-200 text-green-800" },
  NEED_UPLOAD_SUPPORTING_DOCUMENT:{ label: "Supporting Doc Required",icon: "📎", cls: "bg-purple-50 border-purple-200 text-purple-800" },
  NEED_UPLOAD_AUDITED_SALES:      { label: "Audited Sales Required", icon: "📋", cls: "bg-red-50 border-red-200 text-red-800" },
  RE_UPLOADED:                    { label: "Re-uploaded",            icon: "✅", cls: "bg-teal-50 border-teal-200 text-teal-800" },
};

interface StatusSummaryCardProps {
  ui: StatusSummaryUI;
  onUploadNow?: () => void;
}

export function StatusSummaryCard({ ui, onUploadNow }: StatusSummaryCardProps) {
  const rows: SalesDataRow[] = ui.rows;
  const grouped = ui.grouped;
  const actionRequired = rows.filter(
    (r) => r.status === "PENDING" || r.status === "NEED_REUPLOAD",
  );

  return (
    <div className="bg-white border border-gray-200 rounded-2xl overflow-hidden shadow-sm my-2">
      <div className="bg-gradient-to-r from-gray-50 to-slate-50 border-b border-gray-200 px-4 py-3">
        <h3 className="text-sm font-semibold text-gray-800">
          📊 Your Sales Data Status
        </h3>
        <p className="text-xs text-gray-500 mt-0.5">
          {rows.length} submission{rows.length !== 1 ? "s" : ""} across all leases
        </p>
      </div>

      {/* Status pills */}
      <div className="p-4 flex flex-wrap gap-2">
        {Object.entries(grouped).map(([status, count]) => {
          const cfg = STATUS_CONFIG[status] ?? {
            label: status,
            icon: "•",
            cls: "bg-gray-50 border-gray-200 text-gray-700",
          };
          return (
            <span
              key={status}
              className={`flex items-center gap-1.5 text-xs px-3 py-1.5 rounded-full border font-medium ${cfg.cls}`}
            >
              {cfg.icon} {cfg.label}: {count}
            </span>
          );
        })}
      </div>

      {/* Rows table */}
      <div className="overflow-x-auto border-t border-gray-100">
        <table className="w-full text-xs">
          <thead>
            <tr className="bg-gray-50 text-gray-500 uppercase tracking-wide">
              <th className="text-left px-4 py-2 font-medium">Lease</th>
              <th className="text-left px-4 py-2 font-medium">Period</th>
              <th className="text-left px-4 py-2 font-medium">Status</th>
              <th className="text-right px-4 py-2 font-medium">Amount</th>
            </tr>
          </thead>
          <tbody>
            {rows.slice(0, 8).map((row, idx) => {
              const cfg = STATUS_CONFIG[row.status] ?? {
                label: row.status,
                icon: "•",
                cls: "bg-gray-50 border-gray-200 text-gray-700",
              };
              return (
                <tr key={idx} className="border-t border-gray-100 hover:bg-gray-50">
                  <td className="px-4 py-2 font-medium text-gray-800">
                    {row.lease_code}
                  </td>
                  <td className="px-4 py-2 text-gray-500">
                    {row.sales_period_start?.slice(0, 7)}
                  </td>
                  <td className="px-4 py-2">
                    <span className={`px-2 py-0.5 rounded-full border font-medium ${cfg.cls}`}>
                      {cfg.icon} {cfg.label}
                    </span>
                  </td>
                  <td className="px-4 py-2 text-right text-gray-700">
                    {row.sales_amt != null
                      ? `SAR ${Number(row.sales_amt).toLocaleString()}`
                      : "—"}
                  </td>
                </tr>
              );
            })}
            {rows.length > 8 && (
              <tr className="border-t border-gray-100">
                <td colSpan={4} className="px-4 py-2 text-center text-gray-400">
                  …and {rows.length - 8} more
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {/* CTA */}
      {actionRequired.length > 0 && onUploadNow && (
        <div className="border-t border-gray-200 px-4 py-3 bg-amber-50 flex items-center justify-between">
          <p className="text-xs text-amber-700 font-medium">
            🔔 {actionRequired.length} submission{actionRequired.length !== 1 ? "s" : ""} require your action
          </p>
          <button
            onClick={onUploadNow}
            className="text-xs px-3 py-1.5 bg-[#C9A84C] text-white rounded-lg hover:bg-[#B8923D] transition-colors font-medium"
          >
            Upload Now
          </button>
        </div>
      )}
    </div>
  );
}
