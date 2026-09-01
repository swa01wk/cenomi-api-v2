"use client";

import { useState } from "react";
import type { SalesDataReviewRow } from "@/lib/types/chat";

interface SalesDataReviewCardProps {
  rows: SalesDataReviewRow[];
  filename: string;
  onConfirm: (updatedRows: SalesDataReviewRow[]) => void;
  onCancel: () => void;
  disabled?: boolean;
}

const STATUS_BADGE: Record<string, { label: string; cls: string }> = {
  PENDING:       { label: "Pending",       cls: "bg-yellow-100 text-yellow-700" },
  NEED_REUPLOAD: { label: "Needs Re-upload", cls: "bg-orange-100 text-orange-700" },
};

export function SalesDataReviewCard({
  rows,
  filename,
  onConfirm,
  onCancel,
  disabled = false,
}: SalesDataReviewCardProps) {
  const [editedRows, setEditedRows] = useState<SalesDataReviewRow[]>(rows);
  const [errors, setErrors] = useState<Record<number, string>>({});

  function handleAmtChange(idx: number, value: string) {
    const updated = editedRows.map((r, i) =>
      i === idx ? { ...r, sales_amt: parseFloat(value) || 0 } : r,
    );
    setEditedRows(updated);

    // Inline validation
    const errs = { ...errors };
    const amt = parseFloat(value);
    if (isNaN(amt) || amt <= 0) {
      errs[idx] = "Amount must be greater than 0";
    } else {
      delete errs[idx];
    }
    setErrors(errs);
  }

  function handleSubmit() {
    // Final validation
    const errs: Record<number, string> = {};
    editedRows.forEach((r, i) => {
      if (!r.sales_amt || r.sales_amt <= 0) {
        errs[i] = "Amount must be greater than 0";
      }
    });
    setErrors(errs);
    if (Object.keys(errs).length > 0) return;
    onConfirm(editedRows);
  }

  return (
    <div className="glass-light rounded-2xl overflow-hidden my-2">
      {/* Header */}
      <div className="bg-gradient-to-r from-blue-50 to-indigo-50 border-b border-gray-200 px-4 py-3 flex items-center justify-between">
        <div>
          <h3 className="text-sm font-semibold text-gray-800">
            📋 Review Sales Data
          </h3>
          <p className="text-xs text-gray-500 mt-0.5">
            {filename} · {rows.length} row{rows.length !== 1 ? "s" : ""}
          </p>
        </div>
        <span className="text-xs bg-blue-100 text-blue-700 px-2 py-1 rounded-full font-medium">
          Edit amounts before submitting
        </span>
      </div>

      {/* Table */}
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="bg-gray-50 text-xs text-gray-500 uppercase tracking-wide">
              <th className="text-left px-4 py-2 font-medium">Lease</th>
              <th className="text-left px-4 py-2 font-medium">Brand</th>
              <th className="text-left px-4 py-2 font-medium">Property</th>
              <th className="text-left px-4 py-2 font-medium">Period</th>
              <th className="text-left px-4 py-2 font-medium">Status</th>
              <th className="text-right px-4 py-2 font-medium">Sales Amt (SAR)</th>
            </tr>
          </thead>
          <tbody>
            {editedRows.map((row, idx) => {
              const badge = STATUS_BADGE[row.current_status] ?? {
                label: row.current_status,
                cls: "bg-gray-100 text-gray-600",
              };
              return (
                <tr
                  key={idx}
                  className={`border-t border-gray-100 ${
                    errors[idx] ? "bg-red-50" : "hover:bg-gray-50"
                  }`}
                >
                  <td className="px-4 py-3 font-medium text-gray-900">
                    {row.lease_code}
                  </td>
                  <td className="px-4 py-3 text-gray-600">
                    {row.brand || "—"}
                  </td>
                  <td className="px-4 py-3 text-gray-600">
                    {row.property || "—"}
                  </td>
                  <td className="px-4 py-3 text-gray-600 whitespace-nowrap">
                    {row.period}
                  </td>
                  <td className="px-4 py-3">
                    <span className={`text-xs px-2 py-1 rounded-full font-medium ${badge.cls}`}>
                      {badge.label}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex flex-col items-end">
                      <input
                        type="number"
                        min="0.01"
                        step="0.01"
                        value={row.sales_amt || ""}
                        onChange={(e) => handleAmtChange(idx, e.target.value)}
                        disabled={disabled}
                        className={`w-36 text-right border rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-[#C9A84C]/40 focus:border-[#C9A84C] ${
                          errors[idx]
                            ? "border-red-400 bg-red-50"
                            : "border-gray-300"
                        } disabled:opacity-50`}
                        placeholder="Enter amount"
                      />
                      {errors[idx] && (
                        <span className="text-xs text-red-500 mt-1">
                          {errors[idx]}
                        </span>
                      )}
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {/* Footer */}
      <div className="border-t border-gray-200 px-4 py-3 bg-gray-50 flex items-center justify-between">
        <p className="text-xs text-gray-400">
          ⚠️ All rows must be valid — submission is all-or-nothing
        </p>
        <div className="flex gap-2">
          <button
            onClick={onCancel}
            disabled={disabled}
            className="px-4 py-1.5 text-sm text-gray-600 border border-gray-300 rounded-lg hover:bg-gray-100 disabled:opacity-50 transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={handleSubmit}
            disabled={disabled || Object.keys(errors).length > 0}
            className="px-4 py-1.5 text-sm font-semibold bg-gradient-to-r from-[#C9A84C] to-[#E4C063] text-white rounded-lg hover:opacity-90 disabled:opacity-50 transition-all"
          >
            Submit Sales Data
          </button>
        </div>
      </div>
    </div>
  );
}
