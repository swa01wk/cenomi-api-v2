"use client";

/**
 * SalesDataSessionContext — shared state between the chatbot (right panel)
 * and the mirror panel (left panel) in the split-screen layout.
 *
 * The chatbot owns all writes; the left panel reads and can trigger
 * submission via the exposed `onSubmitRows` callback.
 */

import { createContext, useContext, useState, useCallback, type ReactNode } from "react";
import type { ResponseUI, SalesDataReviewRow } from "@/lib/types/chat";

interface SalesDataSession {
  /** Latest structured UI payload from the AI backend */
  latestUI: ResponseUI | null;
  setLatestUI: (ui: ResponseUI | null) => void;

  /** Rows currently in review (editable in left panel table) */
  reviewRows: SalesDataReviewRow[];
  setReviewRows: (rows: SalesDataReviewRow[]) => void;

  /** Current LangGraph workflow stage */
  workflowStage: string | null;
  setWorkflowStage: (stage: string | null) => void;

  /** Whether the backend is processing a turn */
  isPending: boolean;
  setIsPending: (v: boolean) => void;

  /** Called when left panel's Submit button is clicked */
  onSubmitFromPanel: ((rows: SalesDataReviewRow[]) => void) | null;
  registerSubmitHandler: (fn: (rows: SalesDataReviewRow[]) => void) => void;

  /** Send a chat turn from the left-panel three-dot menu (Accept / Ask upload). */
  onPanelMessage: ((text: string) => void) | null;
  registerPanelMessageHandler: (fn: (text: string) => void) => void;
}

const SalesDataSessionContext = createContext<SalesDataSession | null>(null);

export function SalesDataSessionProvider({ children }: { children: ReactNode }) {
  const [latestUI, setLatestUI] = useState<ResponseUI | null>(null);
  const [reviewRows, setReviewRows] = useState<SalesDataReviewRow[]>([]);
  const [workflowStage, setWorkflowStage] = useState<string | null>(null);
  const [isPending, setIsPending] = useState(false);
  const [submitHandler, setSubmitHandler] = useState<((rows: SalesDataReviewRow[]) => void) | null>(null);
  const [panelMessageHandler, setPanelMessageHandler] = useState<((text: string) => void) | null>(null);

  const registerSubmitHandler = useCallback(
    (fn: (rows: SalesDataReviewRow[]) => void) => {
      setSubmitHandler(() => fn);
    },
    [],
  );

  const registerPanelMessageHandler = useCallback(
    (fn: (text: string) => void) => {
      setPanelMessageHandler(() => fn);
    },
    [],
  );

  return (
    <SalesDataSessionContext.Provider
      value={{
        latestUI,
        setLatestUI,
        reviewRows,
        setReviewRows,
        workflowStage,
        setWorkflowStage,
        isPending,
        setIsPending,
        onSubmitFromPanel: submitHandler,
        registerSubmitHandler,
        onPanelMessage: panelMessageHandler,
        registerPanelMessageHandler,
      }}
    >
      {children}
    </SalesDataSessionContext.Provider>
  );
}

export function useSalesDataSession(): SalesDataSession {
  const ctx = useContext(SalesDataSessionContext);
  if (!ctx) throw new Error("useSalesDataSession must be used inside SalesDataSessionProvider");
  return ctx;
}
