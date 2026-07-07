/**
 * stage-actions.spec.ts — StageActions button visibility and transitions
 *
 * Verifies the correct action buttons appear for each role+stage combination,
 * and that the "Submit Report" → "Final Approve" button transition works.
 *
 * Manual guide references:
 *   §5 Step 2.5: "Save Progress" and "Approve Review" buttons above chat input
 *   §6 Step 3.5: "Submit Report" button, then it changes to "Final Approve"
 *   §8 RBAC table: Mall Manager / FM Manager cannot act on wrong stages
 */

import { test, expect } from "@playwright/test";
import {
  injectAuth,
  mockChatAlways,
  mockNextChatResponse,
  fmReviewEntryResponse,
  fmApproveResponse,
  rddReviewEntryResponse,
  rddSubmitReportResponse,
  rddFinalApproveResponse,
} from "./helpers/api-mocks";

const CHAT_URL = "/service-request-chat";

// ─── Helper: navigate to chat with a specific stage already active ────────────

async function openSrInStage(
  page: import("@playwright/test").Page,
  srId: string,
  response: ReturnType<typeof fmReviewEntryResponse>,
): Promise<void> {
  await mockChatAlways(page, response);
  await page.goto(CHAT_URL);
  await page.getByPlaceholder(/Enter SR ID/i).fill(srId);
  await page.getByRole("button", { name: "Open" }).click();
  // Wait for the bot's response to appear
  await page.waitForResponse("**/api/chat/service-request");
}

// ─── FM Manager buttons ───────────────────────────────────────────────────────

test.describe("FM Manager in FM_REVIEW stage", () => {
  test("shows Save Progress and Approve Review buttons", async ({ page }) => {
    await injectAuth(page, "FM_MANAGER");
    await openSrInStage(page, "SR-001", fmReviewEntryResponse("SR-001"));

    await expect(
      page.getByRole("button", { name: "Save Progress" }),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Approve Review" }),
    ).toBeVisible();
  });

  test("clicking Save Progress fires save_fm_progress action", async ({
    page,
  }) => {
    await injectAuth(page, "FM_MANAGER");
    await openSrInStage(page, "SR-001", fmReviewEntryResponse("SR-001"));

    // Next chat call should carry the save action
    const requestPromise = page.waitForRequest("**/api/chat/service-request");
    await mockChatAlways(page, fmReviewEntryResponse("SR-001")); // response for the action call
    await page.getByRole("button", { name: "Save Progress" }).click();

    const req = await requestPromise;
    const body = req.postDataJSON() as Record<string, unknown>;
    expect(body.action).toBe("save_fm_progress");
  });

  test("clicking Approve Review fires approve_fm_review action", async ({
    page,
  }) => {
    await injectAuth(page, "FM_MANAGER");
    await openSrInStage(page, "SR-001", fmReviewEntryResponse("SR-001"));

    const requestPromise = page.waitForRequest("**/api/chat/service-request");
    await mockChatAlways(page, fmApproveResponse("SR-001"));
    await page.getByRole("button", { name: "Approve Review" }).click();

    const req = await requestPromise;
    const body = req.postDataJSON() as Record<string, unknown>;
    expect(body.action).toBe("approve_fm_review");
  });

  test("after FM approval, stage advances to RDD_REVIEW and FM buttons disappear", async ({
    page,
  }) => {
    await injectAuth(page, "FM_MANAGER");
    await openSrInStage(page, "SR-001", fmReviewEntryResponse("SR-001"));

    await mockChatAlways(page, fmApproveResponse("SR-001"));
    await page.getByRole("button", { name: "Approve Review" }).click();
    await page.waitForResponse("**/api/chat/service-request");

    // FM action buttons should no longer be visible after stage advances
    await expect(
      page.getByRole("button", { name: "Save Progress" }),
    ).not.toBeVisible();
    await expect(
      page.getByRole("button", { name: "Approve Review" }),
    ).not.toBeVisible();
  });
});

