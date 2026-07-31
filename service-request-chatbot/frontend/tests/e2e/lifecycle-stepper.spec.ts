/**
 * lifecycle-stepper.spec.ts — LifecycleStepper UI component tests
 *
 * Verifies the sidebar stepper advances correctly through each stage,
 * matching the "What to Verify at Each Step" table in manual guide §8.
 *
 * Manual guide references:
 *   §4 Step 1.5: "Sidebar shows LifecycleStepper with CREATE_SR step completed"
 *   §5 Step 2.2: "The LifecycleStepper shows FM_REVIEW as the active stage"
 *   §6 Step 3.6: "LifecycleStepper shows all steps completed"
 */

import { test, expect } from "@playwright/test";
import {
  injectAuth,
  mockChatAlways,
  mockNextChatResponse,
  buildChatResponse,
  createSrResponse,
  fmReviewEntryResponse,
  fmApproveResponse,
  rddReviewEntryResponse,
  rddFinalApproveResponse,
} from "./helpers/api-mocks";

const CHAT_URL = "/service-request-chat";

test.describe("LifecycleStepper — hidden before SR exists", () => {
  test("stepper is not rendered before any chat turn", async ({ page }) => {
    await injectAuth(page, "MALL_MANAGER");
    await mockChatAlways(page, {}); // any generic response
    await page.goto(CHAT_URL);

    // LifecycleStepper only renders when srId is set
    await expect(page.getByText("Lifecycle Progress")).not.toBeVisible();
    // "Continue Existing SR" panel is shown instead (for FM/DD roles)
    // For MALL_MANAGER the stepper simply stays hidden
  });
});

test.describe("LifecycleStepper — CREATE_SR phase", () => {
  test("stepper appears and marks step 1 active after SR is created", async ({
    page,
  }) => {
    await injectAuth(page, "MALL_MANAGER");
    await mockChatAlways(page, createSrResponse("SR-TEST-001"));

    await page.goto(CHAT_URL);

    // Send the initial message
    await page.getByPlaceholder(/describe your service request/i).fill(
      "I want to raise a handover request for Under Armour in Jawharat Jeddah",
    );
    await page.getByRole("button", { name: /send/i }).click();

    // Stepper should now appear
    await expect(page.getByText("Lifecycle Progress")).toBeVisible();

    // Step 1 "Create SR" should be active (blue)
    const step1 = page.locator("text=Create SR").first();
    await expect(step1).toBeVisible();

    // The step circle should have the blue ring class (active = index 0 = CREATE_SR)
    const stepCircle = page
      .locator(
        '[class*="ring-2"][class*="ring-blue"]',
      )
      .first();
    await expect(stepCircle).toBeVisible();
  });

  test("debug panel shows SR ID and stage after creation", async ({ page }) => {
    await injectAuth(page, "MALL_MANAGER");
    await mockChatAlways(page, createSrResponse("SR-TEST-001"));

    await page.goto(CHAT_URL);

    // Enable debug mode
    await page.getByRole("switch", { name: "Debug" }).click();

    // Send a message
    await page.getByPlaceholder(/describe your service request/i).fill(
      "Create handover SR",
    );
    await page.getByRole("button", { name: /send/i }).click();

    // Debug panel should show SR ID and stage
    await expect(page.getByText("SR-TEST-001")).toBeVisible();
    await expect(page.getByText("CREATE_SR")).toBeVisible();
  });
});

test.describe("LifecycleStepper — FM_REVIEW phase", () => {
  test("step 1 green check, step 2 active when stage=FM_REVIEW", async ({
    page,
  }) => {
    await injectAuth(page, "FM_MANAGER");
    await mockChatAlways(page, fmReviewEntryResponse("SR-TEST-001"));

    await page.goto(CHAT_URL);

    // FM Manager opens an existing SR
    await page.getByPlaceholder(/Enter SR ID/i).fill("SR-TEST-001");
    await page.getByRole("button", { name: "Open" }).click();

    // Stepper appears
    await expect(page.getByText("Lifecycle Progress")).toBeVisible();

    // Step 1 should show green check mark (past stage)
    const greenCircles = page.locator('[class*="bg-green-500"]');
    await expect(greenCircles.first()).toBeVisible();

    // Step 2 "FM Review" should be active
    const fmLabel = page.locator("text=FM Review").first();
    await expect(fmLabel).toBeVisible();
  });

  test("stepper hidden before SR ID entered — Continue Existing SR panel visible", async ({
    page,
  }) => {
    await injectAuth(page, "FM_MANAGER");
    await page.goto(CHAT_URL);

    // The "Continue Existing SR" input should be visible before opening an SR
    await expect(page.getByText("Continue Existing SR")).toBeVisible();
    await expect(page.getByPlaceholder(/Enter SR ID/i)).toBeVisible();

    // Stepper hidden until SR ID is provided
    await expect(page.getByText("Lifecycle Progress")).not.toBeVisible();
  });

  test("Open button disabled when SR ID field is empty", async ({ page }) => {
    await injectAuth(page, "FM_MANAGER");
    await page.goto(CHAT_URL);

    const openBtn = page.getByRole("button", { name: "Open" });
    await expect(openBtn).toBeDisabled();

    await page.getByPlaceholder(/Enter SR ID/i).fill("SR-001");
    await expect(openBtn).toBeEnabled();
  });
});

