"use client";

import { useState, useRef, useEffect, useCallback } from "react";
import { useSearchParams } from "next/navigation";
import { MessageBubble } from "./MessageBubble";
import { ChatInput } from "./ChatInput";
import { SalesDataReviewCard } from "./SalesDataReviewCard";
import { ManualEntryConfirmCard } from "./ManualEntryConfirmCard";
import { StatusSummaryCard } from "./StatusSummaryCard";
import { SubmissionSuccessCard, SubmissionErrorCard } from "./SubmissionResultCard";
import { postSalesDataChat } from "@/lib/api/chat-client";
import { uploadSalesDataCsv, downloadCsvTemplate } from "@/lib/api/upload-client";
import { logout } from "@/lib/api/auth-client";
import { useCurrentUser } from "@/components/auth/AuthGuard";
import { useSalesDataSession } from "@/lib/context/SalesDataSessionContext";
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
  const notificationBootstrappedRef = useRef(false);

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

    let welcome: string;
    if (lease && periodLabel && !isAdmin) {
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
        `Type **"review sales data"** to see pending submissions, or ask me anything.`;
    } else {
      welcome =
        `Welcome back, **${user.label}**! 👋\n\nI can help you:\n` +
        `• Upload your monthly sales data CSV\n` +
        `• Enter sales figures directly in chat\n` +
        `• Check your submission status\n\nWhat would you like to do?`;
    }

    setMessages([{ id: "welcome", role: "assistant", text: welcome, timestamp: new Date() }]);
  }, [user, searchParams]);

  // Auto-send notification context as first user turn (once)
  useEffect(() => {
    if (notificationBootstrappedRef.current) return;
    const lease = searchParams.get("lease");
    const periodLabel = searchParams.get("periodLabel");
    const notificationId = searchParams.get("notificationId");
    if (!lease || !periodLabel || !notificationId) return;

    notificationBootstrappedRef.current = true;

    // Small delay so welcome message appears first
    const timer = setTimeout(() => {
      sendTurn(
        `I received a notification about pending sales data for lease ${lease} for ${periodLabel}. I want to submit it.`,
        { notificationContext: { lease_code: lease, period_label: periodLabel } },
      );
    }, 800);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Register submit handler in context so left panel can trigger submission
  useEffect(() => {
    session.registerSubmitHandler((rows: SalesDataReviewRow[]) => {
      handleReviewConfirm(rows);
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
      session.setLatestUI(null);

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
        session.setLatestUI(ui);
        session.setWorkflowStage(res.workflowStage ?? null);

        // Sync review rows to context when we get them
        if (ui?.type === "sales_data_review") {
          session.setReviewRows((ui as SalesDataReviewUI).rows);
        }

        const stage = res.workflowStage ?? "";
        setShowFileUpload(
          ["UPLOAD_INTENT", ""].includes(stage) &&
            res.responseUI?.type === "sales_data_upload_prompt",
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
      // Pass the upload cache key explicitly so csv_validation_node can find the
      // file regardless of which DB session UUID the orchestrator assigns.
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

  // ── Admin confirm ─────────────────────────────────────────────────────────

  async function handleAdminConfirm() {
    await sendTurn("Confirmed.", { action: "confirm_admin_action" });
  }

  async function handleAdminCancel() {
    await sendTurn("Cancel.", { action: "cancel_admin" });
  }

  // ── UI card renderer ──────────────────────────────────────────────────────

  function renderUICard(msg: ChatMessage) {
    const ui = msg.ui;
    if (!ui) return null;

    switch (ui.type) {
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
            onUploadNow={() => sendTurn("I want to upload sales data")}
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
            className="bg-white border border-gray-200 rounded-2xl overflow-hidden shadow-sm my-2"
          >
            <div className="bg-purple-50 border-b border-purple-100 px-4 py-3">
              <h3 className="text-sm font-semibold text-purple-800">
                📋 Pending Review ({a.rows.length})
              </h3>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="bg-gray-50 text-gray-500 uppercase">
                    <th className="text-left px-4 py-2">Lease</th>
                    <th className="text-left px-4 py-2">Period</th>
                    <th className="text-right px-4 py-2">Amount</th>
                  </tr>
                </thead>
                <tbody>
                  {a.rows.map((r, i) => (
                    <tr key={i} className="border-t border-gray-100 hover:bg-gray-50">
                      <td className="px-4 py-2 font-medium">{r.lease_code}</td>
                      <td className="px-4 py-2 text-gray-500">{r.sales_period_start?.slice(0, 7)}</td>
                      <td className="px-4 py-2 text-right">
                        {r.sales_amt != null ? `SAR ${Number(r.sales_amt).toLocaleString()}` : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        );
      }
      case "admin_confirm": {
        const ac = ui as AdminConfirmUI;
        return (
          <div
            key={`admin-confirm-${msg.id}`}
            className="bg-purple-50 border border-purple-200 rounded-2xl p-4 my-2"
          >
            <h3 className="text-sm font-semibold text-purple-800 mb-2">✅ Confirm Admin Action</h3>
            <div className="text-xs text-purple-700 space-y-1 mb-3">
              <div><span className="font-medium">Action:</span> {ac.action}</div>
              <div><span className="font-medium">Lease:</span> {ac.lease_code}</div>
              {ac.comment_text && (
                <div><span className="font-medium">Comment:</span> "{ac.comment_text}"</div>
              )}
            </div>
            <div className="flex gap-2">
              <button
                onClick={handleAdminCancel}
                disabled={session.isPending}
                className="px-3 py-1.5 text-xs border border-gray-300 rounded-lg hover:bg-gray-100 disabled:opacity-50"
              >
                Cancel
              </button>
              <button
                onClick={handleAdminConfirm}
                disabled={session.isPending}
                className="px-3 py-1.5 text-xs bg-purple-600 text-white rounded-lg hover:bg-purple-700 disabled:opacity-50 font-medium"
              >
                Confirm
              </button>
            </div>
          </div>
        );
      }
      default:
        return null;
    }
  }

  // ── Layout ────────────────────────────────────────────────────────────────

  const isAdmin = user.role === "SALES_DATA_APPROVER";

  return (
    <div className="flex flex-col h-full bg-gray-50">
      {/* Header */}
      <div className="bg-white border-b border-gray-200 px-4 py-3 flex items-center justify-between flex-shrink-0">
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-full bg-gradient-to-br from-[#C9A84C] to-[#E4C063] flex items-center justify-center text-white text-sm font-bold shadow">
            AI
          </div>
          <div>
            <h1 className="text-sm font-semibold text-gray-900">Sales Data Agent</h1>
            <p className="text-xs text-gray-400">Cenomi AI Assistant</p>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <span
            className={`text-xs px-2.5 py-1 rounded-full font-medium ${
              isAdmin ? "bg-purple-100 text-purple-700" : "bg-blue-100 text-blue-700"
            }`}
          >
            {isAdmin ? "👑" : "🏪"} {user.label}
          </span>
          <button
            onClick={() => router.push("/dashboard")}
            className="text-xs text-gray-400 hover:text-gray-600 transition-colors"
          >
            ← Dashboard
          </button>
          <button
            onClick={() => { logout(); router.push("/login"); }}
            className="text-xs text-gray-400 hover:text-gray-600 transition-colors"
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
              <div className="border-2 border-dashed border-[#C9A84C]/50 rounded-xl p-6 text-center cursor-pointer hover:bg-[#FFF9EE] transition-colors">
                <div className="text-3xl mb-2">📁</div>
                <div className="text-sm font-medium text-gray-700">Drop CSV here or click to browse</div>
                <div className="text-xs text-gray-400 mt-1">Max 5 MB · .csv only</div>
                <input
                  type="file"
                  accept=".csv"
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
            <div className="w-8 h-8 rounded-full bg-gradient-to-br from-[#C9A84C] to-[#E4C063] flex items-center justify-center text-white text-sm font-bold mr-2 shadow">
              AI
            </div>
            <div className="bg-white border border-gray-200 rounded-2xl rounded-tl-sm px-4 py-3 shadow-sm">
              <div className="flex gap-1 items-center h-4">
                {[0, 1, 2].map((i) => (
                  <span
                    key={i}
                    className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce"
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
            ? "e.g. Review sales data, Approve LC-2001…"
            : "e.g. Upload sales data, Sales for August LC-1001 is SAR 85,000…"
        }
        showFileUpload={!isAdmin}
        onTemplateDownload={!isAdmin ? downloadCsvTemplate : undefined}
      />
    </div>
  );
}
