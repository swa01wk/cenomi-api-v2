/**
 * Upload client — sends CSV file to the AI backend upload endpoint.
 *
 * Endpoint: POST /api/v2/sales-data/upload  (AI backend, port 8000)
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
  a.download = "sales_data_template.csv";
  a.click();
  URL.revokeObjectURL(url);
}
