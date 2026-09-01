"use client";

import { useState, useRef, useEffect, useCallback } from "react";
import { useSearchParams } from "next/navigation";
import { MessageBubble } from "./MessageBubble";
import { ChatInput } from "./ChatInput";
import { SalesDataReviewCard } from "./SalesDataReviewCard";
import { ManualEntryConfirmCard } from "./ManualEntryConfirmCard";
import { SupportingDocConfirmCard } from "./SupportingDocConfirmCard";
import { StatusSummaryCard } from "./StatusSummaryCard";
import { SubmissionSuccessCard, SubmissionErrorCard } from "./SubmissionResultCard";
import { postSalesDataChat } from "@/lib/api/chat-client";
import { uploadSalesDataCsv, uploadSupportingDoc, downloadCsvTemplate } from "@/lib/api/upload-client";
import { logout } from "@/lib/api/auth-client";
import { useCurrentUser } from "@/components/auth/AuthGuard";
import { useSalesDataSession } from "@/lib/context/SalesDataSessionContext";
import { AdminRowMenu, adminActionMessage } from "@/components/AdminRowMenu";
import { SalesDataStatusBadge } from "@/components/SalesDataStatusBadge";
import { salesDataBrand, salesDataCompany, salesDataProperty } from "@/lib/salesDataLeaseDisplay";
import { useRouter } from "next/navigation";
import type {
  ChatMessage,
  ResponseUI,
  SalesDataReviewUI,
  ManualEntryConfirmUI,
  StatusSummaryUI,
  SubmissionSuccessUI,
  SubmissionErrorUI,
  SalesDataReviewRow,
  AdminSalesDataListUI,
  AdminConfirmUI,
  SupportingDocConfirmUI,
  SupportingDocPromptUI,
  SupportingDocSelectUI,
  PeriodMismatchWarningUI,
  PendingMonthsListUI,
  OtherMonthsReminderUI,
} from "@/lib/types/chat";

