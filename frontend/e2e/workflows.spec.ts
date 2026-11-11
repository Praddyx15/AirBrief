import { expect, test } from "@playwright/test";
import { API_URL, expectNoSeriousA11yViolations, openSample } from "./helpers";

/**
 * Main user journeys against the synthetic data pack (known answers from RUNBOOK.md §6).
 */

test.describe("original brief through the API", () => {
  test("upload a KML and read info and measurements", async ({ request }) => {
    const sample = await request.get(`${API_URL}/api/samples/square_1km_equator/download/`);
    expect(sample.ok()).toBeTruthy();
    const upload = await request.post(`${API_URL}/api/files/`, {
      multipart: { file: { name: "square_1km_equator.kml", mimeType: "application/vnd.google-earth.kml+xml", buffer: await sample.body() } },
    });
    expect(upload.status()).toBe(201);
    const info = (await upload.json()) as { id: string; status: string; feature_count: number; crs: string };
    expect(info.status).toBe("COMPLETED");
    expect(info.feature_count).toBe(1);
    expect(info.crs).toBe("EPSG:4326");

    const measurements = await request.get(`${API_URL}/api/files/${info.id}/measurements/`);
    const page = (await measurements.json()) as { items: { measurement: { area_m2: number; perimeter_m: number } }[] };
    expect(page.items[0]?.measurement.area_m2).toBeGreaterThan(999_900);
    expect(page.items[0]?.measurement.area_m2).toBeLessThan(1_000_100);
  });

  test("unsupported file type is rejected with 415", async ({ request }) => {
    const response = await request.post(`${API_URL}/api/files/`, {
      multipart: { file: { name: "notes.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF-1.4") } },
    });
    expect(response.status()).toBe(415);
    const body = (await response.json()) as { error: { code: string } };
    expect(body.error.code).toBe("unsupported_format");
  });
});

test.describe("workspace", () => {
  test("measure: sample opens with features and measurements", async ({ page }) => {
    await openSample(page, "Farm plots (Shapefile, UTM 43N)");
    await expect(page.getByText("EPSG:32643").first()).toBeVisible();
    await expectNoSeriousA11yViolations(page);
  });

  test("survey: P4 RTK at 100 m gives 2.74 cm/px", async ({ page }) => {
    await openSample(page, "Bengaluru survey");
    await page.getByRole("button", { name: "Estimate survey" }).click();
    await expect(page.getByText("Ground sample distance")).toBeVisible({ timeout: 90_000 });
    await expect(page.getByText("2.74", { exact: true }).first()).toBeVisible();
    await expectNoSeriousA11yViolations(page);
  });

  test("airspace: check runs and shows a verdict", async ({ page }) => {
    await openSample(page, "Bengaluru survey");
    await page.getByRole("navigation", { name: "Workspace modules" }).getByRole("link", { name: /Airspace/ }).click();
    await page.getByRole("button", { name: "Check airspace" }).click();
    await expect(page.getByText(/Airspace: (Pass|Caution|Fail)/)).toBeVisible({ timeout: 90_000 });
    await expectNoSeriousA11yViolations(page);
  });

  test("ground risk: assessment gives the intrinsic ground risk class", async ({ page }) => {
    await openSample(page, "Bengaluru survey");
    await page.getByRole("navigation", { name: "Workspace modules" }).getByRole("link", { name: /Ground risk/ }).click();
    await page.getByRole("button", { name: "Assess ground risk" }).click();
    const summary = page.getByRole("group", { name: "Ground risk summary" });
    await expect(summary).toBeVisible({ timeout: 90_000 });
    await expect(summary.getByText("Intrinsic ground risk class", { exact: true })).toBeVisible();
    await expectNoSeriousA11yViolations(page);
  });

  test("eVTOL: siting ranks sites, explains one, and routes a corridor", async ({ page }) => {
    await openSample(page, "Bengaluru eVTOL");
    await page.getByRole("navigation", { name: "Workspace modules" }).getByRole("link", { name: /eVTOL/ }).click();
    await page.getByRole("button", { name: "Score sites" }).click();
    await expect(page.getByRole("region", { name: "Ranking", exact: true })).toBeVisible({ timeout: 120_000 });
    await page.getByRole("row", { name: "Show site 1 on the map", exact: true }).click();
    await expect(page.getByRole("region", { name: "Site #1 breakdown", exact: true })).toBeVisible();
    await expectNoSeriousA11yViolations(page);

    await page.getByRole("tab", { name: "Corridor" }).click();
    await page.getByRole("button", { name: "Route corridor" }).click();
    await expect(page.getByRole("group", { name: "Corridor summary" })).toBeVisible({ timeout: 120_000 });
    await expectNoSeriousA11yViolations(page);
  });

  test("shows: the injected conflict D07 – D32 is found", async ({ page }) => {
    await openSample(page, "50-drone show");
    await page.getByRole("button", { name: "Validate show" }).click();
    await expect(page.getByText("D07 – D32").first()).toBeVisible({ timeout: 120_000 });
    // Playback never starts by itself.
    await expect(page.getByRole("button", { name: "Play" })).toBeVisible();
    await expectNoSeriousA11yViolations(page);
  });

  test("flight audit: FlightMD sample exceeds 120 m once", async ({ page }) => {
    await page.goto("/w/view?m=flight-audit&sample=flightmd_report_v15");
    await expect(page.getByText("GPS points")).toBeVisible();
    await page.getByRole("button", { name: "Audit flight" }).click();
    await expect(page.getByText("Exceedances (1)")).toBeVisible({ timeout: 90_000 });
    await expectNoSeriousA11yViolations(page);
  });
});
