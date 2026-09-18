/**
 * Auth client for the Sales Data Agent demo.
 *
 * Performs a real login against the AI backend (/api/v1/auth/login) using
 * seeded demo credentials so the JWT returned contains the correct `roles`
 * claim.  This JWT is then forwarded on every chat turn so the AI backend's
 * RBAC (`intents_for_roles`) resolves the correct permitted intent set.
 *
 * Seeded credentials (seed_users.py):
 *   tenant.csv@cenomi.com  / test1234  → TENANT_CSV_UPLOADER  (tenant_profile_id 101)
 *   tenant.doc@cenomi.com  / test1234  → TENANT_DOC_UPLOADER  (tenant_profile_id 102)
 *   sales.approver@cenomi.com / test1234 → SALES_DATA_APPROVER
 *
 * The static token strings ("demo-tenant-csv" etc.) are still forwarded to
 * the mock-backend via the AI backend's `SalesDataApiClient.bearer_token`
 * so the mock-backend's `_TOKEN_ROLE_MAP` continues to scope data correctly.
 * The mock backend receives the raw token the AI backend's
 * `get_current_bearer_token()` forwards — which is now the real JWT.  To
 * keep the mock-backend's `_TOKEN_ROLE_MAP` working we add the real JWT as an
 * accepted token there (see mock-backend token alignment note in the PRD
 * analysis).  For the demo, we use a simpler approach: the mock-backend falls
 * back to the default role when the token is unrecognised, and we seed the
 * correct `tenant_profile_id` separately — OR we just update the mock-backend
 * to decode the `roles` claim from the JWT instead of a static map.
 *
 * Simplest working approach chosen here: perform a real /login and use the
 * returned JWT for the AI backend; the mock-backend will receive this same JWT
 * via the AI backend's forwarded token.  Update mock-backend `_TOKEN_ROLE_MAP`
 * to map the real JWTs instead of static strings.
 */

const TOKEN_KEY = "sales_data_agent_token";
const USER_KEY  = "sales_data_agent_user";

export type DemoRole = "TENANT" | "SALES_DATA_APPROVER";

export interface DemoRoleInfo {
  label: string;
  icon: string;
  description: string;
  username: string;
  password: string;
  type: "tenant" | "admin";
  notifications: MockNotification[];
}

export interface MockNotification {
  id: string;
  title: string;
  body: string;
  leaseCode: string;
  period: string;          // e.g. "2026-08-01"
  periodLabel: string;     // e.g. "August 2026"
  mallName: string;
  status: "pending" | "need_reupload" | "need_supporting_document" | "need_audited_sales";
  createdAt: string;
}

export const DEMO_ROLES: Record<DemoRole, DemoRoleInfo> = {
  TENANT: {
    label: "Tenant",
    icon: "🏪",
    description:
      "Tenant with pending CSV uploads plus file requests: sales re-upload, supporting document, and audited sales.",
    username: "tenant.csv@cenomi.com",
    password: "test1234",
    type: "tenant",
    notifications: [
      {
        id: "notif-1",
        title: "Sales Data Pending",
        body: "Your August 2026 sales data for Lease LC-1001 (Jeddah Mall) has not been submitted yet.",
        leaseCode: "LC-1001",
        period: "2026-08-01",
        periodLabel: "August 2026",
        mallName: "Jeddah Mall",
        status: "pending",
        createdAt: "2026-08-19T09:00:00Z",
      },
      {
        id: "notif-2",
        title: "Sales Data Pending",
        body: "Your August 2026 sales data for Lease LC-1002 (Riyadh Park) has not been submitted yet.",
        leaseCode: "LC-1002",
        period: "2026-08-01",
        periodLabel: "August 2026",
        mallName: "Riyadh Park",
        status: "pending",
        createdAt: "2026-08-19T09:00:00Z",
      },
      {
        id: "notif-3",
        title: "Sales Data Needs Re-upload",
        body: "The Sales Data team requested a PDF re-upload for Lease LC-1003 (Mall of Arabia) — August 2026.",
        leaseCode: "LC-1003",
        period: "2026-08-01",
        periodLabel: "August 2026",
        mallName: "Mall of Arabia",
        status: "need_reupload",
        createdAt: "2026-08-20T09:00:00Z",
      },
      {
        id: "notif-4",
        title: "Supporting Document Required",
        body: "The Sales Data team asked for a supporting document (POS report) for Lease LC-1004 (Riyadh Park) — July 2026.",
        leaseCode: "LC-1004",
        period: "2026-07-01",
        periodLabel: "July 2026",
        mallName: "Riyadh Park",
        status: "need_supporting_document",
        createdAt: "2026-08-21T09:00:00Z",
      },
      {
        id: "notif-5",
        title: "Audited Sales Required",
        body: "The Sales Data team asked for audited sales data for Lease LC-1005 (Mall of Arabia) — July 2026.",
        leaseCode: "LC-1005",
        period: "2026-07-01",
        periodLabel: "July 2026",
        mallName: "Mall of Arabia",
        status: "need_audited_sales",
        createdAt: "2026-08-22T09:00:00Z",
      },
    ],
  },
  SALES_DATA_APPROVER: {
    label: "Sales Data Team",
    icon: "✅",
    description:
      "Admin reviewer — approve submissions or Ask upload: sales data, supporting document, or audited sales.",
    username: "sales.approver@cenomi.com",
    password: "test1234",
    type: "admin",
    notifications: [],
  },
};

