import { expect, test, type Locator, type Page } from "@playwright/test";
import { expectNoSeriousA11yViolations, openSample } from "./helpers";

/**
 * Terrain-true heights (AGL / above take-off / AMSL) in the airspace check, the survey
 * estimator and the eVTOL corridor, and the survey upgrades (corridor mapping, transit, GCPs
 * and storage, thermal and multispectral cameras, spraying in local area units).
 *
 * The synthetic demo pack has gentle terrain (about 920 m on the climbing line), so the numbers
 * below come from flight levels chosen against it: 950 m AMSL over the climbing line is 28–30 m
 * above the ground, under the default 30 m minimum.
 */

const rail = (page: Page) => page.getByRole("navigation", { name: "Workspace modules" });

async function openModule(page: Page, name: RegExp): Promise<void> {
  await rail(page).getByRole("link", { name }).click();
}

function tile(summary: Locator, page: Page, label: string): Locator {
  const exact = new RegExp(`^${label.replace(/[()]/g, "\\$&")}$`);
  return summary.locator("dl").filter({ has: page.locator("dt", { hasText: exact }) });
}

test.describe("height reference: airspace", () => {
  test("AMSL over a route: AGL range, a stretch below the minimum, the height profile and its table", async ({ page }) => {
    await openSample(page, "Climbing line (3D)");
    await openModule(page, /Airspace/);
    const form = page.getByRole("form", { name: "Airspace check" });
    // AGL plans are terrain following: no take-off and no minimum to check.
    await expect(form.getByRole("spinbutton", { name: /^Minimum height above ground/ })).toHaveCount(0);
    await form.getByRole("radiogroup", { name: "Height reference" }).getByRole("radio", { name: "AMSL" }).click();
    await expect(form.getByRole("group", { name: "Take-off" })).toBeVisible();
    await expect(form.getByRole("spinbutton", { name: /^Minimum height above ground/ })).toHaveValue("30");
    const level = form.getByRole("spinbutton", { name: /^Flight level \(AMSL\)/ });
    await level.fill("950");
    await level.press("Tab");
    await page.getByRole("button", { name: "Check airspace" }).click();
    await expect(page.getByText(/Airspace: (Pass|Caution|Fail)/)).toBeVisible({ timeout: 90_000 });

    const summary = page.getByRole("group", { name: "Airspace summary" });
    await expect(tile(summary, page, "Height above ground")).toContainText("–");
    await expect(tile(summary, page, "Below the minimum")).toContainText("Under 30 m AGL");
    await expect(tile(summary, page, "Above the ceiling")).toContainText("None");

    const figure = page.getByRole("figure", { name: "Height profile along the route" });
    await expect(figure).toBeVisible();
    await expect(figure.getByRole("list", { name: "Chart legend" })).toContainText("Below the minimum (dotted)");
    await figure.getByRole("button", { name: "Show table" }).click();
    await expect(figure.getByRole("columnheader", { name: "Status" })).toBeVisible();
    await expect(figure.getByRole("cell", { name: "Below minimum" }).first()).toBeVisible();

    const stretches = page.getByRole("table", { name: /^Stretches of the route/ });
    await expect(stretches.getByRole("row", { name: /^Zoom to below the minimum 1/ })).toBeVisible();
    // The terrain model is described on the readout (here the synthetic demo terrain).
    await expect(page.getByText("Synthetic terrain", { exact: true }).first()).toBeVisible();
    await expectNoSeriousA11yViolations(page);

    // Reopened (reload), the form shows the analysis' own reference and level.
    await page.reload();
    await expect(page.getByText(/Airspace: (Pass|Caution|Fail)/)).toBeVisible({ timeout: 30_000 });
    await expect(form.getByRole("radiogroup", { name: "Height reference" }).getByRole("radio", { name: "AMSL" })).toBeChecked();
    await expect(form.getByRole("spinbutton", { name: /^Flight level \(AMSL\)/ })).toHaveValue(/^950(\.0+)?$/);
  });

  test("pick take-off on map: Escape cancels, Space places it at the map centre, History names the reference", async ({ page }) => {
    await openSample(page, "Bengaluru survey");
    await openModule(page, /Airspace/);
    const form = page.getByRole("form", { name: "Airspace check" });
    await form.getByRole("radiogroup", { name: "Height reference" }).getByRole("radio", { name: "Above take-off" }).click();
    await expect(form.getByRole("spinbutton", { name: /^Height above take-off/ })).toHaveValue("120");
    const takeoff = form.getByRole("group", { name: "Take-off" });
    const canvas = page.locator("#map canvas.maplibregl-canvas");
    const pick = takeoff.getByRole("button", { name: "Pick take-off on map" });

    await pick.click();
    await expect(canvas).toBeFocused();
    await expect(takeoff.getByRole("button", { name: /Click the map/ })).toHaveAttribute("aria-pressed", "true");
    await page.keyboard.press("Escape");
    await expect(pick).toHaveAttribute("aria-pressed", "false");
    await expect(pick).toBeFocused();
    await expect(takeoff.getByLabel(/^Take-off latitude/)).toHaveValue("");

    await pick.click();
    await expect(canvas).toBeFocused();
    await page.keyboard.press("Space");
    await expect(takeoff.getByLabel(/^Take-off latitude/)).toHaveValue(/^12\.9/);
    await expect(takeoff.getByLabel(/^Take-off longitude/)).toHaveValue(/^77\.4/);
    await expect(pick).toBeFocused();

    await page.getByRole("button", { name: "Check airspace" }).click();
    await expect(page.getByText(/Airspace: (Pass|Caution|Fail)/)).toBeVisible({ timeout: 90_000 });
    await expect(page.locator("p").filter({ hasText: /^Take-off:/ })).toContainText("set by you");
    const summary = page.getByRole("group", { name: "Airspace summary" });
    await expect(tile(summary, page, "Free-flight ceiling")).toContainText("Planned 120 m above take-off");

    const results = page.getByRole("region", { name: "Results" });
    await results.getByRole("tab", { name: "History" }).click();
    await expect(results.getByText(/m above take-off/).first()).toBeVisible();
  });
});

