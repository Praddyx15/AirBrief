import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import { API_URL, expectNoSeriousA11yViolations } from "./helpers";

/**
 * The operation evidence brief (/w/report?mode=brief), its bundle and one-page print, and the
 * complete analysis exports (results drawer from tablet width, report page).
 *
 * The analyses are created through the API (airspace and ground risk on the Bengaluru survey
 * sample), so the test checks the brief rather than the module forms. The brief's hash is then
 * recomputed by the API for the same request: the page must show the same SHA-256.
 */

interface Info {
  id: string;
  status: string;
  owner_token?: string | null;
}

/** Owner token of each dataset loaded here: the analyses are run as the dataset owner's. */
const ownerTokens = new Map<string, string>();

async function loadSurvey(request: APIRequestContext): Promise<string> {
  const response = await request.post(`${API_URL}/api/samples/bengaluru_survey/`);
  expect(response.ok()).toBe(true);
  const file = (await response.json()) as Info;
  if (file.owner_token) ownerTokens.set(file.id, file.owner_token);
  await expect
    .poll(async () => ((await (await request.get(`${API_URL}/api/files/${file.id}/`)).json()) as Info).status, { timeout: 60_000 })
    .toBe("COMPLETED");
  return file.id;
}

async function runAnalysis(request: APIRequestContext, kind: string, datasetId: string, params: Record<string, unknown> = {}): Promise<string> {
  const token = ownerTokens.get(datasetId);
  const response = await request.post(`${API_URL}/api/analyses/`, {
    data: { kind, dataset_id: datasetId, params },
    headers: token ? { "X-Owner-Token": token } : undefined,
  });
  expect(response.ok(), await response.text()).toBe(true);
  const info = (await response.json()) as Info;
  await expect
    .poll(async () => ((await (await request.get(`${API_URL}/api/analyses/${info.id}/`)).json()) as Info).status, { timeout: 90_000 })
    .toBe("COMPLETED");
  return info.id;
}

/** Pages of a PDF made by Chromium (page objects, not the page tree). */
function pdfPages(pdf: Buffer): number {
  return (pdf.toString("latin1").match(/\/Type\s*\/Page(?!s)/g) ?? []).length;
}

