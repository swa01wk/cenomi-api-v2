"use client";

import { useState } from "react";
import { submitFeedback } from "@/lib/api/observability-client";
import { getStoredUser } from "@/lib/api/auth-client";

type Props = {
  role: "user" | "assistant";
  text: string;
  traceId?: string;
  showTrace?: boolean;
  isLoading?: boolean;
};

export function MessageBubble({ role, text, traceId, showTrace, isLoading }: Props) {
  const isUser = role === "user";
  const [thumb, setThumb] = useState<"up" | "down" | null>(null);
  const [busy, setBusy] = useState(false);

  async function sendThumb(label: "THUMBS_UP" | "THUMBS_DOWN") {
    if (!traceId || busy) return;
    setBusy(true);
    try {
      await submitFeedback(traceId, {
        feedback_type: "TURN_THUMBS",
        label,
        user_id: getStoredUser()?.userId,
      });
      setThumb(label === "THUMBS_UP" ? "up" : "down");
    } catch {
      // Non-blocking — chat must keep working
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div className="flex max-w-[82%] flex-col gap-1">
        <div
          className={`rounded-2xl px-4 py-2.5 text-sm leading-relaxed ${
            isUser
              ? "bg-blue-600 text-white"
              : "bg-zinc-100 text-zinc-900 dark:bg-zinc-800 dark:text-zinc-100"
          }`}
        >
          {isLoading ? (
            <span className="flex items-center gap-1 py-0.5">
              <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-zinc-400 [animation-delay:0ms]" />
              <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-zinc-400 [animation-delay:150ms]" />
              <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-zinc-400 [animation-delay:300ms]" />
            </span>
          ) : (
            <span className="whitespace-pre-wrap">{text}</span>
          )}
        </div>
        {!isUser && !isLoading && traceId && (
          <div className="flex gap-1 px-1">
            <button
              type="button"
              disabled={busy || thumb !== null}
              onClick={() => sendThumb("THUMBS_UP")}
              className={`text-xs ${thumb === "up" ? "text-emerald-600" : "text-zinc-400 hover:text-zinc-600"}`}
              aria-label="Thumbs up"
            >
              Useful
            </button>
            <span className="text-zinc-300">·</span>
            <button
              type="button"
              disabled={busy || thumb !== null}
              onClick={() => sendThumb("THUMBS_DOWN")}
              className={`text-xs ${thumb === "down" ? "text-red-600" : "text-zinc-400 hover:text-zinc-600"}`}
              aria-label="Thumbs down"
            >
              Not useful
            </button>
          </div>
        )}
        {showTrace && traceId && (
          <span className="px-1 font-mono text-[10px] text-zinc-400 dark:text-zinc-600">
            trace: {traceId}
          </span>
        )}
      </div>
    </div>
  );
}
