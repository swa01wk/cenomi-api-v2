/**
 * auth.spec.ts — Login page tests
 *
 * Covers manual guide §1 (prerequisites) and the implicit requirement
 * that each phase starts with a role-appropriate login.
 *
 * These tests mock the /api/auth/login endpoint so no backend is needed.
 */

import { test, expect } from "@playwright/test";
import {
  mockLoginEndpoint,
  mockLoginFailure,
  injectAuth,
} from "./helpers/api-mocks";

test.describe("Login page", () => {
  test("renders the login form with test account shortcuts", async ({
    page,
  }) => {
    await page.goto("/login");

    await expect(page.getByText("Cenomi Platform")).toBeVisible();
    await expect(page.getByLabel("Username")).toBeVisible();
    await expect(page.getByLabel("Password")).toBeVisible();
    await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible();

    // Test account shortcuts listed on the page (§2 Test Users & Credentials)
    await expect(page.getByText("aisha@cenomi.com")).toBeVisible();
    await expect(page.getByText("khalid@cenomi.com")).toBeVisible();
    await expect(page.getByText("sara@cenomi.com")).toBeVisible();
  });

  test("clicking a test account shortcut fills the form", async ({ page }) => {
    await page.goto("/login");

    await page.getByRole("button", { name: /aisha@cenomi\.com/ }).click();

    await expect(page.getByLabel("Username")).toHaveValue("aisha@cenomi.com");
    await expect(page.getByLabel("Password")).toHaveValue("test1234");
  });

  test("valid credentials redirect to chat page", async ({ page }) => {
    await mockLoginEndpoint(page, "MALL_MANAGER");

    await page.goto("/login");
    await page.getByLabel("Username").fill("aisha@cenomi.com");
    await page.getByLabel("Password").fill("test1234");
    await page.getByRole("button", { name: "Sign in" }).click();

    await page.waitForURL("**/service-request-chat");
    await expect(page).toHaveURL(/service-request-chat/);
  });

  test("invalid credentials show an error message", async ({ page }) => {
    await mockLoginFailure(page);

    await page.goto("/login");
    await page.getByLabel("Username").fill("wrong@example.com");
    await page.getByLabel("Password").fill("badpassword");
    await page.getByRole("button", { name: "Sign in" }).click();

    await expect(page.getByText("Invalid credentials")).toBeVisible();
    // Must stay on login
    await expect(page).toHaveURL(/login/);
  });

  test("unauthenticated access to chat page redirects to login", async ({
    page,
  }) => {
    // No localStorage token set
    await page.goto("/service-request-chat");
    await page.waitForURL("**/login");
    await expect(page).toHaveURL(/login/);
  });

  test("role badge shows after login as FM Manager", async ({ page }) => {
    await mockLoginEndpoint(page, "FM_MANAGER");

    await page.goto("/login");
    await page.getByLabel("Username").fill("khalid@cenomi.com");
    await page.getByLabel("Password").fill("test1234");
    await page.getByRole("button", { name: "Sign in" }).click();

    await page.waitForURL("**/service-request-chat");
    // AuthGuard renders the role label badge
    await expect(page.getByText("FM Manager")).toBeVisible();
  });
});

test.describe("AuthGuard", () => {
  test("authenticated user sees chat page without redirect", async ({
    page,
  }) => {
    await injectAuth(page, "MALL_MANAGER");
    await page.goto("/service-request-chat");

    await expect(page).toHaveURL(/service-request-chat/);
    await expect(
      page.getByText("Service Request Assistant"),
    ).toBeVisible();
  });

  test("sign out clears session and redirects to login", async ({ page }) => {
    await injectAuth(page, "MALL_MANAGER");
    await page.goto("/service-request-chat");

    await page.getByRole("button", { name: "Sign out" }).click();
    await page.waitForURL("**/login");
    await expect(page).toHaveURL(/login/);
  });
});
