"use client";

import type { ChatMessage } from "@/lib/types";

/** Escape first, then apply the few markdown marks the agent uses. */
function renderMarkdown(text: string): string {
  const escaped = text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
  return escaped
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/`(.+?)`/g, '<code class="bg-black/10 px-1 rounded text-xs">$1</code>')
    .replace(/\n/g, "<br/>");
}

export function MessageBubble({
  message,
  children,
}: {
  message: ChatMessage;
  children?: React.ReactNode;
}) {
  const isUser = message.role === "user";
  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"} mb-3`}>
      {!isUser && (
        <div className="w-8 h-8 rounded-full bg-gradient-to-br from-[#C9A84C] to-[#E4C063] flex items-center justify-center text-[#1A1A2E] text-xs font-bold mr-2 flex-shrink-0">
          AI
        </div>
      )}
      <div className={`max-w-[85%] ${isUser ? "" : "w-full"}`}>
        {message.text && (
          <div
            className={`rounded-2xl px-4 py-3 text-sm ${
              isUser
                ? "bg-[#C9A84C]/20 border border-[#C9A84C]/35 text-white rounded-tr-sm"
                : "glass-light text-slate-800 rounded-tl-sm"
            }`}
          >
            <div
              className="leading-relaxed"
              dangerouslySetInnerHTML={{ __html: renderMarkdown(message.text) }}
            />
          </div>
        )}
        {children}
      </div>
    </div>
  );
}
