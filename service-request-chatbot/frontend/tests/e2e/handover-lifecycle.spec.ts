/**
 * handover-lifecycle.spec.ts — Full RDD Handover lifecycle E2E (frontend)
 *
 * Follows the exact sequence from the manual testing guide:
 *   Phase 1 — Mall Manager creates SR (§4)
 *   Phase 2 — FM Manager reviews SR (§5)
 *   Phase 3 — DD Engineer final approval (§6)
 *
 * All backend API calls are mocked via page.route().
 * Tests validate the UI state changes described in manual guide §8.
 */

import { test, expect } from "@playwright/test";
import {
  injectAuth,
  mockNextChatResponse,
  mockChatAlways,
  createSrResponse,
  fmReviewEntryResponse,
  fmApproveResponse,
  rddReviewEntryResponse,
  rddSubmitReportResponse,
  rddFinalApproveResponse,
  buildChatResponse,
} from "./helpers/api-mocks";

const CHAT_URL = "/service-request-chat";
const SR_ID = "SR-LIFECYCLE-E2E-001";

// ─── Phase 1 — Mall Manager creates SR ───────────────────────────────────────

test.describe("Phase 1 — Mall Manager creates SR (manual guide §4)", () => {
  test("full CREATE_SR flow: send message → stepper appears → SR ID shown", async ({
    page,
  }) => {
    await injectAuth(page, "MALL_MANAGER");
    await mockChatAlways(page, createSrResponse(SR_ID));

    await page.goto(CHAT_URL);

    // Before first message: stepper hidden, placeholder shown
    await expect(
      page.getByText("Describe your service request to get started."),
    ).toBeVisible();
    await expect(page.getByText("Lifecycle Progress")).not.toBeVisible();

    // Step 1.2 — Start the request
    await page
      .getByPlaceholder(/describe your service request/i)
      .fill(
        "I want to raise a handover request for Under Armour in Jawharat Jeddah",
      );
    await page.getByRole("button", { name: /send/i }).click();
    await page.waitForResponse("**/api/chat/service-request");

    // Step 1.5 verification: LifecycleStepper appears
    await expect(page.getByText("Lifecycle Progress")).toBeVisible();

    // Bot response contains SR ID
    await expect(page.getByText(SR_ID)).toBeVisible();

    // Stage label visible in debug panel
    await page.getByRole("switch", { name: "Debug" }).click();
    await expect(page.getByText("CREATE_SR")).toBeVisible();
    await expect(page.getByText(SR_ID)).toHaveCount(2); // stepper + debug panel
  });

  test("user message appears in chat thread after sending", async ({ page }) => {
    await injectAuth(page, "MALL_MANAGER");
    await mockChatAlways(page, createSrResponse(SR_ID));

    await page.goto(CHAT_URL);

    const msg = "I want to raise a handover request for Under Armour";
    await page.getByPlaceholder(/describe your service request/i).fill(msg);
    await page.getByRole("button", { name: /send/i }).click();

    await expect(page.getByText(msg)).toBeVisible();
  });

  test("confirmation card renders when bot returns confirmation_card UI", async ({
    page,
  }) => {
    await injectAuth(page, "MALL_MANAGER");
    await mockChatAlways(
      page,
      buildChatResponse({
        message: "Please review and confirm your request.",
        ui: {
          type: "confirmation_card",
          message: "Please review and confirm your request.",
          confirmAction: "confirm",
          fields: [
            { key: "lease_code", label: "Lease Code", value: "t0105712" },
            { key: "brand", label: "Brand", value: "Under Armour" },
            { key: "mall", label: "Mall", value: "Jawharat Jeddah" },
          ],
        },
        state: { workflow_stage: "CREATE_SR", sr_id: null },
      }),
    );

    await page.goto(CHAT_URL);
    await page
      .getByPlaceholder(/describe your service request/i)
      .fill("Create handover SR");
    await page.getByRole("button", { name: /send/i }).click();
    await page.waitForResponse("**/api/chat/service-request");

    // Confirmation card should appear in the message area
    await expect(page.getByText("Under Armour")).toBeVisible();
    await expect(page.getByText("t0105712")).toBeVisible();
  });

  test("lease selection card renders when bot returns lease_selection UI", async ({
    page,
  }) => {
    await injectAuth(page, "MALL_MANAGER");
    await mockChatAlways(
      page,
      buildChatResponse({
        message: "I found multiple leases. Please select one.",
        ui: {
          type: "lease_selection",
          message: "I found multiple leases. Please select one.",
          leases: [
            {
              id: "t0105712",
              tenantName: "Brand Under Armour",
              propertyName: "Jawharat Jeddah",
              unitNumber: "FF050",
            },
          ],
        },
        state: { workflow_stage: "CREATE_SR", sr_id: null },
      }),
    );

    await page.goto(CHAT_URL);
    await page
      .getByPlaceholder(/describe your service request/i)
      .fill("Handover for Under Armour");
    await page.getByRole("button", { name: /send/i }).click();
    await page.waitForResponse("**/api/chat/service-request");

    // Lease selection card should appear in sidebar
    await expect(page.getByText("Brand Under Armour")).toBeVisible();
    await expect(page.getByText("Jawharat Jeddah")).toBeVisible();
  });
});

