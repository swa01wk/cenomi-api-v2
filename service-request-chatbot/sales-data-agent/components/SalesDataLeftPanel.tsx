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

import { useState, useEffect } from "react";
import { useSalesDataSession } from "@/lib/context/SalesDataSessionContext";
import type {
  SalesDataReviewUI,
  SalesDataReviewRow,
  SubmissionSuccessUI,
  SubmissionErrorUI,
  StatusSummaryUI,
  ManualEntryConfirmUI,
} from "@/lib/types/chat";

const STATUS_COLORS: Record<string, string> = {
  PENDING: "bg-yellow-100 text-yellow-800",
  SUBMITTED: "bg-blue-100 text-blue-800",
  APPROVED: "bg-green-100 text-green-800",
  NEED_REUPLOAD: "bg-red-100 text-red-800",
  RE_UPLOADED: "bg-purple-100 text-purple-800",
};

function StatusBadge({ status }: { status: string }) {
  const cls = STATUS_COLORS[status] ?? "bg-gray-100 text-gray-700";
  return (
    <span className={`inline-block text-xs font-semibold px-2 py-0.5 rounded-full ${cls}`}>
      {status.replace(/_/g, " ")}
    </span>
  );
}

// ── Sub-panels ──────────────────────────────────────────────────────────────

function IdlePanel() {
  return (
    <div className="flex flex-col items-center justify-center h-full gap-6 text-center px-8">
      <div className="w-20 h-20 rounded-2xl bg-[#FFF9EE] border-2 border-dashed border-[#C9A84C] flex items-center justify-center">
        <svg className="w-9 h-9 text-[#C9A84C]" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
            d="M9 13h6m-3-3v6m5 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
        </svg>
      </div>
      <div>
        <h3 className="text-base font-semibold text-gray-700 mb-1">Sales Data Upload</h3>
        <p className="text-sm text-gray-400 max-w-xs">
          Use the AI assistant on the right to start uploading your monthly sales data.
          Actions will be mirrored here automatically.
        </p>
      </div>
      <div className="flex gap-2">
        {["PENDING", "SUBMITTED", "APPROVED"].map((s) => (
          <StatusBadge key={s} status={s} />
        ))}
      </div>
      <p className="text-xs text-gray-300">← Waiting for AI assistant</p>
    </div>
  );
}

