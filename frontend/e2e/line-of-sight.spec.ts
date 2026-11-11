import { expect, test, type Page } from "@playwright/test";
import { expectNoSeriousA11yViolations, openSample } from "./helpers";

/**
 * The Line of sight module on the Bengaluru survey sample with a typed observer: coverage KPIs,
 * the radio path profile chart (and its table), the map layers as tables, a form that follows a
 * reopened analysis, and accessibility.
 */

async function openLineOfSight(page: Page): Promise<void> {
  await openSample(page, "Bengaluru survey");
  await page.getByRole("navigation", { name: "Workspace modules" }).getByRole("link", { name: /Line of sight/ }).click();
  await expect(page.getByRole("form", { name: "Line of sight" })).toBeVisible();
}

async function addPilot(page: Page, lat: string, lon: string): Promise<void> {
  const form = page.getByRole("form", { name: "Line of sight" });
  await form.getByRole("button", { name: "Add observer" }).click();
  const observer = form.getByRole("group", { name: /^Observer 1: Pilot/ });
  await expect(observer).toBeVisible();
  await observer.getByLabel(/^Latitude/).fill(lat);
  await observer.getByLabel(/^Latitude/).press("Tab");
  await observer.getByLabel(/^Longitude/).fill(lon);
  await observer.getByLabel(/^Longitude/).press("Tab");
}

async function runAndWait(page: Page): Promise<void> {
  await page.getByRole("button", { name: "Check line of sight" }).click();
  // A new run opens the results drawer by itself (no "Show results" click: the drawer can open
  // between a visibility check and the click, and the button is then "Hide results").
  const drawer = page.getByRole("region", { name: "Results", exact: true });
  await expect(drawer.getByRole("button", { name: "Hide results" })).toHaveAttribute("aria-expanded", "true");
  await expect(page.getByRole("group", { name: "Line-of-sight summary" })).toBeVisible({ timeout: 90_000 });
}