export function SalesDataChat() {
  const user = useCurrentUser();
  const router = useRouter();
  const searchParams = useSearchParams();
  const session = useSalesDataSession();

  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [sessionId, setSessionId] = useState<string | undefined>();
  const [showFileUpload, setShowFileUpload] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const notificationBootstrappedRef = useRef<string | false>(false);

  // Scroll to bottom on new messages
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages, session.isPending]);

  // Welcome message on mount
  useEffect(() => {
    const isAdmin = user.role === "SALES_DATA_APPROVER";

    // Build context-aware welcome based on notification params
    const lease = searchParams.get("lease");
    const periodLabel = searchParams.get("periodLabel");
    const mall = searchParams.get("mall");
    const notifStatus = searchParams.get("status");

    const isDocNotif =
      notifStatus === "need_reupload" ||
      notifStatus === "need_supporting_document" ||
      notifStatus === "need_audited_sales";

    let welcome: string;
    if (lease && periodLabel && isDocNotif && !isAdmin) {
      const ask =
        notifStatus === "need_audited_sales"
          ? "audited sales data"
          : notifStatus === "need_supporting_document"
            ? "a supporting document"
            : "corrected sales data (file)";
      welcome =
        `👋 Hi **${user.label}**! Sales data for **${lease}** (${periodLabel}) ` +
        `needs **${ask}**${mall ? ` — ${mall}` : ""}.\n\n` +
        `Attach the file below. A comment is required, same as the portal.`;
    } else if (lease && periodLabel && !isAdmin) {
      welcome =
        `👋 Hi **${user.label}**! I can see you have a pending sales data notification.\n\n` +
        `📋 **${periodLabel}** sales data for **${lease}**${mall ? ` (${mall})` : ""} is pending submission.\n\n` +
        `Would you like to:\n` +
        `• 📁 **Upload a CSV file**\n` +
        `• ✏️ **Enter the amount directly** in chat\n\n` +
        `Just let me know how you'd like to proceed!`;
    } else if (isAdmin) {
      welcome =
        `Welcome back, **${user.label}**! 👋\n\nI can help you review and approve sales data submissions. ` +
        `Type **"review sales data"** to see pending submissions, then approve or ` +
        `ask upload (sales data / supporting document / audited sales).`;
    } else {
      welcome =
        `Welcome back, **${user.label}**! 👋\n\nI can help you:\n` +
        `• Upload your monthly sales data CSV\n` +
        `• Enter sales figures directly in chat\n` +
        `• Check your submission status\n` +
        `• Upload a file when the team asks for sales data, a supporting document, or audited sales\n\nWhat would you like to do?`;
    }

    setMessages([{ id: "welcome", role: "assistant", text: welcome, timestamp: new Date() }]);
  }, [user, searchParams]);

  // Auto-send notification context as first user turn (once).
  // Set the guard inside the timeout so React Strict Mode's effect
  // cleanup (which clears the timer) can still schedule a real send.
  useEffect(() => {
    const lease = searchParams.get("lease");
    const periodLabel = searchParams.get("periodLabel");
    const notificationId = searchParams.get("notificationId");
    const notifStatus = searchParams.get("status");
    if (!lease || !periodLabel || !notificationId) return;

    const bootstrapKey = `${notificationId}:${lease}:${periodLabel}:${notifStatus ?? ""}`;
    if (notificationBootstrappedRef.current === bootstrapKey) return;

    const timer = window.setTimeout(() => {
      if (notificationBootstrappedRef.current === bootstrapKey) return;
      notificationBootstrappedRef.current = bootstrapKey;
      if (notifStatus === "need_audited_sales") {
        sendTurn(
          `I received a notification that audited sales are required for lease ${lease} for ${periodLabel}. I want to upload audited sales.`,
          {
            notificationContext: {
              lease_code: lease,
              period_label: periodLabel,
              period_hint: periodLabel,
              source: "notification",
            },
          },
        );
      } else if (notifStatus === "need_supporting_document") {
        sendTurn(
          `I received a notification that a supporting document is required for lease ${lease} for ${periodLabel}. I want to upload a supporting document.`,
          {
            notificationContext: {
              lease_code: lease,
              period_label: periodLabel,
              period_hint: periodLabel,
              source: "notification",
            },
          },
        );
      } else if (notifStatus === "need_reupload") {
        sendTurn(
          `I received a notification that sales data for lease ${lease} for ${periodLabel} needs a PDF re-upload. I want to upload a PDF.`,
          {
            notificationContext: {
              lease_code: lease,
              period_label: periodLabel,
              period_hint: periodLabel,
              source: "notification",
            },
          },
        );
      } else {
        sendTurn(
          `I received a notification about pending sales data for lease ${lease} for ${periodLabel}. I want to submit it.`,
          {
            notificationContext: {
              lease_code: lease,
              period_label: periodLabel,
              period_hint: periodLabel,
              source: "notification",
            },
          },
        );
      }
    }, 800);
    return () => window.clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams]);

  // Register submit handler in context so left panel can trigger submission
  useEffect(() => {
    session.registerSubmitHandler((rows: SalesDataReviewRow[]) => {
      handleReviewConfirm(rows);
    });
    session.registerPanelMessageHandler((text: string) => {
      sendTurn(text);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId, session.isPending]);

  // ── Core send function ────────────────────────────────────────────────────

  const sendTurn = useCallback(
    async (
      userText: string,
      opts: {
        action?: string;
        correctedFields?: Record<string, unknown>;
        notificationContext?: Record<string, string>;
        silent?: boolean;
      } = {},
    ) => {
      if (!opts.silent && userText) {
        const userMsg: ChatMessage = {
          id: crypto.randomUUID(),
          role: "user",
          text: userText,
          timestamp: new Date(),
        };
        setMessages((prev) => [...prev, userMsg]);
      }

      session.setIsPending(true);

      try {
        const res = await postSalesDataChat({
          sessionId,
          message: userText,
          action: opts.action,
          correctedFields: {
            ...(opts.correctedFields ?? {}),
            ...(opts.notificationContext
              ? { notification_context: opts.notificationContext }
              : {}),
          },
        });

        if (res.sessionId) setSessionId(res.sessionId);

        const aiMsg: ChatMessage = {
          id: crypto.randomUUID(),
          role: "assistant",
          text: res.responseUI?.message ?? "",
          timestamp: new Date(),
          ui: res.responseUI,
        };
        setMessages((prev) => [...prev, aiMsg]);

        const ui = res.responseUI ?? null;
        session.setLatestUI(ui?.type ? ui : null);
        session.setWorkflowStage(res.workflowStage ?? null);

        // Sync review rows to context when we get them
        if (ui?.type === "sales_data_review") {
          session.setReviewRows((ui as SalesDataReviewUI).rows);
        }

        const stage = res.workflowStage ?? "";
        const uiType = res.responseUI?.type ?? "";
        setShowFileUpload(
          stage === "UPLOAD_INTENT" ||
            stage === "AWAITING_PERIOD_MISMATCH_CONFIRM" ||
            stage === "AWAITING_PENDING_MONTHS_CHOICE" ||
            uiType === "sales_data_upload_prompt" ||
            uiType === "period_mismatch_warning" ||
            uiType === "pending_months_list" ||
            uiType === "lease_validation_error" ||
            ["AWAITING_DOC_UPLOAD", "DOC_SELECT"].includes(stage) ||
            uiType === "supporting_doc_prompt" ||
            uiType === "supporting_doc_select",
        );
      } catch (err) {
        const errMsg: ChatMessage = {
          id: crypto.randomUUID(),
          role: "assistant",
          text: `⚠️ Something went wrong: ${err instanceof Error ? err.message : "Unknown error"}. Please try again.`,
          timestamp: new Date(),
        };
        setMessages((prev) => [...prev, errMsg]);
      } finally {
        session.setIsPending(false);
      }
    },
    [sessionId, session],
  );

  // ── File upload ───────────────────────────────────────────────────────────

  async function handleFileUpload(file: File) {
    const name = file.name.toLowerCase();
    const isCsv = name.endsWith(".csv");
    const isDoc =
      name.endsWith(".pdf") ||
      file.type === "application/pdf" ||
      file.type.startsWith("image/");

    const docStage = ["AWAITING_DOC_UPLOAD", "DOC_SELECT", "DOC_UPLOADING"].includes(
      session.workflowStage ?? "",
    );
    const latestType = session.latestUI?.type ?? "";
    const wantsDoc =
      docStage ||
      latestType === "supporting_doc_prompt" ||
      latestType === "supporting_doc_select";

    if (wantsDoc && isDoc) {
      await handleDocUpload(file);
      return;
    }
    if (isCsv) {
      await handleCsvUpload(file);
      return;
    }
    if (isDoc) {
      await handleDocUpload(file);
      return;
    }

    const errMsg: ChatMessage = {
      id: crypto.randomUUID(),
      role: "assistant",
      text: wantsDoc
        ? "Please attach a **PDF or image**. CSV files are for the monthly sales upload, not this re-upload."
        : "Please attach a **.csv** file for sales data, or a **PDF** if you are responding to a re-upload request.",
      timestamp: new Date(),
    };
    setMessages((prev) => [...prev, errMsg]);
  }

  async function handleCsvUpload(file: File) {
    const uploadingMsg: ChatMessage = {
      id: crypto.randomUUID(),
      role: "assistant",
      text: `📁 Uploading **${file.name}**…`,
      timestamp: new Date(),
    };
    setMessages((prev) => [...prev, uploadingMsg]);
    session.setIsPending(true);

    try {
      const result = await uploadSalesDataCsv(file, sessionId);
      if (result.sessionId) setSessionId(result.sessionId);
      await sendTurn(
        `I've uploaded ${file.name} with ${result.rowCount} row(s). Please validate and process it.`,
        {
          action: "csv_uploaded",
          silent: true,
          correctedFields: { csv_session_id: result.sessionId },
        },
      );
    } catch (err) {
      const errMsg: ChatMessage = {
        id: crypto.randomUUID(),
        role: "assistant",
        text: `❌ Upload failed: ${err instanceof Error ? err.message : "Unknown error"}`,
        timestamp: new Date(),
      };
      setMessages((prev) => [...prev, errMsg]);
      session.setIsPending(false);
    }
  }

  async function handleDocUpload(file: File) {
    const uploadingMsg: ChatMessage = {
      id: crypto.randomUUID(),
      role: "assistant",
      text: `📎 Uploading **${file.name}**…`,
      timestamp: new Date(),
    };
    setMessages((prev) => [...prev, uploadingMsg]);
    session.setIsPending(true);

    try {
      const result = await uploadSupportingDoc(file, sessionId);
      if (result.sessionId) setSessionId(result.sessionId);
      await sendTurn(`I've attached ${file.name}. Please process this document.`, {
        action: "doc_uploaded",
        silent: true,
        correctedFields: { doc_session_id: result.sessionId },
      });
    } catch (err) {
      const errMsg: ChatMessage = {
        id: crypto.randomUUID(),
        role: "assistant",
        text: `❌ Document upload failed: ${err instanceof Error ? err.message : "Unknown error"}`,
        timestamp: new Date(),
      };
      setMessages((prev) => [...prev, errMsg]);
      session.setIsPending(false);
    }
  }

  // ── Review confirm ────────────────────────────────────────────────────────

  async function handleReviewConfirm(updatedRows: SalesDataReviewRow[]) {
    await sendTurn("Confirmed — please submit the data.", {
      action: "CONFIRM_CSV_SUBMISSION",
      correctedFields: { rows: updatedRows },
    });
  }

  // ── Manual entry ──────────────────────────────────────────────────────────

  async function handleManualConfirm() {
    await sendTurn("Confirmed — please submit.", { action: "confirm_manual_entry" });
  }

  async function handleManualCancel() {
    await sendTurn("Cancel — let me change the details.", { action: "cancel_manual_entry" });
  }

  async function handleDocConfirm(comment: string, salesAmt: number | null) {
    const correctedFields: Record<string, unknown> = { comment_text: comment };
    if (salesAmt != null) correctedFields.sales_amt = salesAmt;
    await sendTurn("Confirmed — please submit the document.", {
      action: "confirm_supporting_doc",
      correctedFields,
    });
  }

  async function handleDocCancel() {
    await sendTurn("Cancel — I'll attach a different file.", { action: "cancel_supporting_doc" });
  }

  // ── Admin confirm ─────────────────────────────────────────────────────────

  async function handleAdminConfirm() {
    await sendTurn("Confirmed.", { action: "confirm_admin_action" });
  }

  async function handleAdminCancel() {
    await sendTurn("Cancel.", { action: "cancel_admin" });
  }

  async function handlePeriodMismatchProceed() {
    await sendTurn("Yes, proceed with this file.", {
      action: "confirm_period_mismatch",
    });
  }

  async function handlePeriodMismatchDecline() {
    await sendTurn("No — I'll upload a different file.", {
      action: "cancel_period_mismatch",
    });
  }

  async function handleOtherMonthsProceed(askedLabel: string) {
    await sendTurn(`Proceed with ${askedLabel}.`, {
      action: "proceed_other_months",
    });
  }

  async function handleSwitchPendingMonth(label: string, yearMonth: string) {
    await sendTurn(`Switch to ${label}.`, {
      action: "switch_pending_month",
      correctedFields: { year_month: yearMonth },
    });
  }

  // ── UI card renderer ──────────────────────────────────────────────────────

  function renderUICard(msg: ChatMessage) {
    const ui = msg.ui;
    if (!ui) return null;

    switch (ui.type) {
      case "pending_months_list": {
        const list = ui as PendingMonthsListUI;
        const latest = [...messages].reverse().find((m) => m.ui?.type === "pending_months_list");
        const isLive =
          session.workflowStage === "AWAITING_PENDING_MONTHS_CHOICE" &&
          latest?.id === msg.id;
        if (!isLive) return null;
        return (
          <div className="glass rounded-2xl p-4 my-2 text-white">
            <h3 className="text-sm font-semibold text-[#E4C063] mb-2">Pending months</h3>
            <div className="space-y-3">
              {list.leases.map((lease) => (
                <div key={lease.lease_code}>
                  <p className="text-xs font-mono font-semibold text-white mb-1">
                    {lease.lease_code}
                  </p>
                  <div className="flex flex-wrap gap-1.5">
                    {lease.months.map((month) => (
                      <button
                        key={`${lease.lease_code}-${month.year_month}`}
                        type="button"
                        disabled={session.isPending}
                        onClick={() =>
                          sendTurn(`${lease.lease_code} ${month.label}`)
                        }
                        className="px-2 py-1 text-xs border border-white/20 rounded-lg hover:bg-white/10 disabled:opacity-50"
                      >
                        {month.label}
                      </button>
                    ))}
                  </div>
                </div>
              ))}
            </div>
            <p className="text-xs text-white/50 mt-3">
              Pick a month, or drop a CSV. One file can include several pending months.
            </p>
          </div>
        );
      }
      case "other_months_reminder": {
        const r = ui as OtherMonthsReminderUI;
        const latest = [...messages]
          .reverse()
          .find((m) => m.ui?.type === "other_months_reminder");
        const isLive =
          session.workflowStage === "AWAITING_OTHER_MONTHS_REMINDER" &&
          latest?.id === msg.id;
        if (!isLive) return null;
        const switchMonths = r.other_months.slice(0, 4);
        const extraCount = r.other_months.length - switchMonths.length;
        return (
          <div className="glass rounded-2xl p-4 my-2 text-white">
            <h3 className="text-sm font-semibold text-amber-200 mb-2">
              Other pending months on {r.lease_code}
            </h3>
            <p className="text-xs text-white/80 mb-2">
              You asked for <span className="font-semibold text-white">{r.asked_label}</span>.
              This lease also has:
            </p>
            <ul className="text-xs text-white/70 mb-3 space-y-1 max-h-32 overflow-auto">
              {r.other_months.map((month) => (
                <li key={month.year_month}>• {month.label}</li>
              ))}
            </ul>
            <p className="text-xs text-white/55 mb-3">
              You can include these in the same CSV after you proceed. I&apos;ll confirm
              if the file covers extra months.
            </p>
            <button
              type="button"
              onClick={() => handleOtherMonthsProceed(r.asked_label)}
              disabled={session.isPending}
              className="px-3 py-1.5 text-xs bg-amber-500 text-[#1A1A2E] rounded-lg hover:bg-amber-400 disabled:opacity-50 font-medium"
            >
              Proceed with {r.asked_label}
            </button>
            {r.other_months.length > 0 && (
              <div className="mt-3">
                <p className="text-[11px] text-white/40 mb-1.5">
                  Or start with a different month
                </p>
                <div className="flex flex-wrap gap-1.5">
                  {switchMonths.map((month) => (
                    <button
                      key={month.year_month}
                      type="button"
                      onClick={() => handleSwitchPendingMonth(month.label, month.year_month)}
                      disabled={session.isPending}
                      className="px-2 py-1 text-xs border border-white/20 text-white/80 rounded-lg hover:bg-white/10 disabled:opacity-50"
                    >
                      {month.label}
                    </button>
                  ))}
                  {extraCount > 0 && (
                    <span className="px-2 py-1 text-[11px] text-white/40 self-center">
                      and {extraCount} more — type the month
                    </span>
                  )}
                </div>
              </div>
            )}
          </div>
        );
      }
      case "period_mismatch_warning": {
        const w = ui as PeriodMismatchWarningUI;
        const latestWarning = [...messages]
          .reverse()
          .find((m) => m.ui?.type === "period_mismatch_warning");
        const isLive =
          session.workflowStage === "AWAITING_PERIOD_MISMATCH_CONFIRM" &&
          latestWarning?.id === msg.id;
        if (!isLive) return null;
        return (
          <div
            key={`period-mismatch-${msg.id}`}
            className="glass rounded-2xl p-4 my-2 text-white"
          >
            <h3 className="text-sm font-semibold text-amber-200 mb-2">
              {w.includes_asked_month ? "Extra months in this file" : "Month does not match"}
            </h3>
            <p className="text-xs text-white/80 mb-3">
              {w.includes_asked_month ? (
                <>
                  This file includes{" "}
                  <span className="font-semibold text-white">{w.asked_label}</span> and
                  other months ({w.file_label}). Extra PENDING months can go in the same
                  sheet.
                </>
              ) : (
                <>
                  This file has <span className="font-semibold text-white">{w.file_label}</span>{" "}
                  data. You asked to upload{" "}
                  <span className="font-semibold text-white">{w.asked_label}</span>.
                </>
              )}
            </p>
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                onClick={handlePeriodMismatchDecline}
                disabled={session.isPending}
                className="px-3 py-1.5 text-xs border border-white/20 text-white/80 rounded-lg hover:bg-white/10 disabled:opacity-50"
              >
                Upload a different file
              </button>
              <button
                type="button"
                onClick={handlePeriodMismatchProceed}
                disabled={session.isPending}
                className="px-3 py-1.5 text-xs bg-amber-500 text-[#1A1A2E] rounded-lg hover:bg-amber-400 disabled:opacity-50 font-medium"
              >
                {w.includes_asked_month ? "Submit this file" : "Proceed anyway"}
              </button>
            </div>
          </div>
        );
      }
      case "sales_data_review": {
        const r = ui as SalesDataReviewUI;
        return (
          <SalesDataReviewCard
            key={`review-${msg.id}`}
            rows={session.reviewRows.length > 0 ? session.reviewRows : r.rows}
            filename={r.filename}
            onConfirm={handleReviewConfirm}
            onCancel={() => sendTurn("Cancel", { action: "cancel" })}
            disabled={session.isPending}
          />
        );
      }
      case "manual_entry_confirm": {
        const m = ui as ManualEntryConfirmUI;
        return (
          <ManualEntryConfirmCard
            key={`manual-${msg.id}`}
            ui={m}
            onConfirm={handleManualConfirm}
            onCancel={handleManualCancel}
            disabled={session.isPending}
          />
        );
      }
      case "status_summary": {
        const s = ui as StatusSummaryUI;
        return (
          <StatusSummaryCard
            key={`status-${msg.id}`}
            ui={s}
            onUploadNow={() => {
              setShowFileUpload(true);
              sendTurn("I want to upload sales data");
            }}
            onUploadDocument={(leaseCode, status) => {
              if (status === "NEED_UPLOAD_SUPPORTING_DOCUMENT") {
                sendTurn(`I need to upload a supporting document for lease ${leaseCode}.`);
              } else if (status === "NEED_UPLOAD_AUDITED_SALES") {
                sendTurn(`I need to upload audited sales for lease ${leaseCode}.`);
              } else {
                sendTurn(`I need to re-upload a PDF for lease ${leaseCode}.`);
              }
            }}
          />
        );
      }
      case "submission_success": {
        const s = ui as SubmissionSuccessUI;
        return <SubmissionSuccessCard key={`success-${msg.id}`} ui={s} />;
      }
      case "submission_error": {
        const e = ui as SubmissionErrorUI;
        return (
          <SubmissionErrorCard
            key={`error-${msg.id}`}
            ui={e}
            onReupload={() => sendTurn("I want to re-upload the file")}
          />
        );
      }
      case "admin_sales_data_list": {
        const a = ui as AdminSalesDataListUI;
        return (
          <div
            key={`admin-list-${msg.id}`}
            className="glass rounded-2xl overflow-hidden my-2 text-white"
          >
            <div className="bg-violet-400/15 border-b border-violet-400/25 px-4 py-3">
              <h3 className="text-sm font-semibold text-violet-100">
                📋 Pending Review ({a.rows.length})
              </h3>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-violet-200/70 uppercase tracking-wide">
                    <th className="text-left px-4 py-2">Lease</th>
                    <th className="text-left px-4 py-2">Property</th>
                    <th className="text-left px-4 py-2">Company</th>
                    <th className="text-left px-4 py-2">Brand</th>
                    <th className="text-left px-4 py-2">Period</th>
                    <th className="text-right px-4 py-2">Amount</th>
                    <th className="text-left px-3 py-2">Status</th>
                    <th className="text-right px-3 py-2 w-10" />
                  </tr>
                </thead>
                <tbody>
                  {a.rows.map((r, i) => (
                    <tr key={i} className="border-t border-white/10 hover:bg-white/5">
                      <td className="px-4 py-2 font-semibold font-mono text-white">{r.lease_code}</td>
                      <td className="px-4 py-2 text-white/70">{salesDataProperty(r)}</td>
                      <td className="px-4 py-2 text-white/70">{salesDataCompany(r)}</td>
                      <td className="px-4 py-2 text-white/70">{salesDataBrand(r)}</td>
                      <td className="px-4 py-2 text-white/65">{r.sales_period_start?.slice(0, 7)}</td>
                      <td className="px-4 py-2 text-right text-white">
                        {r.sales_amt != null ? `SAR ${Number(r.sales_amt).toLocaleString()}` : "—"}
                      </td>
                      <td className="px-3 py-2">
                        <SalesDataStatusBadge compact status={r.status} />
                      </td>
                      <td className="px-2 py-2 text-right">
                        <AdminRowMenu
                          compact
                          leaseCode={r.lease_code}
                          documentId={r.document_id}
                          disabled={session.isPending}
                          onAction={(action) =>
                            sendTurn(
                              adminActionMessage(r.lease_code, action, {
                                start: r.sales_period_start,
                                end: r.sales_period_end,
                              }),
                            )
                          }
                        />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="px-4 py-3 bg-violet-400/10 border-t border-violet-400/20 text-[11px] text-violet-100/85 space-y-1">
              <p className="font-medium">Ask upload (same as the portal):</p>
              <p className="text-white/70">
                “Request supporting document for {a.rows[0]?.lease_code}” ·
                “Request audited sales for {a.rows[0]?.lease_code}” ·
                “Request reupload for {a.rows[0]?.lease_code}”
              </p>
            </div>
          </div>
        );
      }
      case "admin_confirm": {
        const ac = ui as AdminConfirmUI;
        const targets = ac.targets?.length ? ac.targets : [{ lease_code: ac.lease_code }];
        return (
          <div
            key={`admin-confirm-${msg.id}`}
            className="glass rounded-2xl p-4 my-2 text-white"
          >
            <h3 className="text-sm font-semibold text-violet-100 mb-2">✅ Confirm Admin Action</h3>
            <div className="text-xs text-white/80 space-y-1 mb-3">
              <div><span className="font-medium text-white">Action:</span> {ac.action_label || ac.action}</div>
              <div>
                <span className="font-medium text-white">
                  {targets.length === 1 ? "Lease:" : `${targets.length} submissions:`}
                </span>
                {targets.length === 1 ? (
                  <span>
                    {" "}
                    {targets[0].lease_code}
                    {targets[0].sales_period_start
                      ? ` (${String(targets[0].sales_period_start).slice(0, 7)})`
                      : ""}
                  </span>
                ) : (
                  <ul className="mt-1 space-y-0.5">
                    {targets.map((t) => (
                      <li key={`${t.lease_code}-${t.sales_period_start}`}>
                        {t.lease_code}
                        {t.sales_period_start
                          ? ` (${String(t.sales_period_start).slice(0, 7)})`
                          : ""}
                        {t.sales_amt != null
                          ? ` — SAR ${Number(t.sales_amt).toLocaleString()}`
                          : ""}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
              {ac.comment_text && (
                <div><span className="font-medium text-white">Comment:</span> "{ac.comment_text}"</div>
              )}
            </div>
            <div className="flex gap-2">
              <button
                onClick={handleAdminCancel}
                disabled={session.isPending}
                className="px-3 py-1.5 text-xs border border-white/20 text-white/80 rounded-lg hover:bg-white/10 disabled:opacity-50"
              >
                Cancel
              </button>
              <button
                onClick={handleAdminConfirm}
                disabled={session.isPending}
                className="px-3 py-1.5 text-xs bg-violet-500 text-white rounded-lg hover:bg-violet-600 disabled:opacity-50 font-medium"
              >
                Confirm
              </button>
            </div>
          </div>
        );
      }
      case "supporting_doc_confirm": {
        const d = ui as SupportingDocConfirmUI;
        const latestConfirm = [...messages]
          .reverse()
          .find((m) => m.ui?.type === "supporting_doc_confirm");
        const isLive =
          session.workflowStage === "AWAITING_DOC_CONFIRM" &&
          latestConfirm?.id === msg.id;
        if (!isLive) return null;
        return (
          <SupportingDocConfirmCard
            key={`doc-confirm-${msg.id}`}
            ui={d}
            onConfirm={handleDocConfirm}
            onCancel={handleDocCancel}
            disabled={session.isPending}
          />
        );
      }
      case "supporting_doc_select": {
        const sel = ui as SupportingDocSelectUI;
        return (
          <div
            key={`doc-select-${msg.id}`}
            className="bg-white/90 backdrop-blur-md border border-orange-200/70 rounded-2xl overflow-hidden shadow-sm my-2"
          >
            <div className="bg-orange-50 border-b border-orange-100 px-4 py-3">
              <h3 className="text-sm font-semibold text-orange-800">
                Which file request?
              </h3>
              <p className="text-[11px] text-orange-700 mt-0.5">
                Sales data, supporting document, or audited sales
              </p>
            </div>
            <ul className="divide-y divide-gray-100">
              {sel.rows.map((r) => (
                <li key={`${r.lease_code}-${r.sales_period_start}-${r.status}`}>
                  <button
                    type="button"
                    className="w-full text-left px-4 py-3 text-xs hover:bg-orange-50"
                    onClick={() =>
                      sendTurn(
                        r.status === "NEED_UPLOAD_AUDITED_SALES"
                          ? `I need to upload audited sales for lease ${r.lease_code} (${r.sales_period_start} to ${r.sales_period_end}).`
                          : r.status === "NEED_UPLOAD_SUPPORTING_DOCUMENT"
                            ? `I need to upload a supporting document for lease ${r.lease_code} (${r.sales_period_start} to ${r.sales_period_end}).`
                            : `I need to re-upload a PDF for lease ${r.lease_code} (${r.sales_period_start} to ${r.sales_period_end}).`,
                      )
                    }
                  >
                    <span className="font-semibold text-gray-800">{r.lease_code}</span>
                    <span className="text-gray-500">
                      {" "}
                      · {r.sales_period_start?.slice(0, 7)} · {r.status.replace(/_/g, " ")}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </div>
        );
      }
      case "supporting_doc_prompt": {
        const p = ui as SupportingDocPromptUI;
        return (
          <div
            key={`doc-prompt-${msg.id}`}
            className="glass-light rounded-2xl p-4 my-2 text-xs text-orange-800"
          >
            Attach the{" "}
            <span className="font-semibold">
              {p.ask_label || p.current_status.replace(/_/g, " ")}
            </span>{" "}
            file for <span className="font-semibold">{p.lease_code}</span>
            {" "}({p.sales_period_start} – {p.sales_period_end}). Comment is required.
          </div>
        );
      }
      case "supporting_doc_success": {
        return (
          <div
            key={`doc-success-${msg.id}`}
            className="glass-light rounded-2xl p-4 my-2 text-sm text-green-800"
          >
            ✅ Document submitted. Status is now{" "}
            <span className="font-semibold">
              {String((ui as { status?: string }).status ?? "").replace(/_/g, " ")}
            </span>
            .
          </div>
        );
      }
      default:
        return null;
    }
  }

  // ── Layout ────────────────────────────────────────────────────────────────

  const isAdmin = user.role === "SALES_DATA_APPROVER";
  const isDocUpload =
    ["AWAITING_DOC_UPLOAD", "DOC_SELECT", "DOC_UPLOADING"].includes(session.workflowStage ?? "") ||
    session.latestUI?.type === "supporting_doc_prompt" ||
    session.latestUI?.type === "supporting_doc_select";

  return (
    <div className="flex flex-col h-full bg-transparent">
      {/* Header */}
      <div className="px-4 py-3 flex items-center justify-between flex-shrink-0 border-b border-white/10">
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-full bg-gradient-to-br from-[#C9A84C] to-[#E4C063] flex items-center justify-center text-[#1A1A2E] text-sm font-bold shadow-[0_0_18px_rgba(201,168,76,0.4)]">
            AI
          </div>
          <div>
            <h1 className="text-sm font-semibold text-white">Sales Data Agent</h1>
            <p className="text-xs text-white/40">Cenomi AI Assistant</p>
          </div>
        </div>
        <div className="flex items-center gap-2 flex-shrink-0">
          <span
            className={`text-[10px] px-2 py-0.5 rounded-full font-medium border ${
              isAdmin
                ? "bg-violet-400/15 text-violet-200 border-violet-400/25"
                : "bg-sky-400/15 text-sky-200 border-sky-400/25"
            }`}
          >
            {user.label}
          </span>
          <button
            onClick={() => router.push("/dashboard")}
            className="text-[11px] text-white/40 hover:text-white/80 transition-colors"
          >
            Dashboard
          </button>
          <button
            onClick={() => { logout(); router.push("/login"); }}
            className="text-[11px] text-white/40 hover:text-white/80 transition-colors"
          >
            Sign out
          </button>
        </div>
      </div>

      {/* Messages */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto px-4 py-4 space-y-1">
        {messages.map((msg) => (
          <div key={msg.id}>
            <MessageBubble message={msg} />
            {renderUICard(msg)}
          </div>
        ))}

        {/* File upload zone */}
        {showFileUpload && !session.isPending && (
          <div className="mx-2 my-3">
            <label className="block">
              <div className="border-2 border-dashed border-[#C9A84C]/40 rounded-xl p-6 text-center cursor-pointer hover:bg-[#C9A84C]/10 transition-colors">
                <div className="text-3xl mb-2">{isDocUpload ? "📎" : "📁"}</div>
                <div className="text-sm font-medium text-white/80">
                  {isDocUpload ? "Drop PDF here or click to browse" : "Drop CSV here or click to browse"}
                </div>
                <div className="text-xs text-white/40 mt-1">
                  {isDocUpload ? "Max 10 MB · PDF or image" : "Max 5 MB · Cenomi template · dates DD-MM-YYYY"}
                </div>
                <input
                  type="file"
                  accept={isDocUpload ? ".pdf,image/*" : ".csv"}
                  className="hidden"
                  onChange={(e) => { const f = e.target.files?.[0]; if (f) handleFileUpload(f); }}
                />
              </div>
            </label>
          </div>
        )}

        {/* Typing indicator */}
        {session.isPending && (
          <div className="flex justify-start mb-3">
            <div className="w-8 h-8 rounded-full bg-gradient-to-br from-[#C9A84C] to-[#E4C063] flex items-center justify-center text-[#1A1A2E] text-sm font-bold mr-2 shadow-[0_0_14px_rgba(201,168,76,0.35)]">
              AI
            </div>
            <div className="glass rounded-2xl rounded-tl-sm px-4 py-3">
              <div className="flex gap-1 items-center h-4">
                {[0, 1, 2].map((i) => (
                  <span
                    key={i}
                    className="w-1.5 h-1.5 bg-[#C9A84C] rounded-full animate-bounce"
                    style={{ animationDelay: `${i * 0.15}s` }}
                  />
                ))}
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Input */}
      <ChatInput
        onSend={(text) => sendTurn(text)}
        onFileUpload={handleFileUpload}
        disabled={session.isPending}
        placeholder={
          isAdmin
            ? "e.g. Review sales data, Approve LC-1234…"
            : isDocUpload
              ? "Attach a PDF, or type a lease code…"
              : "e.g. Upload sales data, Sales for August LC-1234 is SAR 85,000…"
        }
        showFileUpload={!isAdmin}
        accept={isDocUpload ? ".pdf,image/*" : ".csv"}
        uploadLabel={isDocUpload ? "Upload PDF" : "Upload CSV"}
        onTemplateDownload={!isAdmin && !isDocUpload ? downloadCsvTemplate : undefined}
      />
    </div>
  );
}
