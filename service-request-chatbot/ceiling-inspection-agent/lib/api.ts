/**
 * AI backend calls used by the demo.
 *
 *   POST /api/chat/service-request   one chat turn
 *   POST /api/v1/upload              attach a document to the chat draft
 *   GET  /api/v1/lookups/*           access-scoped dropdown options for the form
 *   GET  /api/v1/notifications/inbox action-required notifications (the bell)
 */

import { aiBase, freshToken, getStoredUser } from "@/lib/auth";
import type {
  ChatResponse,
  CompanyOption,
  FormOptions,
  FormValues,
  InboxGroup,
  LeaseOption,
} from "@/lib/types";

async function authHeaders(json = true): Promise<Record<string, string>> {
  const token = await freshToken();
  const headers: Record<string, string> = json ? { "Content-Type": "application/json" } : {};
  if (token) headers.Authorization = `Bearer ${token}`;
  return headers;
}

async function failure(res: Response, what: string): Promise<Error> {
  const detail = await res.text().catch(() => res.statusText);
  return new Error(`${what} failed (${res.status}): ${detail}`);
}

export interface ChatTurn {
  sessionId: string | null;
  message: string;
  action?: string;
  srId?: string | null;
  /** Silent form edits — merged into the draft without an extraction pass. */
  correctedFields?: Partial<FormValues>;
  /** Lease picked in the form's dropdown. */
  selectedLeaseId?: string;
  /** Opening an SR from the notification bell. */
  activeNotification?: { id: string; sub_category: string };
}

export async function sendChatTurn(turn: ChatTurn): Promise<ChatResponse> {
  const body: Record<string, unknown> = {
    session_id: turn.sessionId,
    user_id: getStoredUser()?.role ?? "demo_user",
    message: turn.message,
    attachments: [],
  };
  if (turn.action) body.action = turn.action;
  if (turn.srId) body.sr_id = turn.srId;
  if (turn.correctedFields && Object.keys(turn.correctedFields).length) {
    body.corrected_fields = turn.correctedFields;
  }
  if (turn.selectedLeaseId) body.selected_lease_id = turn.selectedLeaseId;
  if (turn.activeNotification) body.active_notification_id = turn.activeNotification;

  const res = await fetch(`${aiBase()}/api/chat/service-request`, {
    method: "POST",
    headers: await authHeaders(),
    body: JSON.stringify(body),
  });
  if (!res.ok) throw await failure(res, "Chat turn");
  return (await res.json()) as ChatResponse;
}

export async function uploadDocument(params: {
  file: File;
  documentType: string;
  sessionId: string;
  srId?: string | null;
  comment?: string;
}): Promise<{ document_id: string }> {
  const form = new FormData();
  form.append("file", params.file);
  form.append("document_type", params.documentType);
  form.append("session_id", params.sessionId);
  if (params.srId) form.append("sr_id", params.srId);
  if (params.comment?.trim()) form.append("comment", params.comment.trim());

  const res = await fetch(`${aiBase()}/api/v1/upload`, {
    method: "POST",
    headers: await authHeaders(false),
    body: form,
  });
  if (!res.ok) throw await failure(res, "Upload");
  return res.json();
}

async function getJson<T>(path: string, what: string): Promise<T> {
  const res = await fetch(`${aiBase()}${path}`, { headers: await authHeaders(false) });
  if (!res.ok) throw await failure(res, what);
  return (await res.json()) as T;
}

export async function getCompanies(): Promise<CompanyOption[]> {
  return (await getJson<{ companies: CompanyOption[] }>("/api/v1/lookups/companies", "Companies")).companies;
}

export async function getLeases(tenantProfileId?: number): Promise<LeaseOption[]> {
  const query = tenantProfileId === undefined ? "" : `?tenant_profile_id=${tenantProfileId}`;
  return (await getJson<{ leases: LeaseOption[] }>(`/api/v1/lookups/leases${query}`, "Leases")).leases;
}

export async function getFormOptions(): Promise<FormOptions> {
  return getJson<FormOptions>("/api/v1/lookups/ceiling-form", "Form options");
}

const CEILING_SUB_CATEGORIES = new Set(["OPEN_CEILING_INSPECTION", "CLOSE_CEILING_INSPECTION"]);

/** Ceiling inspection rows only — the mock inbox also serves static demo rows for other agents. */
export async function getCeilingInbox(): Promise<InboxGroup[]> {
  const body = await getJson<{ groups: InboxGroup[] }>(
    "/api/v1/notifications/inbox?limit=20",
    "Notifications"
  );
  return body.groups.filter((g) => CEILING_SUB_CATEGORIES.has(g.sub_category));
}