// ─── Phase 2 — FM Manager reviews SR ─────────────────────────────────────────

test.describe("Phase 2 — FM Manager reviews SR (manual guide §5)", () => {
  test("Step 2.2: FM Manager opens existing SR and reaches FM_REVIEW", async ({
    page,
  }) => {
    await injectAuth(page, "FM_MANAGER");
    await mockChatAlways(page, fmReviewEntryResponse(SR_ID));

    await page.goto(CHAT_URL);

    // Step 2.2 — Open the existing SR
    await page.getByPlaceholder(/Enter SR ID/i).fill(SR_ID);
    await page.getByRole("button", { name: "Open" }).click();
    await page.waitForResponse("**/api/chat/service-request");

    // LifecycleStepper shows FM_REVIEW as active
    await expect(page.getByText("Lifecycle Progress")).toBeVisible();
    await expect(page.getByText("FM Review")).toBeVisible();

    // FM action buttons appear (Step 2.5)
    await expect(
      page.getByRole("button", { name: "Save Progress" }),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Approve Review" }),
    ).toBeVisible();
  });

  test("Step 2.5a: Save Progress sends correct action to backend", async ({
    page,
  }) => {
    await injectAuth(page, "FM_MANAGER");
    await mockNextChatResponse(page, fmReviewEntryResponse(SR_ID));
    await page.goto(CHAT_URL);

    await page.getByPlaceholder(/Enter SR ID/i).fill(SR_ID);
    await page.getByRole("button", { name: "Open" }).click();
    await page.waitForResponse("**/api/chat/service-request");

    const requestPromise = page.waitForRequest("**/api/chat/service-request");
    await mockChatAlways(page, fmReviewEntryResponse(SR_ID)); // progress saved response
    await page.getByRole("button", { name: "Save Progress" }).click();

    const req = await requestPromise;
    const body = req.postDataJSON() as Record<string, unknown>;
    expect(body.action).toBe("save_fm_progress");
    // SR ID should be included in the request
    expect(body.sr_id).toBe(SR_ID);
  });

  test("Step 2.5b: Approve Review advances stage to RDD_REVIEW", async ({
    page,
  }) => {
    await injectAuth(page, "FM_MANAGER");
    await mockNextChatResponse(page, fmReviewEntryResponse(SR_ID));
    await page.goto(CHAT_URL);

    await page.getByPlaceholder(/Enter SR ID/i).fill(SR_ID);
    await page.getByRole("button", { name: "Open" }).click();
    await page.waitForResponse("**/api/chat/service-request");

    await mockChatAlways(page, fmApproveResponse(SR_ID));
    await page.getByRole("button", { name: "Approve Review" }).click();
    await page.waitForResponse("**/api/chat/service-request");

    // Bot message confirms approval
    await expect(page.getByText("FM review approved successfully.")).toBeVisible();

    // FM buttons gone; stepper shows RDD_REVIEW as active
    await expect(
      page.getByRole("button", { name: "Approve Review" }),
    ).not.toBeVisible();
    await expect(page.getByText("RDD Review")).toBeVisible();
  });

  test("Document Upload Panel visible during FM_REVIEW", async ({ page }) => {
    await injectAuth(page, "FM_MANAGER");
    await mockChatAlways(page, fmReviewEntryResponse(SR_ID));

    await page.goto(CHAT_URL);
    await page.getByPlaceholder(/Enter SR ID/i).fill(SR_ID);
    await page.getByRole("button", { name: "Open" }).click();
    await page.waitForResponse("**/api/chat/service-request");

    // DocumentUploadPanel is rendered in the sidebar when isLifecycleStage + srId + sessionId
    await expect(
      page.getByText(/upload/i).first(),
    ).toBeVisible();
  });
});

// ─── Phase 3 — DD Engineer final approval ────────────────────────────────────

