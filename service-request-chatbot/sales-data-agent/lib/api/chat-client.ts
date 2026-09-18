/**
 * Chat client — sends turns to the AI backend.
 *
 * Endpoint: POST /api/chat/service-request  (AI backend, port 8000)
 */

import { ensureFreshToken, getStoredUser } from "@/lib/api/auth-client";
import type { ChatRequest, ChatResponse } from "@/lib/types/chat";

function aiBase(): string {
  const base = process.env.NEXT_PUBLIC_AI_BASE_URL ?? "http://localhost:8000";
  return `${base.replace(/\/$/, "")}/api`;
}

export async function postSalesDataChat(req: ChatRequest): Promise<ChatResponse> {
  const token = await ensureFreshToken();
  const user = getStoredUser();

  const payload: Record<string, unknown> = {
    session_id: req.sessionId ?? null,
    user_id: user?.role ?? "demo_user",
    message: req.message,
    attachments: req.attachmentIds?.map((id) => ({ id })) ?? [],
  };

  if (req.action) payload.action = req.action;
  if (req.correctedFields) payload.corrected_fields = req.correctedFields;

  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const res = await fetch(`${aiBase()}/chat/service-request`, {
    method: "POST",
    headers,
    body: JSON.stringify(payload),
  });

  if (!res.ok) {
    const errText = await res.text().catch(() => res.statusText);
    throw new Error(`Chat request failed (${res.status}): ${errText}`);
  }

  const data = await res.json();

  const responseUI = {
    ...data.ui,
    message: data.ui?.message ?? data.message,
  };

  const state: Record<string, unknown> = data.state ?? {};

  return {
    sessionId: data.session_id,
    responseUI,
    workflowStage: (state.workflow_stage as string | null) ?? null,
    intent: (state.intent as string | null) ?? null,
    activeAgent: (state.active_agent as string | null) ?? null,
  };
}
