import { expect, test, type Page } from "@playwright/test";
import { API_URL, expectNoSeriousA11yViolations, openSample } from "./helpers";

/**
 * One result per feature (the toggle, the results table with its summary bar and filters, the
 * report), the parameters that were API-only (airspace grid cell limit, eVTOL cost weights and
 * cells, show dynamics window) and the terrain-model chip in line of sight.
 */

async function openModule(page: Page, name: RegExp): Promise<void> {
  await page.getByRole("navigation", { name: "Workspace modules" }).getByRole("link", { name }).click();
}

async function openAdvanced(page: Page, form: ReturnType<Page["getByRole"]>): Promise<void> {
  const button = form.getByRole("button", { name: "Advanced" });
  if ((await button.getAttribute("aria-expanded")) !== "true") await button.click();
}

test.describe("one result per feature", () => {
  test("airspace on the farm plots: a row per plot, filters with feedback, and the report", async ({ page }) => {
    await openSample(page, "Farm plots (Shapefile, UTM 43N)");
    await openModule(page, /Airspace/);
    const form = page.getByRole("form", { name: "Airspace check" });
    const toggle = form.getByRole("switch", { name: /^One result per feature \(all 3\)/ });
    await toggle.click();
    await expect(toggle).toBeChecked();
    await expect(form.getByRole("combobox", { name: "Name each result by" })).toHaveText(/Feature name/);
    await form.getByRole("button", { name: "Check airspace" }).click();

    const results = page.getByRole("region", { name: "Results" });
    await expect(results.getByText("One result per feature", { exact: true }).first()).toBeVisible({ timeout: 120_000 });
    const table = results.getByRole("table", { name: "One result per feature" });
    for (const plot of ["Plot A", "Plot B", "Plot C"]) {
      await expect(table.getByRole("row", { name: `Show ${plot} on the map`, exact: true })).toBeVisible();
    }
    await expect(table.getByRole("columnheader", { name: /^Band/ })).toBeVisible();
    await expect(table.getByRole("columnheader", { name: /^Free-flight ceiling/ })).toBeVisible();

    // The summary bar filters the table and says what is shown.
    const bar = results.getByRole("radiogroup", { name: "Show only features with this result" });
    const first = bar.getByRole("radio").first();
    const label = (await first.textContent()) ?? "";
    const count = Number(/:\s*(\d+)/.exec(label)?.[1] ?? "0");
    await first.click();
    await expect(first).toBeChecked();
    await expect(results.getByText(new RegExp(`^Showing ${count} of 3 features`))).toBeVisible();
    await results.getByRole("button", { name: "Show all" }).click();
    await expect(results.getByText(/^3 features\. Click a row/)).toBeVisible();

    await results.getByLabel("Find a feature").fill("plot b");
    await expect(table.getByRole("row", { name: "Show Plot B on the map", exact: true })).toBeVisible();
    await expect(table.getByRole("row", { name: "Show Plot A on the map", exact: true })).toHaveCount(0);
    await expect(results.getByText(/^Showing 1 of 3 features/)).toBeVisible();
    await expectNoSeriousA11yViolations(page);

    // The report has the same feature table.
    const url = new URL(page.url());
    await page.goto(`/w/report?d=${url.searchParams.get("d")}&a=${url.searchParams.get("a")}`);
    const reportTable = page.getByRole("table", { name: "One result per feature (most severe first)" });
    await expect(reportTable).toBeVisible({ timeout: 30_000 });
    await expect(reportTable).toContainText("Plot C");
    await expect(page.getByRole("group", { name: /one result per feature key numbers/ })).toBeVisible();
  });

  test("survey: each plot's area and photos, with the toggle back on after a reload", async ({ page }) => {
    await openSample(page, "Farm plots (Shapefile, UTM 43N)");
    await openModule(page, /Survey/);
    const form = page.getByRole("form", { name: "Survey estimate" });
    await form.getByRole("switch", { name: /^One result per feature/ }).click();
    await form.getByRole("button", { name: "Estimate survey" }).click();

    const table = page.getByRole("region", { name: "Results" }).getByRole("table", { name: "One result per feature" });
    await expect(table.getByRole("row", { name: "Show Plot A on the map", exact: true })).toBeVisible({ timeout: 120_000 });
    await expect(table.getByRole("columnheader", { name: /^Area/ })).toBeVisible();
    await expect(table.getByRole("columnheader", { name: /^Photos/ })).toBeVisible();

    await page.reload();
    await expect(form.getByRole("switch", { name: /^One result per feature/ })).toBeChecked({ timeout: 30_000 });
  });
});

