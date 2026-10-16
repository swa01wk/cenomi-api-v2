"use client";

import { useEffect, useRef, useState } from "react";
import { useCurrentUser } from "@/components/AuthGuard";
import { Chips, ConfirmationCard, DocumentCard } from "@/components/ChatCards";
import { MessageBubble } from "@/components/MessageBubble";
import { DEMO_ROLES } from "@/lib/auth";
import type { ChipOption, DocumentRow } from "@/lib/types";
import type { CeilingSession } from "@/lib/useCeilingSession";

/** The chat pane. The conversation itself lives in the page's useCeilingSession. */
export function CeilingChat({ session }: { session: CeilingSession }) {
  const user = useCurrentUser();
  const role = DEMO_ROLES[user.role];
  const { messages, busy, error, stage, reviewSrId, turn } = session;
  const [draft, setDraft] = useState("");
  const scroller = useRef<HTMLDivElement>(null);

  useEffect(() => {
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: "smooth" });
  }, [messages, busy]);

  const onChip = (option: ChipOption) =>
    void turn({ message: option.value || option.label, action: option.action });

  const onUpload = (row: DocumentRow, file: File) => session.upload(row.key, row.label, file);

  const lastAssistant = [...messages].reverse().find((m) => m.role === "assistant");

  return (
    <div className="flex flex-col h-full">
      <div className="px-5 py-4 border-b border-white/10 flex items-center justify-between">
        <div>
          <div className="text-sm font-semibold">Ceiling Inspection Agent</div>
          <div className="text-xs text-white/50">
            {reviewSrId ? `Reviewing ${reviewSrId.slice(0, 8)}…` : "New conversation"}
            {stage ? ` · ${stage}` : ""}
          </div>
        </div>
        <button
          className="text-xs px-3 py-1.5 rounded-lg border border-white/20 hover:bg-white/10"
          onClick={session.reset}
        >
          New chat
        </button>
      </div>

      <div ref={scroller} className="flex-1 overflow-y-auto px-4 py-4">
        {messages.length === 0 && !busy && (
          <div className="text-center text-sm text-white/60 mt-8">
            <p className="mb-1 text-white/80">
              Signed in as {role.person} — {role.label}
            </p>
            <p className="mb-4">{role.step}</p>
            {role.prompts.length > 0 && (
              <p className="mb-4 text-xs text-white/45">
                Fill the form on the left, or start here — the form fills as we talk.
              </p>
            )}
            {role.prompts.length ? (
              <div className="flex flex-col gap-2 items-center">
                {role.prompts.map((prompt) => (
                  <button
                    key={prompt}
                    className="px-3 py-2 rounded-xl glass text-xs hover:bg-white/15"
                    onClick={() => void turn({ message: prompt })}
                  >
                    {prompt}
                  </button>
                ))}
              </div>
            ) : (
              <p>Open a request from the notification bell or “My requests”.</p>
            )}
          </div>
        )}

        {messages.map((message) => {
          const live = message.id === lastAssistant?.id && !busy;
          const ui = message.ui;
          return (
            <MessageBubble key={message.id} message={message}>
              {ui?.type === "confirmation_card" && (
                <ConfirmationCard
                  ui={ui}
                  disabled={!live}
                  onConfirm={() =>
                    void turn({ message: ui.confirmLabel ?? "Confirm", action: ui.confirmAction })
                  }
                  onDecline={() => void turn({ message: "No, I want to change something" })}
                  onChip={onChip}
                />
              )}
              {ui?.type === "document_requirement" && (
                <DocumentCard ui={ui} disabled={!live} onUpload={(row, file) => void onUpload(row, file)} />
              )}
              {ui?.type !== "confirmation_card" && ui?.suggestions?.options?.length ? (
                <Chips options={ui.suggestions.options} disabled={!live} onPick={onChip} />
              ) : null}
            </MessageBubble>
          );
        })}

        {busy && (
          <div className="flex items-center gap-2 text-xs text-white/50 ml-10">
            <span className="w-2 h-2 rounded-full bg-[#C9A84C] animate-pulse" /> Thinking…
          </div>
        )}
        {error && (
          <div className="mt-3 text-xs text-red-300 bg-red-500/10 border border-red-500/30 rounded-lg p-3">
            {error}
          </div>
        )}
      </div>

      <form
        className="p-3 border-t border-white/10 flex gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          const text = draft.trim();
          if (!text || busy) return;
          setDraft("");
          void turn({ message: text });
        }}
      >
        <input
          className="flex-1 rounded-xl bg-white/10 border border-white/15 px-3 py-2 text-sm outline-none focus:border-[#C9A84C]/60 placeholder:text-white/35"
          placeholder="Type a message…"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          disabled={busy}
        />
        <button
          className="px-4 rounded-xl bg-[#C9A84C] text-[#1A1A2E] text-sm font-semibold disabled:opacity-40"
          disabled={busy || !draft.trim()}
        >
          Send
        </button>
      </form>
    </div>
  );
}
