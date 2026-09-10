"use client";

import { useState } from "react";
import { postSessionFeedback } from "@/lib/api/chat-client";

const REASONS = [
  { id: "WRONG_LEASE", label: "Wrong lease" },
  { id: "WRONG_FIELDS", label: "Wrong fields" },
  { id: "TOO_MANY_STEPS", label: "Too many steps" },
  { id: "CONFUSING", label: "Confusing" },
  { id: "OTHER", label: "Other" },
] as const;

type Props = {
  sessionId: string;
  srId?: string | null;
  onDone: () => void;
};

export function FlowUsefulnessCard({ sessionId, srId, onDone }: Props) {
  const [busy, setBusy] = useState(false);
  const [askReason, setAskReason] = useState(false);

  async function submit(label: "USEFUL" | "NOT_USEFUL", reason?: string) {
    setBusy(true);
    try {
      await postSessionFeedback(sessionId, {
        feedback_type: "FLOW_USEFUL",
        label,
        sr_id: srId ?? undefined,
        metadata: reason ? { reason } : {},
      });
    } catch {
      // never block the SR card
    } finally {
      setBusy(false);
      onDone();
    }
  }

  return (
    <div className="rounded-lg border border-zinc-200 bg-white px-4 py-3 text-sm dark:border-zinc-800 dark:bg-zinc-900">
      <p className="font-medium text-zinc-800 dark:text-zinc-200">
        Was creating this service request with the assistant useful?
      </p>
      {!askReason ? (
        <div className="mt-2 flex gap-2">
          <button
            type="button"
            disabled={busy}
            onClick={() => submit("USEFUL")}
            className="rounded-md bg-emerald-600 px-3 py-1 text-white hover:bg-emerald-700 disabled:opacity-40"
          >
            Yes
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={() => setAskReason(true)}
            className="rounded-md border border-zinc-300 px-3 py-1 text-zinc-700 hover:bg-zinc-50 dark:border-zinc-700 dark:text-zinc-200"
          >
            No
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={onDone}
            className="px-2 text-xs text-zinc-400 underline"
          >
            Skip
          </button>
        </div>
      ) : (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {REASONS.map((r) => (
            <button
              key={r.id}
              type="button"
              disabled={busy}
              onClick={() => submit("NOT_USEFUL", r.id)}
              className="rounded-full border border-zinc-300 px-2.5 py-0.5 text-xs text-zinc-600 hover:bg-zinc-50 dark:border-zinc-700 dark:text-zinc-300"
            >
              {r.label}
            </button>
          ))}
          <button type="button" onClick={onDone} className="text-xs text-zinc-400 underline">
            Skip
          </button>
        </div>
      )}
    </div>
  );
}
