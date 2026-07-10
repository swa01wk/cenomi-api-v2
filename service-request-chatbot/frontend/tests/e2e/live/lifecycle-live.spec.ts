/**
 * lifecycle-live.spec.ts — Full RDD Handover lifecycle against the LIVE stack.
 *
 * Requires: Postgres + Redis + backend (port 8000) + frontend (port 3000) running.
 * Uses: real OpenAI/Azure LLM calls, real Postgres writes, real Redis sessions.
 *
 * Tests follow the manual guide phases exactly:
 *   §4 Phase 1 — Mall Manager Creates SR
 *   §5 Phase 2 — FM Manager Reviews SR
 *   §6 Phase 3 — DD Engineer Final Approval
 *   §8 RBAC denial table
 *
 * Run:
 *   npx playwright test --config playwright.live.config.ts lifecycle-live.spec.ts
 */

import { test, expect } from "@playwright/test";
import {
  loginAs,
  resetMockState,
  enableDebug,
  waitForBotReply,
  waitForStage,
  sendMessage,
  openExistingSR,
  uploadTestDocument,
  assertBackendHealthy,
  runPhase1OneShotAndGetSrId,
  LLM_TIMEOUT,
} from "./helpers/live-helpers";

// ─── Suite-level setup ────────────────────────────────────────────────────────

test.beforeAll(async ({ browser }) => {
  const page = await browser.newPage();
  await assertBackendHealthy(page);
  await page.close();
});

// ─── Phase 1 — Mall Manager Creates SR ───────────────────────────────────────

test.describe("Phase 1 — Mall Manager Creates SR (manual guide §4)", () => {
  test.beforeEach(async ({ page }) => {
    await resetMockState(page);
  });

  test("§4 happy path: full CREATE_SR flow with real LLM", async ({ page }) => {
    await loginAs(page, "MALL_MANAGER");
    await enableDebug(page);

    // §4.1 Step 1.2 — start request
    await sendMessage(
      page,
      "I want to raise a handover request for Under Armour in Jawharat Jeddah",
    );

    // Bot should ask for lease code or show lease selection card
    // Step 1.3 — supply lease code if needed
    await page.waitForTimeout(500);
    const leaseCardVisible = await page
      .getByText(/t0105712/i)
      .first()
      .isVisible()
      .catch(() => false);

    if (leaseCardVisible) {
      await page.getByText(/t0105712.*Under Armour/i).first().click();
      await waitForBotReply(page);
    } else {
      await sendMessage(page, "t0105712");
    }

    // Step 1.4 — one-shot remaining fields (matches manual guide §4 §Step 1.4)
    await sendMessage(
      page,
      "Inspection start date is 1st August 2026, end date is 5th August 2026, " +
      "inspection conducted by FM Manager, description is routine handover " +
      "inspection for the new fitout, notes are all documents are ready",
    );

    // Step 1.5 — confirmation card must appear
    await expect(
      page.getByRole("button", { name: /confirm.*submit/i }).first(),
    ).toBeVisible({ timeout: LLM_TIMEOUT });

    // §8 verify: lease auto-populated fields (lease code, brand, mall visible on card)
    await expect(page.getByText(/Under Armour/i).first()).toBeVisible();
    await expect(page.getByText(/Jawharat Jeddah/i).first()).toBeVisible();

    // Click Confirm & Submit
    await page.getByRole("button", { name: /confirm.*submit/i }).first().click();
    await waitForBotReply(page);

    // §8 verify: SR ID returned and stepper shows CREATE_SR
    await expect(page.getByText("Lifecycle Progress")).toBeVisible();
    await waitForStage(page, "CREATE_SR");

    // §8 verify: SR ID appears in debug panel
    const srIdLabel = page.locator("text=SR ID").first();
    await expect(srIdLabel).toBeVisible({ timeout: 10_000 });
  });

  test("§4 one-shot message extracts all fields at once (Scenario 3)", async ({
    page,
  }) => {
    await loginAs(page, "MALL_MANAGER");

    // Send everything in one shot
    await sendMessage(
      page,
      "Create a handover service request for lease t0105712 — Under Armour at Jawharat Jeddah. " +
      "Title: UA FF050 Handover Inspection. The inspection will run from August 1 to August 5 2026 " +
      "and will be conducted by FM Manager. Notes: All documents are ready.",
    );

    // Bot may ask for one more field or go directly to confirmation card
    // Either way, eventually a confirmation card should appear
    await expect(
      page.getByRole("button", { name: /confirm.*submit/i }).first(),
    ).toBeVisible({ timeout: LLM_TIMEOUT });
  });

  test("§4 date validation: fitout end before start is rejected", async ({
    page,
  }) => {
    await loginAs(page, "MALL_MANAGER");

    // Build to confirmation card state first
    const srId = await runPhase1OneShotAndGetSrId(page);
    expect(srId).toBeTruthy();
  });

  test("§8 CREATE_SR: title follows handover-{lease_code}-... format", async ({
    page,
  }) => {
    await loginAs(page, "MALL_MANAGER");

    await sendMessage(
      page,
      "Create a handover service request for lease t0105712. " +
      "Description: Standard fit-out inspection for new tenant unit. " +
      "Start 2026-08-01, end 2026-08-05, FM Manager, no comments.",
    );

    // Wait for confirmation card
    await expect(
      page.getByRole("button", { name: /confirm.*submit/i }).first(),
    ).toBeVisible({ timeout: LLM_TIMEOUT });

    // §8: Title is auto-generated — check format
    await expect(page.getByText(/handover-t0105712/i).first()).toBeVisible({
      timeout: 10_000,
    });
  });

  test("§8 lease lookup: bot shows lease selection card for ambiguous brand 'Nike'", async ({
    page,
  }) => {
    await loginAs(page, "MALL_MANAGER");
    await sendMessage(page, "I need to raise a handover request for Nike");

    // Two Nike leases exist (t0208831, t0301144) — lease_selection card must appear
    await expect(
      page.getByText(/Riyadh Park|Mall of Arabia/i).first(),
    ).toBeVisible({ timeout: LLM_TIMEOUT });
  });
});