test.describe("Operations role in FM_REVIEW stage", () => {
  test("also shows Save Progress and Approve Review buttons", async ({
    page,
  }) => {
    await injectAuth(page, "OPERATIONS");
    await openSrInStage(page, "SR-001", fmReviewEntryResponse("SR-001"));

    await expect(
      page.getByRole("button", { name: "Save Progress" }),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Approve Review" }),
    ).toBeVisible();
  });
});

// ─── DD Engineer buttons ──────────────────────────────────────────────────────

test.describe("DD Engineer in RDD_REVIEW stage", () => {
  test("shows Submit Report button initially", async ({ page }) => {
    await injectAuth(page, "DD_ENGINEER");
    await openSrInStage(page, "SR-001", rddReviewEntryResponse("SR-001"));

    await expect(
      page.getByRole("button", { name: "Submit Report" }),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Final Approve" }),
    ).not.toBeVisible();
  });

  test("clicking Submit Report fires submit_rdd_report action", async ({
    page,
  }) => {
    await injectAuth(page, "DD_ENGINEER");
    await openSrInStage(page, "SR-001", rddReviewEntryResponse("SR-001"));

    const requestPromise = page.waitForRequest("**/api/chat/service-request");
    await mockChatAlways(page, rddSubmitReportResponse("SR-001"));
    await page.getByRole("button", { name: "Submit Report" }).click();

    const req = await requestPromise;
    const body = req.postDataJSON() as Record<string, unknown>;
    expect(body.action).toBe("submit_rdd_report");
  });

  test("Submit Report → Final Approve button transition (manual guide §6 Step 3.5)", async ({
    page,
  }) => {
    await injectAuth(page, "DD_ENGINEER");
    await openSrInStage(page, "SR-001", rddReviewEntryResponse("SR-001"));

    // "Submit Report" visible before clicking
    await expect(
      page.getByRole("button", { name: "Submit Report" }),
    ).toBeVisible();

    // Mock the submit response — rdd_status becomes REPORT_SUBMITTED
    await mockChatAlways(page, rddSubmitReportResponse("SR-001"));
    await page.getByRole("button", { name: "Submit Report" }).click();
    await page.waitForResponse("**/api/chat/service-request");

    // "Submit Report" should disappear; "Final Approve" should appear
    await expect(
      page.getByRole("button", { name: "Submit Report" }),
    ).not.toBeVisible();
    await expect(
      page.getByRole("button", { name: "Final Approve" }),
    ).toBeVisible();
  });

  test("clicking Final Approve fires approve_rdd_final action", async ({
    page,
  }) => {
    await injectAuth(page, "DD_ENGINEER");

    // Start at REPORT_SUBMITTED state so "Final Approve" is shown immediately
    await mockChatAlways(page, {
      ...rddSubmitReportResponse("SR-001"),
      state: {
        workflow_stage: "RDD_REVIEW",
        sr_id: "SR-001",
        rdd_status: "REPORT_SUBMITTED",
      },
    });
    await page.goto(CHAT_URL);
    await page.getByPlaceholder(/Enter SR ID/i).fill("SR-001");
    await page.getByRole("button", { name: "Open" }).click();
    await page.waitForResponse("**/api/chat/service-request");

    await expect(
      page.getByRole("button", { name: "Final Approve" }),
    ).toBeVisible();

    const requestPromise = page.waitForRequest("**/api/chat/service-request");
    await mockChatAlways(page, rddFinalApproveResponse("SR-001"));
    await page.getByRole("button", { name: "Final Approve" }).click();

    const req = await requestPromise;
    const body = req.postDataJSON() as Record<string, unknown>;
    expect(body.action).toBe("approve_rdd_final");
  });
});

// ─── RBAC — wrong role sees no action buttons ─────────────────────────────────

