import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import { API_URL, expectNoSeriousA11yViolations, hasHorizontalScroll, openSample } from "./helpers";

/**
 * The pages: the upload flow on /w (focus, FlightMD reports, shapefiles without a .prj, narrow
 * screens), recent datasets and samples, settings, the report (map colours and legend, table
 * captions, toolbar, narrow screens) and the 404 page.
 */

const PHONE = { width: 375, height: 812 };

async function sampleFile(request: APIRequestContext, name: string): Promise<Buffer> {
  const response = await request.get(`${API_URL}/api/samples/${name}/download/`);
  expect(response.ok(), `download of sample ${name}`).toBeTruthy();
  return response.body();
}

async function chooseFile(page: Page, name: string, buffer: Buffer, mimeType = "application/octet-stream"): Promise<void> {
  await page.getByLabel("Choose a file to upload").setInputFiles({ name, mimeType, buffer });
}

/**
 * `openSample` for a phone: below 768 px the workspace is a read-only viewer without the module
 * rail, so wait for the dataset in the URL and the viewer's bottom sheet instead.
 */
async function openSampleOnPhone(page: Page, title: string): Promise<void> {
  await page.goto("/w");
  await page.getByRole("button", { name: `Open sample ${title}`, exact: true }).click();
  await page.waitForURL(/\/w\/view\?(.*&)?d=/);
  await expect(page.getByRole("tablist", { name: "Viewer sections" })).toBeVisible();
}

function workspaceIds(page: Page): { d: string | null; a: string | null } {
  const url = new URL(page.url());
  return { d: url.searchParams.get("d"), a: url.searchParams.get("a") };
}

