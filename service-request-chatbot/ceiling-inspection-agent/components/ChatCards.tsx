"use client";

import { useRef } from "react";
import type { ChipOption, DocumentRow, ResponseUI } from "@/lib/types";

const chipClass =
  "px-3 py-1.5 rounded-full text-xs border border-[#C9A84C]/50 text-[#E4C063] hover:bg-[#C9A84C]/15 disabled:opacity-40 disabled:cursor-not-allowed";

export function Chips({
  options,
  disabled,
  onPick,
}: {
  options: ChipOption[];
  disabled: boolean;
  onPick: (option: ChipOption) => void;
}) {
  if (!options.length) return null;
  return (
    <div className="flex flex-wrap gap-2 mt-2">
      {options.map((option) => (
        <button
          key={`${option.action ?? ""}:${option.label}`}
          className={chipClass}
          disabled={disabled}
          onClick={() => onPick(option)}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

export function ConfirmationCard({
  ui,
  disabled,
  onConfirm,
  onDecline,
  onChip,
}: {
  ui: ResponseUI;
  disabled: boolean;
  onConfirm: () => void;
  onDecline: () => void;
  onChip: (option: ChipOption) => void;
}) {
  return (
    <div className="glass-light rounded-2xl p-4 mt-2 text-slate-800">
      <div className="text-xs uppercase tracking-wide text-slate-500">{ui.stage}</div>
      <div className="font-semibold mb-3">{ui.requestType}</div>
      <dl className="grid grid-cols-[auto,1fr] gap-x-4 gap-y-1.5 text-sm">
        {(ui.fields ?? []).map((field) => (
          <div key={field.key} className="contents">
            <dt className="text-slate-500">{field.label}</dt>
            <dd className="font-medium break-words">{field.value || "—"}</dd>
          </div>
        ))}
      </dl>
      <div className="flex gap-2 mt-4">
        <button
          className="px-4 py-2 rounded-lg bg-[#C9A84C] text-[#1A1A2E] text-sm font-semibold disabled:opacity-40"
          disabled={disabled}
          onClick={onConfirm}
        >
          {ui.confirmLabel ?? "Confirm & Submit"}
        </button>
        <button
          className="px-4 py-2 rounded-lg border border-slate-300 text-sm disabled:opacity-40"
          disabled={disabled}
          onClick={onDecline}
        >
          Change something
        </button>
      </div>
      {ui.suggestions?.options?.length ? (
        <Chips options={ui.suggestions.options} disabled={disabled} onPick={onChip} />
      ) : null}
    </div>
  );
}

function UploadButton({
  row,
  disabled,
  onFile,
}: {
  row: DocumentRow;
  disabled: boolean;
  onFile: (file: File) => void;
}) {
  const input = useRef<HTMLInputElement>(null);
  return (
    <>
      <input
        ref={input}
        type="file"
        accept="application/pdf,image/jpeg,image/png"
        className="hidden"
        onChange={(event) => {
          const file = event.target.files?.[0];
          if (file) onFile(file);
          event.target.value = "";
        }}
      />
      <button
        className="px-3 py-1 rounded-md bg-[#1A1A2E] text-white text-xs disabled:opacity-40"
        disabled={disabled}
        onClick={() => input.current?.click()}
      >
        Upload
      </button>
    </>
  );
}

export function DocumentCard({
  ui,
  disabled,
  onUpload,
}: {
  ui: ResponseUI;
  disabled: boolean;
  onUpload: (row: DocumentRow, file: File) => void;
}) {
  return (
    <div className="glass-light rounded-2xl p-4 mt-2 text-slate-800">
      <div className="font-semibold text-sm mb-2">Documents</div>
      <ul className="space-y-2">
        {(ui.documents ?? []).map((row) => (
          <li key={row.key} className="flex items-center justify-between gap-3 text-sm">
            <span>
              {row.label}
              {row.required && <span className="text-red-500"> *</span>}
              {row.file_name && (
                <span className="block text-xs text-slate-500">{row.file_name}</span>
              )}
            </span>
            {row.status === "uploaded" ? (
              <span className="text-xs text-emerald-600 font-medium">Uploaded ✓</span>
            ) : (
              <UploadButton row={row} disabled={disabled} onFile={(file) => onUpload(row, file)} />
            )}
          </li>
        ))}
      </ul>
      <p className="text-xs text-slate-500 mt-3">PDF, JPEG or PNG.</p>
    </div>
  );
}
