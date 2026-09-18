"use client";

import { useState } from "react";
import type { SupportingDocConfirmUI } from "@/lib/types/chat";

interface SupportingDocConfirmCardProps {
  ui: SupportingDocConfirmUI;
  onConfirm: (comment: string, salesAmt: number | null) => void;
  onCancel: () => void;
  disabled?: boolean;
}

const ASK_TITLES: Record<string, string> = {
  NEED_REUPLOAD: "Sales data",
  NEED_UPLOAD_SUPPORTING_DOCUMENT: "Supporting document",
  NEED_UPLOAD_AUDITED_SALES: "Audited sales data",
};

export function SupportingDocConfirmCard({
  ui,
  onConfirm,
  onCancel,
  disabled = false,
}: SupportingDocConfirmCardProps) {
  const [comment, setComment] = useState(ui.comment_text ?? "");
  const [amount, setAmount] = useState(
    ui.sales_amt != null && ui.sales_amt !== undefined ? String(ui.sales_amt) : "",
  );
  const [commentError, setCommentError] = useState(false);
  const [amountError, setAmountError] = useState(false);

  const askLabel = ui.ask_label || ASK_TITLES[ui.current_status] || "Document";
  const commentOk = comment.trim().length > 0;
  const parsedAmt = amount.trim() === "" ? null : Number(amount);
  const amountOk = parsedAmt === null || (Number.isFinite(parsedAmt) && parsedAmt > 0);

  function handleSubmit() {
    const missingComment = !comment.trim();
    const badAmt = amount.trim() !== "" && (!Number.isFinite(Number(amount)) || Number(amount) <= 0);
    setCommentError(missingComment);
    setAmountError(badAmt);
    if (missingComment || badAmt) return;
    onConfirm(comment.trim(), parsedAmt);
  }

  return (
    <div className="glass-light rounded-2xl overflow-hidden my-2">
      <div className="bg-gradient-to-r from-orange-50 to-amber-50 border-b border-gray-200 px-4 py-3">
        <h3 className="text-sm font-semibold text-gray-800">
          Confirm {askLabel}
        </h3>
        <p className="text-xs text-gray-500 mt-0.5">
          File, required comment, and amount — same as the portal Upload File modal
        </p>
      </div>

      <div className="p-4">
        <table className="w-full text-sm">
          <tbody>
            {[
              { label: "Lease Code", value: ui.lease_code },
              { label: "Period", value: `${ui.sales_period_start} – ${ui.sales_period_end}` },
              { label: "File", value: ui.filename },
              { label: "Ask type", value: askLabel },
              { label: "Current status", value: ui.current_status.replace(/_/g, " ") },
              { label: "New status", value: ui.target_status.replace(/_/g, " ") },
            ].map(({ label, value }) => (
              <tr key={label} className="border-t border-gray-100 first:border-t-0">
                <td className="py-2 pr-4 text-gray-500 text-xs font-medium w-1/3">
                  {label}
                </td>
                <td className="py-2 font-semibold text-gray-900">{value}</td>
              </tr>
            ))}
          </tbody>
        </table>

        <label className="block mt-3">
          <span className="text-xs font-medium text-gray-500">
            Sales amount (SAR)
          </span>
          <input
            type="number"
            min={0}
            step="0.01"
            value={amount}
            onChange={(e) => {
              setAmount(e.target.value);
              setAmountError(false);
            }}
            disabled={disabled}
            placeholder="Keep current amount or enter a new figure"
            className={`mt-1 w-full text-sm border rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-[#C9A84C]/40 disabled:bg-gray-50 ${
              amountError ? "border-red-400" : "border-gray-300"
            }`}
          />
          {amountError && (
            <p className="text-xs text-red-600 mt-1">Amount must be greater than 0.</p>
          )}
        </label>

        <label className="block mt-3">
          <span className="text-xs font-medium text-gray-500">
            Comment <span className="text-red-600">*</span>
          </span>
          <textarea
            value={comment}
            onChange={(e) => {
              setComment(e.target.value);
              setCommentError(false);
            }}
            disabled={disabled}
            rows={2}
            placeholder="Required — e.g. POS report attached"
            className={`mt-1 w-full text-sm border rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-[#C9A84C]/40 disabled:bg-gray-50 ${
              commentError ? "border-red-400" : "border-gray-300"
            }`}
          />
          {commentError && (
            <p className="text-xs text-red-600 mt-1">Comment is required.</p>
          )}
        </label>
      </div>

      <div className="border-t border-gray-200 px-4 py-3 bg-gray-50 flex justify-end gap-2">
        <button
          onClick={onCancel}
          disabled={disabled}
          className="px-4 py-1.5 text-sm text-gray-600 border border-gray-300 rounded-lg hover:bg-gray-100 disabled:opacity-50 transition-colors"
        >
          Cancel
        </button>
        <button
          onClick={handleSubmit}
          disabled={disabled || !commentOk || !amountOk}
          className="px-4 py-1.5 text-sm font-semibold bg-gradient-to-r from-[#C9A84C] to-[#E4C063] text-white rounded-lg hover:opacity-90 disabled:opacity-50 transition-all"
        >
          Confirm &amp; Submit
        </button>
      </div>
    </div>
  );
}
