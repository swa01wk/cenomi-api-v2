import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright configuration for Cenomi chatbot frontend E2E tests.
 *
 * Design: tests mock all backend API calls via page.route(), so they run
 * without a live backend or database. The only requirement is the Next.js
 * dev server on port 3000.
 *
 * Run:
 *   npx playwright test                         # all tests
 *   npx playwright test tests/e2e/auth          # single file
 *   npx playwright test --ui                    # interactive UI mode
 */
export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: process.env.CI ? 2 : undefined,

  reporter: [
    ["list"],
    ["html", { outputFolder: "playwright-report", open: "never" }],
  ],

  use: {
    baseURL: "http://localhost:3000",
    trace: "on-first-retry",
    screenshot: "only-on-failure",
  },

  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],

  // Start the Next.js dev server automatically if not already running.
  webServer: {
    command: "npm run dev",
    url: "http://localhost:3000",
    reuseExistingServer: true,
    timeout: 120_000,
  },
});