export interface CurrentUser {
  role: DemoRole;
  label: string;
  token: string;
  notifications: MockNotification[];
}

function aiBase(): string {
  // Auth router is mounted at /api (not /api/v1) in the AI backend's main.py.
  // Full login path: POST /api/auth/login
  const base =
    (typeof process !== "undefined" && process.env.NEXT_PUBLIC_AI_BASE_URL) ??
    "http://localhost:8000";
  return `${base.replace(/\/$/, "")}/api`;
}

/**
 * Login with the selected demo role.
 *
 * Calls POST /api/v1/auth/login on the AI backend using the seeded credentials
 * and stores the returned JWT.  The JWT contains the `roles` claim which the
 * AI backend uses for RBAC enforcement.
 */
export async function loginWithRole(role: DemoRole): Promise<CurrentUser> {
  const info = DEMO_ROLES[role];

  let res: Response;
  try {
    res = await fetch(`${aiBase()}/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: info.username, password: info.password }),
    });
  } catch {
    throw new Error(
      "Cannot reach the AI backend on localhost:8000. Start it with: cd backend && uvicorn app.main:app --reload --port 8000"
    );
  }

  if (!res.ok) {
    const errText = await res.text().catch(() => res.statusText);
    throw new Error(
      `Login failed for ${info.username} (${res.status}): ${errText}. ` +
      "Make sure the AI backend is running and seed_users.py has been executed."
    );
  }

  const data = await res.json();
  const token: string = data.access_token;

  const user: CurrentUser = { role, label: info.label, token, notifications: info.notifications };
  if (typeof window !== "undefined") {
    localStorage.setItem(TOKEN_KEY, token);
    localStorage.setItem(USER_KEY, JSON.stringify(user));
  }
  return user;
}

export function logout(): void {
  if (typeof window !== "undefined") {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
  }
}

export function getStoredToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(TOKEN_KEY);
}

function _jwtExpired(token: string, skewSeconds = 60): boolean {
  try {
    const parts = token.split(".");
    if (parts.length !== 3) return true;
    const padded = parts[1] + "=".repeat((4 - (parts[1].length % 4)) % 4);
    const payload = JSON.parse(atob(padded.replace(/-/g, "+").replace(/_/g, "/")));
    const exp = Number(payload.exp);
    if (!exp) return true;
    return Date.now() / 1000 >= exp - skewSeconds;
  } catch {
    return true;
  }
}

/**
 * Return a valid JWT, silently re-logging in if the stored token is expired.
 * Demo sessions often outlive the default 60-minute JWT.
 */
export async function ensureFreshToken(): Promise<string | null> {
  const user = getStoredUser();
  const token = getStoredToken();
  if (!user) return token;
  if (token && !_jwtExpired(token)) return token;

  try {
    const refreshed = await loginWithRole(user.role);
    return refreshed.token;
  } catch {
    return token;
  }
}

export function getStoredUser(): CurrentUser | null {
  if (typeof window === "undefined") return null;
  const raw = localStorage.getItem(USER_KEY);
  if (!raw) return null;
  try {
    const user = JSON.parse(raw) as CurrentUser;
    const info = DEMO_ROLES[user.role];
    if (info) {
      user.label = info.label;
      user.notifications = info.notifications;
    }
    return user;
  } catch {
    return null;
  }
}

export function isAuthenticated(): boolean {
  return !!getStoredToken();
}