test.describe("LifecycleStepper — RDD_REVIEW phase", () => {
  test("steps 1 and 2 green, step 3 active when stage=RDD_REVIEW", async ({
    page,
  }) => {
    await injectAuth(page, "DD_ENGINEER");
    await mockChatAlways(page, rddReviewEntryResponse("SR-TEST-001"));

    await page.goto(CHAT_URL);

    await page.getByPlaceholder(/Enter SR ID/i).fill("SR-TEST-001");
    await page.getByRole("button", { name: "Open" }).click();

    await expect(page.getByText("Lifecycle Progress")).toBeVisible();

    // Steps 1 and 2 should be green (past)
    const greenCircles = page.locator('[class*="bg-green-500"]');
    await expect(greenCircles).toHaveCount(2);

    // Step 3 "RDD Review" should be visible and active
    await expect(page.locator("text=RDD Review").first()).toBeVisible();
  });
});

test.describe("LifecycleStepper — SR_COMPLETED", () => {
  test("all 4 steps green when SR_COMPLETED", async ({ page }) => {
    await injectAuth(page, "DD_ENGINEER");

    // First response enters RDD_REVIEW, second completes SR
    await mockNextChatResponse(page, rddReviewEntryResponse("SR-TEST-001"));
    await mockChatAlways(page, rddFinalApproveResponse("SR-TEST-001"));

    await page.goto(CHAT_URL);

    // Open SR
    await page.getByPlaceholder(/Enter SR ID/i).fill("SR-TEST-001");
    await page.getByRole("button", { name: "Open" }).click();
    await expect(page.getByText("Lifecycle Progress")).toBeVisible();

    // Final approve
    await page
      .getByPlaceholder(/describe your service request/i)
      .fill("Final approve");
    await page.getByRole("button", { name: /send/i }).click();

    // All 4 steps (Create SR, FM Review, RDD Review, Complete) should be green
    const greenCircles = page.locator('[class*="bg-green-500"]');
    await expect(greenCircles).toHaveCount(4);
  });
});

// ─── Path B (HANDED_OVER / Already Trading) stepper tests ─────────────────────

test.describe("LifecycleStepper — HANDED_OVER (Path B)", () => {
  const HANDED_OVER_SR_ID = "SR-HANDED-OVER-STEPPER-001";

  function handedOverSrResponse(srId = HANDED_OVER_SR_ID) {
    return buildChatResponse({
      message: `Your Already Trading SR ${srId} has been created and automatically approved.`,
      ui: {
        type: "message",
        message: `Your Already Trading SR ${srId} has been created and automatically approved.`,
        workflow_complete: true,
        workflow_stage: "HANDED_OVER_COMPLETED",
      },
      state: { workflow_stage: "HANDED_OVER_COMPLETED", sr_id: srId },
      sr_id: srId,
    });
  }

  test("stepper appears for HANDED_OVER_COMPLETED stage", async ({ page }) => {
    await injectAuth(page, "MALL_MANAGER");
    await mockChatAlways(page, handedOverSrResponse());

    await page.goto(CHAT_URL);

    await page
      .getByPlaceholder(/describe your service request/i)
      .fill("lease t0105712 already trading");
    await page.getByRole("button", { name: /send/i }).click();
    await page.waitForResponse("**/api/chat/service-request");

    // Lifecycle stepper must appear for Path B
    await expect(page.getByText("Lifecycle Progress")).toBeVisible();
  });

  test("Path B stepper does NOT show FM Review or RDD Review labels", async ({
    page,
  }) => {
    await injectAuth(page, "MALL_MANAGER");
    await mockChatAlways(page, handedOverSrResponse());

    await page.goto(CHAT_URL);

    await page
      .getByPlaceholder(/describe your service request/i)
      .fill("lease t0105712 already trading");
    await page.getByRole("button", { name: /send/i }).click();
    await page.waitForResponse("**/api/chat/service-request");

    // HANDED_OVER has no FM or RDD review phases
    await expect(page.getByText("FM Review")).not.toBeVisible();
    await expect(page.getByText("RDD Review")).not.toBeVisible();
  });

  test("Path A stepper still shows FM Review and RDD Review (regression guard)", async ({
    page,
  }) => {
    await injectAuth(page, "MALL_MANAGER");
    // Standard Path A response
    await mockChatAlways(page, createSrResponse("SR-PATH-A-REG-001"));

    await page.goto(CHAT_URL);

    await page
      .getByPlaceholder(/describe your service request/i)
      .fill("I want to initiate a handover for t0105712");
    await page.getByRole("button", { name: /send/i }).click();
    await page.waitForResponse("**/api/chat/service-request");

    // Path A stepper MUST still have FM Review and RDD Review steps
    await expect(page.getByText("Lifecycle Progress")).toBeVisible();
    await expect(page.getByText("FM Review")).toBeVisible();
    await expect(page.getByText("RDD Review")).toBeVisible();
  });

  test("debug panel shows HANDED_OVER_COMPLETED stage", async ({ page }) => {
    await injectAuth(page, "MALL_MANAGER");
    await mockChatAlways(page, handedOverSrResponse());

    await page.goto(CHAT_URL);

    // Enable debug mode
    await page.getByRole("switch", { name: "Debug" }).click();

    await page
      .getByPlaceholder(/describe your service request/i)
      .fill("Already trading for t0105712");
    await page.getByRole("button", { name: /send/i }).click();
    await page.waitForResponse("**/api/chat/service-request");

    // Debug panel should show the HANDED_OVER_COMPLETED stage and the SR ID
    await expect(page.getByText("HANDED_OVER_COMPLETED")).toBeVisible();
    await expect(page.getByText(HANDED_OVER_SR_ID)).toBeVisible();
  });
});