test.describe("survey upgrades", () => {
  test("above take-off: the GSD range over the relief and the worst overlap", async ({ page }) => {
    await openSample(page, "Bengaluru survey");
    const form = page.getByRole("form", { name: "Survey estimate" });
    await form.getByRole("radiogroup", { name: "Height reference" }).getByRole("radio", { name: "Above take-off" }).click();
    await expect(form.getByRole("spinbutton", { name: /^Height above take-off/ })).toHaveValue("100");
    await page.getByRole("button", { name: "Estimate survey" }).click();
    const summary = page.getByRole("group", { name: "Survey estimate" });
    await expect(summary).toBeVisible({ timeout: 90_000 });
    // e.g. "GSD 2.71–2.77 cm/px (2 m relief); worst side overlap 70 %."
    await expect(page.getByText(/^GSD \d+(\.\d+)?–\d+(\.\d+)? cm\/px \(.+ relief\); worst side overlap \d+\s%\.$/)).toBeVisible();
    await expect(tile(summary, page, "Worst overlap")).toContainText("% side");
    await expect(tile(summary, page, "Height")).toContainText("Above the take-off ground");
  });

  test("corridor mapping along a line: corridor, ground control points and storage", async ({ page }) => {
    await openSample(page, "Climbing line (3D)");
    await openModule(page, /Survey/);
    const form = page.getByRole("form", { name: "Survey estimate" });
    await form.getByRole("radiogroup", { name: "Coverage" }).getByRole("radio", { name: "Corridor" }).click();
    await expect(form.getByRole("spinbutton", { name: /^Corridor width/ })).toHaveValue("100");
    await page.getByRole("button", { name: "Estimate survey" }).click();
    const summary = page.getByRole("group", { name: "Survey estimate" });
    await expect(summary).toBeVisible({ timeout: 90_000 });
    // 1,000 m × 100 m at 45 m spacing: 3 lines along the centreline, 5 GCPs (the minimum).
    await expect(tile(summary, page, "Corridor")).toContainText("100 m wide");
    await expect(tile(summary, page, "Line spacing")).toContainText("3 lines along the centreline");
    await expect(tile(summary, page, "Ground control points")).toContainText("5");
    await expect(tile(summary, page, "Storage")).toContainText("GB");
    await expect(page.getByRole("heading", { name: "Battery blocks (1)" })).toBeVisible();
  });

  test("transit legs and a battery reserve are counted", async ({ page }) => {
    await openSample(page, "Bengaluru survey");
    const form = page.getByRole("form", { name: "Survey estimate" });
    const reserve = form.getByRole("spinbutton", { name: /^Battery reserve/ });
    await reserve.fill("20");
    await reserve.press("Tab");
    await form.getByRole("switch", { name: "Count transit legs" }).click();
    await expect(form.getByRole("group", { name: "Take-off" })).toBeVisible();
    await page.getByRole("button", { name: "Estimate survey" }).click();
    const summary = page.getByRole("group", { name: "Survey estimate" });
    await expect(summary).toBeVisible({ timeout: 90_000 });
    await expect(tile(summary, page, "Transit")).toContainText("to and from the take-off point");
    await expect(tile(summary, page, "Batteries")).toContainText("20 % reserve");
    await expect(page.getByRole("table", { name: /Work blocks/ }).getByRole("columnheader", { name: /Transit in \/ out/ })).toBeVisible();
  });

  test("thermal and multispectral presets are offered; the result names the sensor", async ({ page }) => {
    await openSample(page, "Bengaluru survey");
    await page.getByRole("combobox", { name: "Camera", exact: true }).click();
    await expect(page.getByRole("option", { name: /DJI P4 Multispectral band camera/ })).toBeVisible();
    await page.getByRole("option", { name: /DJI Zenmuse H20T thermal/ }).click();
    await page.getByRole("button", { name: "Estimate survey" }).click();
    await expect(page.getByText(/DJI Zenmuse H20T thermal .*\(thermal\)\./)).toBeVisible({ timeout: 90_000 });
  });

  test("spraying: spray mix, tank loads and sorties, shown in a local area unit", async ({ page }) => {
    await openSample(page, "Bengaluru survey");
    const form = page.getByRole("form", { name: "Survey estimate" });
    await form.getByRole("radiogroup", { name: "Job type" }).getByRole("radio", { name: "Spraying" }).click();
    await expect(form.getByRole("radiogroup", { name: "Spray height presets" }).getByRole("radio", { name: "3 m" })).toBeChecked();
    await page.getByRole("button", { name: "Estimate spraying" }).click();
    const summary = page.getByRole("group", { name: "Spraying estimate" });
    await expect(summary).toBeVisible({ timeout: 90_000 });
    // 118.86 ha × 20 L/ha = 2,377 L: 238 loads of the 10 L tank; each sortie sprays until its tank
    // runs out and the next resumes there, so the sorties are the tank loads.
    await expect(tile(summary, page, "Tank loads")).toContainText("238");
    await expect(tile(summary, page, "Sorties")).toContainText("238");
    const results = page.getByRole("region", { name: "Results" });
    await expect(results.getByText("Spraying effort estimate only — no route or waypoints are generated.", { exact: true })).toBeVisible();

    await page.getByRole("radiogroup", { name: "Area unit" }).getByRole("radio", { name: "Acre" }).click();
    await expect(tile(summary, page, "Area")).toContainText("293.71");
    await expect(tile(summary, page, "Area")).toContainText("ac");
    // Work rates convert the other way from rates per area: 3.567 ha/h = 8.81 ac/h (of 9 ha/h = 22.24 ac/h).
    await expect(tile(summary, page, "Spraying rate")).toContainText("8.81");
    await expect(tile(summary, page, "Spraying rate")).toContainText("ac/h");
    await expect(tile(summary, page, "Spraying rate")).toContainText("22.24");
    await expect(page.getByRole("heading", { name: /^Sorties \(\d+\)$/ })).toBeVisible();
    await expectNoSeriousA11yViolations(page);
  });
});