test.describe("Phase 3 — DD Engineer RDD review (manual guide §6)", () => {
  test("Step 3.2: DD Engineer opens SR in RDD_REVIEW stage", async ({
    page,
  }) => {
    await injectAuth(page, "DD_ENGINEER");
    await mockChatAlways(page, rddReviewEntryResponse(SR_ID));

    await page.goto(CHAT_URL);
    await page.getByPlaceholder(/Enter SR ID/i).fill(SR_ID);
    await page.getByRole("button", { name: "Open" }).click();
    await page.waitForResponse("**/api/chat/service-request");

    await expect(page.getByText("Lifecycle Progress")).toBeVisible();
    // "Submit Report" button appears (Step 3.5)
    await expect(
      page.getByRole("button", { name: "Submit Report" }),
    ).toBeVisible();
  });

  test("Step 3.5 → 3.6: Submit Report → Final Approve button transition", async ({
    page,
  }) => {
    await injectAuth(page, "DD_ENGINEER");

    await mockNextChatResponse(page, rddReviewEntryResponse(SR_ID));
    await page.goto(CHAT_URL);
    await page.getByPlaceholder(/Enter SR ID/i).fill(SR_ID);
    await page.getByRole("button", { name: "Open" }).click();
    await page.waitForResponse("**/api/chat/service-request");

    // Step 3.5: Submit report
    await mockNextChatResponse(page, rddSubmitReportResponse(SR_ID));
    await page.getByRole("button", { name: "Submit Report" }).click();
    await page.waitForResponse("**/api/chat/service-request");

    await expect(page.getByText("RDD report submitted successfully.")).toBeVisible();

    // "Submit Report" → "Final Approve"
    await expect(
      page.getByRole("button", { name: "Submit Report" }),
    ).not.toBeVisible();
    await expect(
      page.getByRole("button", { name: "Final Approve" }),
    ).toBeVisible();

    // Step 3.6: Final approval
    await mockChatAlways(page, rddFinalApproveResponse(SR_ID));
    await page.getByRole("button", { name: "Final Approve" }).click();
    await page.waitForResponse("**/api/chat/service-request");

    // LifecycleStepper shows all complete
    await expect(
      page.getByText(
        "Final approval complete. The handover service request is now completed.",
      ),
    ).toBeVisible();

    // All 4 stepper steps should be green
    const greenCircles = page.locator('[class*="bg-green-500"]');
    await expect(greenCircles).toHaveCount(4);
  });
});

// ─── Cross-phase SR continuity ────────────────────────────────────────────────

test.describe("SR ID continuity across turns", () => {
  test("SR ID is passed in every subsequent chat request after being set", async ({
    page,
  }) => {
    await injectAuth(page, "FM_MANAGER");

    // First call: enter FM_REVIEW
    await mockNextChatResponse(page, fmReviewEntryResponse(SR_ID));
    await page.goto(CHAT_URL);
    await page.getByPlaceholder(/Enter SR ID/i).fill(SR_ID);
    await page.getByRole("button", { name: "Open" }).click();
    await page.waitForResponse("**/api/chat/service-request");

    // Second call: action button — SR ID must be in body
    const requestPromise = page.waitForRequest("**/api/chat/service-request");
    await mockChatAlways(page, fmReviewEntryResponse(SR_ID));
    await page.getByRole("button", { name: "Save Progress" }).click();

    const req = await requestPromise;
    const body = req.postDataJSON() as Record<string, unknown>;
    expect(body.sr_id).toBe(SR_ID);
  });

  test("session ID is carried across turns", async ({ page }) => {
    await injectAuth(page, "MALL_MANAGER");
    await mockChatAlways(page, createSrResponse(SR_ID));

    await page.goto(CHAT_URL);

    // First turn — no session_id yet
    const req1Promise = page.waitForRequest("**/api/chat/service-request");
    await page
      .getByPlaceholder(/describe your service request/i)
      .fill("Start request");
    await page.getByRole("button", { name: /send/i }).click();
    const req1 = await req1Promise;
    const body1 = req1.postDataJSON() as Record<string, unknown>;
    expect(body1.session_id).toBeNull();
    await page.waitForResponse("**/api/chat/service-request");

    // Second turn — session_id from first response should be sent
    const req2Promise = page.waitForRequest("**/api/chat/service-request");
    await page
      .getByPlaceholder(/describe your service request/i)
      .fill("Follow-up");
    await page.getByRole("button", { name: /send/i }).click();
    const req2 = await req2Promise;
    const body2 = req2.postDataJSON() as Record<string, unknown>;
    expect(body2.session_id).toBe("test-session-001");
  });
});
