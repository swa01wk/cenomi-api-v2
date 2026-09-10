import type {
  FeedbackPayload,
  MetricsSummary,
  PaginatedTraceList,
  TraceDetail,
  TraceFiltersState,
} from "@/lib/types/observability";
import { getStoredToken } from "@/lib/api/auth-client";

// Observability trace/session endpoints live under /api (no v1 prefix).
// Only the metrics summary endpoint uses /api/v1.
function apiBase(): string {
  const base = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";
  return base.replace(/\/$/, "");
}

function v1Base(): string {
  const raw = process.env.NEXT_PUBLIC_API_V1_PREFIX;
  const prefix = raw && raw.length > 0 ? raw : "/api/v1";
  return `${apiBase()}${prefix}`;
}

function observabilityBase(): string {
  return `${apiBase()}/api/observability`;
}

// ─── Trace list ───────────────────────────────────────────────────────────────

export async function listTraces(
  filters?: Partial<TraceFiltersState>,
): Promise<PaginatedTraceList> {
  const params = new URLSearchParams();
  if (filters?.status) params.set("status", filters.status);
  if (filters?.agent) params.set("agent", filters.agent);
  if (filters?.intent) params.set("intent", filters.intent);
  if (filters?.user_id) params.set("user_id", filters.user_id);
  if (filters?.date_from) params.set("from_date", filters.date_from);
  if (filters?.date_to) params.set("to_date", filters.date_to);
  if (filters?.error_only) params.set("has_error", "true");
  if (filters?.page) params.set("page", String(filters.page));
  if (filters?.page_size) params.set("page_size", String(filters.page_size));

  const qs = params.toString();
  const url = `${observabilityBase()}/traces${qs ? `?${qs}` : ""}`;

  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) {
    throw new Error(`listTraces failed: ${res.status} ${res.statusText}`);
  }
  return (await res.json()) as PaginatedTraceList;
}

// ─── Trace detail ─────────────────────────────────────────────────────────────

export async function getTraceDetail(traceId: string): Promise<TraceDetail | null> {
  const res = await fetch(`${observabilityBase()}/traces/${traceId}`, {
    cache: "no-store",
  });
  if (res.status === 404) return null;
  if (!res.ok) {
    throw new Error(`getTraceDetail failed: ${res.status} ${res.statusText}`);
  }
  return (await res.json()) as TraceDetail;
}

// ─── Metrics ──────────────────────────────────────────────────────────────────

export async function submitFeedback(
  traceId: string,
  body: FeedbackPayload,
): Promise<void> {
  const token = getStoredToken();
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (token) headers.Authorization = `Bearer ${token}`;
  const res = await fetch(`${observabilityBase()}/traces/${traceId}/feedback`, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    throw new Error(`submitFeedback failed: ${res.status} ${res.statusText}`);
  }
}

export type MetricsWindow = { from?: string; to?: string; agent?: string };

function metricsQuery(window?: MetricsWindow): string {
  const params = new URLSearchParams();
  if (window?.from) params.set("from", window.from);
  if (window?.to) params.set("to", window.to);
  if (window?.agent) params.set("agent", window.agent);
  const qs = params.toString();
  return qs ? `?${qs}` : "";
}

export async function getMetricsSummary(window?: MetricsWindow): Promise<MetricsSummary> {
  const res = await fetch(`${v1Base()}/observability/metrics/summary${metricsQuery(window)}`, {
    cache: "no-store",
  });
  if (!res.ok) {
    throw new Error(`getMetricsSummary failed: ${res.status} ${res.statusText}`);
  }
  return (await res.json()) as MetricsSummary;
}

export async function getMetricsUsers(window?: MetricsWindow): Promise<{
  items: Array<Record<string, number | string>>;
  total: number;
  page: number;
  page_size: number;
}> {
  const res = await fetch(`${v1Base()}/observability/metrics/users${metricsQuery(window)}`, {
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`getMetricsUsers failed: ${res.status}`);
  return res.json();
}

export async function getMetricsFunnel(window?: MetricsWindow): Promise<{
  steps: Array<{ key: string; count: number }>;
  drop_off_by_stage: Array<{ workflow_stage: string; last_sessions: number }>;
}> {
  const res = await fetch(`${v1Base()}/observability/metrics/funnel${metricsQuery(window)}`, {
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`getMetricsFunnel failed: ${res.status}`);
  return res.json();
}

export async function getMetricsFeedback(window?: MetricsWindow): Promise<{
  thumbs_up: number;
  thumbs_down: number;
  thumbs_up_rate: number;
  useful: number;
  not_useful: number;
  usefulness_rate: number;
  csat_avg: number | null;
  csat_n: number;
  not_useful_reasons: Array<{ reason: string; count: number }>;
}> {
  const res = await fetch(`${v1Base()}/observability/metrics/feedback${metricsQuery(window)}`, {
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`getMetricsFeedback failed: ${res.status}`);
  return res.json();
}
