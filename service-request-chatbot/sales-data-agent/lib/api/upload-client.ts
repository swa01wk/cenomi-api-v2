/**
 * Upload client — sends CSV / supporting-doc files to the AI backend.
 *
 * Endpoints (AI backend, port 8000):
 *   POST /api/v2/sales-data/upload
 *   POST /api/v2/sales-data/supporting-doc
 */

import { ensureFreshToken } from "@/lib/api/auth-client";

function aiBase(): string {
  const base = process.env.NEXT_PUBLIC_AI_BASE_URL ?? "http://localhost:8000";
  return `${base.replace(/\/$/, "")}/api/v2`;
}

export interface UploadResult {
  sessionId: string;
  filename: string;
  rowCount: number;
  columns: string[];
}

export interface SupportingDocUploadResult {
  sessionId: string;
  filename: string;
  contentType: string;
  sizeBytes: number;
}

export async function uploadSalesDataCsv(
  file: File,
  sessionId?: string | null,
): Promise<UploadResult> {
  const token = await ensureFreshToken();
  const formData = new FormData();
  formData.append("file", file);
  if (sessionId) {
    formData.append("session_id", sessionId);
  }

  const headers: Record<string, string> = {};
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const res = await fetch(`${aiBase()}/sales-data/upload`, {
    method: "POST",
    headers,
    body: formData,
  });

  if (!res.ok) {
    const errText = await res.text().catch(() => res.statusText);
    throw new Error(`Upload failed (${res.status}): ${errText}`);
  }

  const data = await res.json();
  return {
    sessionId: data.session_id,
    filename: data.filename,
    rowCount: data.row_count,
    columns: data.columns ?? [],
  };
}

export async function uploadSupportingDoc(
  file: File,
  sessionId?: string | null,
): Promise<SupportingDocUploadResult> {
  const token = await ensureFreshToken();
  const formData = new FormData();
  formData.append("file", file);
  if (sessionId) {
    formData.append("session_id", sessionId);
  }

  const headers: Record<string, string> = {};
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const res = await fetch(`${aiBase()}/sales-data/supporting-doc`, {
    method: "POST",
    headers,
    body: formData,
  });

  if (!res.ok) {
    const errText = await res.text().catch(() => res.statusText);
    throw new Error(`Document upload failed (${res.status}): ${errText}`);
  }

  const data = await res.json();
  return {
    sessionId: data.session_id,
    filename: data.filename,
    contentType: data.content_type,
    sizeBytes: data.size_bytes,
  };
}

/** Download the CSV template from the mock backend. */
export async function downloadCsvTemplate(): Promise<void> {
  const mockBase = process.env.NEXT_PUBLIC_MOCK_BASE_URL ?? "http://localhost:8001";
  const token = await ensureFreshToken();
  const headers: Record<string, string> = {};
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const res = await fetch(`${mockBase}/api/v1/sales-data/template`, { headers });
  if (!res.ok) throw new Error("Could not download template");

  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "salesdatatemplate.csv";
  a.click();
  URL.revokeObjectURL(url);
}

export interface SalesDataListRow {
  lease_code: string;
  lease_id?: number;
  brand_name?: string;
  mall_name?: string;
  property_name?: string;
  company_name?: string;
  sales_period_start: string;
  sales_period_end: string;
  status: string;
  sales_amt?: number | null;
  comment_text?: string | null;
  document_id?: string | null;
  updated_at?: string;
}

function apiBase(): string {
  return (process.env.NEXT_PUBLIC_MOCK_BASE_URL ?? "http://localhost:8001").replace(/\/$/, "");
}

function unwrapSalesDataRows(body: unknown): SalesDataListRow[] {
  if (Array.isArray(body)) return body as SalesDataListRow[];
  if (!body || typeof body !== "object") return [];
  const data = (body as { data?: unknown }).data;
  if (Array.isArray(data)) return data as SalesDataListRow[];
  if (data && typeof data === "object" && Array.isArray((data as { list?: unknown }).list)) {
    return (data as { list: SalesDataListRow[] }).list;
  }
  return [];
}

/** Live GET /api/v1/sales-data (or mock). Unwraps both mock list and live `{ data: { list } }`. */
export async function fetchSalesDataRows(): Promise<SalesDataListRow[]> {
  const token = await ensureFreshToken();
  const headers: Record<string, string> = {};
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const res = await fetch(
    `${apiBase()}/api/v1/sales-data?disable_pagination=true`,
    { headers },
  );
  if (!res.ok) {
    throw new Error(`Could not load sales data (${res.status})`);
  }
  return unwrapSalesDataRows(await res.json());
}

export interface SalesDataDocument {
  url: string;
  contentType: string;
  fileName: string;
}

function fileNameFromDisposition(header: string | null): string | null {
  if (!header) return null;
  const utf = /filename\*=(?:UTF-8'')?([^;]+)/i.exec(header);
  if (utf?.[1]) return decodeURIComponent(utf[1].replace(/["']/g, "").trim());
  const plain = /filename=([^;]+)/i.exec(header);
  if (plain?.[1]) return decodeURIComponent(plain[1].replace(/["']/g, "").trim());
  return null;
}

/**
 * Portal DocumentViewWrapperSalesData: GET /document-view?document_id=
 * Returns an object URL the caller must revoke.
 */
export async function fetchSalesDataDocument(documentId: string): Promise<SalesDataDocument> {
  const token = await ensureFreshToken();
  const headers: Record<string, string> = {};
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const res = await fetch(
    `${apiBase()}/document-view?document_id=${encodeURIComponent(documentId)}`,
    { headers, cache: "no-store" },
  );
  if (!res.ok) {
    if (res.status === 404) throw new Error("Document not found");
    throw new Error(`Could not load document (${res.status})`);
  }
  const blob = await res.blob();
  const headerType = (res.headers.get("Content-Type") ?? "").split(";")[0].trim();
  const fileName =
    fileNameFromDisposition(res.headers.get("Content-Disposition")) ||
    `document-${documentId}`;
  return {
    url: URL.createObjectURL(blob),
    contentType: headerType || blob.type || "application/octet-stream",
    fileName,
  };
}
