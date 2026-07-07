/**
 * Reusable API mock helpers for Playwright E2E tests.
 *
 * All tests mock the backend at the network layer so they run without
 * a live server. Use these helpers to control what the backend returns
 * for any given turn of the lifecycle.
 */

import type { Page } from "@playwright/test";

// ─── Types matching the backend response shape ────────────────────────────────

export interface ChatStatePayload {
  workflow_stage?: string | null;
  sr_id?: string | null;
  rdd_status?: string | null;
  fm_status?: string | null;
}

export interface ChatApiResponse {
  session_id: string;
  message: string;
  trace_id?: string | null;
  ui: {
    type: string;
    message: string;
    [key: string]: unknown;
  };
  state: ChatStatePayload;
  draft_preview?: null | Record<string, unknown>;
  sr_id?: string | null;
}

export type UserRole =
  | "MALL_MANAGER"
  | "FM_MANAGER"
  | "OPERATIONS"
  | "DD_ENGINEER"
  | "ADMIN";

// ─── Auth helpers ─────────────────────────────────────────────────────────────

/**
 * Injects a fake logged-in session into localStorage before the page loads.
 * This bypasses the real login endpoint and the AuthGuard redirect.
 */
export async function injectAuth(page: Page, role: UserRole): Promise<void> {
  await page.addInitScript(
    ({ role: r }: { role: UserRole }) => {
      const user = {
        userId: `test-${r.toLowerCase()}`,
        role: r,
        mallNames: ["Jawharat Jeddah"],
        accessToken: "fake-jwt-token",
      };
      localStorage.setItem("cenomi_access_token", "fake-jwt-token");
      localStorage.setItem("cenomi_user", JSON.stringify(user));
    },
    { role },
  );
}

/**
 * Mocks the /api/auth/login endpoint to return a successful login response
 * for the given role. Used in auth flow tests that go through the login page.
 */
export async function mockLoginEndpoint(
  page: Page,
  role: UserRole = "MALL_MANAGER",
): Promise<void> {
  await page.route("**/api/auth/login", async (route) => {
    const body = route.request().postDataJSON() as { username?: string };
    const email = body.username ?? "aisha@cenomi.com";
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        access_token: "fake-jwt-token",
        token_type: "bearer",
        user_id: `test-${role.toLowerCase()}`,
        role,
        mall_names: ["Jawharat Jeddah"],
        expires_in: 3600,
        email,
      }),
    });
  });
}

/**
 * Mocks the /api/auth/login endpoint to return 401 Unauthorized.
 */
export async function mockLoginFailure(page: Page): Promise<void> {
  await page.route("**/api/auth/login", async (route) => {
    await route.fulfill({
      status: 401,
      contentType: "application/json",
      body: JSON.stringify({ detail: "Invalid credentials" }),
    });
  });
}

// ─── Chat endpoint helpers ────────────────────────────────────────────────────

/**
 * Registers a one-time response for the next chat API call.
 * Subsequent calls fall through to any previously registered handler.
 */
export async function mockNextChatResponse(
  page: Page,
  response: Partial<ChatApiResponse>,
): Promise<void> {
  await page.route("**/api/chat/service-request", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(buildChatResponse(response)),
    });
    // Unroute after one use so subsequent calls get the next mock.
    await page.unroute("**/api/chat/service-request");
  });
}

/**
 * Registers a persistent handler that always returns the given response.
 * Useful for tests that only need one shape of response throughout.
 */
export async function mockChatAlways(
  page: Page,
  response: Partial<ChatApiResponse>,
): Promise<void> {
  await page.route("**/api/chat/service-request", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(buildChatResponse(response)),
    });
  });
}

// ─── Canned response factories ────────────────────────────────────────────────

/** Builds a minimal valid chat response, merging in any provided overrides. */
export function buildChatResponse(
  overrides: Partial<ChatApiResponse> = {},
): ChatApiResponse {
  const base: ChatApiResponse = {
    session_id: "test-session-001",
    message: "How can I help you?",
    trace_id: null,
    ui: {
      type: "text",
      message: "How can I help you?",
      ...(overrides.ui ?? {}),
    },
    state: {
      ...(overrides.state ?? {}),
    },
    draft_preview: null,
    sr_id: null,
  };
  return { ...base, ...overrides, ui: base.ui, state: base.state };
}

/** CREATE_SR submitted — SR ID returned, stage = CREATE_SR */
export function createSrResponse(srId = "SR-TEST-001"): ChatApiResponse {
  return buildChatResponse({
    message: `Your handover service request has been submitted. SR ID: ${srId}`,
    ui: {
      type: "text",
      message: `Your handover service request has been submitted. SR ID: ${srId}`,
    },
    state: { workflow_stage: "CREATE_SR", sr_id: srId },
    sr_id: srId,
  });
}

/** FM Manager opens SR — stage advances to FM_REVIEW */
export function fmReviewEntryResponse(srId = "SR-TEST-001"): ChatApiResponse {
  return buildChatResponse({
    message: "I've loaded the SR. You are now in FM Review mode.",
    ui: {
      type: "text",
      message: "I've loaded the SR. You are now in FM Review mode.",
    },
    state: { workflow_stage: "FM_REVIEW", sr_id: srId },
    sr_id: srId,
  });
}

/** FM approval succeeded — stage becomes RDD_REVIEW on next turn */
export function fmApproveResponse(srId = "SR-TEST-001"): ChatApiResponse {
  return buildChatResponse({
    message: "FM review approved successfully.",
    ui: { type: "text", message: "FM review approved successfully." },
    state: { workflow_stage: "RDD_REVIEW", sr_id: srId, fm_status: "APPROVED" },
    sr_id: srId,
  });
}

/** DD Engineer opens SR — stage = RDD_REVIEW */
export function rddReviewEntryResponse(srId = "SR-TEST-001"): ChatApiResponse {
  return buildChatResponse({
    message: "You are now in RDD Review mode.",
    ui: { type: "text", message: "You are now in RDD Review mode." },
    state: { workflow_stage: "RDD_REVIEW", sr_id: srId },
    sr_id: srId,
  });
}

/** RDD report submitted — rdd_status = REPORT_SUBMITTED */
export function rddSubmitReportResponse(srId = "SR-TEST-001"): ChatApiResponse {
  return buildChatResponse({
    message: "RDD report submitted successfully.",
    ui: { type: "text", message: "RDD report submitted successfully." },
    state: {
      workflow_stage: "RDD_REVIEW",
      sr_id: srId,
      rdd_status: "REPORT_SUBMITTED",
    },
    sr_id: srId,
  });
}

/** Final approval — SR_COMPLETED */
export function rddFinalApproveResponse(srId = "SR-TEST-001"): ChatApiResponse {
  return buildChatResponse({
    message:
      "Final approval complete. The handover service request is now completed.",
    ui: {
      type: "text",
      message:
        "Final approval complete. The handover service request is now completed.",
    },
    state: {
      workflow_stage: "SR_COMPLETED",
      sr_id: srId,
      rdd_status: "APPROVED",
    },
    sr_id: srId,
  });
}
