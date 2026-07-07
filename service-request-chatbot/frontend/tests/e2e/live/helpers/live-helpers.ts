/**
 * Live-stack test helpers.
 *
 * These helpers work against the REAL backend (no API mocking).
 * They handle auth, timing for LLM responses, and state resets.
 */

import type { Page, Locator } from "@playwright/test";
import { expect } from "@playwright/test";

// ─── Constants ────────────────────────────────────────────────────────────────

export const BACKEND_URL = process.env.BACKEND_URL ?? "http://127.0.0.1:8000";
export const FRONTEND_URL = process.env.FRONTEND_URL ?? "http://localhost:3000";

/** Max wait for any single LLM response to arrive in the UI (90s). */
export const LLM_TIMEOUT = 90_000;

/** Credentials from manual guide §2. */
export const USERS = {
  MALL_MANAGER: { email: "aisha@cenomi.com",  password: "test1234", role: "MALL_MANAGER" },
  FM_MANAGER:   { email: "khalid@cenomi.com", password: "test1234", role: "FM_MANAGER"   },
  OPERATIONS:   { email: "omar@cenomi.com",   password: "test1234", role: "OPERATIONS"   },
  DD_ENGINEER:  { email: "sara@cenomi.com",   password: "test1234", role: "DD_ENGINEER"  },
  ADMIN:        { email: "admin@cenomi.com",  password: "test1234", role: "ADMIN"        },
} as const;

export type UserKey = keyof typeof USERS;

// ─── Auth helpers ─────────────────────────────────────────────────────────────

/** Log in through the real login page and wait for the chat page to load. */
export async function loginAs(page: Page, user: UserKey): Promise<void> {
  const { email, password } = USERS[user];
  await page.goto("/login");
  await page.getByLabel("Username").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.waitForURL("**/service-request-chat", { timeout: 15_000 });
}

/**
 * Faster variant: inject credentials directly into localStorage to skip the
 * login page round-trip.  Only use when the login flow itself is not under test.
 */
export async function injectLiveAuth(
  page: Page,
  user: UserKey,
  token: string,
): Promise<void> {
  const { role } = USERS[user];
  await page.addInitScript(
    ({ role: r, tk }: { role: string; tk: string }) => {
      const u = {
        userId: `live-${r.toLowerCase()}`,
        role: r,
        mallNames: ["Jawharat Jeddah"],
        accessToken: tk,
      };
      localStorage.setItem("cenomi_access_token", tk);
      localStorage.setItem("cenomi_user", JSON.stringify(u));
    },
    { role, tk: token },
  );
}

/**
 * Acquire a real JWT from the backend /api/auth/login endpoint.
 * Returns the token string so it can be injected into localStorage.
 */
export async function acquireToken(
  page: Page,
  user: UserKey,
): Promise<string> {
  const { email, password } = USERS[user];
  const response = await page.request.post(`${BACKEND_URL}/api/auth/login`, {
    data: { username: email, password },
  });
  expect(response.ok()).toBeTruthy();
  const body = await response.json() as { access_token: string };
  return body.access_token;
}

// ─── State reset ──────────────────────────────────────────────────────────────

/**
 * Resets the in-memory mock SR state on the backend.
 * Call before each test that creates a new SR lifecycle.
 * Manual guide §7: POST /dev/mock/reset
 */
export async function resetMockState(page: Page): Promise<void> {
  const res = await page.request.post(`${BACKEND_URL}/dev/mock/reset`);
  // In production mode the endpoint returns 404 — that's fine, just continue.
  if (!res.ok() && res.status() !== 404) {
    throw new Error(`Mock reset failed: ${res.status()} ${await res.text()}`);
  }
}

// ─── Chat interaction helpers ─────────────────────────────────────────────────

/** Locate the main chat input textarea / input. */
export function chatInput(page: Page): Locator {
  return page.getByPlaceholder(/describe your service request/i);
}

/**
 * Send a message and wait for the bot to finish responding.
 *
 * Strategy: after clicking Send, we wait for the loading indicator to disappear
 * (the "…" assistant bubble that renders while isPending=true).  This is more
 * robust than waiting for a fixed timeout.
 */
export async function sendMessage(
  page: Page,
  message: string,
  { timeout = LLM_TIMEOUT }: { timeout?: number } = {},
): Promise<void> {
  await chatInput(page).fill(message);
  await page.getByRole("button", { name: /send/i }).click();

  // Wait for the pending indicator to appear and then disappear
  await waitForBotReply(page, { timeout });
}

/**
 * Wait for the bot to finish replying (pending indicator disappears).
 */
export async function waitForBotReply(
  page: Page,
  { timeout = LLM_TIMEOUT }: { timeout?: number } = {},
): Promise<void> {
  // The loading bubble renders when isPending=true — it's an assistant bubble
  // with animate-bounce dots.  We wait for it to appear briefly, then disappear.
  // If the bot replies immediately (e.g. cached), the loading bubble may not
  // appear at all — so we just wait for any response from the network.
  await page.waitForResponse(
    (resp) => resp.url().includes("/api/chat/service-request") && resp.status() === 200,
    { timeout },
  );
  // Extra 500ms buffer for React to finish rendering
  await page.waitForTimeout(500);
}

// ─── Stage & lifecycle helpers ────────────────────────────────────────────────

/**
 * Wait until the debug panel shows the expected workflow_stage.
 * Requires debug mode to be ON.
 */
export async function waitForStage(
  page: Page,
  stage: string,
  { timeout = LLM_TIMEOUT }: { timeout?: number } = {},
): Promise<void> {
  await expect(page.locator(`text=${stage}`).first()).toBeVisible({ timeout });
}

