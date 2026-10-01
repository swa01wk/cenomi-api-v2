/**
 * Service requests raised in this browser, so the next actor can open them.
 *
 * The demo has no SR list endpoint (the portal is the real list). Like the
 * sales-data demo's client-side notifications, the ids live in localStorage:
 * sign in as the Tenant, raise one, switch role, open it.
 */

const KEY = "ceiling_demo_requests";

export interface RecentRequest {
  srId: string;
  summary: string;
  createdAt: string;
}

export function listRecent(): RecentRequest[] {
  try {
    return JSON.parse(localStorage.getItem(KEY) ?? "[]") as RecentRequest[];
  } catch {
    return [];
  }
}

export function rememberRequest(srId: string, summary: string): void {
  const rest = listRecent().filter((r) => r.srId !== srId);
  const next = [{ srId, summary, createdAt: new Date().toISOString() }, ...rest].slice(0, 20);
  localStorage.setItem(KEY, JSON.stringify(next));
}

export function clearRecent(): void {
  localStorage.removeItem(KEY);
}
