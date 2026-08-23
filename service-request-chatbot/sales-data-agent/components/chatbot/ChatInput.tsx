"use client";

import { useState, KeyboardEvent, useRef } from "react";

interface ChatInputProps {
  onSend: (text: string) => void;
  onFileUpload?: (file: File) => void;
  disabled?: boolean;
  placeholder?: string;
  showFileUpload?: boolean;
  onTemplateDownload?: () => void;
}

export function ChatInput({
  onSend,
  onFileUpload,
  disabled = false,
  placeholder = "Type a message…",
  showFileUpload = false,
  onTemplateDownload,
}: ChatInputProps) {
  const [text, setText] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  function handleKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  }

  function handleSend() {
    const trimmed = text.trim();
    if (!trimmed || disabled) return;
    onSend(trimmed);
    setText("");
  }

  function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (file && onFileUpload) {
      onFileUpload(file);
      e.target.value = "";
    }
  }

  return (
    <div className="border-t border-gray-200 bg-white p-4">
      {/* Toolbar */}
      {(showFileUpload || onTemplateDownload) && (
        <div className="flex gap-2 mb-2">
          {showFileUpload && onFileUpload && (
            <>
              <button
                onClick={() => fileRef.current?.click()}
                disabled={disabled}
                className="text-xs flex items-center gap-1.5 px-3 py-1.5 bg-blue-50 text-blue-700 rounded-lg hover:bg-blue-100 disabled:opacity-50 transition-colors"
              >
                📁 Upload CSV
              </button>
              <input
                ref={fileRef}
                type="file"
                accept=".csv"
                className="hidden"
                onChange={handleFileChange}
              />
            </>
          )}
          {onTemplateDownload && (
            <button
              onClick={onTemplateDownload}
              className="text-xs flex items-center gap-1.5 px-3 py-1.5 bg-gray-50 text-gray-600 rounded-lg hover:bg-gray-100 transition-colors"
            >
              ⬇ Download Template
            </button>
          )}
        </div>
      )}

      {/* Text input */}
      <div className="flex gap-2 items-end">
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={disabled}
          placeholder={placeholder}
          rows={1}
          className="flex-1 resize-none border border-gray-300 rounded-xl px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-[#C9A84C]/40 focus:border-[#C9A84C] disabled:opacity-50 disabled:bg-gray-50 max-h-32 overflow-y-auto"
          style={{ minHeight: "42px" }}
          onInput={(e) => {
            const target = e.target as HTMLTextAreaElement;
            target.style.height = "auto";
            target.style.height = Math.min(target.scrollHeight, 128) + "px";
          }}
        />
        <button
          onClick={handleSend}
          disabled={disabled || !text.trim()}
          className="bg-gradient-to-r from-[#C9A84C] to-[#E4C063] text-white rounded-xl px-4 py-2.5 text-sm font-medium hover:opacity-90 disabled:opacity-40 transition-all flex-shrink-0"
        >
          Send
        </button>
      </div>
    </div>
  );
}
