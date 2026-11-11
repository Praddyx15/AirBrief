import { expect, test, type Locator, type Page } from "@playwright/test";
import { expectNoSeriousA11yViolations, openSample } from "./helpers";

/**
 * Sun times, the daylight-profile window check, wind at height and the power lines near the
 * Bengaluru survey area (the synthetic pack has a 220 kV line crossing it and a 33 kV feeder
 * about 40 m east of it).
 *
 * Reference values for the survey area's centre on 21 June 2026 (NOAA equations, UTC+05:30):
 * sunrise 05:55, sunset 18:48, civil dusk 19:12. A 17:30–19:30 window under FAA Part 107 has
 * 23 min of civil twilight (lighting required) and 18 min of night.
 */

// The form takes the browser's zone: pin it so the local times are the IST ones above.
test.use({ timezoneId: "Asia/Kolkata" });

async function openEnvironment(page: Page): Promise<Locator> {
  await openSample(page, "Bengaluru survey");
  await page.getByRole("navigation", { name: "Workspace modules" }).getByRole("link", { name: /Environment/ }).click();
  const form = page.getByRole("form", { name: "Environment evidence" });
  await expect(form).toBeVisible();
  return form;
}

async function planWindow(form: Locator): Promise<void> {
  await form.getByLabel(/^Date \(local\)/).fill("2026-06-21");
  await form.getByLabel(/^Start \(local\)/).fill("17:30");
  await form.getByLabel(/^End \(local\)/).fill("19:30");
}

function tile(page: Page, summary: Locator, label: RegExp): Locator {
  return summary.locator("dl").filter({ has: page.locator("dt", { hasText: label }) });
}