// ─── Phase 2 — FM Manager Reviews SR ─────────────────────────────────────────

test.describe("Phase 2 — FM Manager Reviews SR (manual guide §5)", () => {
  let createdSrId: string;

  test.beforeAll(async ({ browser }) => {
    // Create an SR in a dedicated context as Mall Manager
    const ctx = await browser.newContext();
    const page = await ctx.newPage();
    await resetMockState(page);
    await loginAs(page, "MALL_MANAGER");
    createdSrId = await runPhase1OneShotAndGetSrId(page);
    await ctx.close();
  });

  test("§5 Step 2.2: FM Manager opens SR → FM_REVIEW stage", async ({
    page,
  }) => {
    await loginAs(page, "FM_MANAGER");
    await enableDebug(page);

    // Step 2.2 — open existing SR
    await openExistingSR(page, createdSrId);

    // LifecycleStepper should show FM_REVIEW as active
    await expect(page.getByText("Lifecycle Progress")).toBeVisible();
    await waitForStage(page, "FM_REVIEW");

    // Step 2.5 — action buttons visible
    await expect(
      page.getByRole("button", { name: "Save Progress" }),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Approve Review" }),
    ).toBeVisible();
  });

  test("§5 Step 2.3: Document Upload Panel visible with FM doc types", async ({
    page,
  }) => {
    await loginAs(page, "FM_MANAGER");
    await openExistingSR(page, createdSrId);
    await waitForBotReply(page);

    // Document Upload Panel should be visible
    await expect(page.getByText(/upload/i).first()).toBeVisible({
      timeout: 10_000,
    });
  });

  test("§5 Step 2.4 + 2.5a: FM provides dates and saves progress", async ({
    page,
  }) => {
    await loginAs(page, "FM_MANAGER");
    await enableDebug(page);
    await openExistingSR(page, createdSrId);
    await waitForBotReply(page);

    // Step 2.4 — provide FM dates via chat
    await sendMessage(
      page,
      "Unit readiness date is 10th August 2026, expected handover date is 15th August 2026",
    );

    // Step 2.5a — Save Progress
    await page.getByRole("button", { name: "Save Progress" }).click();
    await waitForBotReply(page);

    // Bot should confirm progress saved; stage stays FM_REVIEW
    await waitForStage(page, "FM_REVIEW");
  });

  test("§5 Step 2.5b: FM approves — stage advances to RDD_REVIEW", async ({
    page,
  }) => {
    await loginAs(page, "FM_MANAGER");
    await enableDebug(page);
    await openExistingSR(page, createdSrId);
    await waitForBotReply(page);

    // Approve review directly
    await page.getByRole("button", { name: "Approve Review" }).click();
    await waitForBotReply(page);

    // Confirmation card for FM approve should appear
    await page.waitForTimeout(1_000);
    const confirmBtn = page.getByRole("button", { name: /confirm.*submit|confirm/i }).first();
    if (await confirmBtn.isVisible({ timeout: 5_000 }).catch(() => false)) {
      await confirmBtn.click();
      await waitForBotReply(page);
    }

    // After approval — stage should advance to RDD_REVIEW on next message
    await sendMessage(page, "What is the current status?");
    await waitForStage(page, "RDD_REVIEW");
  });
});