test.describe("upload on /w", () => {
  test("a FlightMD report is pointed to the flight audit and is not uploaded", async ({ page, request }) => {
    const report = await sampleFile(request, "flightmd_report_v15");
    let uploads = 0;
    page.on("request", (r) => {
      if (r.method() === "POST" && new URL(r.url()).pathname === "/api/files/") uploads += 1;
    });
    await page.goto("/w");
    await chooseFile(page, "flightmd_report_v15.json", report, "application/json");

    await expect(page.getByText("This is a FlightMD report", { exact: true })).toBeVisible();
    const open = page.getByRole("link", { name: "Open Flight audit" });
    await expect(open).toBeFocused();
    await expect(open).toHaveAttribute("href", /m=flight-audit/);
    await expect(page.getByText(/type None|Could not process/)).toHaveCount(0);
    expect(uploads).toBe(0);
  });

  test("a failed upload moves focus to its actions, and Choose another file back to Browse", async ({ page, request }) => {
    const corrupt = await sampleFile(request, "corrupt");
    await page.goto("/w");
    await chooseFile(page, "corrupt.kml", corrupt);

    const chooseAnother = page.getByRole("button", { name: "Choose another file" });
    await expect(chooseAnother).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(page.getByRole("button", { name: "Browse…" })).toBeFocused();
  });

  test.describe("on a phone", () => {
    test.use({ viewport: PHONE });

    test("a shapefile without .prj stays on /w as Needs CRS and can be processed again", async ({ page, request }) => {
      // Two uploads processed by the server plus an accessibility scan: more than the default budget.
      test.slow();
      const zip = await sampleFile(request, "no_prj");
      // Hold the upload so the progress panel can be checked.
      let release!: () => void;
      const gate = new Promise<void>((resolve) => {
        release = resolve;
      });
      await page.route("**/api/files/", async (route) => {
        if (route.request().method() !== "POST") return route.fallback();
        await gate;
        await route.continue().catch(() => undefined);
      });

      await page.goto("/w");
      await chooseFile(page, "no_prj.zip", zip, "application/zip");
      const progress = page.getByRole("group", { name: "Upload of no_prj.zip" });
      await expect(progress).toBeVisible();
      await expect(progress).toBeFocused();
      await expect(progress.getByText(/^Step \d of 5:$/)).toBeVisible();
      expect(await hasHorizontalScroll(page)).toBe(false);
      // Nothing above the progress panel (Cancel upload, Open now) is marked as disabled.
      expect(await progress.evaluate((el) => el.closest("[aria-disabled='true']") === null)).toBe(true);
      release();
      await page.unrouteAll({ behavior: "ignoreErrors" });

      await expect(page.getByText("No coordinate system in no_prj.zip")).toBeVisible();
      await expect(page).toHaveURL(/\/w(#.*)?$/);
      await expect(page.getByLabel("CRS override")).toBeFocused();
      await expect(page.getByRole("list", { name: "Recent datasets" }).getByText("Needs CRS").first()).toBeVisible();
      await expectNoSeriousA11yViolations(page);

      await page.getByLabel("CRS override").fill("EPSG:32643");
      await page.getByRole("button", { name: "Process no_prj.zip again with this CRS" }).click();
      await page.waitForURL(/\/w\/view/);
      // A phone has the compact header (no CRS line): the coordinate system is in the Details sheet.
      await page.getByRole("tab", { name: "Details" }).click();
      await expect(page.getByRole("tabpanel", { name: "Details" }).getByText("EPSG:32643", { exact: true })).toBeVisible();
      await expect(page.getByText("Needs CRS")).toHaveCount(0);
    });
  });
});

test.describe("recent datasets and samples", () => {
  test("a sample reopens this browser's copy, and file names are not extra Tab stops", async ({ page }) => {
    await openSample(page, "Farm plots (Shapefile, UTM 43N)");
    const { d } = workspaceIds(page);
    expect(d).toBeTruthy();

    await page.goto("/w");
    const recent = page.getByRole("list", { name: "Recent datasets" });
    const firstRow = recent.getByRole("listitem").first();
    await expect(firstRow.getByRole("link").first()).toHaveAttribute("tabindex", "-1");
    await expect(firstRow.getByRole("link", { name: /^Open / })).not.toHaveAttribute("tabindex", "-1");
    await expect(page.getByRole("link", { name: "Skip to samples" })).toHaveAttribute("href", "#samples");

    const open = page.getByRole("button", { name: "Open sample Farm plots (Shapefile, UTM 43N)", exact: true });
    await expect(page.getByRole("button", { name: "New copy of sample Farm plots (Shapefile, UTM 43N)" })).toBeVisible();
    await open.click();
    await page.waitForURL(/\/w\/view/);
    expect(workspaceIds(page).d).toBe(d);
  });

  test.describe("on a phone", () => {
    test.use({ viewport: PHONE });

    test("Try a sample lands on the samples once the lists have loaded", async ({ page }) => {
      await page.goto("/w#samples");
      await expect(page.getByRole("button", { name: "Open sample Bengaluru survey", exact: true })).toBeVisible();
      await expect(page.getByRole("heading", { name: "Start from a sample" })).toBeInViewport();
    });
  });
});

test.describe("settings", () => {
  test.describe("on a phone", () => {
    test.use({ viewport: PHONE });

    test("data packs are cards with written labels; server limits have words, not keys", async ({ page }) => {
      await page.goto("/settings");
      const packs = page.getByRole("list", { name: "Installed data packs" });
      await expect(packs).toBeVisible();
      await expect(packs.getByText("Coverage (south-west → north-east)").first()).toBeVisible();
      // formatCoord keeps "° N" together with a narrow no-break space (U+202F), hence \s.
      await expect(packs.getByText(/^\d+\.\d{3}°\sN, \d+\.\d{3}°\sE → \d+\.\d{3}°\sN, \d+\.\d{3}°\sE$/).first()).toBeVisible();
      await expect(page.getByText("Largest upload")).toBeVisible();
      await expect(page.getByText(/Max upload mb|Upload ttl hours|Rate limit per hour/)).toHaveCount(0);
      expect(await hasHorizontalScroll(page)).toBe(false);
      await expectNoSeriousA11yViolations(page);
    });
  });

  test("Reset settings resets the theme too and can be undone", async ({ page }) => {
    await page.goto("/settings");
    const html = page.locator("html");
    await page.getByRole("radio", { name: "Light", exact: true }).click();
    await page.getByRole("radio", { name: "Imperial (ft, mi²)" }).click();
    await expect(html).toHaveClass(/\blight\b/);

    const reset = page.getByRole("button", { name: "Reset settings" });
    await reset.click();
    await expect(html).toHaveClass(/\bdark\b/);
    await expect(page.getByRole("radio", { name: "Metric (m, km²)" })).toHaveAttribute("aria-checked", "true");

    await page.getByRole("button", { name: "Undo" }).click();
    await expect(html).toHaveClass(/\blight\b/);
    await expect(page.getByRole("radio", { name: "Imperial (ft, mi²)" })).toHaveAttribute("aria-checked", "true");

    // Back to the defaults for the other tests (by keyboard: a toast may cover the button).
    await reset.focus();
    await page.keyboard.press("Enter");
    await expect(html).toHaveClass(/\bdark\b/);
  });
});

test.describe("report", () => {
  test("flight audit: the map uses the workspace colours, captions sit above tables, Print comes first", async ({ page }) => {
    await page.goto("/w/view?m=flight-audit&sample=flightmd_report_v15");
    await page.getByRole("button", { name: "Audit flight" }).click();
    await expect(page.getByText("Exceedances (1)")).toBeVisible({ timeout: 90_000 });
    await expect(page).toHaveURL(/[?&]a=/);
    const { a } = workspaceIds(page);

    await page.goto(`/w/report?a=${a}`);
    const legend = page.getByRole("list", { name: "Legend" });
    await expect(legend.getByText("Above the limit", { exact: true })).toBeVisible();
    await expect(legend.getByText("Below the limit", { exact: true })).toBeVisible();
    await expect(legend.getByText("Exceedances", { exact: true })).toBeVisible();
    await expect(legend.getByText("Take-off point", { exact: true })).toBeVisible();

    const caption = page.locator("caption", { hasText: "Ceiling exceedances" });
    const table = page.getByRole("table", { name: "Ceiling exceedances" });
    const captionBox = await caption.boundingBox();
    const headerBox = await table.getByRole("row").first().boundingBox();
    expect(captionBox && headerBox && captionBox.y < headerBox.y).toBe(true);

    const print = page.getByRole("button", { name: "Print" });
    const exportButton = page.getByRole("button", { name: "Export" });
    const printBox = await print.boundingBox();
    const exportBox = await exportButton.boundingBox();
    expect(printBox && exportBox && printBox.x < exportBox.x).toBe(true);
    await exportButton.click();
    await expect(page.getByRole("link", { name: /Analysis \(GeoJSON\)/ })).toBeVisible();
    // Case-sensitive: "GeoJSON", never the upper-cased extension.
    await expect(page.getByText(/GEOJSON/)).toHaveCount(0);
    await page.keyboard.press("Escape");
    await expectNoSeriousA11yViolations(page);
  });

  test("survey: battery blocks are coloured and labelled, and the synthetic terrain is flagged", async ({ page }) => {
    await openSample(page, "Bengaluru survey");
    await page.getByRole("button", { name: "Estimate survey" }).click();
    await expect(page.getByText("Ground sample distance")).toBeVisible({ timeout: 90_000 });
    await expect(page).toHaveURL(/[?&]a=/);
    const { d, a } = workspaceIds(page);

    await page.goto(`/w/report?d=${d}&a=${a}`);
    await expect(page.getByRole("heading", { name: "Survey estimate", level: 2 })).toBeVisible();
    const legend = page.getByRole("list", { name: "Legend" });
    await expect(legend.getByText("Survey area", { exact: true })).toBeVisible();
    await expect(legend.getByText("Battery 1", { exact: true })).toBeVisible();
    // The survey reads the pack's terrain (GSD over relief), so a synthetic pack is flagged once.
    await expect(page.getByText("Synthetic data", { exact: true })).toHaveCount(1);
    await expect(page.getByText("Flight blocks")).toHaveCount(0);
  });

  test.describe("on a phone", () => {
    test.use({ viewport: PHONE });

    test("measurements are cards with the area first, and nothing scrolls sideways", async ({ page }) => {
      await openSampleOnPhone(page, "Farm plots (Shapefile, UTM 43N)");
      const { d } = workspaceIds(page);
      await page.goto(`/w/report?d=${d}`);
      const cards = page.getByRole("list", { name: "Measurements per feature" });
      await expect(cards).toBeVisible();
      await expect(cards.getByText("Area", { exact: true }).first()).toBeVisible();
      // The first value of a polygon's card is its area.
      await expect(cards.getByRole("listitem").first().getByRole("term").first()).toHaveText("Area");
      await expect(page.getByText("Shapefile", { exact: true }).first()).toBeVisible();
      await expect(page.getByText(/From (prj|kml)\b/)).toHaveCount(0);
      expect(await hasHorizontalScroll(page)).toBe(false);
      await expectNoSeriousA11yViolations(page);
    });
  });
});

test.describe("landing and 404", () => {
  test("copying the quick start always gives feedback", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("button", { name: "Copy quick-start commands" }).click();
    await expect(page.getByRole("button", { name: "Copied" }).or(page.getByText("Could not copy"))).toBeVisible();
  });

  test("the 404 page has its own title", async ({ page }) => {
    await page.goto("/no-such-page");
    await expect(page).toHaveTitle("Page not found · AirBrief");
  });
});