/**
 * Enable the Debug toggle and wait for the panel to appear.
 */
export async function enableDebug(page: Page): Promise<void> {
  const toggle = page.getByRole("switch", { name: "Debug" });
  const isOn = await toggle.getAttribute("aria-checked");
  if (isOn !== "true") {
    await toggle.click();
  }
  // Debug panel should now show Session ID section
  await expect(page.getByText("Session ID")).toBeVisible({ timeout: 5_000 });
}

/**
 * Read the SR ID from the debug panel.
 * Returns null if the panel is not visible or no SR ID is set.
 */
export async function getDebugSrId(page: Page): Promise<string | null> {
  try {
    const srSection = page.locator("text=SR ID").first();
    await expect(srSection).toBeVisible({ timeout: 5_000 });
    // The SR ID text is in the sibling element below the "SR ID" label
    const srIdText = await page.evaluate(() => {
      const labels = Array.from(document.querySelectorAll("p"));
      const label = labels.find((el) => el.textContent?.trim() === "SR ID");
      return label?.nextElementSibling?.textContent?.trim() ?? null;
    });
    return srIdText;
  } catch {
    return null;
  }
}

// ─── Open existing SR (FM/DD flow) ────────────────────────────────────────────

/**
 * Enter an SR ID in the "Continue Existing SR" sidebar and click Open.
 */
export async function openExistingSR(page: Page, srId: string): Promise<void> {
  await page.getByPlaceholder(/Enter SR ID/i).fill(srId);
  await page.getByRole("button", { name: "Open" }).click();
  await waitForBotReply(page);
}

// ─── File upload helper ────────────────────────────────────────────────────────

/**
 * Upload a minimal PDF fixture via the Document Upload Panel.
 * Creates a temporary in-memory PDF blob to satisfy the file type requirement.
 */
export async function uploadTestDocument(
  page: Page,
  documentType: string,
): Promise<void> {
  // Select document type
  await page.selectOption("select[name='docType'], select", { label: documentType });

  // Create a minimal PDF in-memory and set it on the file input
  const fileChooserPromise = page.waitForEvent("filechooser");
  await page.getByText(/choose file/i).click();
  const fileChooser = await fileChooserPromise;

  // Use a minimal valid PDF bytes
  await fileChooser.setFiles({
    name: "test-document.pdf",
    mimeType: "application/pdf",
    buffer: Buffer.from(
      "%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj " +
      "2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj " +
      "3 0 obj<</Type/Page/MediaBox[0 0 612 792]>>endobj\n" +
      "xref\n0 4\n0000000000 65535 f\n0000000009 00000 n\n" +
      "0000000058 00000 n\n0000000115 00000 n\n" +
      "trailer<</Size 4/Root 1 0 R>>\nstartxref\n190\n%%EOF",
    ),
  });

  // Click upload
  await page.getByRole("button", { name: /upload/i }).click();

  // Wait for upload confirmation
  await page.waitForResponse(
    (resp) => resp.url().includes("/api/upload") && resp.status() === 200,
    { timeout: 30_000 },
  );
}

// ─── Backend health check ─────────────────────────────────────────────────────

/** Verify the backend is reachable before running live tests. */
export async function assertBackendHealthy(page: Page): Promise<void> {
  const res = await page.request.get(`${BACKEND_URL}/api/v1/health`);
  expect(
    res.ok(),
    `Backend health check failed. Is the backend running at ${BACKEND_URL}?`,
  ).toBeTruthy();
}

// ─── Phase helper: CREATE_SR one-shot ────────────────────────────────────────

/**
 * Run Phase 1 (CREATE_SR) using the "one-shot" technique from manual guide §4.
 * Sends all field data in a single message, waits for the confirmation card,
 * then clicks Confirm & Submit.
 *
 * Returns the SR ID from the debug panel.
 */
export async function runPhase1OneShotAndGetSrId(
  page: Page,
): Promise<string> {
  await enableDebug(page);

  // Step 1.2 — start the request
  await sendMessage(
    page,
    "I want to raise a handover request for Under Armour in Jawharat Jeddah",
  );

  // Step 1.3 — provide lease code if asked (or it may auto-match)
  // Wait a beat to see if a lease selection card appears
  await page.waitForTimeout(1_000);
  const leaseCard = page.getByText(/t0105712.*Under Armour/i);
  if (await leaseCard.isVisible()) {
    await leaseCard.click();
    await waitForBotReply(page);
  } else {
    // Check if bot is asking for lease code
    const askingForLease = await page.getByText(/lease code/i).isVisible();
    if (askingForLease) {
      await sendMessage(page, "t0105712");
    }
  }

  // Step 1.4 — one-shot all remaining fields
  await sendMessage(
    page,
    "Inspection start date is 1st August 2026, end date is 5th August 2026, " +
    "inspection conducted by FM Manager, description is routine handover " +
    "inspection for the new fitout, notes are all documents are ready",
  );

  // Step 1.5 — wait for confirmation card
  await expect(
    page.getByRole("button", { name: /confirm.*submit/i }).first(),
  ).toBeVisible({ timeout: LLM_TIMEOUT });

  // Click Confirm & Submit
  await page.getByRole("button", { name: /confirm.*submit/i }).first().click();
  await waitForBotReply(page);

  // Extract SR ID from debug panel
  const srId = await getDebugSrId(page);
  expect(srId, "SR ID must be set after successful CREATE_SR submission").toBeTruthy();
  return srId!;
}
