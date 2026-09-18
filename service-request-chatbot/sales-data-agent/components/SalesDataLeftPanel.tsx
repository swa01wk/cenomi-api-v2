"use client";

/**
 * SalesDataLeftPanel — mirrors the chatbot's current state in a platform-styled panel.
 *
 * State flow:
 *   idle              → upload dropzone + template download
 *   processing        → animated skeleton / spinner overlay
 *   sales_data_review → editable review table (rows editable here)
 *   submission_success → success banner
 *   submission_error  → error banner with retry hint
 *   status_summary    → status table read-only
 */

import { useSalesDataSession } from "@/lib/context/SalesDataSessionContext";
import { AdminRowMenu, adminActionMessage } from "@/components/AdminRowMenu";
import { SalesDataStatusBadge } from "@/components/SalesDataStatusBadge";
import { salesDataBrand, salesDataCompany, salesDataProperty } from "@/lib/salesDataLeaseDisplay";
import type {
  SalesDataReviewUI,
  SalesDataReviewRow,
  SubmissionSuccessUI,
  SubmissionErrorUI,
  StatusSummaryUI,
  ManualEntryConfirmUI,
  SupportingDocConfirmUI,
  SupportingDocPromptUI,
  SupportingDocSuccessUI,
  SupportingDocSelectUI,
  AdminSalesDataListUI,
  AdminConfirmUI,
  AdminActionSuccessUI,
  PeriodMismatchWarningUI,
  PendingMonthsListUI,
  OtherMonthsReminderUI,
} from "@/lib/types/chat";

const STATUS_COLORS: Record<string, string> = {
  PENDING: "bg-amber-400/15 text-amber-200 border-amber-400/25",
  SUBMITTED: "bg-sky-400/15 text-sky-200 border-sky-400/25",
  APPROVED: "bg-emerald-400/15 text-emerald-200 border-emerald-400/25",
  NEED_REUPLOAD: "bg-red-400/15 text-red-200 border-red-400/25",
  RE_UPLOADED: "bg-violet-400/15 text-violet-200 border-violet-400/25",
  NEED_UPLOAD_SUPPORTING_DOCUMENT: "bg-orange-400/15 text-orange-200 border-orange-400/25",
  UPLOADED_SUPPORTING_DOCUMENT: "bg-teal-400/15 text-teal-200 border-teal-400/25",
  NEED_UPLOAD_AUDITED_SALES: "bg-rose-400/15 text-rose-200 border-rose-400/25",
  UPLOADED_AUDITED_SALES: "bg-teal-400/15 text-teal-200 border-teal-400/25",
};

function StatusBadge({ status }: { status: string }) {
  const cls = STATUS_COLORS[status] ?? "bg-white/10 text-white/70 border-white/15";
  return (
    <span className={`inline-block text-xs font-semibold px-2 py-0.5 rounded-full border ${cls}`}>
      {status.replace(/_/g, " ")}
    </span>
  );
}

// ── Sub-panels ──────────────────────────────────────────────────────────────

function IdlePanel() {
  return (
    <div className="flex flex-col items-center justify-center h-full gap-6 text-center px-8">
      <div className="w-20 h-20 rounded-2xl bg-[#C9A84C]/10 border-2 border-dashed border-[#C9A84C]/60 flex items-center justify-center shadow-[0_0_28px_rgba(201,168,76,0.18)]">
        <svg className="w-9 h-9 text-[#C9A84C]" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
            d="M9 13h6m-3-3v6m5 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
        </svg>
      </div>
      <div>
        <h3 className="text-base font-semibold text-white mb-1">Sales Data Upload</h3>
        <p className="text-sm text-white/45 max-w-xs">
          Use the AI assistant on the right to start uploading your monthly sales data.
          Actions will be mirrored here automatically.
        </p>
      </div>
      <div className="flex gap-2">
        {["PENDING", "SUBMITTED", "APPROVED"].map((s) => (
          <StatusBadge key={s} status={s} />
        ))}
      </div>
      <p className="text-xs text-white/30">← Waiting for AI assistant</p>
    </div>
  );
}