test.describe("environment", () => {
  test("sun times, the planned window and the power lines near the survey area", async ({ page }) => {
    const form = await openEnvironment(page);
    await planWindow(form);
    // Times are labelled with the zone in the form too.
    await expect(form.getByText(/Times are local, UTC\+05:30/)).toBeVisible();
    await form.getByRole("combobox", { name: "Daylight profile", exact: true }).click();
    await page.getByRole("option", { name: "FAA Part 107 (US)" }).click();
    await form.getByRole("button", { name: "Check environment" }).click();

    const summary = page.getByRole("group", { name: "Environment summary" });
    await expect(summary).toBeVisible({ timeout: 90_000 });
    await expect(tile(page, summary, /^Sunrise$/)).toContainText("05:55");
    await expect(tile(page, summary, /^Sunrise$/)).toContainText("UTC+05:30");
    await expect(tile(page, summary, /^Sunset$/)).toContainText("18:48");
    await expect(tile(page, summary, /^Civil dusk$/)).toContainText("19:12");
    const window = tile(page, summary, /^Planned window$/);
    await expect(window).toContainText("18");
    await expect(window).toContainText("Outside the profile");
    await expect(tile(page, summary, /^Magnetic declination$/)).toContainText("West of true north");
    await expect(tile(page, summary, /^Nearest power line$/)).toContainText("Crosses");

    // The window statement, as evidence (never a verdict).
    await expect(page.getByText("23 min past sunset in civil twilight (anti-collision lighting required)").first()).toBeVisible();
    const windowTable = page.getByRole("table", { name: /^Planned window 17:30–19:30 by sun state/ });
    await expect(windowTable.getByRole("row")).toHaveCount(4); // header + day, civil twilight, night
    await expect(windowTable).toContainText("Lighting required");

    // EMI list: nearest first, the 220 kV line crosses the area, the 33 kV feeder is ~40 m away.
    const emi = page.getByRole("table", { name: "Power lines, substations and masts near the analysed geometry" });
    await expect(emi).toBeVisible();
    const line220 = emi.getByRole("row", { name: /220 kV power line · Synthetic 220 kV line/ });
    await expect(line220).toContainText("Crosses");
    const feeder = emi.getByRole("row", { name: /33 kV power line · Synthetic 33 kV feeder/ });
    await expect(feeder).toContainText("40.2");
    await feeder.click();
    // 218.6 m, shown without decimals above 100 m.
    await expect(emi.getByRole("row", { name: /220 kV substation · Synthetic 220\/33 kV substation/ })).toContainText("219");

    // Ribbon and chart, each with a table alternative.
    const ribbon = page.getByRole("figure", { name: /^Daylight on 21 Jun 2026 \(UTC\+05:30\)$/ });
    await expect(ribbon.getByRole("img")).toHaveAttribute("aria-label", /Civil twilight 18:48–19:12/);
    await ribbon.getByRole("button", { name: "Show table" }).click();
    await expect(ribbon.getByRole("table")).toContainText("Civil twilight");
    const chart = page.getByRole("figure", { name: "Sun elevation (UTC+05:30)" });
    await chart.getByRole("button", { name: "Show table" }).click();
    await expect(chart.getByRole("table")).toContainText("12:15");

    await expectNoSeriousA11yViolations(page);
  });

  test("a reloaded analysis restores its date, window and profile", async ({ page }) => {
    const form = await openEnvironment(page);
    await planWindow(form);
    await form.getByRole("button", { name: "Check environment" }).click();
    const summary = page.getByRole("group", { name: "Environment summary" });
    await expect(summary).toBeVisible({ timeout: 90_000 });
    // Daylight-only profile (the default): everything after sunset is outside it.
    await expect(page.getByText("Window extends 42 min past sunset.").first()).toBeVisible();

    await page.reload();
    await expect(summary).toBeVisible({ timeout: 30_000 });
    await expect(form.getByLabel(/^Date \(local\)/)).toHaveValue("2026-06-21");
    await expect(form.getByLabel(/^Start \(local\)/)).toHaveValue("17:30");
    await expect(form.getByLabel(/^End \(local\)/)).toHaveValue("19:30");
    await expect(form.getByRole("combobox", { name: "Daylight profile", exact: true })).toContainText("Daylight only");
  });

  test("wind at height against an illustrative rated limit", async ({ page }) => {
    const form = await openEnvironment(page);
    await planWindow(form);
    await form.getByRole("switch", { name: "Wind at height" }).click();
    const surface = form.getByLabel(/^Surface wind/);
    await surface.fill("8");
    await surface.press("Tab");
    await form.getByRole("radiogroup", { name: "Rated wind limit presets" }).getByRole("radio", { name: "12 m/s" }).click();
    await form.getByRole("button", { name: "Check environment" }).click();

    const summary = page.getByRole("group", { name: "Environment summary" });
    await expect(summary).toBeVisible({ timeout: 90_000 });
    // 8 m/s at 10 m with α = 1/7 → 11.4 m/s at 120 m, 0.6 m/s below the 12 m/s limit.
    const wind = tile(page, summary, /^Wind at 120\s?m$/);
    await expect(wind).toContainText("11.4");
    await expect(wind).toContainText(/0\.6\s?m\/s below the 12\s?m\/s rated limit/);
    await expect(page.getByRole("img", { name: /est\. 11\.4 m\/s at 120 m vs 12 m\/s rated/ })).toBeVisible();
  });

  test("the printable report summarises the environment evidence", async ({ page }) => {
    const form = await openEnvironment(page);
    await planWindow(form);
    await form.getByRole("button", { name: "Check environment" }).click();
    await expect(page.getByRole("group", { name: "Environment summary" })).toBeVisible({ timeout: 90_000 });
    await expect(page).toHaveURL(/[?&]a=/);
    const url = new URL(page.url());

    await page.goto(`/w/report?d=${url.searchParams.get("d")}&a=${url.searchParams.get("a")}`);
    await expect(page.getByRole("heading", { name: "Environment", level: 2 })).toBeVisible();
    const numbers = page.getByRole("group", { name: "Environment key numbers" });
    await expect(numbers.locator("dl").filter({ has: page.locator("dt", { hasText: /^Sunset$/ }) })).toContainText("18:48");
    await expect(numbers.locator("dl").filter({ has: page.locator("dt", { hasText: /^Nearest power line$/ }) })).toContainText("Crosses");
    await expect(page.getByText("Window extends 42 min past sunset.", { exact: false }).first()).toBeVisible();
    await expect(page.getByRole("table", { name: /^Power lines and masts within 300\s?m/ })).toContainText("Synthetic 33 kV feeder");
    // The synthetic pack feeds the power lines: the report says so.
    await expect(page.getByText("Synthetic data", { exact: true })).toBeVisible();
    const legend = page.getByRole("list", { name: "Legend" });
    await expect(legend.getByText("Power lines and masts (proximity evidence)", { exact: true })).toBeVisible();
    await expectNoSeriousA11yViolations(page);
  });

  test("the form explains a missing surface wind before running", async ({ page }) => {
    const form = await openEnvironment(page);
    await form.getByRole("switch", { name: "Wind at height" }).click();
    await form.getByRole("button", { name: "Check environment" }).click();
    const errors = page.getByRole("alert").filter({ hasText: "Please fix the following" });
    await expect(errors).toBeVisible();
    await expect(errors.getByRole("link", { name: /Surface wind/ })).toBeVisible();
    await expectNoSeriousA11yViolations(page);
  });
});
