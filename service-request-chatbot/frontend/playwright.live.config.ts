import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright configuration for LIVE stack E2E tests.
 *
 * Requires:
 *   - PostgreSQL + Redis running  (docker compose up -d)
 *   - Backend on :8000            (uvicorn app.main:app --reload)
 *   - Frontend on :3000           (npm run dev  OR  npm start)
 *   - Valid OPENAI_API_KEY in backend .env
 *
 * Usage:
 *   npx playwright test --config playwright.live.config.ts
 *   npx playwright test --config playwright.live.config.ts --ui
 *   npx playwright test --config playwright.live.config.ts tests/e2e/live/lifecycle-live.spec.ts
 *
 * Unlike the default config (playwright.config.ts), this config:
 *   - Does NOT mock any API calls — all requests hit the real backend
 *   - Uses longer timeouts to accommodate LLM response latency (up to 90s/turn)
 *   - Runs tests serially (workers=1) to prevent DB/session contention
 *   - Does NOT auto-start the frontend dev server (assumes it's already running)
 */
export default defineConfig({
  testDir: "./tests/e2e/live",
  fullyParallel: false,   // serial — each test may touch shared mock SR state
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: 0,             // LLM tests are expensive — no retries by default

  reporter: [
    ["list"],
    ["html", { outputFolder: "playwright-report-live", open: "never" }],
    ["json", { outputFile: "playwright-report-live/results.json" }],
  ],

  use: {
    baseURL: "http://localhost:3000",
    trace: "on",          // always record — useful for debugging LLM responses
    screenshot: "on",
    video: "on-first-retry",

    // LLM responses can take 30–90 seconds per turn
    actionTimeout: 30_000,
    navigationTimeout: 30_000,
  },

  timeout: 300_000,       // 5 minutes per test (multi-turn LLM conversations)
  expect: {
    timeout: 90_000,      // each expect() waits up to 90s for LLM responses
  },

  projects: [
    {
      name: "chromium-live",
      use: { ...devices["Desktop Chrome"] },
    },
  ],

  // No webServer block — assumes the stack is already running.
  // Use run-live-tests.sh to start everything automatically.
});
