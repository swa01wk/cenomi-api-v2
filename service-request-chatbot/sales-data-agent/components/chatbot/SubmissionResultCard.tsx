"use client";

import type { SubmissionSuccessUI, SubmissionErrorUI } from "@/lib/types/chat";

interface SubmissionSuccessCardProps {
  ui: SubmissionSuccessUI;
}

export function SubmissionSuccessCard({ ui }: SubmissionSuccessCardProps) {
  return (
    <div className="bg-green-50 border border-green-200 rounded-2xl p-4 my-2">
      <div className="flex items-center gap-3 mb-3">
        <div className="w-10 h-10 bg-green-100 rounded-full flex items-center justify-center text-xl">
          🎉
        </div>
        <div>
          <h3 className="text-sm font-semibold text-green-800">
            Submission Successful!
          </h3>
          <p className="text-xs text-green-600">
            {ui.row_count} row{ui.row_count !== 1 ? "s" : ""} submitted
          </p>
        </div>
      </div>
      <p className="text-xs text-green-700 leading-relaxed">
        The Sales Data Team has been notified. You can track your submission
        status on the Sales Data listing page.
      </p>
    </div>
  );
}

interface SubmissionErrorCardProps {
  ui: SubmissionErrorUI;
  onReupload?: () => void;
}

export function SubmissionErrorCard({ ui, onReupload }: SubmissionErrorCardProps) {
  return (
    <div className="bg-red-50 border border-red-200 rounded-2xl p-4 my-2">
      <div className="flex items-center gap-3 mb-3">
        <div className="w-10 h-10 bg-red-100 rounded-full flex items-center justify-center text-xl">
          ❌
        </div>
        <div>
          <h3 className="text-sm font-semibold text-red-800">
            Submission Failed
          </h3>
          <p className="text-xs text-red-600">
            {ui.error_type === "network"
              ? "Network error"
              : `${ui.errors.length} validation error${ui.errors.length !== 1 ? "s" : ""}`}
          </p>
        </div>
      </div>

      {ui.errors.length > 0 && ui.error_type !== "network" && (
        <ul className="text-xs text-red-700 space-y-1 mb-3">
          {ui.errors.map((e, i) => (
            <li key={i} className="flex items-start gap-1.5">
              <span className="text-red-400">•</span>
              {e.index !== undefined ? `Row ${e.index + 1}: ` : ""}
              {e.message}
            </li>
          ))}
        </ul>
      )}

      <p className="text-xs text-red-600 mb-3">
        None of your rows were saved (all-or-nothing transaction).
      </p>

      {ui.show_reupload && onReupload && (
        <button
          onClick={onReupload}
          className="text-xs px-3 py-1.5 bg-red-100 text-red-700 rounded-lg hover:bg-red-200 transition-colors"
        >
          Try Again
        </button>
      )}
    </div>
  );
}
