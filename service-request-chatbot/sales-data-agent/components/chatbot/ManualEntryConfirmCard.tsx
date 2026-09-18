"use client";

import type { ManualEntryConfirmUI } from "@/lib/types/chat";

interface ManualEntryConfirmCardProps {
  ui: ManualEntryConfirmUI;
  onConfirm: () => void;
  onCancel: () => void;
  disabled?: boolean;
}

export function ManualEntryConfirmCard({
  ui,
  onConfirm,
  onCancel,
  disabled = false,
}: ManualEntryConfirmCardProps) {
  const amtDisplay = ui.sales_amt
    ? `SAR ${Number(ui.sales_amt).toLocaleString("en-US", { minimumFractionDigits: 2 })}`
    : "—";

  return (
    <div className="glass-light rounded-2xl overflow-hidden my-2">
      <div className="bg-gradient-to-r from-amber-50 to-yellow-50 border-b border-gray-200 px-4 py-3">
        <h3 className="text-sm font-semibold text-gray-800">
          ✏️ Confirm Manual Entry
        </h3>
        <p className="text-xs text-gray-500 mt-0.5">
          Please review before submitting
        </p>
      </div>

      <div className="p-4">
        <table className="w-full text-sm">
          <tbody>
            {[
              { label: "Lease Code", value: ui.lease_code },
              { label: "Brand", value: ui.brand || "—" },
              { label: "Property", value: ui.property || "—" },
              {
                label: "Period Start",
                value: ui.sales_period_start,
              },
              {
                label: "Period End",
                value: ui.sales_period_end,
              },
              { label: "Sales Amount", value: amtDisplay },
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
          onClick={onConfirm}
          disabled={disabled}
          className="px-4 py-1.5 text-sm font-semibold bg-gradient-to-r from-[#C9A84C] to-[#E4C063] text-white rounded-lg hover:opacity-90 disabled:opacity-50 transition-all"
        >
          Confirm &amp; Submit
        </button>
      </div>
    </div>
  );
}
