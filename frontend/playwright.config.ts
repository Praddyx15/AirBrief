import { defineConfig, devices } from "@playwright/test";

/**
 * End-to-end tests. They expect the API and the web app to be running already
 * (see RUNBOOK.md §4 and .github/workflows/ci.yml, job `e2e`):
 *
 *   python -m seed.datapack --synthetic --name demo
 *   DEFAULT_DATAPACK=demo uvicorn api.main:app --port 8000
 *   cd frontend && npm run build && npm start -- -H 127.0.0.1
 *   PLAYWRIGHT_BROWSERS_PATH=0 npm run e2e
 *
 * PLAYWRIGHT_BASE_URL (default http://localhost:3000) points at the web app and
 * E2E_API_URL (default http://localhost:8000) at the API.
 */
export default defineConfig({
  testDir: "./e2e",
  outputDir: "./test-results",
  timeout: 120_000,
  expect: { timeout: 30_000 },
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  forbidOnly: Boolean(process.env.CI),
  reporter: [["list"], ["html", { outputFolder: "playwright-report", open: "never" }]],
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL ?? "http://localhost:3000",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "off",
    colorScheme: "dark",
    // Software WebGL, so the map renders in headless runs (CI has no GPU) and its controls,
    // labels and attribution are part of what the accessibility checks see.
    launchOptions: { args: ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"] },
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } } }],
});
