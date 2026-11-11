import { expect, test, type Page } from "@playwright/test";
import { expectNoSeriousA11yViolations, openSample } from "./helpers";

/**
 * The SORA 2.5 operational volume in the ground-risk module (terms from the aircraft class,
 * assemblies entry, Table 2 grid, adjacent area, assemblies within 1 km, map layers) and the
 * airspace "not assessed" banner with its Digital Sky link.
 *
 * Needs a demo data pack built by the current `seed.datapack --synthetic` (it has the
 * `assemblies` layer with "Synthetic Kengeri stadium" about 0.8 km east of the survey area).
 */

async function openModule(page: Page, name: RegExp): Promise<void> {
  await page.getByRole("navigation", { name: "Workspace modules" }).getByRole("link", { name }).click();
}

const groundRiskForm = (page: Page) => page.getByRole("form", { name: "Ground risk" });

test.describe("ground risk: SORA 2.5 operational volume", () => {
  test("the aircraft class fills the contingency terms, and each term can be edited", async ({ page }) => {
    await openSample(page, "Bengaluru survey");
    await openModule(page, /Ground risk/);
    const form = groundRiskForm(page);
    await expect(form.getByRole("switch", { name: "SORA 2.5 operational volume" })).toBeChecked();
    await expect(form.getByText(/^Preview:/)).toBeVisible();

    await form.getByRole("button", { name: "Contingency volume terms" }).click();
    // Mapping quadcopter: a multirotor stopping at 35° pitch.
    await expect(form.getByRole("spinbutton", { name: /^Pitch angle/ })).toHaveValue("35");
    await form.getByRole("combobox", { name: "Aircraft class" }).click();
    await page.getByRole("option", { name: /^Fixed-wing mapper/ }).click();
    await expect(form.getByRole("radio", { name: /Fixed-wing \(turn\)/ })).toHaveAttribute("aria-checked", "true");
    const bank = form.getByRole("spinbutton", { name: /^Bank angle/ });
    await expect(bank).toHaveValue("30");
    await bank.fill("40");
    await bank.press("Tab");
    await expect(bank).toHaveValue("40");
    await form.getByRole("button", { name: "Use the aircraft class values" }).click();
    await expect(bank).toHaveValue("30");
  });

  test("a run shows the evidence label, Table 2 with its cell, the adjacent area and the assemblies", async ({ page }) => {
    await openSample(page, "Bengaluru survey");
    await openModule(page, /Ground risk/);
    const form = groundRiskForm(page);

    // A user event about 1 km north of the survey area.
    const add = form.getByRole("group", { name: "Add an assembly" });
    await add.getByRole("textbox", { name: /^Name/ }).fill("Harvest fair");
    await add.getByRole("textbox", { name: /^Latitude, longitude/ }).fill("12.9120, 77.4910");
    await add.getByRole("spinbutton", { name: /^Expected people/ }).fill("12000");
    await add.getByRole("button", { name: "Add assembly" }).click();
    const list = form.getByRole("list", { name: "Assemblies to check" });
    await expect(list.getByText("Harvest fair", { exact: true })).toBeVisible();
    await expect(form.getByRole("button", { name: "Remove Harvest fair" })).toBeVisible();

    await page.getByRole("button", { name: "Assess ground risk" }).click();
    const summary = page.getByRole("group", { name: "Ground risk summary" });
    await expect(summary).toBeVisible({ timeout: 90_000 });
    await expect(page.getByRole("note").filter({ hasText: "SORA Step 2 / Step 8 evidence — not a GRC, ARC or SAIL" })).toBeVisible();
    const igrc = summary.locator("dl").filter({ has: page.locator("dt", { hasText: /^Intrinsic ground risk class$/ }) });
    await expect(igrc).toContainText(/band /);

    const sizes = page.getByRole("group", { name: "Operational volume sizes" });
    await expect(sizes.locator("dt", { hasText: /^Contingency volume$/ })).toBeVisible();
    await expect(page.getByRole("table", { name: "Contingency volume, ground risk buffer and adjacent-area terms" })).toContainText("S_CM");

    const table2 = page.getByRole("table", { name: /^Intrinsic ground risk class by population density/ });
    await expect(table2).toBeVisible();
    await expect(table2.getByRole("cell", { name: /applies/ })).toHaveCount(1);

    await expect(page.getByRole("group", { name: "Adjacent area" }).locator("dt", { hasText: /^Average density$/ })).toBeVisible();

    const assemblies = page.getByRole("table", { name: "Outdoor assemblies within 1 km of the operational volume" });
    await expect(assemblies.getByRole("row", { name: "Show Synthetic Kengeri stadium on the map" })).toBeVisible();
    await expect(assemblies.getByRole("row", { name: "Show Harvest fair on the map" })).toContainText("Your entry");

    const layers = page.getByRole("complementary", { name: "Layers and features" }).getByRole("region", { name: "Layers" });
    for (const name of ["Contingency volume", "Ground risk buffer", "Adjacent area", "Population density", "Hotspots"]) {
      await expect(layers.getByRole("button", { name: `View ${name} as table` })).toBeVisible();
    }
    await layers.getByRole("button", { name: "View Population density as table" }).click();
    await expect(page.getByRole("table", { name: "Population density as a table" })).toBeVisible();
    await expectNoSeriousA11yViolations(page);

    // Reloaded, the form shows this analysis' own parameters, events included.
    await page.reload();
    await expect(groundRiskForm(page).getByRole("list", { name: "Assemblies to check" }).getByText("Harvest fair", { exact: true })).toBeVisible({
      timeout: 30_000,
    });
    await expect(groundRiskForm(page).getByRole("switch", { name: "SORA 2.5 operational volume" })).toBeChecked();
  });

  test("after switching modules, the form shows the run's own contingency terms, not the class defaults", async ({ page }) => {
    await openSample(page, "Bengaluru survey");
    await openModule(page, /Ground risk/);
    const form = groundRiskForm(page);
    await form.getByRole("button", { name: "Contingency volume terms" }).click();
    const pitch = form.getByRole("spinbutton", { name: /^Pitch angle/ });
    await pitch.fill("25");
    await pitch.press("Tab");
    await page.getByRole("button", { name: "Assess ground risk" }).click();
    await expect(page.getByRole("group", { name: "Ground risk summary" })).toBeVisible({ timeout: 90_000 });

    await openModule(page, /Airspace/);
    await openModule(page, /Ground risk/);
    const again = groundRiskForm(page);
    await again.getByRole("button", { name: "Contingency volume terms" }).click();
    await expect(again.getByRole("spinbutton", { name: /^Pitch angle/ })).toHaveValue("25");
  });

  test("Enter in the assemblies entry adds the event without running the analysis; a map click places it", async ({ page }) => {
    await openSample(page, "Bengaluru survey");
    await openModule(page, /Ground risk/);
    const form = groundRiskForm(page);
    const add = form.getByRole("group", { name: "Add an assembly" });
    await add.getByRole("textbox", { name: /^Name/ }).fill("Weekly market");

    await add.getByRole("button", { name: "Place on map" }).click();
    await expect(add.getByRole("button", { name: /^Click the map/ })).toHaveAttribute("aria-pressed", "true");
    const canvas = page.locator("#map canvas.maplibregl-canvas");
    const box = await canvas.boundingBox();
    if (!box) throw new Error("map canvas not found");
    // Clicking the canvas (not page coordinates) waits until nothing covers the map: until the
    // map has loaded, its placeholder takes the clicks and the pick stays pending.
    await canvas.click({ position: { x: box.width / 2 + 40, y: box.height / 2 + 20 } });
    const coords = add.getByRole("textbox", { name: /^Latitude, longitude/ });
    await expect(coords).toHaveValue(/^-?\d+\.\d{6}, -?\d+\.\d{6}$/);
    await expect(add.getByRole("button", { name: "Place on map" })).toHaveAttribute("aria-pressed", "false");

    await coords.press("Enter");
    await expect(form.getByRole("list", { name: "Assemblies to check" }).getByText("Weekly market", { exact: true })).toBeVisible();
    await expect(add.getByRole("textbox", { name: /^Name/ })).toBeFocused();
    // The module form was not submitted: no analysis started.
    await page.waitForTimeout(1_000);
    await expect(page).not.toHaveURL(/[?&]a=/);
    await expect(page.getByRole("group", { name: "Ground risk summary" })).toHaveCount(0);
  });

  test("switched off, the earlier footprint method runs without the volume sections", async ({ page }) => {
    await openSample(page, "Bengaluru survey");
    await openModule(page, /Ground risk/);
    const form = groundRiskForm(page);
    await form.getByRole("switch", { name: "SORA 2.5 operational volume" }).click();
    await expect(form.getByText("Assemblies are checked against the operational volume: switch it on to add them.")).toBeVisible();
    await page.getByRole("button", { name: "Assess ground risk" }).click();
    await expect(page.getByRole("group", { name: "Ground risk summary" })).toBeVisible({ timeout: 90_000 });
    await expect(page.getByRole("group", { name: "Operational volume sizes" })).toHaveCount(0);
    await expect(page.getByRole("table", { name: "Highest-density cells in the footprint" })).toBeVisible();
  });
});

