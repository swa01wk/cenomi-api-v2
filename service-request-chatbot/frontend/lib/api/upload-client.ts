import { getStoredToken } from "@/lib/api/auth-client";

// ─── Response shape from POST /api/upload ─────────────────────────────────────

export type DocumentUploadResponse = {
  filename: string;
  content_type: string;
  document_type: string;
  document_id: string | null;
  signed_url: string | null;
  file_path: string | null;
  comment?: string | null;
  status: "uploaded" | string;
};

// ─── API base ─────────────────────────────────────────────────────────────────

function apiBase(): string {
  // Backend mounts upload at /api/v1/upload and /api/v2/upload.
  const base = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
  return `${base.replace(/\/$/, "")}/api/v1`;
}

// ─── Upload function ──────────────────────────────────────────────────────────

/**
 * Upload a service request document to the platform via the backend.
 */
export async function uploadDocument(
  file: File,
  documentType: string,
  sessionId: string,
  srId?: string | null,
  comment?: string | null,
): Promise<DocumentUploadResponse> {
  const token = getStoredToken();

  const form = new FormData();
  form.append("file", file);
  form.append("document_type", documentType);
  form.append("session_id", sessionId);
  if (srId) form.append("sr_id", srId);
  if (comment?.trim()) form.append("comment", comment.trim());

  const headers: Record<string, string> = {};
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const res = await fetch(`${apiBase()}/upload`, {
    method: "POST",
    headers,
    body: form,
  });

  if (!res.ok) {
    const errText = await res.text().catch(() => res.statusText);
    throw new Error(`Upload failed (${res.status}): ${errText}`);
  }

  return (await res.json()) as DocumentUploadResponse;
}

/** Delete an uploaded document from the platform + session draft. */
export async function deleteDocument(
  documentId: string,
  sessionId: string,
  srId?: string | null,
  leaseId?: string | null,
): Promise<{ document_id: string; status: string }> {
  const token = getStoredToken();
  const params = new URLSearchParams({ document_id: documentId, session_id: sessionId });
  if (srId) params.set("sr_id", srId);
  if (leaseId) params.set("lease_id", leaseId);

  const headers: Record<string, string> = {};
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const res = await fetch(`${apiBase()}/upload?${params.toString()}`, {
    method: "DELETE",
    headers,
  });

  if (!res.ok) {
    const errText = await res.text().catch(() => res.statusText);
    throw new Error(`Delete failed (${res.status}): ${errText}`);
  }

  return (await res.json()) as { document_id: string; status: string };
}
