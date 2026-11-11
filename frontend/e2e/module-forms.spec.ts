import { expect, test, type Page } from "@playwright/test";
import { openSample } from "./helpers";

/**
 * Survey battery blocks, survey height warnings, module forms that follow the analysis on
 * screen, H3 results as tables, and measure totals and table focus.
 */

async function openModule(page: Page, name: RegExp): Promise<void> {
  await page.getByRole("navigation", { name: "Workspace modules" }).getByRole("link", { name }).click();
}

/** Open the results drawer. Starting a run opens it by itself, so it may open while this runs. */
async function openResults(page: Page): Promise<void> {
  await page.getByRole("button", { name: "Show results" }).click({ timeout: 2_000 }).catch(() => undefined);
  await expect(page.getByRole("button", { name: "Hide results" })).toBeVisible();
}

test.describe("survey", () => {
  test("battery blocks are numbered from 1 and the battery count matches them", async ({ page }) => {
    await openSample(page, "Bengaluru survey");
    await page.getByRole("button", { name: "Estimate survey" }).click();
    const heading = page.getByRole("heading", { name: /^Battery blocks \(\d+\)$/ });
    await expect(heading).toBeVisible({ timeout: 90_000 });
    const count = Number(/\((\d+)\)/.exec((await heading.textContent()) ?? "")?.[1] ?? "0");
    expect(count).toBeGreaterThan(0);

    for (let i = 1; i <= count; i += 1) {
      await expect(page.getByRole("row", { name: `Show battery block ${i} on the map`, exact: true })).toBeVisible();
    }
    await expect(page.getByRole("row", { name: `Show battery block ${count + 1} on the map`, exact: true })).toHaveCount(0);

    const summary = page.getByRole("group", { name: "Survey estimate" });
    const batteries = summary.locator("dl").filter({ has: page.locator("dt", { hasText: /^Batteries$/ }) });
    await expect(batteries.locator("dd").first()).toHaveText(String(count));
  });

  test("a height above the limit is flagged in the form, on the Height tile and before the numbers", async ({ page }) => {
    await openSample(page, "Bengaluru survey");
    await page.getByRole("radiogroup", { name: "Height mode" }).getByRole("radio", { name: "Target GSD" }).click();
    const gsd = page.getByLabel(/Target ground sample distance/);
    await gsd.fill("5");
    await gsd.press("Tab");
    // P4 RTK: 5 cm/px needs about 182 m above ground. Heights put a narrow no-break space
    // between number and unit, so match the text as a (whitespace-normalised) string.
    await expect(page.getByRole("form", { name: "Survey estimate" }).getByText("above the 120 m height limit")).toBeVisible();

    await page.getByRole("button", { name: "Estimate survey" }).click();
    const summary = page.getByRole("group", { name: "Survey estimate" });
    await expect(summary).toBeVisible({ timeout: 90_000 });
    const height = summary.locator("dl").filter({ has: page.locator("dt", { hasText: /^Height$/ }) });
    await expect(height).toContainText("Above the 120 m limit");
    const warning = page.getByRole("status").filter({ hasText: /Planned altitude .* is above the limit/ });
    await expect(warning).toBeVisible();
    // Before the numbers: the warning comes ahead of the summary tiles in reading order.
    await expect(warning.locator('xpath=following::*[@role="group"][@aria-label="Survey estimate"]')).toHaveCount(1);
  });

  test("a cleared custom camera name stays empty", async ({ page }) => {
    await openSample(page, "Bengaluru survey");
    // The Camera section is also a region named "Camera": target the select itself.
    await page.getByRole("combobox", { name: "Camera", exact: true }).click();
    await page.getByRole("option", { name: "Custom camera" }).click();
    const name = page.getByRole("textbox", { name: /^Camera name/ });
    await expect(name).toHaveValue("Custom camera");
    await name.fill("");
    await expect(name).toHaveValue("");
    // It stays empty after leaving the field (no snapping back to the default name).
    await name.press("Tab");
    await expect(name).toHaveValue("");
    await name.pressSequentially("My cam");
    await expect(name).toHaveValue("My cam");
  });
});