test.describe("eVTOL corridor cruise reference", () => {
  test("a fixed AMSL level: cruise level, AGL range and the stretches below the minimum", async ({ page }) => {
    await openSample(page, "Bengaluru eVTOL");
    await page.getByRole("tab", { name: "Corridor" }).click();
    const form = page.getByRole("form", { name: "Corridor routing" });
    await form.getByRole("radiogroup", { name: "Height reference" }).getByRole("radio", { name: "AMSL" }).click();
    const level = form.getByRole("spinbutton", { name: /^Cruise level \(AMSL\)/ });
    await level.fill("1000");
    await level.press("Tab");
    const minimum = form.getByRole("spinbutton", { name: /^Minimum height above ground/ });
    await minimum.fill("100");
    await minimum.press("Tab");
    await page.getByRole("button", { name: "Route corridor" }).click();
    const summary = page.getByRole("group", { name: "Corridor summary" });
    await expect(summary).toBeVisible({ timeout: 120_000 });
    await expect(tile(summary, page, "Cruise level (AMSL)")).toContainText("1,000");
    await expect(tile(summary, page, "Height above ground")).toContainText("Below 100 m for");
    await expect(page.getByRole("figure", { name: "Height profile along the route" })).toBeVisible();
    await expect(page.getByRole("table", { name: /^Stretches of the route/ })).toBeVisible();
  });
});

test.describe("settings", () => {
  test("the land area unit and the bigha size are kept in this browser", async ({ page }) => {
    await page.goto("/settings");
    const unit = page.getByRole("radiogroup", { name: "Land area unit" });
    await unit.getByRole("radio", { name: "Bigha" }).click();
    await expect(unit.getByRole("radio", { name: "Bigha" })).toBeChecked();
    // Gujarat: 16 guntha = 1,618.7 m².
    await page.getByRole("combobox", { name: "Bigha size" }).click();
    await page.getByRole("option", { name: /^Gujarat/ }).click();
    await expect(page.getByRole("spinbutton", { name: /^Bigha size/ })).toHaveValue(/^1,?618\.7$/);

    await page.reload();
    await expect(page.getByRole("radiogroup", { name: "Land area unit" }).getByRole("radio", { name: "Bigha" })).toBeChecked();
    await expect(page.getByRole("combobox", { name: "Bigha size" })).toContainText("Gujarat");
    await expectNoSeriousA11yViolations(page);
  });
});
