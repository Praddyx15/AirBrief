import { expect, test, type Page } from "@playwright/test";
import { openSample } from "./helpers";

/**
 * The workspace shell and settings: settings that work on a direct load, analyses that survive
 * module switches, the drawer tabs, unavailable datasets and the tablet inspector.
 */

const rail = (page: Page) => page.getByRole("navigation", { name: "Workspace modules" });

test.describe("settings on a direct load", () => {
  test("units can be changed after opening /settings directly and after a reload", async ({ page }) => {
    await page.goto("/settings");
    const imperial = page.getByRole("radio", { name: "Imperial (ft, mi²)" });
    await expect(imperial).toBeEnabled();
    await expect(page.getByRole("textbox", { name: "FlightMD URL" })).toBeEnabled();
    await imperial.click();
    await expect(imperial).toHaveAttribute("aria-checked", "true");

    await page.reload();
    const again = page.getByRole("radio", { name: "Imperial (ft, mi²)" });
    await expect(again).toBeEnabled();
    await expect(again).toHaveAttribute("aria-checked", "true");

    // Put the default back for the other tests (settings live in this browser context only).
    await page.getByRole("radio", { name: "Metric (m, km²)" }).click();
    await expect(page.getByRole("radio", { name: "Metric (m, km²)" })).toHaveAttribute("aria-checked", "true");
  });
});

test.describe("workspace shell", () => {
  test("a finished analysis is still shown after switching modules and back", async ({ page }) => {
    await openSample(page, "Bengaluru survey");
    await rail(page).getByRole("link", { name: /Airspace/ }).click();
    await page.getByRole("button", { name: "Check airspace" }).click();
    await expect(page.getByText(/Airspace: (Pass|Caution|Fail)/)).toBeVisible({ timeout: 90_000 });
    // Reported once, by the toast.
    await expect(page.getByText("Airspace check finished")).toHaveCount(1);

    await rail(page).getByRole("link", { name: /Survey/ }).click();
    await expect(page).not.toHaveURL(/[?&]a=/);
    await rail(page).getByRole("link", { name: /Airspace/ }).click();
    await expect(page).toHaveURL(/[?&]a=/);
    await expect(page.getByText(/Airspace: (Pass|Caution|Fail)/)).toBeVisible();
    await expect(page.getByText("No airspace results yet")).toHaveCount(0);
  });

  test("a job keeps being watched after switching modules while it runs", async ({ page }) => {
    await openSample(page, "Bengaluru survey");
    await page.getByRole("button", { name: "Estimate survey" }).click();
    await rail(page).getByRole("link", { name: /Ground risk/ }).click();
    await expect(page.getByText("Survey estimate finished")).toBeVisible({ timeout: 90_000 });
    await expect(page.getByText("Survey estimate finished")).toHaveCount(1);

    await rail(page).getByRole("link", { name: /Survey/ }).click();
    await expect(page.getByText("Ground sample distance")).toBeVisible();
  });

  test("clicking the selected drawer tab opens the closed drawer", async ({ page }) => {
    await openSample(page, "Farm plots (Shapefile, UTM 43N)");
    const results = page.getByRole("region", { name: "Results" });
    await expect(results.getByRole("button", { name: "Show results" })).toBeVisible();
    await results.getByRole("tab", { name: "Measurements" }).click();
    await expect(results.getByRole("button", { name: "Hide results" })).toBeVisible();
  });

  test("History tells runs apart by their parameters", async ({ page }) => {
    await openSample(page, "Bengaluru survey");
    await rail(page).getByRole("link", { name: /Airspace/ }).click();
    await page.getByRole("button", { name: "Check airspace" }).click();
    await expect(page.getByText(/Airspace: (Pass|Caution|Fail)/)).toBeVisible({ timeout: 90_000 });
    const results = page.getByRole("region", { name: "Results" });
    await results.getByRole("tab", { name: "History" }).click();
    await expect(results.getByRole("columnheader", { name: /Parameters/ })).toBeVisible();
    await expect(results.getByText(/m AGL/).first()).toBeVisible();
  });

  test("a missing dataset shows a plain message without Retry or feature tools", async ({ page }) => {
    await page.goto("/w/view?d=00000000-0000-4000-8000-000000000000");
    const alert = page.getByRole("alert").filter({ hasText: "Dataset not found" });
    await expect(alert).toBeVisible();
    await expect(alert).not.toContainText("00000000");
    await expect(alert.getByRole("button", { name: "Retry" })).toHaveCount(0);
    await expect(page.getByRole("heading", { level: 1, name: "Dataset unavailable" })).toBeVisible();
    await expect(page.getByRole("button", { name: /Select all/ })).toHaveCount(0);
    await expect(page.getByRole("switch", { name: /Dataset features/ })).toHaveCount(0);
  });

  test("tablet: the inspector replaces the layers panel and Escape returns focus", async ({ page }) => {
    await page.setViewportSize({ width: 820, height: 1000 });
    await openSample(page, "Farm plots (Shapefile, UTM 43N)");
    const layers = page.getByRole("complementary", { name: "Layers and features" });
    const show = page.getByRole("button", { name: "Show inspector" });
    await expect(layers).toBeVisible();

    await show.click();
    const inspector = page.locator("#workspace-inspector");
    await expect(inspector).toBeVisible();
    await expect(layers).toBeHidden();
    await expect(page.locator("#inspector-title")).toBeFocused();

    // A map tap never opens it; the layers panel comes back when it closes.
    await page.keyboard.press("Escape");
    await expect(inspector).toBeHidden();
    await expect(layers).toBeVisible();
    await expect(page.getByRole("button", { name: "Show inspector" })).toBeFocused();
  });

  test("tablet: picking a feature from the list moves focus to the inspector", async ({ page }) => {
    await page.setViewportSize({ width: 820, height: 1000 });
    await openSample(page, "Farm plots (Shapefile, UTM 43N)");
    const layers = page.getByRole("complementary", { name: "Layers and features" });
    const row = layers.getByRole("row", { name: /Select Plot A/ });
    await row.focus();
    await page.keyboard.press("Enter");

    // The list folds away for the inspector; focus must follow, not fall to <body>.
    await expect(page.locator("#workspace-inspector")).toBeVisible();
    await expect(layers).toBeHidden();
    await expect(page.locator("#inspector-title")).toBeFocused();
  });

  test("tablet: after a run the inspector closes and the drawer keeps its controls on screen", async ({ page }) => {
    await page.setViewportSize({ width: 768, height: 1024 });
    await openSample(page, "Bengaluru survey");
    await page.getByRole("button", { name: "Show inspector" }).click();
    await page.getByRole("button", { name: "Estimate survey" }).click();
    await expect(page.locator("#workspace-inspector")).toBeHidden();
    await expect(page.getByText("Ground sample distance")).toBeVisible({ timeout: 90_000 });

    const results = page.getByRole("region", { name: "Results" });
    await expect(results.getByRole("button", { name: "Edit parameters" }).first()).toBeVisible();
    // Status badge + "Edit parameters" in the tab bar must not push "Hide results" off-screen.
    const box = await results.getByRole("button", { name: "Hide results" }).boundingBox();
    const right = box ? box.x + box.width : Number.POSITIVE_INFINITY;
    expect(right).toBeLessThanOrEqual(768);
  });
});