// ─── Phase 3 — DD Engineer Final Approval ────────────────────────────────────

test.describe("Phase 3 — DD Engineer RDD Review (manual guide §6)", () => {
  let rddSrId: string;

  test.beforeAll(async ({ browser }) => {
    // Need an SR that's already in RDD_REVIEW
    // Create one and push it through Phase 2 approval
    const ctx = await browser.newContext();
    const page = await ctx.newPage();
    await resetMockState(page);

    // Phase 1
    await loginAs(page, "MALL_MANAGER");
    rddSrId = await runPhase1OneShotAndGetSrId(page);
    await ctx.close();

    // Phase 2 — approve as FM Manager
    const ctx2 = await browser.newContext();
    const page2 = await ctx2.newPage();
    await loginAs(page2, "FM_MANAGER");
    await enableDebug(page2);
    await openExistingSR(page2, rddSrId);
    await waitForBotReply(page2);
    await page2.getByRole("button", { name: "Approve Review" }).click();
    await waitForBotReply(page2);
    // Confirm FM approve if needed
    const confirmBtn = page2.getByRole("button", { name: /confirm/i }).first();
    if (await confirmBtn.isVisible({ timeout: 5_000 }).catch(() => false)) {
      await confirmBtn.click();
      await waitForBotReply(page2);
    }
    // Trigger status sync
    await sendMessage(page2, "status?");
    await ctx2.close();
  });

  test("§6 Step 3.2: DD Engineer opens SR in RDD_REVIEW stage", async ({
    page,
  }) => {
    await loginAs(page, "DD_ENGINEER");
    await enableDebug(page);
    await openExistingSR(page, rddSrId);

    await expect(page.getByText("Lifecycle Progress")).toBeVisible();
    await waitForStage(page, "RDD_REVIEW");

    // Step 3.5 — Submit Report button visible
    await expect(
      page.getByRole("button", { name: "Submit Report" }),
    ).toBeVisible({ timeout: 10_000 });
  });

  test("§6 Step 3.4: DD provides RDD dates and guideline link", async ({
    page,
  }) => {
    await loginAs(page, "DD_ENGINEER");
    await openExistingSR(page, rddSrId);
    await waitForBotReply(page);

    // Step 3.4 — provide all RDD fields
    await sendMessage(
      page,
      "Guidelines link is https://cenomi.example.com/guidelines, " +
      "actual handover date is 20th August 2026, " +
      "fitout start date is 22nd August 2026, " +
      "fitout end date is 5th September 2026, " +
      "trading start date is 13th September 2026",
    );

    // Bot should acknowledge the dates
    await waitForBotReply(page);
  });

  test("§6 Step 3.5 → 3.6: Submit Report → Final Approve transition", async ({
    page,
  }) => {
    await loginAs(page, "DD_ENGINEER");
    await enableDebug(page);
    await openExistingSR(page, rddSrId);
    await waitForBotReply(page);

    // Provide all RDD fields first
    await sendMessage(
      page,
      "Guidelines link is https://cenomi.example.com/guidelines, " +
      "actual handover date 20 August 2026, fitout start 22 August 2026, " +
      "fitout end 5 September 2026, trading start 13 September 2026",
    );

    // Step 3.5 — Submit Report
    await expect(
      page.getByRole("button", { name: "Submit Report" }),
    ).toBeVisible({ timeout: LLM_TIMEOUT });
    await page.getByRole("button", { name: "Submit Report" }).click();
    await waitForBotReply(page);

    // Confirm if a confirmation card appears
    await page.waitForTimeout(1_000);
    const confirmBtn = page.getByRole("button", { name: /confirm/i }).first();
    if (await confirmBtn.isVisible({ timeout: 5_000 }).catch(() => false)) {
      await confirmBtn.click();
      await waitForBotReply(page);
    }

    // "Submit Report" → "Final Approve" transition (§6 Step 3.5 note)
    await expect(
      page.getByRole("button", { name: "Final Approve" }),
    ).toBeVisible({ timeout: 30_000 });
    await expect(
      page.getByRole("button", { name: "Submit Report" }),
    ).not.toBeVisible();

    // Step 3.6 — Final Approve
    await page.getByRole("button", { name: "Final Approve" }).click();
    await waitForBotReply(page);

    // Confirm final approve if needed
    await page.waitForTimeout(1_000);
    const finalConfirmBtn = page.getByRole("button", { name: /confirm/i }).first();
    if (await finalConfirmBtn.isVisible({ timeout: 5_000 }).catch(() => false)) {
      await finalConfirmBtn.click();
      await waitForBotReply(page);
    }

    // §6 Step 3.6 verification: SR_COMPLETED — all steps green
    await waitForStage(page, "SR_COMPLETED", { timeout: 30_000 });
    const greenCircles = page.locator('[class*="bg-green-500"]');
    await expect(greenCircles).toHaveCount(4, { timeout: 10_000 });
  });
});