test.describe("RBAC — cross-role button suppression (manual guide §8)", () => {
  test("Mall Manager in FM_REVIEW sees no action buttons", async ({ page }) => {
    await injectAuth(page, "MALL_MANAGER");
    await mockChatAlways(page, fmReviewEntryResponse("SR-001"));
    await page.goto(CHAT_URL);

    // Mall Manager can't enter the SR via the sidebar (no Continue Existing SR
    // panel after SR ID is set), but we can force the state via the chat mock.
    // Trigger via chat input instead.
    await page
      .getByPlaceholder(/describe your service request/i)
      .fill("open SR SR-001");
    await page.getByRole("button", { name: /send/i }).click();
    await page.waitForResponse("**/api/chat/service-request");

    // Even if stage=FM_REVIEW, Mall Manager must NOT see FM action buttons
    await expect(
      page.getByRole("button", { name: "Save Progress" }),
    ).not.toBeVisible();
    await expect(
      page.getByRole("button", { name: "Approve Review" }),
    ).not.toBeVisible();
  });

  test("FM Manager in RDD_REVIEW sees no action buttons", async ({ page }) => {
    await injectAuth(page, "FM_MANAGER");
    await mockChatAlways(page, rddReviewEntryResponse("SR-001"));
    await page.goto(CHAT_URL);

    await page
      .getByPlaceholder(/describe your service request/i)
      .fill("open SR SR-001");
    await page.getByRole("button", { name: /send/i }).click();
    await page.waitForResponse("**/api/chat/service-request");

    await expect(
      page.getByRole("button", { name: "Submit Report" }),
    ).not.toBeVisible();
    await expect(
      page.getByRole("button", { name: "Final Approve" }),
    ).not.toBeVisible();
  });

  test("Mall Manager in RDD_REVIEW sees no action buttons", async ({ page }) => {
    await injectAuth(page, "MALL_MANAGER");
    await mockChatAlways(page, rddReviewEntryResponse("SR-001"));
    await page.goto(CHAT_URL);

    await page
      .getByPlaceholder(/describe your service request/i)
      .fill("open SR SR-001");
    await page.getByRole("button", { name: /send/i }).click();
    await page.waitForResponse("**/api/chat/service-request");

    await expect(
      page.getByRole("button", { name: "Submit Report" }),
    ).not.toBeVisible();
    await expect(
      page.getByRole("button", { name: "Final Approve" }),
    ).not.toBeVisible();
  });

  test("DD Engineer in FM_REVIEW sees no action buttons", async ({ page }) => {
    await injectAuth(page, "DD_ENGINEER");
    await mockChatAlways(page, fmReviewEntryResponse("SR-001"));
    await page.goto(CHAT_URL);

    await page.getByPlaceholder(/Enter SR ID/i).fill("SR-001");
    await page.getByRole("button", { name: "Open" }).click();
    await page.waitForResponse("**/api/chat/service-request");

    await expect(
      page.getByRole("button", { name: "Save Progress" }),
    ).not.toBeVisible();
    await expect(
      page.getByRole("button", { name: "Approve Review" }),
    ).not.toBeVisible();
  });
});

// ─── Button state ─────────────────────────────────────────────────────────────

test.describe("Action buttons — disabled state while pending", () => {
  test("buttons are disabled while a request is in-flight", async ({
    page,
  }) => {
    await injectAuth(page, "FM_MANAGER");

    // Slow the API response so we can catch the disabled state
    await page.route("**/api/chat/service-request", async (route) => {
      await new Promise((r) => setTimeout(r, 500));
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(fmReviewEntryResponse("SR-001")),
      });
    });

    await page.goto(CHAT_URL);
    await page.getByPlaceholder(/Enter SR ID/i).fill("SR-001");
    await page.getByRole("button", { name: "Open" }).click();
    await page.waitForResponse("**/api/chat/service-request");

    // After first response, buttons are enabled
    const saveBtn = page.getByRole("button", { name: "Save Progress" });
    await expect(saveBtn).toBeEnabled();

    // Start a new slow request
    await page.route("**/api/chat/service-request", async (route) => {
      await new Promise((r) => setTimeout(r, 800));
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(fmReviewEntryResponse("SR-001")),
      });
    });

    await saveBtn.click();
    // While in-flight, button should be disabled
    await expect(saveBtn).toBeDisabled();
  });
});
