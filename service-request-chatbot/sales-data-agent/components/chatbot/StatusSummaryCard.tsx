"use client";

import type { StatusSummaryUI, SalesDataRow } from "@/lib/types/chat";
import { salesDataBrand, salesDataCompany, salesDataProperty } from "@/lib/salesDataLeaseDisplay";

const STATUS_CONFIG: Record<string, { label: string; icon: string; cls: string }> = {
  PENDING:                        { label: "Pending",                icon: "⏳", cls: "bg-yellow-50 border-yellow-200 text-yellow-800" },
  NEED_REUPLOAD:                  { label: "Needs Re-upload",        icon: "🔄", cls: "bg-orange-50 border-orange-200 text-orange-800" },
  SUBMITTED:                      { label: "Submitted",              icon: "✅", cls: "bg-blue-50 border-blue-200 text-blue-800" },
  APPROVED:                       { label: "Approved",               icon: "🎉", cls: "bg-green-50 border-green-200 text-green-800" },
  NEED_UPLOAD_SUPPORTING_DOCUMENT:{ label: "Supporting Doc Required",icon: "📎", cls: "bg-purple-50 border-purple-200 text-purple-800" },
  NEED_UPLOAD_AUDITED_SALES:      { label: "Audited Sales Required", icon: "📋", cls: "bg-red-50 border-red-200 text-red-800" },
  RE_UPLOADED:                    { label: "Re-uploaded",            icon: "✅", cls: "bg-teal-50 border-teal-200 text-teal-800" },
  UPLOADED_SUPPORTING_DOCUMENT:   { label: "Supporting Doc Uploaded", icon: "✅", cls: "bg-teal-50 border-teal-200 text-teal-800" },
  UPLOADED_AUDITED_SALES:         { label: "Audited Sales Uploaded", icon: "✅", cls: "bg-teal-50 border-teal-200 text-teal-800" },
};

const DOC_CTA: Record<string, string> = {
  NEED_REUPLOAD: "Re-upload sales data",
  NEED_UPLOAD_SUPPORTING_DOCUMENT: "Upload supporting document",
  NEED_UPLOAD_AUDITED_SALES: "Upload audited sales",
};

interface StatusSummaryCardProps {
  ui: StatusSummaryUI;
  onUploadNow?: () => void;
  onUploadDocument?: (leaseCode: string, status: string) => void;
}

export function StatusSummaryCard({ ui, onUploadNow, onUploadDocument }: StatusSummaryCardProps) {
  const rows: SalesDataRow[] = ui.rows;
  const grouped = ui.grouped;
  const pending = rows.filter((r) => r.status === "PENDING");
  const fileRequests = rows.filter(
    (r) =>
      r.status === "NEED_REUPLOAD" ||
      r.status === "NEED_UPLOAD_SUPPORTING_DOCUMENT" ||
      r.status === "NEED_UPLOAD_AUDITED_SALES",
  );

  return (
    <div className="glass-light rounded-2xl overflow-hidden my-2">
      <div className="bg-gradient-to-r from-gray-50 to-slate-50 border-b border-gray-200 px-4 py-3">
        <h3 className="text-sm font-semibold text-gray-800">
          📊 Your Sales Data Status
        </h3>
        <p className="text-xs text-gray-500 mt-0.5">
          {rows.length} submission{rows.length !== 1 ? "s" : ""} across all leases
        </p>
      </div>

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

      <div className="overflow-x-auto border-t border-gray-100">
        <table className="w-full text-xs">
          <thead>
            <tr className="bg-gray-50 text-gray-500 uppercase tracking-wide">
              <th className="text-left px-4 py-2 font-medium">Lease</th>
              <th className="text-left px-4 py-2 font-medium">Property</th>
              <th className="text-left px-4 py-2 font-medium">Company</th>
              <th className="text-left px-4 py-2 font-medium">Brand</th>
              <th className="text-left px-4 py-2 font-medium">Period</th>
              <th className="text-left px-4 py-2 font-medium">Status</th>
              <th className="text-right px-4 py-2 font-medium">Amount</th>
              <th className="text-right px-4 py-2 font-medium">Action</th>
            </tr>
          </thead>
          <tbody>
            {rows.slice(0, 8).map((row, idx) => {
              const cfg = STATUS_CONFIG[row.status] ?? {
                label: row.status,
                icon: "•",
                cls: "bg-gray-50 border-gray-200 text-gray-700",
              };
              const cta = DOC_CTA[row.status];
              return (
                <tr key={idx} className="border-t border-gray-100 hover:bg-gray-50">
                  <td className="px-4 py-2 font-medium text-gray-800">
                    {row.lease_code}
                  </td>
                  <td className="px-4 py-2 text-gray-600">{salesDataProperty(row)}</td>
                  <td className="px-4 py-2 text-gray-600">{salesDataCompany(row)}</td>
                  <td className="px-4 py-2 text-gray-600">{salesDataBrand(row)}</td>
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
                  <td className="px-4 py-2 text-right">
                    {cta && onUploadDocument ? (
                      <button
                        type="button"
                        onClick={() => onUploadDocument(row.lease_code, row.status)}
                        className="text-[11px] px-2 py-1 bg-orange-600 text-white rounded-md hover:bg-orange-700 font-medium"
                      >
                        {cta}
                      </button>
                    ) : (
                      <span className="text-gray-300">—</span>
                    )}
                  </td>
                </tr>
              );
            })}
            {rows.length > 8 && (
              <tr className="border-t border-gray-100">
                <td colSpan={8} className="px-4 py-2 text-center text-gray-400">
                  …and {rows.length - 8} more
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {fileRequests.length > 0 && onUploadDocument && (
        <div className="border-t border-orange-200 px-4 py-3 bg-orange-50">
          <p className="text-xs text-orange-800 font-medium">
            {fileRequests.length} lease{fileRequests.length !== 1 ? "s" : ""} need a
            file from you (sales data, supporting document, or audited sales). Use the
            row actions above — same as portal Pending / Ask upload.
          </p>
        </div>
      )}
      {pending.length > 0 && onUploadNow && (
        <div className="border-t border-gray-200 px-4 py-3 bg-amber-50 flex items-center justify-between">
          <p className="text-xs text-amber-700 font-medium">
            🔔 {pending.length} pending submission{pending.length !== 1 ? "s" : ""}
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