test.describe("forms follow the analysis on screen", () => {
  test("airspace: a reloaded check shows its own parameters; Reset restores the defaults", async ({ page }) => {
    await openSample(page, "Bengaluru survey");
    await openModule(page, /Airspace/);
    // Scoped to the form: once a check has run, the Layers panel has a "Flyability grid" switch too.
    const form = page.getByRole("form", { name: "Airspace check" });
    // The presets are a single-choice group (the same control as every other module form).
    const presets = form.getByRole("radiogroup", { name: /^Height .*presets$/ });
    await presets.getByRole("radio", { name: "30 m" }).click();
    await expect(presets.getByRole("radio", { name: "30 m" })).toBeChecked();
    const grid = form.getByRole("switch", { name: "Flyability grid", exact: true });
    await grid.click();
    await expect(grid).not.toBeChecked();
    await page.getByRole("button", { name: "Check airspace" }).click();
    await expect(page.getByText(/Airspace: (Pass|Caution|Fail)/)).toBeVisible({ timeout: 90_000 });

    await page.reload();
    await expect(page.getByText(/Airspace: (Pass|Caution|Fail)/)).toBeVisible({ timeout: 30_000 });
    await expect(presets.getByRole("radio", { name: "30 m" })).toBeChecked();
    await expect(form.getByLabel(/Height above ground/).first()).toHaveValue(/^30(\.0+)?$/);
    await expect(grid).not.toBeChecked();

    await form.getByRole("button", { name: "Reset to defaults" }).click();
    await expect(presets.getByRole("radio", { name: "120 m" })).toBeChecked();
    await expect(grid).toBeChecked();
  });

  test("Reset to defaults also replaces text that is not a number", async ({ page }) => {
    await openSample(page, "Bengaluru survey");
    await openModule(page, /Airspace/);
    // Scoped to the airspace form: the sample opens on the survey module, whose form also has a
    // "Height above ground" field, and it is still on screen while the module link navigates.
    const form = page.getByRole("form", { name: "Airspace check" });
    const height = form.getByRole("spinbutton", { name: /^Height above ground/ });
    await expect(height).toHaveValue("120");
    // The stored height stays 120 m (the default) while the field shows the invalid text.
    await height.fill("abc");
    await height.press("Tab");
    await expect(height).toHaveValue("abc");
    await expect(height).toHaveAttribute("aria-invalid", "true");
    await form.getByRole("button", { name: "Reset to defaults" }).click();
    await expect(height).toHaveValue("120");
    await expect(height).not.toHaveAttribute("aria-invalid", "true");
  });
});

test.describe("H3 results as tables", () => {
  test("airspace: the flyability grid has a table", async ({ page }) => {
    await openSample(page, "Bengaluru survey");
    await openModule(page, /Airspace/);
    await page.getByRole("button", { name: "Check airspace" }).click();
    await expect(page.getByText(/Airspace: (Pass|Caution|Fail)/)).toBeVisible({ timeout: 90_000 });
    await page.getByRole("button", { name: "View Flyability grid as table" }).click();
    const table = page.getByRole("table", { name: "Flyability grid as a table" });
    await expect(table).toBeVisible();
    await expect(table.getByRole("columnheader", { name: /^Status/ })).toBeVisible();
    expect(await table.getByRole("row").count()).toBeGreaterThan(1);
  });

  test("ground risk: density cells and hotspots have tables, and the iGRC tile names its severity", async ({ page }) => {
    await openSample(page, "Bengaluru survey");
    await openModule(page, /Ground risk/);
    await page.getByRole("button", { name: "Assess ground risk" }).click();
    const summary = page.getByRole("group", { name: "Ground risk summary" });
    await expect(summary).toBeVisible({ timeout: 90_000 });
    const igrc = summary.locator("dl").filter({ has: page.locator("dt", { hasText: /^Intrinsic ground risk class$/ }) });
    await expect(igrc).toContainText(/Low \(|Medium \(5–6\)|High \(7 or more\)|Outside the table/);
    await expect(igrc).toContainText(/band /);

    // From the Layers panel (the map legend repeats the density button for its ramp).
    const layers = page.getByRole("complementary", { name: "Layers and features" }).getByRole("region", { name: "Layers" });
    await layers.getByRole("button", { name: "View Population density as table" }).click();
    await expect(page.getByRole("table", { name: "Population density as a table" })).toBeVisible();
    await layers.getByRole("button", { name: "View Hotspots as table" }).click();
    await expect(page.getByRole("table", { name: "Hotspots as a table" })).toBeVisible();
  });
});

test.describe("measure", () => {
  test("a polygon-only dataset has no Length total", async ({ page }) => {
    await openSample(page, "Farm plots (Shapefile, UTM 43N)");
    const totals = page.getByRole("group", { name: "Dataset totals" });
    await expect(totals.locator("dt", { hasText: /^Area$/ })).toBeVisible();
    await expect(totals.locator("dt", { hasText: /^Perimeter$/ })).toBeVisible();
    await expect(totals.locator("dt", { hasText: /^Length$/ })).toHaveCount(0);
  });

  test("a row of the Measurements table brings its feature into view", async ({ page }) => {
    await openSample(page, "Farm plots (Shapefile, UTM 43N)");
    await openResults(page);
    const table = page.getByRole("table", { name: "Measurements per feature" });
    await expect(table).toBeVisible({ timeout: 30_000 });
    const readout = page.locator('[data-map-overlay="readout"]');
    await expect(readout).toHaveAttribute("aria-label", /Map centre: /, { timeout: 30_000 });
    // Zoom onto Plot A first, so Plot C is out of view and the camera has to move to show it.
    const initial = await readout.getAttribute("aria-label");
    await table.getByRole("row", { name: "Select Plot A", exact: true }).click();
    await expect.poll(() => readout.getAttribute("aria-label"), { timeout: 15_000 }).not.toBe(initial);
    await page.waitForFunction(
      async () => {
        const read = () => document.querySelector('[data-map-overlay="readout"]')?.getAttribute("aria-label");
        const a = read();
        await new Promise((r) => setTimeout(r, 400));
        return a === read();
      },
      undefined,
      { timeout: 15_000 },
    );
    const before = await readout.getAttribute("aria-label");
    await table.getByRole("row", { name: "Select Plot C", exact: true }).click();
    await expect.poll(() => readout.getAttribute("aria-label"), { timeout: 15_000 }).not.toBe(before);
  });
});
