import { expect, test, type Page } from "@playwright/test";
import { expectNoSeriousA11yViolations, openSample } from "./helpers";

/**
 * Zones in the airspace check: the red-zones notice, the rule sets (DGCA 2021 bands from the
 * airport perimeter vs the conservative rules), aerodromes marked not operational, and a
 * dataset's polygons used as zones.
 *
 * "Bengaluru eVTOL" covers HAL airport (VOBG, a medium airport of the synthetic pack), so its
 * bands show up in the chips; the farm plots are small polygons away from every airport.
 */

const rail = (page: Page) => page.getByRole("navigation", { name: "Workspace modules" });
const form = (page: Page) => page.getByRole("form", { name: "Airspace check" });
const bands = (page: Page) => page.getByRole("list", { name: "Aerodrome bands reached by the flight" });

async function openAirspace(page: Page, sample: string): Promise<void> {
  await openSample(page, sample);
  await rail(page).getByRole("link", { name: /Airspace/ }).click();
  await expect(form(page)).toBeVisible();
}

async function run(page: Page, verdict: RegExp = /Airspace: (Pass|Caution|Fail)/): Promise<void> {
  await page.getByRole("button", { name: "Check airspace" }).click();
  await expect(page.getByText(verdict)).toBeVisible({ timeout: 90_000 });
}

async function pickRules(page: Page, label: string): Promise<void> {
  await form(page).getByRole("combobox", { name: "Rules" }).click();
  await page.getByRole("option", { name: label }).click();
  await expect(form(page).getByRole("combobox", { name: "Rules" })).toHaveText(label);
}

test.describe("airspace zones v2", () => {
  test("the red-zones notice links to Digital Sky in the form and the results", async ({ page }) => {
    await openAirspace(page, "Bengaluru survey");
    const notice = form(page).getByRole("note", { name: "Red zones notice" });
    await expect(notice).toContainText("Red zones are not published as open data");
    await expect(notice.getByRole("link", { name: "Digital Sky map (opens in a new tab)" })).toHaveAttribute("href", "https://digitalsky.dgca.gov.in");
    await expect(form(page).getByRole("combobox", { name: "Rules" })).toHaveText("DGCA 2021");
    await run(page);
    // the form's notice and the result's
    await expect(page.getByRole("note", { name: "Red zones notice" })).toHaveCount(2);
    await expect(bands(page)).toContainText("Elsewhere");
    await expectNoSeriousA11yViolations(page);
  });

  test("the rules select changes the band chips; an aerodrome marked not operational loses its bands", async ({ page }) => {
    await openAirspace(page, "Bengaluru eVTOL");
    await run(page);
    await expect(bands(page)).toContainText("VOBG 0–5 km");
    await expect(bands(page)).toContainText("verify on Digital Sky");
    await expect(bands(page)).toContainText("VOBG 8–12 km");
    await expect(page.getByText("Rules: DGCA 2021.")).toBeVisible();

    await pickRules(page, "Conservative (illustrative)");
    await run(page);
    await expect(page.getByText("Rules: Conservative (illustrative).")).toBeVisible({ timeout: 30_000 });
    await expect(bands(page)).toContainText("VOBG 0–8 km");
    await expect(bands(page)).not.toContainText("0–5 km");

    // reopened (reload), the form keeps the analysis' own rules
    await page.reload();
    await expect(page.getByText(/Airspace: (Pass|Caution|Fail)/)).toBeVisible({ timeout: 30_000 });
    await expect(form(page).getByRole("combobox", { name: "Rules" })).toHaveText("Conservative (illustrative)");

    await pickRules(page, "DGCA 2021");
    const status = form(page).getByRole("region", { name: "Aerodrome status" });
    const hal = status.getByRole("switch", { name: /^Operational: VOBG/ });
    await expect(hal).toBeChecked();
    await hal.click();
    await expect(hal).not.toBeChecked();
    await run(page);
    await expect(page.getByText("Rules: DGCA 2021.")).toBeVisible({ timeout: 30_000 });
    await expect(bands(page)).not.toContainText("VOBG");
    await expect(page.getByText(/Marked not operational for this check, so their bands were not applied: VOBG/)).toBeVisible();
  });

  test("zones from a layer: the farm plots become red zones, yellow when mapped so", async ({ page }) => {
    await openAirspace(page, "Farm plots (Shapefile, UTM 43N)");
    const layers = form(page).getByRole("region", { name: /^Zones from layers/ });
    await layers.getByRole("switch", { name: /plots/i }).first().click();
    await expect(form(page).getByRole("region", { name: "Zones from layers (1)" })).toBeVisible();
    await run(page, /Airspace: Fail/);
    const zones = page.getByRole("table", { name: "Zones touched by the planned flight" });
    await expect(zones.getByText("Red zone").first()).toBeVisible();

    const mapping = form(page).getByRole("group", { name: /^Columns of/ });
    await mapping.getByRole("combobox", { name: "Otherwise" }).click();
    await page.getByRole("option", { name: "Yellow zone" }).click();
    await run(page, /Airspace: Caution/);
    await expect(zones.getByText("Yellow zone").first()).toBeVisible();
    await expect(zones.getByText("Permission").first()).toBeVisible();
  });
});
