"use client";

import type { ChatMessage } from "@/lib/types/chat";

interface MessageBubbleProps {
  message: ChatMessage;
}

export function MessageBubble({ message }: MessageBubbleProps) {
  const isUser = message.role === "user";

  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"} mb-3`}>
      {!isUser && (
        <div className="w-8 h-8 rounded-full bg-gradient-to-br from-[#C9A84C] to-[#E4C063] flex items-center justify-center text-[#1A1A2E] text-sm font-bold mr-2 flex-shrink-0 shadow-[0_0_14px_rgba(201,168,76,0.35)]">
          AI
        </div>
      )}
      <div
        className={`max-w-[80%] rounded-2xl px-4 py-3 text-sm ${
          isUser
            ? "bg-[#C9A84C]/20 border border-[#C9A84C]/35 text-white rounded-tr-sm backdrop-blur-md"
            : "glass-light text-slate-800 rounded-tl-sm"
        }`}
      >
        <div
          className="whitespace-pre-wrap leading-relaxed"
          dangerouslySetInnerHTML={{
            __html: formatMarkdown(message.text),
          }}
        />
        <div className={`text-xs mt-1 ${isUser ? "text-white/45" : "text-slate-400"}`}>
          {message.timestamp.toLocaleTimeString([], {
            hour: "2-digit",
            minute: "2-digit",
          })}
        </div>
      </div>
      {isUser && (
        <div className="w-8 h-8 rounded-full bg-white/10 border border-white/15 flex items-center justify-center text-white/70 text-sm ml-2 flex-shrink-0">
          👤
        </div>
      )}
    </div>
  );
}

function formatMarkdown(text: string): string {
  return text
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/\*(.+?)\*/g, "<em>$1</em>")
    .replace(/`(.+?)`/g, '<code class="bg-black/10 px-1 rounded text-xs">$1</code>')
    .replace(/\n/g, "<br/>");
}
