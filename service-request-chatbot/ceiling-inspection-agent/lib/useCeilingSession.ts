"use client";

/**
 * One conversation shared by the chat and the form.
 *
 * The page owns this hook so both panes always show the same SR: the chat
 * renders `messages`, the form renders `formState`, and either side sends a
 * turn through `turn()`. Opening an SR (from the list or the notification
 * bell) starts a fresh session, exactly as the portal does.
 */

import { useCallback, useRef, useState } from "react";
import { sendChatTurn, uploadDocument, type ChatTurn } from "@/lib/api";
import { rememberRequest } from "@/lib/recent";
import type { ChatMessage, FormState, FormValues, ResponseUI } from "@/lib/types";

let messageSeq = 0;
const nextId = () => `m${++messageSeq}`;

export interface TurnOptions {
  message: string;
  action?: string;
  /** Show the message as a user bubble (default: when it is non-empty). */
  showUser?: boolean;
  correctedFields?: Partial<FormValues>;
  selectedLeaseId?: string;
  /** Start a new session (null) instead of continuing the current one. */
  session?: string | null;
  srId?: string | null;
  activeNotification?: ChatTurn["activeNotification"];
}

export interface CeilingSession {
  sessionId: string | null;
  reviewSrId: string | null;
  messages: ChatMessage[];
  stage: string | null;
  formState: FormState | null;
  busy: boolean;
  error: string | null;
  turn: (opts: TurnOptions) => Promise<void>;
  openSr: (srId: string) => void;
  openNotification: (srId: string, subCategory: string) => void;
  upload: (documentType: string, label: string, file: File, comment?: string) => Promise<void>;
  reset: () => void;
}

export function useCeilingSession(onTurnDone?: () => void): CeilingSession {
  const [sessionId, setSessionId] = useState<string | null>(null);
  // The SR this conversation is reviewing. Create conversations have none.
  const [reviewSrId, setReviewSrId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [stage, setStage] = useState<string | null>(null);
  const [formState, setFormState] = useState<FormState | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Turns are serialised: a form blur landing mid-turn waits for the reply,
  // otherwise it would race the session id of a conversation just started.
  const queue = useRef<Promise<void>>(Promise.resolve());
  const live = useRef({ sessionId, reviewSrId });
  live.current = { sessionId, reviewSrId };

  const runTurn = useCallback(
    async (opts: TurnOptions) => {
      const session = opts.session === undefined ? live.current.sessionId : opts.session;
      const srId = opts.srId === undefined ? live.current.reviewSrId : opts.srId;
      const showUser = opts.showUser ?? Boolean(opts.message.trim());
      if (showUser && opts.message.trim()) {
        setMessages((m) => [...m, { id: nextId(), role: "user", text: opts.message, at: new Date() }]);
      }
      setBusy(true);
      setError(null);
      try {
        const res = await sendChatTurn({
          sessionId: session,
          message: opts.message,
          action: opts.action,
          srId,
          correctedFields: opts.correctedFields,
          selectedLeaseId: opts.selectedLeaseId,
          activeNotification: opts.activeNotification,
        });
        live.current.sessionId = res.session_id;
        setSessionId(res.session_id);
        setStage(res.state?.workflow_stage ?? null);
        if (res.form_state) setFormState(res.form_state);
        const ui: ResponseUI = res.ui ?? {};
        // A create just went through: keep its id so the next role can open it.
        if (!srId && res.state?.sr_id) {
          rememberRequest(res.state.sr_id, (res.message || ui.message || "").split(" — ")[0]);
        }
        const text = res.message || ui.message || "";
        // A silent form patch that only re-asks the same question adds nothing
        // to the transcript; one that moves the agent on (next question,
        // review card) is shown.
        const silent = !opts.message.trim() && Boolean(opts.correctedFields) && !opts.action;
        if (text) {
          setMessages((m) => {
            const last = [...m].reverse().find((x) => x.role === "assistant");
            if (silent && last?.text === text) return m;
            return [...m, { id: nextId(), role: "assistant", text, ui, at: new Date() }];
          });
        }
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setBusy(false);
        onTurnDone?.();
      }
    },
    [onTurnDone]
  );

  const turn = useCallback(
    (opts: TurnOptions) => {
      const next = queue.current.then(() => runTurn(opts));
      queue.current = next.catch(() => undefined);
      return next;
    },
    [runTurn]
  );

  const clear = () => {
    live.current = { sessionId: null, reviewSrId: null };
    setSessionId(null);
    setReviewSrId(null);
    setMessages([]);
    setStage(null);
    setFormState(null);
    setError(null);
  };

  const reset = useCallback(clear, []);

  // Opening an SR from the list: new session, empty message, sr_id — the same
  // contract the portal uses, which sends the turn through status sync.
  const openSr = useCallback(
    (srId: string) => {
      clear();
      setReviewSrId(srId);
      live.current.reviewSrId = srId;
      void turn({ message: "", showUser: false, session: null, srId });
    },
    [turn]
  );

  // Opening from the bell: the notification agent's Open path
  // (active_notification_id → open_notification → sr_status_sync).
  const openNotification = useCallback(
    (srId: string, subCategory: string) => {
      clear();
      setReviewSrId(srId);
      live.current.reviewSrId = srId;
      void turn({
        message: "",
        showUser: false,
        session: null,
        srId: null,
        activeNotification: { id: srId, sub_category: subCategory },
      });
    },
    [turn]
  );

  const upload = useCallback(
    async (documentType: string, label: string, file: File, comment?: string) => {
      const session = live.current.sessionId;
      if (!session) return;
      setBusy(true);
      setError(null);
      try {
        await uploadDocument({
          file,
          documentType,
          sessionId: session,
          srId: live.current.reviewSrId,
          comment,
        });
        setMessages((m) => [
          ...m,
          { id: nextId(), role: "user", text: `📎 ${file.name} — ${label}`, at: new Date() },
        ]);
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
        setBusy(false);
        return;
      }
      await turn({ message: `I've uploaded the ${label}.`, showUser: false });
    },
    [turn]
  );

  return {
    sessionId,
    reviewSrId,
    messages,
    stage,
    formState,
    busy,
    error,
    turn,
    openSr,
    openNotification,
    upload,
    reset,
  };
}