/** [width, height] of the first page in PDF points. */
function pdfPageSize(pdf: Buffer): [number, number] {
  const box = /\/MediaBox\s*\[\s*([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s*\]/.exec(pdf.toString("latin1"));
  expect(box).not.toBeNull();
  return [Number(box?.[3]) - Number(box?.[1]), Number(box?.[4]) - Number(box?.[2])];
}

const preview = (page: Page) => page.getByRole("region", { name: "Brief preview" });

test.describe("operation evidence brief", () => {
  test("airspace + ground risk: sections, provenance, bundle and one A4 page", async ({ page, request }) => {
    const datasetId = await loadSurvey(request);
    const airspace = await runAnalysis(request, "airspace", datasetId, { altitude_agl_m: 60 });
    const groundRisk = await runAnalysis(request, "ground_risk", datasetId);

    // Entry point: the dataset header's Export menu (the header comes before the results drawer,
    // whose own "Export" menu lists "Analysis export formats").
    await page.goto(`/w/view?d=${datasetId}`);
    await page.getByRole("button", { name: "Export", exact: true }).first().click();
    await page.getByRole("list", { name: "Export formats", exact: true }).getByRole("link", { name: /^Create brief/ }).click();
    await page.waitForURL(/\/w\/report\?.*mode=brief/);
    await expect(page.getByRole("tab", { name: "Brief", selected: true })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Operation evidence brief", level: 1 })).toBeVisible();

    // Both analyses of the dataset are ticked by default.
    const contents = page.getByRole("form", { name: "Brief contents" });
    await expect(contents.getByRole("checkbox", { name: "Airspace check" })).toBeChecked();
    await expect(contents.getByRole("checkbox", { name: "Ground risk" })).toBeChecked();

    await contents.getByRole("textbox", { name: "Operator" }).fill("Example Aerial Surveys");
    await contents.getByRole("textbox", { name: "Reference" }).fill("OPS-E2E-1");
    await contents.getByRole("combobox", { name: "Aircraft" }).click();
    await page.getByRole("option", { name: /^Mapping quadcopter/ }).click();
    await contents.getByRole("spinbutton", { name: /^Pilot longitude/ }).fill("77.491");
    await contents.getByRole("spinbutton", { name: /^Pilot latitude/ }).fill("12.905");
    await contents.getByRole("spinbutton", { name: /^Pilot latitude/ }).press("Tab");
    await contents.getByRole("button", { name: "Create brief" }).click();

    const sheet = preview(page).getByRole("article", { name: "Operation evidence brief — OPS-E2E-1" });
    await expect(sheet).toBeVisible({ timeout: 60_000 });
    await expect(contents.getByRole("status")).toContainText("Brief created");

    // Evidence, never an authorisation; the demo pack is synthetic.
    await expect(sheet.getByRole("note").filter({ hasText: /^Evidence, not authorisation\./ })).toBeVisible();
    await expect(sheet.getByRole("note").filter({ hasText: "SYNTHETIC DEMO DATA — not evidence" })).toBeVisible();

    // Every section: assessed ones as cards, the rest listed as not assessed (never blank).
    for (const name of ["Operation", "Airspace", "Heights and terrain", "Obstacles", "Ground risk"]) {
      await expect(sheet.getByRole("heading", { name, exact: true, level: 3 })).toBeVisible();
    }
    const missing = sheet.getByRole("region", { name: "Not assessed in this brief" });
    for (const name of ["Environment", "Line of sight", "Survey and spray effort", "Flight audit", "Drone show", "eVTOL siting and corridor"]) {
      await expect(missing.getByText(name, { exact: true })).toBeVisible();
    }
    await expect(sheet.getByRole("img", { name: /^Map of the operation evidence/ })).toBeVisible();
    await expect(sheet.getByText("Example Aerial Surveys", { exact: true })).toBeVisible();

    // Provenance: the same request gives the same brief SHA-256 from the API.
    const api = await request.post(`${API_URL}/api/briefs/`, {
      data: {
        analysis_ids: [airspace, groundRisk],
        operation: {
          operator: "Example Aerial Surveys",
          reference: "OPS-E2E-1",
          aircraft: { preset: "mapping_quad", name: null, takeoff_mass_kg: null, max_dimension_m: null, max_speed_ms: null },
          pilot_position: [77.491, 12.905],
          planned_start_utc: null,
          planned_end_utc: null,
          utc_offset_min: 0,
          notes: null,
        },
      },
    });
    expect(api.ok()).toBe(true);
    const brief = (await api.json()) as { provenance: { brief_sha256: string; analyses: { params_sha256: string; input_sha256: string | null }[] } };
    await expect(sheet.getByRole("heading", { name: "Provenance", level: 3 })).toBeVisible();
    await expect(sheet.getByText(brief.provenance.brief_sha256)).toBeVisible();
    for (const a of brief.provenance.analyses) {
      await expect(sheet.getByText(a.params_sha256)).toBeVisible();
      if (a.input_sha256) await expect(sheet.getByText(a.input_sha256).first()).toBeVisible();
    }

    // The bundle (zip) with a fixed, safe name.
    const [download] = await Promise.all([
      page.waitForEvent("download"),
      preview(page).getByRole("button", { name: "Download bundle (.zip)" }).click(),
    ]);
    expect(download.suggestedFilename()).toMatch(/^airbrief_brief_[0-9a-f]{12}\.zip$/);

    await expectNoSeriousA11yViolations(page);

    // Print: one A4 page, nothing wider than the sheet. The PDF is made with the print stylesheet,
    // as the browser prints: emulating "screen" before page.pdf() would print the whole workspace
    // page (header, form, toolbar) instead of the brief. The text scale measured on screen must
    // hold while print media is active (the hidden measuring copy is not laid out on paper).
    const fitNote = preview(page).getByText(/Prints on one A4 page/);
    await expect(fitNote).toBeVisible();
    const fitClaim = (await fitNote.textContent()) ?? "";
    await page.emulateMedia({ media: "print" });
    const overflow = await sheet.evaluate((el) => el.scrollWidth - el.clientWidth);
    expect(overflow).toBeLessThanOrEqual(1);
    // Two frames: resize observers have run and React has rendered what they set.
    await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
    await expect(fitNote).toHaveText(fitClaim);
    const pdf = await page.pdf({ preferCSSPageSize: true, printBackground: true });
    expect(pdfPages(pdf)).toBe(1);
    const [width, height] = pdfPageSize(pdf);
    expect(Math.abs(width - 595.28)).toBeLessThan(2); // A4 portrait, 210 × 297 mm in points
    expect(Math.abs(height - 841.89)).toBeLessThan(2);
    await page.emulateMedia({ media: null });
  });

  test("the command palette opens the brief, and the report has a Brief tab", async ({ page, request }) => {
    const datasetId = await loadSurvey(request);
    const airspace = await runAnalysis(request, "airspace", datasetId);

    await page.goto(`/w/report?d=${datasetId}&a=${airspace}`);
    await expect(page.getByRole("tab", { name: "Report", selected: true })).toBeVisible();
    await page.keyboard.press("Control+k");
    const palette = page.getByRole("dialog", { name: "Command palette" });
    await palette.getByRole("combobox", { name: "Search commands" }).fill("brief");
    await palette.getByRole("option", { name: "Create an operation evidence brief" }).click();
    await page.waitForURL(/mode=brief/);
    await expect(page.getByRole("tab", { name: "Brief", selected: true })).toBeVisible();
    await expect(page.getByRole("form", { name: "Brief contents" }).getByRole("checkbox", { name: "Airspace check" })).toBeChecked();

    // Back to the report tab: the analysis exports include KML and every CSV table.
    await page.getByRole("tab", { name: "Report" }).click();
    await page.getByRole("button", { name: "Export", exact: true }).click();
    const formats = page.getByRole("list", { name: "Export formats" });
    await expect(formats.getByRole("link", { name: /Analysis \(KML\)/ })).toHaveAttribute("href", /format=kml/);
    await expect(formats.getByRole("link", { name: /^CSV: All tables \(one file\)/ })).toBeVisible();
    await expect(formats.getByRole("link", { name: /^CSV: Zones/ })).toHaveAttribute("href", /format=csv&table=zones|table=zones/);
    await expect(formats.getByRole("link", { name: "Create brief" })).toBeVisible();

    // fetched by the menu's saver and saved from a blob, not by following the API link
    const saved = page.waitForEvent("download");
    await formats.getByRole("link", { name: /^Analysis \(JSON\)/ }).click();
    const download = await saved;
    expect(download.url()).toMatch(/^blob:/);
    expect(download.suggestedFilename()).toMatch(/\.json$/);
  });
});

test.describe("analysis exports in the results drawer", () => {
  test.use({ viewport: { width: 900, height: 900 } });

  test("every format is offered from tablet width", async ({ page, request }) => {
    const datasetId = await loadSurvey(request);
    const airspace = await runAnalysis(request, "airspace", datasetId);
    await page.goto(`/w/view?d=${datasetId}&m=airspace&a=${airspace}`);
    const results = page.getByRole("region", { name: "Results" });
    const exportButton = results.getByRole("button", { name: "Export" });
    await expect(exportButton).toBeVisible({ timeout: 30_000 });
    await exportButton.click();
    const formats = page.getByRole("list", { name: "Analysis export formats" });
    for (const name of [/Analysis \(JSON\)/, /Analysis \(GeoJSON\)/, /Analysis \(KML\)/]) {
      await expect(formats.getByRole("link", { name })).toBeVisible();
    }
    await expect(formats.getByRole("link", { name: /^CSV: All tables \(one file\)/ })).toBeVisible();
    await expect(formats.getByRole("link", { name: /^CSV: Flyability grid cells/ })).toBeVisible();
    await expect(formats.getByRole("link", { name: /Analysis \(KML\)/ })).toHaveAttribute("href", /format=kml/);
    await page.keyboard.press("Escape");
  });

  test("one export at a time: repeated clicks save one file, and the menu says an export is running", async ({ page, request }) => {
    const datasetId = await loadSurvey(request);
    const airspace = await runAnalysis(request, "airspace", datasetId);
    await page.goto(`/w/view?d=${datasetId}&m=airspace&a=${airspace}`);
    const results = page.getByRole("region", { name: "Results" });
    const exportButton = results.getByRole("button", { name: "Export" });
    await expect(exportButton).toBeVisible({ timeout: 30_000 });
    await exportButton.click();
    const formats = page.getByRole("list", { name: "Analysis export formats" });
    const json = formats.getByRole("link", { name: /^Analysis \(JSON\)/ });
    const csv = formats.getByRole("link", { name: /^CSV: All tables \(one file\)/ });
    await expect(csv).toBeVisible();

    let release!: () => void;
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    let requests = 0;
    // the file downloads, not the CSV table list (…/export/tables/)
    await page.route(
      (url) => /\/api\/analyses\/[^/]+\/export\/$/.test(url.pathname),
      async (route) => {
        requests += 1;
        await held;
        await route.continue();
      },
    );
    const downloads: string[] = [];
    page.on("download", (d) => downloads.push(d.suggestedFilename()));

    await json.click();
    await expect(json).toHaveAttribute("aria-busy", "true");
    await expect(json).toContainText("Exporting…");
    await expect(csv).toHaveAttribute("aria-disabled", "true");
    await expect(csv).toContainText("Another export is still running: wait for it");
    await json.click();
    await csv.click({ force: true });
    // a waiting entry takes no ctrl-click or middle click either (no new tab with a second export)
    const prevented = await csv.evaluate((a) => {
      const seen: boolean[] = [];
      for (const [type, init] of [
        ["click", { ctrlKey: true }],
        ["auxclick", { button: 1 }],
      ] as const) {
        window.addEventListener(
          type,
          (e) => {
            seen.push(e.defaultPrevented);
            e.preventDefault();
          },
          { once: true },
        );
        a.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, ...init }));
      }
      return seen;
    });
    expect(prevented).toEqual([true, true]);
    release();
    await expect(json).not.toHaveAttribute("aria-busy", "true");
    await expect.poll(() => downloads.length).toBe(1);
    expect(downloads[0]).toMatch(/\.json$/);
    expect(requests).toBe(1);
  });
});