test.describe("airspace: not assessed without a data pack", () => {
  test("an area outside every data pack is never a pass, and Digital Sky is linked", async ({ page }) => {
    const far = {
      type: "FeatureCollection",
      features: [{ type: "Feature", properties: { name: "Alpine field" }, geometry: { type: "Point", coordinates: [10.0, 45.0] } }],
    };
    await page.goto("/w");
    await page.getByLabel("Choose a file to upload").setInputFiles({
      name: "alpine_field.geojson",
      mimeType: "application/geo+json",
      buffer: Buffer.from(JSON.stringify(far)),
    });
    await page.waitForURL(/\/w\/view\?(.*&)?d=/, { timeout: 60_000 });
    await openModule(page, /Airspace/);
    await page.getByRole("button", { name: "Check airspace" }).click();
    await expect(page.getByText("Airspace: Caution")).toBeVisible({ timeout: 90_000 });
    // The verdict summary also starts with "Airspace not assessed": match the banner by its title.
    const banner = page.getByRole("status").filter({ has: page.getByText("Airspace not assessed", { exact: true }) });
    await expect(banner).toBeVisible();
    await expect(banner).toContainText("This is not an all-clear.");
    const link = page.getByRole("link", { name: "Verify on Digital Sky (opens in a new tab)" });
    await expect(link).toHaveAttribute("href", "https://digitalsky.dgca.gov.in");
    await expectNoSeriousA11yViolations(page);
  });

  test("a covered area keeps the permanent Digital Sky link and no banner", async ({ page }) => {
    await openSample(page, "Bengaluru survey");
    await openModule(page, /Airspace/);
    await page.getByRole("button", { name: "Check airspace" }).click();
    await expect(page.getByText(/Airspace: (Pass|Caution|Fail)/)).toBeVisible({ timeout: 90_000 });
    await expect(page.getByRole("link", { name: "Verify on Digital Sky (opens in a new tab)" })).toBeVisible();
    await expect(page.getByRole("status").filter({ has: page.getByText("Airspace not assessed", { exact: true }) })).toHaveCount(0);
  });
});
