import { expect, test } from "@playwright/test";
import { API_URL, expectNoSeriousA11yViolations, openSample } from "./helpers";

/**
 * GPX ingest, flight audit of a dataset's position track (training area, distance from
 * take-off, time above the limit) and the drone-show site upgrades (derived show box, audience
 * distance rules, pads, "Check airspace for this show box"). Synthetic data pack; known answers
 * from airbrief_core/samples.py (training flight) and docs/features/tracks.md.
 */

test.describe("flight audit of a dataset track", () => {
  test("the GPX training flight opens on its track, leaves the training box once and goes beyond 500 m once", async ({ page }) => {
    test.setTimeout(240_000);
    await openSample(page, "Training flight (GPX)");
    await expect(page).toHaveURL(/m=flight-audit/);

    const form = page.getByRole("form", { name: "Flight audit" });
    await expect(form.getByRole("tab", { name: "Dataset track" })).toHaveAttribute("aria-selected", "true", { timeout: 60_000 });
    // The track card: name, 401 timed points over 6 min 40 s. Scoped to the source's tab panel:
    // the closed track is also listed (hidden <option>) in the Training area select below.
    const trackCard = form.getByRole("tabpanel", { name: "Dataset track" });
    await expect(trackCard.getByText("Training flight", { exact: true })).toBeVisible();
    await expect(trackCard.getByText("401", { exact: true })).toBeVisible();
    await expect(trackCard.getByText("6 min 40 s")).toBeVisible();
    // The closed route named "Training box" is the default training area.
    await expect(form.getByRole("combobox", { name: "Training area" })).toHaveText(/Training box/);

    await form.getByRole("spinbutton", { name: "Maximum distance from take-off" }).fill("500");
    await form.getByRole("spinbutton", { name: "Maximum distance from take-off" }).press("Tab");
    await form.getByRole("button", { name: "Audit flight" }).click();

    const results = page.getByRole("region", { name: "Results" });
    await expect(results.getByRole("heading", { name: "Left the training area (1)" })).toBeVisible({ timeout: 120_000 });
    await expect(results.getByRole("heading", { name: /^Beyond 500\s?m from take-off \(1\)$/ })).toBeVisible();
    await expect(results.getByText("Flight time", { exact: true })).toBeVisible();
    await expect(results.getByText("Farthest from take-off", { exact: true })).toBeVisible();
    // 60 s outside the box, from t = 200 s (3:20.0) after take-off.
    const exitRow = results.getByRole("table", { name: /Segments outside Training box/ }).getByRole("row").nth(1);
    await expect(exitRow).toContainText("3:20.0");
    await expect(exitRow).toContainText("1 min 00 s");
    await expectNoSeriousA11yViolations(page);
  });

  test("a FlightMD report sample chosen in the command palette opens the flight audit with it (not as a dataset)", async ({ page }) => {
    test.setTimeout(120_000);
    await page.goto("/w");
    await page.getByRole("button", { name: "Search and commands" }).click();
    const palette = page.getByRole("dialog", { name: "Command palette" });
    await palette.getByRole("combobox").fill("FlightMD report (schema 1.5)");
    await palette.getByRole("option", { name: /FlightMD report \(schema 1\.5\)/ }).click();
    await expect(page).toHaveURL(/\/w\/view\?.*m=flight-audit.*sample=flightmd_report_v15/);
    await expect(page).not.toHaveURL(/[?&]d=/);
    const form = page.getByRole("form", { name: "Flight audit" });
    await expect(form.getByRole("tab", { name: "Sample" })).toHaveAttribute("aria-selected", "true");
    await expect(form.getByText("FlightMD report (schema 1.5)", { exact: true }).first()).toBeVisible({ timeout: 60_000 });
  });

  test("without a dataset the Dataset track source is disabled and says why", async ({ page }) => {
    await page.goto("/w/view?m=flight-audit");
    const form = page.getByRole("form", { name: "Flight audit" });
    await expect(form.getByRole("tab", { name: "Dataset track" })).toBeDisabled();
    await expect(form.getByText("Dataset track: open a GPX, KML or CSV track with times to audit it here.")).toBeVisible();
    // The FlightMD sources are unchanged.
    await expect(form.getByRole("tab", { name: "Link" })).toHaveAttribute("aria-selected", "true");
  });
});

test.describe("GPX upload", () => {
  test("the upload area accepts .gpx and opens the dataset", async ({ page }) => {
    test.setTimeout(120_000);
    const gpx = await page.request.get(`${API_URL}/api/samples/training_flight_gpx/download/`);
    expect(gpx.ok()).toBeTruthy();
    await page.goto("/w");
    const input = page.locator('input[type="file"]').first();
    await expect(input).toHaveAttribute("accept", /\.gpx/);
    await input.setInputFiles({ name: "my_training_flight.gpx", mimeType: "application/gpx+xml", buffer: await gpx.body() });
    await page.waitForURL(/\/w\/view\?d=/, { timeout: 60_000 });
    await expect(page.getByRole("navigation", { name: "Workspace modules" })).toBeVisible();
  });
});

test.describe("drone show site checks", () => {
  test("a show without a show box gets one derived from the tracks, a ballistic audience distance and a one-click airspace check", async ({ page }) => {
    test.setTimeout(300_000);
    await openSample(page, "50-drone show (CSV)");
    const form = page.getByRole("form", { name: "Drone-show validation" });
    await form.getByRole("radio", { name: "Ballistic" }).click();
    await form.getByRole("radiogroup", { name: "Safety factor presets" }).getByRole("radio", { name: "50 %" }).click();
    await form.getByRole("button", { name: "Validate show" }).click();

    const results = page.getByRole("region", { name: "Results" });
    await expect(results.getByText("Derived from the tracks (+5 m margin)", { exact: false })).toBeVisible({ timeout: 120_000 });
    await expect(results.getByText(/\(Ballistic\)/)).toBeVisible();
    await expect(results.getByText(/safety factor/)).toBeVisible();
    await expect(results.getByText("Pad spacing", { exact: true })).toBeVisible();
    await expectNoSeriousA11yViolations(page);

    await results.getByRole("button", { name: "Check airspace for this show box" }).click();
    await expect(page).toHaveURL(/m=airspace/, { timeout: 60_000 });
    await expect(page.getByRole("region", { name: "Results" }).getByText(/^Airspace: /)).toBeVisible({ timeout: 120_000 });
  });

  test("the origin and height reference can be set and are reported", async ({ page }) => {
    test.setTimeout(240_000);
    await openSample(page, "50-drone show (clean)");
    const form = page.getByRole("form", { name: "Drone-show validation" });
    await form.getByRole("switch", { name: "Set the origin" }).click();
    await form.getByRole("spinbutton", { name: "Origin longitude" }).fill("77.592");
    await form.getByRole("spinbutton", { name: "Origin latitude" }).fill("13.0005");
    await form.getByRole("spinbutton", { name: "Origin latitude" }).press("Tab");
    await expect(form.getByRole("button", { name: "Pick the origin on the map" })).toBeVisible();
    await form.getByRole("button", { name: "Validate show" }).click();

    const results = page.getByRole("region", { name: "Results" });
    // String matcher: the degree and hemisphere are separated by U+202F, which a regex space misses.
    await expect(results.getByText("Origin 13.00050° N, 77.59200° E (given)")).toBeVisible({ timeout: 120_000 });
    await expect(results.getByText(/heights relative to the origin/)).toBeVisible();
    await expect(results.getByText("“Show box” polygon")).toBeVisible();
  });
});
