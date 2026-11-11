import { expect, test, type Page } from "@playwright/test";
import { API_URL, openSample } from "./helpers";

/**
 * The eVTOL, drone-show and flight-audit modules: weights, selection vs hover, reopened
 * analyses, sticky playback, honest height limits, file errors and inline run errors.
 * Synthetic data pack, known answers from RUNBOOK.md §6.
 */

async function flightmdSample(page: Page, name: string): Promise<Buffer> {
  const response = await page.request.get(`${API_URL}/api/samples/${name}/download/`);
  expect(response.ok()).toBeTruthy();
  return response.body();
}

test.describe("eVTOL", () => {
  test("weights clamp visibly, hover never changes the selected site, a reopened corridor restores its endpoints", async ({ page }) => {
    test.setTimeout(360_000);
    await openSample(page, "Bengaluru eVTOL");
    await page.getByRole("navigation", { name: "Workspace modules" }).getByRole("link", { name: /eVTOL/ }).click();

    // A typed weight outside 0–1 shows the value that is used, and says so.
    const demand = page.getByRole("spinbutton", { name: "Demand", exact: true });
    await demand.fill("5");
    await demand.press("Tab");
    await expect(demand).toHaveValue("1.00");
    await expect(page.getByText("Weights range from 0 to 1: 5 was set to 1.00.")).toBeVisible();
    await demand.fill("0.35");
    await demand.press("Tab");
    await expect(demand).toHaveValue("0.35");
    await expect(page.getByText(/Weights range from 0 to 1/)).toHaveCount(0);

    await page.getByRole("button", { name: "Score sites" }).click();
    await expect(page.getByRole("region", { name: "Ranking", exact: true })).toBeVisible({ timeout: 120_000 });

    // Click selects; hovering another row on the way to the breakdown does not.
    const row3 = page.getByRole("row", { name: "Show site 3 on the map", exact: true });
    await row3.click();
    await expect(page.getByRole("region", { name: "Site #3 breakdown", exact: true })).toBeVisible();
    await page.getByRole("row", { name: "Show site 5 on the map", exact: true }).hover();
    await page.getByRole("region", { name: "Site #3 breakdown", exact: true }).hover();
    await expect(page.getByRole("region", { name: "Site #3 breakdown", exact: true })).toBeVisible();
    await expect(row3).toHaveAttribute("aria-selected", "true");
    // The score chart follows the selected site, not rank #1.
    await expect(page.getByRole("figure", { name: "Site scores" })).toContainText("site #3 selected");

    // The breakdown reconciles with the score: sub-score × weight share = points.
    const breakdown = page.getByRole("table", { name: /How the score of site 3 adds up/ });
    await expect(breakdown.getByRole("columnheader", { name: "Points" })).toBeVisible();
    await expect(breakdown.getByRole("rowheader", { name: "Demand" })).toBeVisible();

    // Route site 2 → site 3, then reopen the corridor from its URL. Both endpoints are picked
    // explicitly and differ from what a fresh form would pick (the first two choices: the
    // dataset's points before siting loads, sites #1 and #2 after), so the reload proves the
    // corridor's own endpoints were put back.
    const sitingId = new URL(page.url()).searchParams.get("a");
    await page.getByRole("tab", { name: "Corridor" }).click();
    await page.getByRole("combobox", { name: "Origin" }).click();
    await page.getByRole("option", { name: /^Site #2 \(score \d+\.\d\)$/ }).click();
    await page.getByRole("combobox", { name: "Destination" }).click();
    await page.getByRole("option", { name: /^Site #3 \(score \d+\.\d\)$/ }).click();
    await expect(page.getByRole("combobox", { name: "Origin" })).toHaveText(/^Site #2 \(/);
    await page.getByRole("button", { name: "Route corridor" }).click();
    await expect(page.getByRole("group", { name: "Corridor summary" })).toBeVisible({ timeout: 120_000 });
    // The URL now names the corridor, not the siting run it was routed from.
    await page.waitForURL((url) => {
      const a = url.searchParams.get("a");
      return a !== null && a !== sitingId;
    });

    await page.reload();
    await expect(page.getByRole("tab", { name: "Corridor" })).toHaveAttribute("aria-selected", "true", { timeout: 60_000 });
    await expect(page.getByRole("combobox", { name: "Origin" })).toHaveText(/^Site #2 \(/);
    await expect(page.getByRole("combobox", { name: "Destination" })).toHaveText(/^Site #3 \(/);
  });
});

test.describe("shows", () => {
  test("playback button is not a toggle, single-sample conflicts are not 0 s, the transport bar stays in view", async ({ page }) => {
    test.setTimeout(240_000);
    await openSample(page, "50-drone show");
    await page.getByRole("button", { name: "Validate show" }).click();
    const row = page.getByRole("row", { name: "Go to conflict between D07 and D32" }).first();
    await expect(row).toBeVisible({ timeout: 120_000 });

    const play = page.getByRole("button", { name: "Play", exact: true });
    await expect(play).toBeVisible();
    await expect(play).not.toHaveAttribute("aria-pressed", /.*/);

    // The pair is seen at a single 10 Hz check sample: less than one interval, never "0 s".
    await expect(row.getByRole("cell").last()).toHaveText("< 0.1 s");
    await row.click();
    await expect(row).toHaveAttribute("aria-selected", "true");
    await expect(page.getByRole("group", { name: "Show playback" })).toBeInViewport();
  });
});

test.describe("flight audit", () => {
  test("a limit above the default ceiling is reported as capped, and a reopened audit restores it", async ({ page }) => {
    test.setTimeout(240_000);
    await page.goto("/w/view?m=flight-audit&sample=flightmd_report_v12");
    await expect(page.getByText("GPS points")).toBeVisible();
    const limit = page.getByRole("spinbutton", { name: "Height limit above ground" });
    await limit.fill("150");
    await limit.press("Tab");
    // String matchers normalise whitespace; the UI joins number and unit with a narrow no-break
    // space (U+202F, as in "120 m"), which a regex with a plain space never matches.
    await expect(page.getByText("Default rules allow up to 120 m outside mapped zones")).toBeVisible();

    await page.getByRole("button", { name: "Audit flight" }).click();
    const summary = page.getByRole("group", { name: "Flight audit summary" });
    await expect(summary).toBeVisible({ timeout: 90_000 });
    await expect(summary).toContainText("Limit 120 m: the default rules outside mapped zones cap your 150 m");
    await expect(page.getByText("Above the 120 m default ceiling outside mapped zones").first()).toBeVisible();
    await expect(page.getByText("so the audit used 120 m along the whole flight")).toBeVisible();
    // A clicked exceedance stays selected (its chart band and map segment are emphasised too).
    const exceedance = page.getByRole("row", { name: /^Zoom to the segment from / }).first();
    await exceedance.click();
    await expect(exceedance).toHaveAttribute("aria-selected", "true");
    await page.waitForURL(/[?&]a=/);

    // Recent audits name the flight and its verdict.
    await expect(page.getByRole("button", { name: /^Open audit of 2026-03-14_06-12-08_survey\.bin, Fail, / }).first()).toBeVisible();

    await page.reload();
    await expect(page.getByRole("spinbutton", { name: "Height limit above ground" })).toHaveValue("150", { timeout: 60_000 });
    await expect(page.getByText("Saved with the opened audit")).toBeVisible();
  });

  test("a rejected file is named, replaces the previous report, and a drop works from any tab", async ({ page }) => {
    const report = await flightmdSample(page, "flightmd_report_v15");
    await page.goto("/w/view?m=flight-audit");
    const form = page.getByRole("form", { name: "Flight audit" });
    await expect(form).toBeVisible();

    // Drop on the default Link tab: the panel switches to Upload JSON and loads the file.
    await expect(page.getByRole("tab", { name: "Link" })).toHaveAttribute("aria-selected", "true");
    await form.evaluate((el, text) => {
      const transfer = new DataTransfer();
      transfer.items.add(new File([text], "flight_A.json", { type: "application/json" }));
      el.dispatchEvent(new DragEvent("dragover", { dataTransfer: transfer, bubbles: true, cancelable: true }));
      el.dispatchEvent(new DragEvent("drop", { dataTransfer: transfer, bubbles: true, cancelable: true }));
    }, report.toString("utf-8"));
    await expect(page.getByRole("tab", { name: "Upload JSON" })).toHaveAttribute("aria-selected", "true");
    await expect(form.getByText("flight_A.json")).toBeVisible();
    // Log times are in UTC.
    await expect(form.getByText(/14 Mar 2026, 06:12 UTC/)).toBeVisible();
    await expect(page.getByRole("button", { name: "Audit flight" })).toBeEnabled();

    await page.locator('input[type="file"]').setInputFiles({
      name: "flight_B_wrong.kml",
      mimeType: "application/vnd.google-earth.kml+xml",
      buffer: Buffer.from('<?xml version="1.0"?><kml xmlns="http://www.opengis.net/kml/2.2"/>'),
    });
    await expect(page.getByRole("alert").filter({ hasText: "“flight_B_wrong.kml” is a .kml file, not a FlightMD report JSON." })).toBeVisible();
    await expect(form.getByText("flight_A.json")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Audit flight" })).toBeDisabled();
  });

  test("an unreachable report link shows the error inline with Retry and an upload alternative", async ({ page }) => {
    await page.goto("/w/view?m=flight-audit");
    await page.getByRole("textbox", { name: "FlightMD report link" }).fill("https://flightmd-unreachable.invalid/report/fmd-123");
    await page.getByRole("button", { name: "Audit flight" }).click();
    const error = page.getByRole("alert").filter({ hasText: "Flight audit could not start" });
    await expect(error).toBeVisible({ timeout: 60_000 });
    await expect(error.getByRole("button", { name: "Retry" })).toBeVisible();
    await error.getByRole("button", { name: "Upload the JSON instead" }).click();
    await expect(page.getByRole("tab", { name: "Upload JSON" })).toHaveAttribute("aria-selected", "true");
  });
});