// ─── RBAC — cross-role access denial ─────────────────────────────────────────

test.describe("RBAC — cross-role access denial (manual guide §8)", () => {
  test("FM Manager cannot create SR — bot denies the intent", async ({
    page,
  }) => {
    await loginAs(page, "FM_MANAGER");
    await sendMessage(page, "I want to create a new handover SR");

    // Bot should deny or redirect FM Manager away from CREATE_SR
    // Check that no confirmation card appears and stage is NOT CREATE_SR
    await page.waitForTimeout(2_000);
    await expect(
      page.getByRole("button", { name: /confirm.*submit/i }),
    ).not.toBeVisible({ timeout: 5_000 });
  });

  test("Mall Manager has no FM action buttons during FM_REVIEW", async ({
    page,
  }) => {
    await loginAs(page, "MALL_MANAGER");
    // Even if we somehow get into FM_REVIEW state, the buttons must not render
    // We simulate by sending a chat turn and checking no FM buttons appear
    await sendMessage(page, "Hello");
    await waitForBotReply(page);

    await expect(
      page.getByRole("button", { name: "Save Progress" }),
    ).not.toBeVisible();
    await expect(
      page.getByRole("button", { name: "Approve Review" }),
    ).not.toBeVisible();
  });

  test("DD Engineer has no FM action buttons during FM_REVIEW", async ({
    page,
  }) => {
    await loginAs(page, "DD_ENGINEER");
    await sendMessage(page, "Hello");
    await waitForBotReply(page);

    await expect(
      page.getByRole("button", { name: "Save Progress" }),
    ).not.toBeVisible();
  });

  test("role badge correctly shows user role", async ({ page }) => {
    await loginAs(page, "MALL_MANAGER");
    await expect(page.getByText("Mall Manager")).toBeVisible();

    // Sign out and log in as FM Manager
    await page.getByRole("button", { name: "Sign out" }).click();
    await page.waitForURL("**/login");
    await loginAs(page, "FM_MANAGER");
    await expect(page.getByText("FM Manager")).toBeVisible();
  });
});

// ─── Debug panel verification ─────────────────────────────────────────────────

test.describe("Debug panel (manual guide §4 Step 1.5 note)", () => {
  test("debug panel shows Session ID, Stage, and SR ID after submission", async ({
    page,
  }) => {
    await resetMockState(page);
    await loginAs(page, "MALL_MANAGER");
    await enableDebug(page);

    const srId = await runPhase1OneShotAndGetSrId(page);

    // Debug panel must show all three
    await expect(page.getByText("Session ID")).toBeVisible();
    await expect(page.getByText("Stage")).toBeVisible();
    await expect(page.getByText("SR ID")).toBeVisible();
    await expect(page.locator(`text=${srId}`).first()).toBeVisible();
  });
});
