/**
 * Demo login against the AI backend (POST /api/auth/login).
 *
 * One seeded user per actor in the ceiling inspection flow (backend
 * scripts/seed_users.py). The returned JWT carries the role, and the AI
 * backend's RBAC decides what each one may do — the demo does not.
 */

const TOKEN_KEY = "ceiling_demo_token";
const USER_KEY = "ceiling_demo_user";

export type DemoRole = "TENANT" | "DD_ENGINEER" | "FM_MANAGER";

export interface DemoRoleInfo {
  label: string;
  person: string;
  step: string;
  description: string;
  username: string;
  password: string;
  /** Starter messages shown in an empty chat. */
  prompts: string[];
}

export const DEMO_ROLES: Record<DemoRole, DemoRoleInfo> = {
  DD_ENGINEER: {
    label: "RDD Project Manager",
    person: "Sara",
    step: "Level 1 review",
    description: "DD_ENGINEER. Uploads the inspection report, approves it, or asks for more information.",
    username: "sara@cenomi.com",
    password: "test1234",
    prompts: [],
  },
  FM_MANAGER: {
    label: "FM Manager",
    person: "Khalid",
    step: "Level 2 approval",
    description: "Approves once the report is on file, or asks for more information.",
    username: "khalid@cenomi.com",
    password: "test1234",
    prompts: [],
  },
  TENANT: {
    label: "Tenant",
    person: "Authorized Signatory",
    step: "Raises the request, later acknowledges the report",
    description:
      "Holds CAN_RAISE_HANDOVER_SR. Raises an Open or Close Ceiling Inspection in chat, and acknowledges the submitted report at the end.",
    username: "tenant.signatory@cenomi.com",
    password: "test1234",
    prompts: [
      "Raise an OCI for lease t0105712",
      "I need a close ceiling inspection for lease t0105712",
      "Raise a ceiling inspection for lease t0105712",
    ],
  },
};

export interface CurrentUser {
  role: DemoRole;
  token: string;
}

export function aiBase(): string {
  return (process.env.NEXT_PUBLIC_AI_BASE_URL ?? "http://localhost:8000").replace(/\/$/, "");
}

export async function loginWithRole(role: DemoRole): Promise<CurrentUser> {
  const info = DEMO_ROLES[role];
  let res: Response;
  try {
    res = await fetch(`${aiBase()}/api/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: info.username, password: info.password }),
    });
  } catch {
    throw new Error(
      `Cannot reach the AI backend at ${aiBase()}. Start it with: cd backend && uvicorn app.main:app --reload --port 8000`
    );
  }
  if (!res.ok) {
    const detail = await res.text().catch(() => res.statusText);
    throw new Error(
      `Login failed for ${info.username} (${res.status}): ${detail}. Run scripts/seed_users.py in the backend first.`
    );
  }
  const data = await res.json();
  const user: CurrentUser = { role, token: data.access_token };
  localStorage.setItem(TOKEN_KEY, user.token);
  localStorage.setItem(USER_KEY, JSON.stringify(user));
  return user;
}

export function logout(): void {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
}

export function getStoredUser(): CurrentUser | null {
  if (typeof window === "undefined") return null;
  const raw = localStorage.getItem(USER_KEY);
  if (!raw) return null;
  try {
    const user = JSON.parse(raw) as CurrentUser;
    return DEMO_ROLES[user.role] ? user : null;
  } catch {
    return null;
  }
}

function jwtExpired(token: string, skewSeconds = 60): boolean {
  try {
    const part = token.split(".")[1];
    const padded = part + "=".repeat((4 - (part.length % 4)) % 4);
    const payload = JSON.parse(atob(padded.replace(/-/g, "+").replace(/_/g, "/")));
    return Date.now() / 1000 >= Number(payload.exp) - skewSeconds;
  } catch {
    return true;
  }
}

/** A valid token, re-logging in silently when a long demo outlives the JWT. */
export async function freshToken(): Promise<string | null> {
  const user = getStoredUser();
  if (!user) return null;
  if (!jwtExpired(user.token)) return user.token;
  try {
    return (await loginWithRole(user.role)).token;
  } catch {
    return user.token;
  }
}
