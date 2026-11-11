import { expect, test } from "@playwright/test";
import { API_URL, expectNoSeriousA11yViolations, hasHorizontalScroll } from "./helpers";

test.describe("smoke", () => {
  test("API health is ok", async ({ request }) => {
    const response = await request.get(`${API_URL}/health`);
    expect(response.ok()).toBeTruthy();
    const body = (await response.json()) as { status: string; db: string; datapacks: string[] };
    expect(body.status).toBe("ok");
    expect(body.db).toBe("ok");
    expect(body.datapacks.length).toBeGreaterThan(0);
  });

  test("landing page loads and is accessible", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    await expect(page.getByRole("link", { name: /open workspace/i }).first()).toBeVisible();
    await expectNoSeriousA11yViolations(page);
  });

  test("skip link moves focus to the main content", async ({ page }) => {
    await page.goto("/");
    await page.keyboard.press("Tab");
    const skip = page.getByRole("link", { name: "Skip to main content" });
    await expect(skip).toBeFocused();
    await skip.press("Enter");
    await expect(page.locator("#main")).toBeFocused();
  });

  test("home shows the upload area and samples", async ({ page }) => {
    await page.goto("/w");
    await expect(page.getByRole("button", { name: /browse/i })).toBeVisible();
    await expect(page.getByRole("button", { name: "Open sample Bengaluru survey", exact: true })).toBeVisible();
    // Attack samples are never offered as cards.
    await expect(page.getByText("Zip-slip attack")).toHaveCount(0);
    await expectNoSeriousA11yViolations(page);
  });

  test("settings page is accessible", async ({ page }) => {
    await page.goto("/settings");
    await expect(page.locator("#main")).toBeVisible();
    await expectNoSeriousA11yViolations(page);
  });

  for (const width of [375, 768, 1024, 1440]) {
    test(`no horizontal scroll at ${width}px`, async ({ page }, testInfo) => {
      await page.setViewportSize({ width, height: 900 });
      for (const path of ["/", "/w", "/settings"]) {
        await page.goto(path);
        await page.waitForLoadState("networkidle");
        expect(await hasHorizontalScroll(page), `${path} at ${width}px`).toBe(false);
        await page.screenshot({ path: testInfo.outputPath(`${path.replace(/\W+/g, "_") || "root"}-${width}.png`), fullPage: true });
      }
    });
  }
});