function ProcessingOverlay() {
  return (
    <div className="absolute inset-0 bg-[#0c0e18]/55 backdrop-blur-md flex items-center justify-center z-10 rounded-xl">
      <div className="flex flex-col items-center gap-3">
        <div className="w-10 h-10 border-4 border-[#C9A84C] border-t-transparent rounded-full animate-spin" />
        <p className="text-sm text-white/70 font-medium">Processing…</p>
      </div>
    </div>
  );
}

interface ReviewTablePanelProps {
  ui: SalesDataReviewUI;
  rows: SalesDataReviewRow[];
  onRowsChange: (rows: SalesDataReviewRow[]) => void;
  onSubmit: (rows: SalesDataReviewRow[]) => void;
  isPending: boolean;
}

function ReviewTablePanel({ ui, rows, onRowsChange, onSubmit, isPending }: ReviewTablePanelProps) {
  function handleAmtChange(idx: number, value: string) {
    const updated = rows.map((r, i) =>
      i === idx ? { ...r, sales_amt: parseFloat(value) || 0 } : r,
    );
    onRowsChange(updated);
  }

  const anyInvalid = rows.some((r) => !r.sales_amt || r.sales_amt <= 0);

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between px-5 py-3 border-b border-white/10">
        <div>
          <p className="text-sm font-semibold text-white">Review Sales Data</p>
          <p className="text-xs text-white/40">{ui.filename} · {ui.row_count} row{ui.row_count !== 1 ? "s" : ""}</p>
        </div>
        <span className="text-xs font-semibold text-[#E4C063] bg-[#C9A84C]/15 border border-[#C9A84C]/30 px-2 py-1 rounded-full">
          Editable
        </span>
      </div>

      {/* Table */}
      <div className="flex-1 overflow-auto px-4 py-3">
        <table className="w-full text-xs border-separate border-spacing-0">
          <thead>
            <tr>
              {["Lease", "Brand", "Property", "Period", "Status", "Sales (SAR)"].map((h) => (
                <th key={h} className="text-left font-semibold text-white/40 pb-2 pr-3">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-white/5">
            {rows.map((row, idx) => (
              <tr key={idx} className="hover:bg-white/5 transition-colors">
                <td className="py-2 pr-3 font-mono font-semibold text-white">{row.lease_code}</td>
                <td className="py-2 pr-3 text-white/70">{row.brand || "—"}</td>
                <td className="py-2 pr-3 text-white/70">{row.property || "—"}</td>
                <td className="py-2 pr-3 text-white/60 whitespace-nowrap">{row.period}</td>
                <td className="py-2 pr-3">
                  <StatusBadge status={row.current_status} />
                </td>
                <td className="py-2">
                  <input
                    type="number"
                    value={row.sales_amt || ""}
                    onChange={(e) => handleAmtChange(idx, e.target.value)}
                    min={0.01}
                    step={0.01}
                    className="w-28 bg-white/10 border border-white/15 text-white rounded-lg px-2 py-1 text-xs focus:outline-none focus:ring-2 focus:ring-[#C9A84C] focus:border-transparent"
                    disabled={isPending}
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Footer */}
      <div className="border-t border-white/10 px-5 py-3 flex items-center justify-between">
        {anyInvalid && (
          <p className="text-xs text-red-500">All sales amounts must be &gt; 0</p>
        )}
        <button
          onClick={() => onSubmit(rows)}
          disabled={isPending || anyInvalid}
          className="ml-auto bg-gradient-to-r from-[#C9A84C] to-[#E4C063] text-[#1A1A2E] text-xs font-semibold px-5 py-2 rounded-lg hover:opacity-90 disabled:opacity-50 transition-all shadow"
        >
          {isPending ? "Submitting…" : "Submit Sales Data →"}
        </button>
      </div>
    </div>
  );
}

function SuccessPanel({ ui }: { ui: SubmissionSuccessUI }) {
  return (
    <div className="flex flex-col items-center justify-center h-full gap-4 text-center px-8">
      <div className="w-16 h-16 rounded-full bg-emerald-400/15 border border-emerald-400/30 flex items-center justify-center">
        <svg className="w-8 h-8 text-emerald-300" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
        </svg>
      </div>
      <div>
        <h3 className="text-base font-semibold text-emerald-300 mb-1">Submission Successful</h3>
        <p className="text-sm text-white/55">
          {ui.row_count} record{ui.row_count !== 1 ? "s" : ""} submitted successfully.
        </p>
        <p className="text-xs text-white/35 mt-2">
          The Sales Data team has been notified and will review your submission.
        </p>
      </div>
      <StatusBadge status="SUBMITTED" />
    </div>
  );
}

function ErrorPanel({ ui }: { ui: SubmissionErrorUI }) {
  return (
    <div className="flex flex-col items-center justify-center h-full gap-4 text-center px-8">
      <div className="w-16 h-16 rounded-full bg-red-400/15 border border-red-400/30 flex items-center justify-center">
        <svg className="w-8 h-8 text-red-300" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
            d="M12 9v2m0 4h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z" />
        </svg>
      </div>
      <div>
        <h3 className="text-base font-semibold text-red-300 mb-1">Submission Failed</h3>
        <p className="text-xs text-white/50 mb-3">
          {ui.error_type === "stale_state"
            ? "This record is no longer pending. It may have been submitted already."
            : "One or more rows had validation errors. Please fix them and re-upload."}
        </p>
        {ui.errors.slice(0, 3).map((e, i) => (
          <p key={i} className="text-xs text-red-200 bg-red-400/10 border border-red-400/20 rounded px-2 py-1 mb-1">
            {e.message}
          </p>
        ))}
      </div>
    </div>
  );
}

function StatusTablePanel({ ui }: { ui: StatusSummaryUI }) {
  return (
    <div className="flex flex-col h-full">
      <div className="px-5 py-3 border-b border-white/10">
        <p className="text-sm font-semibold text-white">My Sales Data</p>
        <div className="flex gap-2 mt-2 flex-wrap">
          {Object.entries(ui.grouped).map(([status, count]) => (
            <span key={status} className="text-xs text-white/50">
              <StatusBadge status={status} /> <span className="ml-1">{count}</span>
            </span>
          ))}
        </div>
      </div>
      <div className="flex-1 overflow-auto px-4 py-3">
        <table className="w-full text-xs border-separate border-spacing-0">
          <thead>
            <tr>
              {["Lease", "Property", "Company", "Brand", "Period", "Status", "Amount"].map((h) => (
                <th key={h} className="text-left font-semibold text-white/40 pb-2 pr-3">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {ui.rows.map((r, i) => (
              <tr key={i} className="hover:bg-white/5">
                <td className="py-2 pr-3 font-mono font-semibold text-white">{r.lease_code}</td>
                <td className="py-2 pr-3 text-white/55">{salesDataProperty(r)}</td>
                <td className="py-2 pr-3 text-white/55">{salesDataCompany(r)}</td>
                <td className="py-2 pr-3 text-white/55">{salesDataBrand(r)}</td>
                <td className="py-2 pr-3 text-white/45 whitespace-nowrap">
                  {r.sales_period_start?.slice(0, 7)}
                </td>
                <td className="py-2 pr-3"><StatusBadge status={r.status} /></td>
                <td className="py-2 text-white/70">
                  {r.sales_amt != null ? `SAR ${Number(r.sales_amt).toLocaleString()}` : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function ManualConfirmPanel({ ui }: { ui: ManualEntryConfirmUI }) {
  return (
    <div className="flex flex-col items-center justify-center h-full gap-4 px-8">
      <div className="w-full max-w-xs bg-[#C9A84C]/10 border border-[#C9A84C]/30 rounded-xl p-5 space-y-3">
        <p className="text-sm font-semibold text-white mb-2">Manual Entry Preview</p>
        {[
          ["Lease Code", ui.lease_code],
          ["Brand", ui.brand || "—"],
          ["Property", ui.property || "—"],
          ["Period Start", ui.sales_period_start],
          ["Period End", ui.sales_period_end],
          ["Sales Amount", `SAR ${Number(ui.sales_amt).toLocaleString()}`],
        ].map(([label, value]) => (
          <div key={label} className="flex justify-between text-xs">
            <span className="text-white/45">{label}</span>
            <span className="font-semibold text-white">{value}</span>
          </div>
        ))}
      </div>
      <p className="text-xs text-white/35">Confirm in the chat panel →</p>
    </div>
  );
}

function PeriodMismatchPanel({ ui }: { ui: PeriodMismatchWarningUI }) {
  const { onPanelMessage, isPending } = useSalesDataSession();
  const extraOk = Boolean(ui.includes_asked_month);
  return (
    <div className="flex flex-col items-center justify-center h-full gap-4 text-center px-8">
      <div className="w-16 h-16 rounded-2xl bg-amber-400/10 border-2 border-dashed border-amber-400/50 flex items-center justify-center text-3xl">
        {extraOk ? "📅" : "⚠️"}
      </div>
      <div>
        <h3 className="text-base font-semibold text-white mb-1">
          {extraOk ? "Extra months in this file" : "Month does not match"}
        </h3>
        <p className="text-sm text-white/55 max-w-xs">
          {extraOk ? (
            <>
              This file includes{" "}
              <span className="font-semibold text-white">{ui.asked_label}</span> and
              other months ({ui.file_label}). Extra PENDING months can go in the same
              sheet.
            </>
          ) : (
            <>
              This file has <span className="font-semibold text-white">{ui.file_label}</span>{" "}
              data. You asked to upload{" "}
              <span className="font-semibold text-white">{ui.asked_label}</span>.
            </>
          )}
        </p>
      </div>
      <div className="flex flex-wrap justify-center gap-2">
        <button
          type="button"
          disabled={isPending || !onPanelMessage}
          onClick={() => onPanelMessage?.("No — I'll upload a different file.")}
          className="px-3 py-1.5 text-xs border border-white/20 text-white/80 rounded-lg hover:bg-white/10 disabled:opacity-50"
        >
          Upload a different file
        </button>
        <button
          type="button"
          disabled={isPending || !onPanelMessage}
          onClick={() => onPanelMessage?.("Yes, proceed with this file.")}
          className="px-3 py-1.5 text-xs bg-amber-500 text-[#1A1A2E] rounded-lg hover:bg-amber-400 disabled:opacity-50 font-medium"
        >
          {extraOk ? "Submit this file" : "Proceed anyway"}
        </button>
      </div>
      <p className="text-xs text-white/35">You can also drop another CSV in chat →</p>
    </div>
  );
}

function PendingMonthsPanel({ ui }: { ui: PendingMonthsListUI }) {
  const { onPanelMessage, isPending } = useSalesDataSession();
  return (
    <div className="flex flex-col h-full px-5 py-4 gap-4">
      <div>
        <p className="text-sm font-semibold text-white">Pending months</p>
        <p className="text-xs text-white/45 mt-1">
          Pick a month, or drop a CSV in chat. One file can cover several of these.
        </p>
      </div>
      <div className="space-y-3 overflow-auto">
        {ui.leases.map((lease) => (
          <div key={lease.lease_code}>
            <p className="text-xs font-mono font-semibold text-[#E4C063] mb-1.5">
              {lease.lease_code}
            </p>
            <div className="flex flex-wrap gap-1.5">
              {lease.months.map((month) => (
                <button
                  key={`${lease.lease_code}-${month.year_month}`}
                  type="button"
                  disabled={isPending || !onPanelMessage}
                  onClick={() => onPanelMessage?.(`${lease.lease_code} ${month.label}`)}
                  className="px-2 py-1 text-xs border border-white/20 text-white/80 rounded-lg hover:bg-white/10 disabled:opacity-50"
                >
                  {month.label}
                </button>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function OtherMonthsReminderPanel({ ui }: { ui: OtherMonthsReminderUI }) {
  const { onPanelMessage, isPending } = useSalesDataSession();
  const switchMonths = ui.other_months.slice(0, 4);
  const extraCount = ui.other_months.length - switchMonths.length;
  return (
    <div className="flex flex-col h-full px-5 py-4 gap-4 overflow-auto">
      <div>
        <h3 className="text-base font-semibold text-white mb-1">
          Other pending months
        </h3>
        <p className="text-sm text-white/55">
          <span className="font-mono font-semibold text-white">{ui.lease_code}</span>{" "}
          also has other PENDING months besides {ui.asked_label}. You can include them
          in the same CSV after you proceed.
        </p>
      </div>
      <ul className="text-xs text-white/70 space-y-1 max-h-40 overflow-auto">
        {ui.other_months.map((month) => (
          <li key={month.year_month}>• {month.label}</li>
        ))}
      </ul>
      <button
        type="button"
        disabled={isPending || !onPanelMessage}
        onClick={() => onPanelMessage?.(`Proceed with ${ui.asked_label}.`)}
        className="px-3 py-1.5 text-xs bg-amber-500 text-[#1A1A2E] rounded-lg hover:bg-amber-400 disabled:opacity-50 font-medium self-start"
      >
        Proceed with {ui.asked_label}
      </button>
      {ui.other_months.length > 0 && (
        <div>
          <p className="text-[11px] text-white/40 mb-1.5">Or start with a different month</p>
          <div className="flex flex-wrap gap-1.5">
            {switchMonths.map((month) => (
              <button
                key={month.year_month}
                type="button"
                disabled={isPending || !onPanelMessage}
                onClick={() => onPanelMessage?.(`Switch to ${month.label}.`)}
                className="px-2 py-1 text-xs border border-white/20 text-white/80 rounded-lg hover:bg-white/10 disabled:opacity-50"
              >
                {month.label}
              </button>
            ))}
            {extraCount > 0 && (
              <span className="px-2 py-1 text-[11px] text-white/40 self-center">
                and {extraCount} more — type the month
              </span>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function DocPromptPanel({ ui }: { ui: SupportingDocPromptUI }) {
  return (
    <div className="flex flex-col items-center justify-center h-full gap-4 text-center px-8">
      <div className="w-16 h-16 rounded-2xl bg-orange-400/10 border-2 border-dashed border-orange-400/50 flex items-center justify-center text-3xl">
        📎
      </div>
      <div>
        <h3 className="text-base font-semibold text-white mb-1">
          {ui.ask_label || "Upload file"}
        </h3>
        <p className="text-sm text-white/55">
          Attach a PDF or image for <span className="font-mono font-semibold text-white">{ui.lease_code}</span>
        </p>
        <p className="text-xs text-white/35 mt-1">
          {ui.sales_period_start} – {ui.sales_period_end}
        </p>
      </div>
      <StatusBadge status={ui.current_status} />
      <p className="text-xs text-white/35">Use the chat panel to attach the file →</p>
    </div>
  );
}

function DocConfirmPanel({ ui }: { ui: SupportingDocConfirmUI }) {
  return (
    <div className="flex flex-col items-center justify-center h-full gap-4 px-8">
      <div className="w-full max-w-xs bg-orange-400/10 border border-orange-400/25 rounded-xl p-5 space-y-3">
        <p className="text-sm font-semibold text-white mb-2">
          {ui.ask_label || "Document"} preview
        </p>
        {[
          ["Lease", ui.lease_code],
          ["Period", `${ui.sales_period_start} – ${ui.sales_period_end}`],
          ["File", ui.filename],
          ["Amount", ui.sales_amt != null ? `SAR ${Number(ui.sales_amt).toLocaleString()}` : "—"],
          ["New status", ui.target_status.replace(/_/g, " ")],
        ].map(([label, value]) => (
          <div key={label} className="flex justify-between text-xs">
            <span className="text-white/45">{label}</span>
            <span className="font-semibold text-white">{value}</span>
          </div>
        ))}
      </div>
      <p className="text-xs text-white/35">Confirm in the chat panel →</p>
    </div>
  );
}

function DocSuccessPanel({ ui }: { ui: SupportingDocSuccessUI }) {
  return (
    <div className="flex flex-col items-center justify-center h-full gap-4 text-center px-8">
      <div className="w-16 h-16 rounded-full bg-emerald-400/15 border border-emerald-400/30 flex items-center justify-center">
        <svg className="w-8 h-8 text-emerald-300" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
        </svg>
      </div>
      <div>
        <h3 className="text-base font-semibold text-emerald-300 mb-1">Document Submitted</h3>
        <p className="text-sm text-white/55">
          {ui.lease_code} is now {ui.status.replace(/_/g, " ")}.
        </p>
      </div>
      <StatusBadge status={ui.status} />
    </div>
  );
}

function DocSelectPanel({ ui }: { ui: SupportingDocSelectUI }) {
  return (
    <div className="flex flex-col h-full">
      <div className="px-5 py-3 border-b border-white/10">
        <p className="text-sm font-semibold text-white">Which file request?</p>
        <p className="text-xs text-white/40 mt-0.5">
          Sales data re-upload, supporting document, or audited sales
        </p>
      </div>
      <div className="flex-1 overflow-auto px-4 py-3 space-y-2">
        {ui.rows.map((r) => (
          <div
            key={`${r.lease_code}-${r.status}`}
            className="flex items-center justify-between gap-3 rounded-xl border border-white/10 bg-white/5 px-4 py-3"
          >
            <div>
              <p className="text-sm font-mono font-semibold text-white">{r.lease_code}</p>
              <p className="text-xs text-white/40 mt-0.5">
                {r.sales_period_start?.slice(0, 7)}
              </p>
            </div>
            <StatusBadge status={r.status} />
          </div>
        ))}
      </div>
      <p className="text-xs text-white/35 px-5 py-3 border-t border-white/10">
        Choose the lease in the chat panel →
      </p>
    </div>
  );
}

function AdminListPanel({ ui }: { ui: AdminSalesDataListUI }) {
  const { onPanelMessage, isPending } = useSalesDataSession();
  return (
    <div className="flex flex-col h-full">
      <div className="px-5 py-3 border-b border-white/10">
        <p className="text-sm font-semibold text-white">Pending Review</p>
        <p className="text-xs text-white/40 mt-0.5">
          {ui.rows.length} submission{ui.rows.length !== 1 ? "s" : ""} awaiting action
        </p>
      </div>
      <div className="flex-1 overflow-auto px-4 py-3">
        <table className="w-full text-xs border-separate border-spacing-0">
          <thead>
            <tr>
              {["Lease", "Property", "Company", "Brand", "Period", "Amount", "Status", ""].map((h) => (
                <th key={h || "actions"} className={`font-semibold text-white/40 pb-2 ${h === "Amount" ? "text-right pr-3" : "text-left pr-3"}`}>
                  {h || "Actions"}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {ui.rows.map((r, i) => (
              <tr key={i} className="hover:bg-white/5">
                <td className="py-2 pr-3 font-mono font-semibold text-white">{r.lease_code}</td>
                <td className="py-2 pr-3 text-white/55">{salesDataProperty(r)}</td>
                <td className="py-2 pr-3 text-white/55">{salesDataCompany(r)}</td>
                <td className="py-2 pr-3 text-white/55">{salesDataBrand(r)}</td>
                <td className="py-2 pr-3 text-white/45 whitespace-nowrap">
                  {r.sales_period_start?.slice(0, 7)}
                </td>
                <td className="py-2 pr-3 text-right text-white/80">
                  {r.sales_amt != null ? `SAR ${Number(r.sales_amt).toLocaleString()}` : "—"}
                </td>
                <td className="py-2 pr-3">
                  <SalesDataStatusBadge status={r.status} />
                </td>
                <td className="py-2 text-right">
                  <AdminRowMenu
                    leaseCode={r.lease_code}
                    documentId={r.document_id}
                    disabled={isPending || !onPanelMessage}
                    onAction={(action) =>
                      onPanelMessage?.(
                        adminActionMessage(r.lease_code, action, {
                          start: r.sales_period_start,
                          end: r.sales_period_end,
                        }),
                      )
                    }
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-xs text-white/35 px-5 py-3 border-t border-white/10">
        Use ⋮ on a row — Accept or Ask upload, same as the portal.
      </p>
    </div>
  );
}

function AdminConfirmPanel({ ui }: { ui: AdminConfirmUI }) {
  const targets = ui.targets?.length ? ui.targets : [{ lease_code: ui.lease_code }];
  return (
    <div className="flex flex-col items-center justify-center h-full gap-4 px-8">
      <div className="w-full max-w-sm bg-violet-400/10 border border-violet-400/25 rounded-xl p-5 space-y-3">
        <p className="text-sm font-semibold text-white">Confirm admin action</p>
        <div className="flex justify-between text-xs">
          <span className="text-white/45">Action</span>
          <span className="font-semibold text-violet-100">{ui.action_label || ui.action}</span>
        </div>
        {targets.map((t) => (
          <div key={`${t.lease_code}-${t.sales_period_start}`} className="flex justify-between text-xs">
            <span className="text-white/45">
              {t.lease_code}
              {t.sales_period_start ? ` (${String(t.sales_period_start).slice(0, 7)})` : ""}
            </span>
            <span className="font-semibold text-white">
              {t.sales_amt != null ? `SAR ${Number(t.sales_amt).toLocaleString()}` : "—"}
            </span>
          </div>
        ))}
        {ui.comment_text && (
          <p className="text-xs text-white/55 pt-1 border-t border-white/10">
            Comment: {ui.comment_text}
          </p>
        )}
      </div>
      <p className="text-xs text-white/35">Confirm in the chat panel →</p>
    </div>
  );
}

function AdminSuccessPanel({ ui }: { ui: AdminActionSuccessUI }) {
  const codes = ui.lease_codes?.length ? ui.lease_codes : [ui.lease_code];
  return (
    <div className="flex flex-col items-center justify-center h-full gap-4 text-center px-8">
      <div className="w-16 h-16 rounded-full bg-emerald-400/15 border border-emerald-400/30 flex items-center justify-center">
        <svg className="w-8 h-8 text-emerald-300" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
        </svg>
      </div>
      <div>
        <h3 className="text-base font-semibold text-emerald-300 mb-1">
          {ui.action_label || ui.action}
        </h3>
        <p className="text-sm text-white/55">
          {codes.filter(Boolean).join(", ")}
        </p>
      </div>
    </div>
  );
}

// ── Main component ──────────────────────────────────────────────────────────

export function SalesDataLeftPanel() {
  const { latestUI, reviewRows, setReviewRows, workflowStage, isPending, onSubmitFromPanel } =
    useSalesDataSession();

  const uiType = latestUI?.type ?? "idle";

  function renderContent() {
    if (uiType === "period_mismatch_warning") {
      return <PeriodMismatchPanel ui={latestUI as PeriodMismatchWarningUI} />;
    }
    if (uiType === "pending_months_list") {
      return <PendingMonthsPanel ui={latestUI as PendingMonthsListUI} />;
    }
    if (uiType === "other_months_reminder") {
      return <OtherMonthsReminderPanel ui={latestUI as OtherMonthsReminderUI} />;
    }
    if (uiType === "sales_data_review") {
      return (
        <ReviewTablePanel
          ui={latestUI as SalesDataReviewUI}
          rows={reviewRows}
          onRowsChange={setReviewRows}
          onSubmit={onSubmitFromPanel ?? (() => {})}
          isPending={isPending}
        />
      );
    }
    if (uiType === "submission_success") {
      return <SuccessPanel ui={latestUI as SubmissionSuccessUI} />;
    }
    if (uiType === "submission_error") {
      return <ErrorPanel ui={latestUI as SubmissionErrorUI} />;
    }
    if (uiType === "status_summary") {
      return <StatusTablePanel ui={latestUI as StatusSummaryUI} />;
    }
    if (uiType === "manual_entry_confirm") {
      return <ManualConfirmPanel ui={latestUI as ManualEntryConfirmUI} />;
    }
    if (uiType === "supporting_doc_prompt") {
      return <DocPromptPanel ui={latestUI as SupportingDocPromptUI} />;
    }
    if (uiType === "supporting_doc_select") {
      return <DocSelectPanel ui={latestUI as SupportingDocSelectUI} />;
    }
    if (uiType === "supporting_doc_confirm") {
      return <DocConfirmPanel ui={latestUI as SupportingDocConfirmUI} />;
    }
    if (uiType === "supporting_doc_success") {
      return <DocSuccessPanel ui={latestUI as SupportingDocSuccessUI} />;
    }
    if (uiType === "admin_sales_data_list") {
      return <AdminListPanel ui={latestUI as AdminSalesDataListUI} />;
    }
    if (uiType === "admin_confirm") {
      return <AdminConfirmPanel ui={latestUI as AdminConfirmUI} />;
    }
    if (uiType === "admin_action_success" || uiType === "admin_action_result") {
      return <AdminSuccessPanel ui={latestUI as AdminActionSuccessUI} />;
    }
    return <IdlePanel />;
  }

  const stageLabel =
    workflowStage
      ? workflowStage.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())
      : "Waiting";

  return (
    <div className="flex flex-col h-full glass-strong rounded-2xl overflow-hidden relative">
      {/* Platform-style topbar */}
      <div className="flex items-center gap-3 px-5 py-3 border-b border-white/10 flex-shrink-0">
        <div className="flex items-center gap-2">
          <span className="text-lg">📊</span>
          <div>
            <p className="text-xs font-semibold tracking-wide text-white">Cenomi Platform</p>
            <p className="text-[10px] text-white/40">Sales Data Upload</p>
          </div>
        </div>
        <div className="ml-auto flex items-center gap-2">
          {isPending && (
            <div className="w-4 h-4 border-2 border-[#C9A84C] border-t-transparent rounded-full animate-spin" />
          )}
          <span
            className={`text-[10px] px-2 py-0.5 rounded-full font-medium ${
              workflowStage
                ? "bg-[#C9A84C]/20 text-[#E4C063] border border-[#C9A84C]/30"
                : "bg-white/10 text-white/50 border border-white/10"
            }`}
          >
            {stageLabel}
          </span>
        </div>
      </div>

      {/* Breadcrumb bar */}
      <div className="flex items-center gap-1 px-5 py-2 border-b border-white/10 text-[10px] text-white/35 flex-shrink-0">
        <span>Dashboard</span>
        <span>›</span>
        <span>Operations</span>
        <span>›</span>
        <span className="text-[#C9A84C] font-medium">Sales Data</span>
      </div>

      {/* Content area */}
      <div className="flex-1 overflow-hidden relative glass-canvas m-2 rounded-xl">
        {isPending && uiType !== "sales_data_review" && <ProcessingOverlay />}
        {renderContent()}
      </div>
    </div>
  );
}