function ProcessingOverlay() {
  return (
    <div className="absolute inset-0 bg-white/80 backdrop-blur-sm flex items-center justify-center z-10 rounded-xl">
      <div className="flex flex-col items-center gap-3">
        <div className="w-10 h-10 border-4 border-[#C9A84C] border-t-transparent rounded-full animate-spin" />
        <p className="text-sm text-gray-500 font-medium">Processing…</p>
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
      <div className="flex items-center justify-between px-5 py-3 border-b border-gray-100">
        <div>
          <p className="text-sm font-semibold text-gray-800">Review Sales Data</p>
          <p className="text-xs text-gray-400">{ui.filename} · {ui.row_count} row{ui.row_count !== 1 ? "s" : ""}</p>
        </div>
        <span className="text-xs font-semibold text-[#C9A84C] bg-[#FFF9EE] px-2 py-1 rounded-full">
          Editable
        </span>
      </div>

      {/* Table */}
      <div className="flex-1 overflow-auto px-4 py-3">
        <table className="w-full text-xs border-separate border-spacing-0">
          <thead>
            <tr>
              {["Lease", "Period", "Status", "Sales (SAR)"].map((h) => (
                <th key={h} className="text-left font-semibold text-gray-500 pb-2 pr-3">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-50">
            {rows.map((row, idx) => (
              <tr key={idx} className="hover:bg-gray-50 transition-colors">
                <td className="py-2 pr-3 font-mono font-semibold text-gray-800">{row.lease_code}</td>
                <td className="py-2 pr-3 text-gray-600 whitespace-nowrap">{row.period}</td>
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
                    className="w-28 border border-gray-200 rounded-lg px-2 py-1 text-xs focus:outline-none focus:ring-2 focus:ring-[#C9A84C] focus:border-transparent"
                    disabled={isPending}
                  />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* Footer */}
      <div className="border-t border-gray-100 px-5 py-3 flex items-center justify-between">
        {anyInvalid && (
          <p className="text-xs text-red-500">All sales amounts must be &gt; 0</p>
        )}
        <button
          onClick={() => onSubmit(rows)}
          disabled={isPending || anyInvalid}
          className="ml-auto bg-gradient-to-r from-[#C9A84C] to-[#E4C063] text-white text-xs font-semibold px-5 py-2 rounded-lg hover:opacity-90 disabled:opacity-50 transition-all shadow"
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
      <div className="w-16 h-16 rounded-full bg-green-100 flex items-center justify-center">
        <svg className="w-8 h-8 text-green-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
        </svg>
      </div>
      <div>
        <h3 className="text-base font-semibold text-green-700 mb-1">Submission Successful</h3>
        <p className="text-sm text-gray-500">
          {ui.row_count} record{ui.row_count !== 1 ? "s" : ""} submitted successfully.
        </p>
        <p className="text-xs text-gray-400 mt-2">
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
      <div className="w-16 h-16 rounded-full bg-red-100 flex items-center justify-center">
        <svg className="w-8 h-8 text-red-500" fill="none" viewBox="0 0 24 24" stroke="currentColor">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
            d="M12 9v2m0 4h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z" />
        </svg>
      </div>
      <div>
        <h3 className="text-base font-semibold text-red-600 mb-1">Submission Failed</h3>
        <p className="text-xs text-gray-500 mb-3">
          {ui.error_type === "stale_state"
            ? "This record is no longer pending. It may have been submitted already."
            : "One or more rows had validation errors. Please fix them and re-upload."}
        </p>
        {ui.errors.slice(0, 3).map((e, i) => (
          <p key={i} className="text-xs text-red-500 bg-red-50 rounded px-2 py-1 mb-1">
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
      <div className="px-5 py-3 border-b border-gray-100">
        <p className="text-sm font-semibold text-gray-800">My Sales Data</p>
        <div className="flex gap-2 mt-2 flex-wrap">
          {Object.entries(ui.grouped).map(([status, count]) => (
            <span key={status} className="text-xs text-gray-500">
              <StatusBadge status={status} /> <span className="ml-1">{count}</span>
            </span>
          ))}
        </div>
      </div>
      <div className="flex-1 overflow-auto px-4 py-3">
        <table className="w-full text-xs border-separate border-spacing-0">
          <thead>
            <tr>
              {["Lease", "Period", "Status", "Amount"].map((h) => (
                <th key={h} className="text-left font-semibold text-gray-500 pb-2 pr-3">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {ui.rows.map((r, i) => (
              <tr key={i} className="hover:bg-gray-50">
                <td className="py-2 pr-3 font-mono font-semibold text-gray-800">{r.lease_code}</td>
                <td className="py-2 pr-3 text-gray-500 whitespace-nowrap">
                  {r.sales_period_start?.slice(0, 7)}
                </td>
                <td className="py-2 pr-3"><StatusBadge status={r.status} /></td>
                <td className="py-2 text-gray-600">
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
      <div className="w-full max-w-xs bg-[#FFF9EE] border border-[#C9A84C]/30 rounded-xl p-5 space-y-3">
        <p className="text-sm font-semibold text-gray-800 mb-2">Manual Entry Preview</p>
        {[
          ["Lease Code", ui.lease_code],
          ["Period Start", ui.sales_period_start],
          ["Period End", ui.sales_period_end],
          ["Sales Amount", `SAR ${Number(ui.sales_amt).toLocaleString()}`],
        ].map(([label, value]) => (
          <div key={label} className="flex justify-between text-xs">
            <span className="text-gray-500">{label}</span>
            <span className="font-semibold text-gray-800">{value}</span>
          </div>
        ))}
      </div>
      <p className="text-xs text-gray-400">Confirm in the chat panel →</p>
    </div>
  );
}

// ── Main component ──────────────────────────────────────────────────────────

export function SalesDataLeftPanel() {
  const { latestUI, reviewRows, setReviewRows, workflowStage, isPending, onSubmitFromPanel } =
    useSalesDataSession();

  const uiType = latestUI?.type ?? "idle";

  function renderContent() {
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
    return <IdlePanel />;
  }

  const stageLabel =
    workflowStage
      ? workflowStage.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())
      : "Waiting";

  return (
    <div className="flex flex-col h-full bg-white rounded-xl border border-gray-200 shadow-sm overflow-hidden relative">
      {/* Platform-style topbar */}
      <div className="flex items-center gap-3 px-5 py-3 bg-[#1A1A2E] text-white flex-shrink-0">
        <div className="flex items-center gap-2">
          <span className="text-lg">📊</span>
          <div>
            <p className="text-xs font-bold tracking-wide">Cenomi Platform</p>
            <p className="text-[10px] text-white/50">Sales Data Upload</p>
          </div>
        </div>
        <div className="ml-auto flex items-center gap-2">
          {isPending && (
            <div className="w-4 h-4 border-2 border-[#C9A84C] border-t-transparent rounded-full animate-spin" />
          )}
          <span
            className={`text-[10px] px-2 py-0.5 rounded-full font-medium ${
              workflowStage
                ? "bg-[#C9A84C]/20 text-[#C9A84C]"
                : "bg-white/10 text-white/50"
            }`}
          >
            {stageLabel}
          </span>
        </div>
      </div>

      {/* Breadcrumb bar */}
      <div className="flex items-center gap-1 px-5 py-2 bg-gray-50 border-b border-gray-100 text-[10px] text-gray-400 flex-shrink-0">
        <span>Dashboard</span>
        <span>›</span>
        <span>Operations</span>
        <span>›</span>
        <span className="text-[#C9A84C] font-medium">Sales Data</span>
      </div>

      {/* Content area */}
      <div className="flex-1 overflow-hidden relative">
        {isPending && uiType !== "sales_data_review" && <ProcessingOverlay />}
        {renderContent()}
      </div>
    </div>
  );
}
