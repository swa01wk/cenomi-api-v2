"use client";

import { useRef, useState } from "react";

interface FileUploadButtonProps {
  onFile: (file: File) => void;
  disabled?: boolean;
  uploading?: boolean;
}

export function FileUploadButton({
  onFile,
  disabled = false,
  uploading = false,
}: FileUploadButtonProps) {
  const fileRef = useRef<HTMLInputElement>(null);
  const [dragOver, setDragOver] = useState(false);

  function handleChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (file) {
      onFile(file);
      e.target.value = "";
    }
  }

  function handleDrop(e: React.DragEvent) {
    e.preventDefault();
    setDragOver(false);
    const file = e.dataTransfer.files[0];
    if (file?.name.endsWith(".csv")) onFile(file);
  }

  return (
    <div
      onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
      onDragLeave={() => setDragOver(false)}
      onDrop={handleDrop}
      className={`border-2 border-dashed rounded-xl p-6 text-center transition-all cursor-pointer ${
        dragOver ? "border-[#C9A84C] bg-[#FFF9EE]" : "border-gray-300 hover:border-[#C9A84C]/50"
      } ${disabled ? "opacity-50 pointer-events-none" : ""}`}
      onClick={() => !disabled && fileRef.current?.click()}
    >
      <input
        ref={fileRef}
        type="file"
        accept=".csv"
        className="hidden"
        onChange={handleChange}
        disabled={disabled}
      />
      <div className="text-3xl mb-2">{uploading ? "⏳" : "📁"}</div>
      <div className="text-sm font-medium text-gray-700">
        {uploading ? "Uploading…" : "Drop CSV here or click to browse"}
      </div>
      <div className="text-xs text-gray-400 mt-1">
        Max 5 MB · .csv files only
      </div>
    </div>
  );
}