test.describe("parameters that were API-only", () => {
  test("airspace grid cell limit, eVTOL corridor costs and cells, show dynamics window", async ({ page }) => {
    await openSample(page, "Bengaluru survey");
    await openModule(page, /Airspace/);
    const airspace = page.getByRole("form", { name: "Airspace check" });
    await openAdvanced(page, airspace);
    await expect(airspace.getByRole("spinbutton", { name: "Grid cell limit" })).toHaveValue(/^20,?000$/);

    await openModule(page, /eVTOL/);
    await page.getByRole("tab", { name: "Corridor" }).click();
    const corridor = page.getByRole("form", { name: "Corridor routing" });
    await openAdvanced(page, corridor);
    const weights = corridor.getByRole("group", { name: "Route cost weights" });
    await expect(weights.getByRole("spinbutton", { name: "Ground risk cost" })).toHaveValue(/^0\.5$/);
    await expect(weights.getByRole("spinbutton", { name: "Airspace cost" })).toHaveValue(/^0\.3$/);
    await expect(weights.getByRole("spinbutton", { name: "Obstacle cost" })).toHaveValue(/^0\.2$/);
    await expect(corridor.getByRole("spinbutton", { name: "Cell limit" })).toHaveValue(/^60,?000$/);
    await expect(corridor.getByRole("spinbutton", { name: "Grid resolution (H3)" })).toHaveValue("8");
  });

  test("show dynamics window and airborne threshold", async ({ page }) => {
    await openSample(page, "50-drone show");
    const form = page.getByRole("form", { name: "Drone-show validation" });
    await openAdvanced(page, form);
    await expect(form.getByRole("spinbutton", { name: "Dynamics window" })).toHaveValue(/^1(\.0+)?$/);
    await expect(form.getByRole("spinbutton", { name: "Airborne threshold" })).toHaveValue(/^1(\.0+)?$/);
  });
});

test.describe("one result per feature, features sent with the request", () => {
  test("rows stay apart when an id repeats another feature's number, and there is no ED-318 entry", async ({ page, request }) => {
    test.setTimeout(240_000);
    const sample = await request.post(`${API_URL}/api/samples/bengaluru_survey/`);
    expect(sample.ok()).toBe(true);
    const datasetId = ((await sample.json()) as { id: string }).id;
    const plot = (name: string, lat: number) => ({
      type: "Feature",
      properties: { name },
      geometry: {
        type: "Polygon",
        coordinates: [[[77.488, lat], [77.492, lat], [77.492, lat + 0.003], [77.488, lat + 0.003], [77.488, lat]]],
      },
    });
    // the north plot's id 1 is also the south plot's place in the list
    const features = [{ ...plot("North plot", 12.906), id: 1 }, plot("South plot", 12.901)];
    const created = await request.post(`${API_URL}/api/analyses/`, {
      data: {
        kind: "airspace",
        dataset_id: datasetId,
        params: { grid: false, batch: { mode: "per_feature", features: { type: "FeatureCollection", features } } },
      },
    });
    expect(created.ok(), await created.text()).toBe(true);
    const analysisId = ((await created.json()) as { id: string }).id;
    await expect
      .poll(async () => ((await (await request.get(`${API_URL}/api/analyses/${analysisId}/`)).json()) as { status: string }).status, {
        timeout: 120_000,
      })
      .toBe("COMPLETED");

    await page.goto(`/w/view?d=${datasetId}&m=airspace&a=${analysisId}`);
    const results = page.getByRole("region", { name: "Results" });
    const table = results.getByRole("table", { name: "One result per feature" });
    const north = table.getByRole("row", { name: "Show North plot on the map", exact: true });
    const south = table.getByRole("row", { name: "Show South plot on the map", exact: true });
    await expect(north).toBeVisible({ timeout: 60_000 });
    await expect(south).toBeVisible();
    await north.click();
    await expect(north).toHaveAttribute("aria-selected", "true");
    await expect(south).toHaveAttribute("aria-selected", "false");
    await south.click();
    await expect(south).toHaveAttribute("aria-selected", "true");
    await expect(north).toHaveAttribute("aria-selected", "false");

    // no zones layer in a per-feature result, so nothing to export as ED-318
    await results.getByRole("button", { name: "Export" }).click();
    const formats = page.getByRole("list", { name: "Analysis export formats" });
    await expect(formats.getByRole("link", { name: /^Analysis \(GeoJSON\)/ })).toBeVisible();
    await expect(formats.getByRole("link", { name: /^Zones \(ED-318/ })).toHaveCount(0);
  });
});

test("line of sight results carry the terrain-model chip", async ({ page }) => {
  await openSample(page, "Bengaluru survey");
  await openModule(page, /Line of sight/);
  await page.getByRole("button", { name: "Check line of sight" }).click();
  const results = page.getByRole("region", { name: "Results" });
  await expect(results.getByRole("group", { name: "Line-of-sight summary" })).toBeVisible({ timeout: 120_000 });
  await expect(results.getByText("Synthetic terrain", { exact: true })).toBeVisible();
});
