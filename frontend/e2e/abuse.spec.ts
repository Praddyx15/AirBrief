import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import { API_URL } from "./helpers";

/**
 * A development-mode API opened from another machine shows a persistent warning strip under
 * the header; on a loopback host (local work, the demo video) it never shows.
 *
 * "Another machine" is the same web server under the name airbrief.test, which Chromium maps to
 * the configured host. The meta response is stubbed with app_env "development" so the tests do
 * not depend on how the API was started; the stub allows any origin, since the API's own CORS
 * list does not include airbrief.test.
 */

const STRIP = "Development mode: anyone who can reach this server can see and delete everything on it.";
const BASE = new URL(process.env.PLAYWRIGHT_BASE_URL ?? "http://localhost:3000");
const REMOTE = `http://airbrief.test:${BASE.port || "80"}`;

// Replaces the config's launch args, so the software-WebGL flags are repeated here.
test.use({
  launchOptions: {
    args: [
      `--host-resolver-rules=MAP airbrief.test ${BASE.hostname}`,
      "--use-angle=swiftshader",
      "--enable-unsafe-swiftshader",
    ],
  },
});

async function stubDevelopmentMeta(page: Page, request: APIRequestContext): Promise<void> {
  const meta = (await (await request.get(`${API_URL}/api/meta/`)).json()) as Record<string, unknown>;
  await page.route(/\/api\/meta\/?$/, (route) =>
    route.fulfill({ json: { ...meta, app_env: "development" }, headers: { "access-control-allow-origin": "*" } }),
  );
}

test("no development warning on a loopback host", async ({ page, request }) => {
  await stubDevelopmentMeta(page, request);
  const meta = page.waitForResponse(/\/api\/meta\/?$/);
  await page.goto("/settings");
  await meta;
  await expect(page.getByRole("link", { name: "AirBrief home" })).toBeVisible();
  await expect(page.getByText(STRIP)).toHaveCount(0);
});

test("development warning from another machine stays on every page", async ({ page, request }) => {
  await stubDevelopmentMeta(page, request);
  await page.goto(`${REMOTE}/settings`);
  const strip = page.getByRole("status").filter({ hasText: STRIP });
  await expect(strip).toBeVisible();
  await expect(strip.getByRole("button")).toHaveCount(0);

  await page.goto(`${REMOTE}/w`);
  await expect(page.getByRole("status").filter({ hasText: STRIP })).toBeVisible();
});

test("no warning from another machine when the API runs in production", async ({ page, request }) => {
  const meta = (await (await request.get(`${API_URL}/api/meta/`)).json()) as Record<string, unknown>;
  await page.route(/\/api\/meta\/?$/, (route) =>
    route.fulfill({ json: { ...meta, app_env: "production" }, headers: { "access-control-allow-origin": "*" } }),
  );
  const loaded = page.waitForResponse(/\/api\/meta\/?$/);
  await page.goto(`${REMOTE}/settings`);
  await loaded;
  await expect(page.getByRole("link", { name: "AirBrief home" })).toBeVisible();
  await expect(page.getByText(STRIP)).toHaveCount(0);
});