test.describe("line of sight", () => {
  test("a typed observer gives coverage, a radio path and its profile chart", async ({ page }) => {
    await openLineOfSight(page);
    await addPilot(page, "12.902", "77.488");
    await runAndWait(page);

    const summary = page.getByRole("group", { name: "Line-of-sight summary" });
    await expect(summary.getByText("Area in visual line of sight", { exact: true })).toBeVisible();
    await expect(summary.getByText("Worst Fresnel clearance", { exact: true })).toBeVisible();
    await expect(summary.getByText("Free-space path loss", { exact: true })).toBeVisible();
    await expect(summary.getByText("VLOS guidance distance", { exact: true })).toBeVisible();

    // The observers table lists the typed pilot with the share of the area it sees.
    const observers = page.getByRole("table", { name: /each pilot, observer or ground station can see/ });
    await expect(observers.getByRole("rowheader", { name: /^Pilot/ })).toBeVisible();
    await expect(observers.getByRole("cell", { name: /%$/ }).first()).toBeVisible();

    // No ground station: the pilot is the radio station; the path goes to the farthest corner.
    const paths = page.getByRole("table", { name: /Radio paths with first Fresnel zone clearance/ });
    await expect(paths.getByRole("rowheader", { name: "Farthest corner" })).toBeVisible();
    const chart = page.getByRole("figure", { name: "Path profile: Pilot → Farthest corner" });
    await expect(chart).toBeVisible();
    await expect(chart.getByRole("list", { name: "Chart legend" })).toContainText("Radio line of sight");
    await expect(chart.getByRole("list", { name: "Chart legend" })).toContainText("First Fresnel zone, lower edge");
    await chart.getByRole("button", { name: "Show table" }).click();
    const profile = chart.getByRole("table");
    await expect(profile.getByRole("columnheader", { name: "Fresnel lower edge" })).toBeVisible();
    expect(await profile.getByRole("row").count()).toBeGreaterThan(2);

    await expect(page.getByText("Geometric evidence from the data-pack surface model — not a VLOS approval.").first()).toBeVisible();
    await expectNoSeriousA11yViolations(page);
  });

  test("the visibility layers have tables and a reopened run restores its observer", async ({ page }) => {
    await openLineOfSight(page);
    await addPilot(page, "12.9035", "77.4905");
    const form = page.getByRole("form", { name: "Line of sight" });
    await form.getByRole("radiogroup", { name: "Target height presets" }).getByRole("radio", { name: "60 m" }).click();
    await runAndWait(page);

    await page.getByRole("button", { name: "View Visible area as table" }).click();
    const visible = page.getByRole("table", { name: "Visible area as a table" });
    await expect(visible).toBeVisible();
    expect(await visible.getByRole("row").count()).toBeGreaterThan(1);

    await page.reload();
    await expect(page.getByRole("group", { name: "Line-of-sight summary" })).toBeVisible({ timeout: 30_000 });
    const observer = page.getByRole("form", { name: "Line of sight" }).getByRole("group", { name: /^Observer 1: Pilot/ });
    await expect(observer.getByLabel(/^Latitude/)).toHaveValue("12.9035");
    await expect(observer.getByLabel(/^Longitude/)).toHaveValue("77.4905");
    await expect(
      page.getByRole("form", { name: "Line of sight" }).getByRole("radiogroup", { name: "Target height presets" }).getByRole("radio", { name: "60 m" }),
    ).toBeChecked();
  });

  test("Place on map: the map takes the focus, Space places the map centre, Escape cancels", async ({ page }) => {
    await openLineOfSight(page);
    const form = page.getByRole("form", { name: "Line of sight" });
    await form.getByRole("button", { name: "Add observer" }).click();
    const observer = form.getByRole("group", { name: /^Observer 1: Pilot/ });
    await expect(observer.getByRole("textbox", { name: /^Name/ })).toBeFocused(); // focus follows the new row
    const canvas = page.locator("#map canvas.maplibregl-canvas");

    // Escape cancels and gives the focus back to the button.
    await observer.getByRole("button", { name: "Place on map" }).click();
    await expect(canvas).toBeFocused();
    await expect(observer.getByRole("button", { name: /Click the map/ })).toHaveAttribute("aria-pressed", "true");
    await page.keyboard.press("Escape");
    const place = observer.getByRole("button", { name: "Place on map" });
    await expect(place).toHaveAttribute("aria-pressed", "false");
    await expect(place).toBeFocused();
    await expect(observer.getByLabel(/^Latitude/)).toHaveValue("");

    // Keyboard: Space on the focused map places the observer at the map centre (the sample area).
    await place.click();
    await expect(canvas).toBeFocused();
    await page.keyboard.press("Space");
    await expect(observer.getByLabel(/^Latitude/)).toHaveValue(/^12\.9/);
    await expect(observer.getByLabel(/^Longitude/)).toHaveValue(/^77\.4/);
    await expect(place).toBeFocused();

    // Pointer: a click on the map moves it.
    const before = await observer.getByLabel(/^Longitude/).inputValue();
    await place.click();
    await expect(observer.getByRole("button", { name: /Click the map/ })).toHaveAttribute("aria-pressed", "true");
    const box = await canvas.boundingBox();
    if (!box) throw new Error("map canvas not found");
    await page.mouse.click(box.x + box.width / 2 + 80, box.y + box.height / 2);
    await expect(observer.getByLabel(/^Longitude/)).not.toHaveValue(before);
    await expect(observer.getByLabel(/^Longitude/)).toHaveValue(/^77\.4/);
  });

  test("an observer without coordinates blocks the run with an error summary", async ({ page }) => {
    await openLineOfSight(page);
    const form = page.getByRole("form", { name: "Line of sight" });
    await form.getByRole("button", { name: "Add observer" }).click();
    await page.getByRole("button", { name: "Check line of sight" }).click();
    const errors = page.getByRole("alert").filter({ hasText: "Latitude" });
    await expect(errors).toBeVisible();
    await expect(errors).toBeFocused();
  });
});
