import { expect, test, type APIRequestContext, type Page, type Request } from "@playwright/test";
import { API_URL, expectNoSeriousA11yViolations, openSample } from "./helpers";

/**
 * Assessment choices (docs/features/choices.md): three radio groups in Settings, the "Help me
 * choose" questionnaire, the flight audit and eVTOL forms starting from the setting (a run may
 * differ and a reopened run keeps its own), the first-time note, and the ED-318 export layout
 * named in the export menus. Each test starts with fresh browser storage, so the defaults.
 */

const choicesRegion = (page: Page) => page.getByRole("region", { name: "Assessment choices" });
const auditForm = (page: Page) => page.getByRole("form", { name: "Flight audit" });
const auditChoice = (page: Page) => auditForm(page).getByRole("group", { name: "Above a band's free-flight ceiling" });

async function setChoice(page: Page, group: string | RegExp, option: string): Promise<void> {
  await page.goto("/settings");
  const radio = choicesRegion(page).getByRole("group", { name: group }).getByRole("radio", { name: option, exact: true });
  await radio.check();
  await expect(radio).toBeChecked();
}

function isCreateAnalysis(request: Request): boolean {
  return request.method() === "POST" && /^\/api\/analyses\/?$/.test(new URL(request.url()).pathname);
}

async function completedAirspace(request: APIRequestContext): Promise<{ datasetId: string; analysisId: string }> {
  const sample = await request.post(`${API_URL}/api/samples/bengaluru_survey/`);
  expect(sample.ok()).toBe(true);
  const datasetId = ((await sample.json()) as { id: string }).id;
  const created = await request.post(`${API_URL}/api/analyses/`, {
    data: { kind: "airspace", dataset_id: datasetId, params: { grid: false } },
  });
  expect(created.ok(), await created.text()).toBe(true);
  const analysisId = ((await created.json()) as { id: string }).id;
  await expect
    .poll(async () => ((await (await request.get(`${API_URL}/api/analyses/${analysisId}/`)).json()) as { status: string }).status, {
      timeout: 90_000,
    })
    .toBe("COMPLETED");
  return { datasetId, analysisId };
}

test.describe("assessment choices", () => {
  test("Settings: three choices, kept after a reload, reset with the other settings", async ({ page }) => {
    await page.goto("/settings");
    const region = choicesRegion(page);
    const audit = region.getByRole("group", { name: "Flight audit: flying above a band's free-flight ceiling" });
    const evtol = region.getByRole("group", { name: "eVTOL siting and corridors: within 5 km of an airport" });
    const ed318 = region.getByRole("group", { name: "ED-318 zone exports: where the vertical limits go" });
    await expect(audit.getByRole("radio", { name: "An exceedance (fail)" })).toBeChecked();
    await expect(evtol.getByRole("radio", { name: "Permission required", exact: true })).toBeChecked();
    await expect(ed318.getByRole("radio", { name: "In the geometry" })).toBeChecked();
    // one plain sentence per option, read out with it
    await expect(audit.getByRole("radio", { name: "Permission required (caution)" })).toHaveAccessibleDescription(
      /reported as flight in yellow airspace, permission required/,
    );

    await audit.getByRole("radio", { name: "Permission required (caution)" }).check();
    await evtol.getByRole("radio", { name: "Keep out, like a red zone" }).check();
    await ed318.getByRole("radio", { name: "In the properties" }).check();
    await page.reload();
    await expect(audit.getByRole("radio", { name: "Permission required (caution)" })).toBeChecked();
    await expect(evtol.getByRole("radio", { name: "Keep out, like a red zone" })).toBeChecked();
    await expect(ed318.getByRole("radio", { name: "In the properties" })).toBeChecked();
    await expectNoSeriousA11yViolations(page);

    await page.getByRole("button", { name: "Reset settings" }).click();
    await expect(audit.getByRole("radio", { name: "An exceedance (fail)" })).toBeChecked();
    await expect(evtol.getByRole("radio", { name: "Permission required", exact: true })).toBeChecked();
    await expect(ed318.getByRole("radio", { name: "In the geometry" })).toBeChecked();
  });

  test("Help me choose: the answers pick the choices, each with a reason, and Apply sets them", async ({ page }) => {
    await page.goto("/settings");
    const help = page.getByRole("button", { name: "Help me choose" });
    await help.click();
    const dialog = page.getByRole("dialog", { name: "Help me choose" });
    const recommended = dialog.getByRole("region", { name: "Recommended" });

    await dialog.getByRole("radio", { name: "Reviewing flights after the fact" }).check();
    // flight work: the eVTOL question is not asked and its choice is left as it is
    await expect(dialog.getByRole("group", { name: /^Near airports/ })).toHaveCount(0);
    await expect(recommended).toContainText("Permission required, unchanged (not asked).");
    await dialog.getByRole("radio", { name: "Sometimes" }).check();
    await expect(recommended).toContainText("An exceedance (fail). Only sometimes: the audit stays strict");
    await dialog.getByRole("radio", { name: "No, green zones only" }).check();
    await expect(recommended).toContainText("An exceedance (fail). You stay in green zones");
    await dialog.getByRole("radio", { name: "Routinely" }).check();
    await expect(recommended).toContainText("Permission required (caution). You fly in yellow zones with permission");
    await dialog.getByRole("radio", { name: "Not sure" }).check();
    await expect(recommended).toContainText("In the geometry. Not sure: the ED-318 layout is the standard one.");
    await dialog.getByRole("radio", { name: "A national feed with the limits in the properties" }).check();
    await expect(recommended).toContainText("In the properties.");

    // eVTOL work: the permission question goes, the airport one comes
    await dialog.getByRole("radio", { name: "eVTOL or vertiport studies" }).check();
    await expect(dialog.getByRole("group", { name: /^Do you get Digital Sky permission/ })).toHaveCount(0);
    await dialog.getByRole("radio", { name: "Keep out completely" }).check();
    await expect(recommended).toContainText("Keep out, like a red zone. Sites and corridors stay out");
    await expect(recommended).toContainText("An exceedance (fail), unchanged (not asked).");
    await expectNoSeriousA11yViolations(page);

    await dialog.getByRole("button", { name: "Apply" }).click();
    await expect(dialog).toBeHidden();
    await expect(help).toBeFocused();
    const region = choicesRegion(page);
    await expect(region.getByRole("radio", { name: "An exceedance (fail)" })).toBeChecked();
    await expect(region.getByRole("radio", { name: "Keep out, like a red zone" })).toBeChecked();
    await expect(region.getByRole("radio", { name: "In the properties" })).toBeChecked();
  });

  test("the first-time note: dismissible, remembered, and its link opens the questionnaire", async ({ page }) => {
    await page.goto("/w/view?m=flight-audit");
    const note = auditForm(page).getByRole("note").filter({ hasText: "Using the default assessment choices." });
    await expect(note).toBeVisible();
    await note.getByRole("link", { name: "Answer 3 questions to set yours" }).click();
    await page.waitForURL(/\/settings#help-me-choose$/);
    const dialog = page.getByRole("dialog", { name: "Help me choose" });
    await expect(dialog).toBeVisible();
    // nothing picks a choice yet, so nothing to apply (Apply would mark the choices as set)
    const apply = dialog.getByRole("button", { name: "Apply" });
    await expect(apply).toBeDisabled();
    await dialog.getByRole("radio", { name: "eVTOL or vertiport studies" }).check();
    await expect(apply).toBeDisabled();
    await expect(dialog.getByText("Nothing to apply yet: answer a question above.")).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.getByRole("button", { name: "Help me choose" })).toBeFocused();

    // still the defaults: the note is still there
    await page.goto("/w/view?m=flight-audit");
    await expect(note).toBeVisible();
    await note.getByRole("button", { name: "Dismiss the note on assessment choices" }).click();
    await expect(note).toHaveCount(0);
    await expect(page.locator("body")).not.toBeFocused();
    await page.reload();
    await expect(auditChoice(page)).toBeVisible();
    await expect(auditForm(page).getByText("Using the default assessment choices.")).toHaveCount(0);

    // the eVTOL panel shares the dismissal
    await page.goto("/w/view?m=evtol");
    await expect(page.getByRole("form", { name: "Vertiport siting" })).toBeVisible();
    await expect(page.getByText("Using the default assessment choices.")).toHaveCount(0);
  });

  test("the panels start from the setting, a run may differ, and a reopened audit keeps its own", async ({ page }) => {
    test.setTimeout(240_000);
    await page.goto("/w/view?m=flight-audit");
    await expect(auditChoice(page).getByRole("radio", { name: "An exceedance (fail)" })).toBeChecked();
    await expect(auditChoice(page)).toContainText("From your assessment choices; this run can differ.");

    await setChoice(page, "eVTOL siting and corridors: within 5 km of an airport", "Keep out, like a red zone");
    await page.goto("/w/view?m=evtol");
    const siting = page.getByRole("form", { name: "Vertiport siting" });
    const sitingChoice = siting.getByRole("group", { name: "Within 5 km of an airport (verify on Digital Sky)" });
    await expect(sitingChoice.getByRole("radio", { name: "Keep out, like a red zone" })).toBeChecked();
    await expect(siting.getByText(/is excluded, like one in a red zone/)).toBeVisible();
    // the choice set in Settings hides the first-time note
    await expect(page.getByText("Using the default assessment choices.")).toHaveCount(0);
    await page.getByRole("tab", { name: "Corridor" }).click();
    const corridorChoice = page.getByRole("form", { name: "Corridor routing" }).getByRole("group", { name: /^Within 5 km of an airport/ });
    await expect(corridorChoice.getByRole("radio", { name: "Keep out, like a red zone" })).toBeChecked();

    await setChoice(page, "Flight audit: flying above a band's free-flight ceiling", "Permission required (caution)");
    await page.goto("/w/view?m=flight-audit&sample=flightmd_report_v12");
    await expect(page.getByText("GPS points")).toBeVisible();
    const permission = auditChoice(page).getByRole("radio", { name: "Permission required (caution)" });
    const exceedance = auditChoice(page).getByRole("radio", { name: "An exceedance (fail)" });
    await expect(permission).toBeChecked();

    // this run differs from the setting, and records it
    await exceedance.check();
    await expect(auditChoice(page)).toContainText("This run differs from your assessment choices (Permission required (caution)).");
    const created = page.waitForRequest(isCreateAnalysis);
    await page.getByRole("button", { name: "Audit flight" }).click();
    expect(((await created).postDataJSON() as { params: { band_ceiling: string } }).params.band_ceiling).toBe("exceedance");
    await expect(page.getByRole("group", { name: "Flight audit summary" })).toBeVisible({ timeout: 90_000 });
    await page.waitForURL(/[?&]a=/);

    await page.reload();
    await expect(page.getByRole("group", { name: "Flight audit summary" })).toBeVisible({ timeout: 60_000 });
    await expect(exceedance).toBeChecked();
    await expect(auditChoice(page)).toContainText("This run differs from your assessment choices");
  });

  test("the ED-318 export entries name the layout from Settings and ask for it", async ({ page, request }) => {
    test.setTimeout(240_000);
    const { datasetId, analysisId } = await completedAirspace(request);
    await setChoice(page, "ED-318 zone exports: where the vertical limits go", "In the properties");

    await page.goto(`/w/view?d=${datasetId}&m=airspace&a=${analysisId}`);
    const exportButton = page.getByRole("region", { name: "Results" }).getByRole("button", { name: "Export" });
    await expect(exportButton).toBeVisible({ timeout: 30_000 });
    await exportButton.click();
    const zones = page.getByRole("list", { name: "Analysis export formats" }).getByRole("link", { name: /^Zones \(ED-318, limits in properties\)/ });
    await expect(zones).toHaveAttribute("href", /format=ed318&ed318_limits=properties/);
    await page.keyboard.press("Escape");

    await setChoice(page, "ED-318 zone exports: where the vertical limits go", "In the geometry");
    await page.goto(`/w/view?d=${datasetId}&m=airspace&a=${analysisId}`);
    // the dataset header's menu comes before the results drawer's
    const datasetExport = page.getByRole("button", { name: "Export", exact: true }).first();
    await expect(datasetExport).toBeEnabled({ timeout: 30_000 });
    await datasetExport.click();
    const fileZones = page.getByRole("list", { name: "Export formats", exact: true }).getByRole("link", { name: /^Zones \(ED-318, limits in geometry\)/ });
    await expect(fileZones).toHaveAttribute("href", /format=ed318&ed318_limits=geometry/);
  });

  test("Help me choose leaves alone a choice whose question it did not ask", async ({ page }) => {
    await setChoice(page, "Flight audit: flying above a band's free-flight ceiling", "Permission required (caution)");
    const region = choicesRegion(page);
    const audit = region.getByRole("group", { name: "Flight audit: flying above a band's free-flight ceiling" });
    const evtol = region.getByRole("group", { name: "eVTOL siting and corridors: within 5 km of an airport" });
    const help = page.getByRole("button", { name: "Help me choose" });
    const dialog = page.getByRole("dialog", { name: "Help me choose" });
    const recommended = dialog.getByRole("region", { name: "Recommended" });

    // eVTOL work: the permission question is not asked
    await help.click();
    await dialog.getByRole("radio", { name: "eVTOL or vertiport studies" }).check();
    await dialog.getByRole("radio", { name: "Keep out completely" }).check();
    await expect(recommended).toContainText("Permission required (caution), unchanged (not asked).");
    await dialog.getByRole("button", { name: "Apply" }).click();
    await expect(dialog).toBeHidden();
    await expect(audit.getByRole("radio", { name: "Permission required (caution)" })).toBeChecked();
    await expect(evtol.getByRole("radio", { name: "Keep out, like a red zone" })).toBeChecked();

    // flight work: the airport question is not asked
    await help.click();
    await dialog.getByRole("radio", { name: "Reviewing flights after the fact" }).check();
    await dialog.getByRole("radio", { name: "No, green zones only" }).check();
    await expect(recommended).toContainText("Keep out, like a red zone, unchanged (not asked).");
    await dialog.getByRole("button", { name: "Apply" }).click();
    await expect(dialog).toBeHidden();
    await expect(audit.getByRole("radio", { name: "An exceedance (fail)" })).toBeChecked();
    await expect(evtol.getByRole("radio", { name: "Keep out, like a red zone" })).toBeChecked();
  });

  test("the dataset's ED-318 entry: only with polygons, says how unmapped ones export, and an error is never saved", async ({ page }) => {
    test.setTimeout(180_000);
    const datasetExport = page.getByRole("button", { name: "Export", exact: true }).first();
    const formats = page.getByRole("list", { name: "Export formats", exact: true });
    const zones = formats.getByRole("link", { name: /^Zones \(ED-318/ });

    // points and lines only
    await openSample(page, "Training flight (GPX)");
    await expect(datasetExport).toBeEnabled({ timeout: 60_000 });
    await datasetExport.click();
    await expect(formats.getByRole("link", { name: /^Features \(GeoJSON\)/ })).toBeVisible();
    await expect(zones).toHaveCount(0);
    await page.keyboard.press("Escape");

    await openSample(page, "Bengaluru survey");
    await expect(datasetExport).toBeEnabled({ timeout: 60_000 });
    await datasetExport.click();
    await expect(zones).toContainText("one without a zone column is exported as red (prohibited)");
    const saved = page.waitForEvent("download");
    await zones.click();
    expect((await saved).suggestedFilename()).toMatch(/\.ed318\.geojson$/);

    const downloads: string[] = [];
    page.on("download", (d) => downloads.push(d.suggestedFilename()));
    await page.route(
      (url) => url.pathname.endsWith("/export/") && url.searchParams.get("format") === "ed318",
      (route) =>
        route.fulfill({
          status: 422,
          json: { error: { code: "invalid_params", message: "Feature 0 is not a valid zone: the ceiling is above 10 km." } },
          headers: { "access-control-allow-origin": "*" },
        }),
    );
    await zones.click();
    await expect(page.getByText("Feature 0 is not a valid zone: the ceiling is above 10 km.")).toBeVisible();
    await expect(page).toHaveURL(/\/w\/view/);
    expect(downloads).toEqual([]);
  });

  test("Help me choose leaves alone a choice whose question was not answered", async ({ page }) => {
    await setChoice(page, "Flight audit: flying above a band's free-flight ceiling", "Permission required (caution)");
    await setChoice(page, "eVTOL siting and corridors: within 5 km of an airport", "Keep out, like a red zone");
    await page.getByRole("button", { name: "Help me choose" }).click();
    const dialog = page.getByRole("dialog", { name: "Help me choose" });
    const recommended = dialog.getByRole("region", { name: "Recommended" });

    // only the ED-318 question: the use is not answered, so both others are asked but left open
    await dialog.getByRole("radio", { name: "A national feed with the limits in the properties" }).check();
    await expect(recommended).toContainText("Permission required (caution), unchanged (not answered).");
    await expect(recommended).toContainText("Keep out, like a red zone, unchanged (not answered).");
    await dialog.getByRole("button", { name: "Apply" }).click();
    await expect(dialog).toBeHidden();

    const region = choicesRegion(page);
    await expect(region.getByRole("radio", { name: "Permission required (caution)" })).toBeChecked();
    await expect(region.getByRole("radio", { name: "Keep out, like a red zone" })).toBeChecked();
    await expect(region.getByRole("radio", { name: "In the properties" })).toBeChecked();
  });

  test("dataset exports run one at a time and say so; a modified click is left to the browser", async ({ page }) => {
    test.setTimeout(120_000);
    await openSample(page, "Bengaluru survey");
    const datasetExport = page.getByRole("button", { name: "Export", exact: true }).first();
    await expect(datasetExport).toBeEnabled({ timeout: 60_000 });
    await datasetExport.click();
    const formats = page.getByRole("list", { name: "Export formats", exact: true });
    const geojson = formats.getByRole("link", { name: /^Features \(GeoJSON\)/ });
    const csv = formats.getByRole("link", { name: /^Measurements \(CSV\)/ });

    // ctrl-click: the page doesn't take it over (the listener on window runs after React's)
    const prevented = await geojson.evaluate((a) => {
      const seen: boolean[] = [];
      window.addEventListener(
        "click",
        (e) => {
          seen.push(e.defaultPrevented);
          e.preventDefault();
        },
        { once: true },
      );
      a.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, ctrlKey: true }));
      return seen;
    });
    expect(prevented).toEqual([false]);

    let release!: () => void;
    const held = new Promise<void>((resolve) => {
      release = resolve;
    });
    let requests = 0;
    await page.route(
      (url) => url.pathname.endsWith("/export/"),
      async (route) => {
        requests += 1;
        await held;
        await route.continue();
      },
    );
    const saved = page.waitForEvent("download");
    await geojson.click();
    await expect(geojson).toHaveAttribute("aria-busy", "true");
    await expect(geojson).toContainText("Exporting…");
    await expect(csv).toHaveAttribute("aria-disabled", "true");
    await expect(csv).toContainText("Another export is still running: wait for it");
    // both ignored while the first one runs
    await csv.click({ force: true });
    await geojson.click();
    release();
    expect((await saved).suggestedFilename()).toMatch(/\.geojson$/);
    await expect(csv).not.toHaveAttribute("aria-disabled", "true");
    await expect(csv).toContainText("One row per feature");
    expect(requests).toBe(1);

    // two clicks in one go, before React re-renders: the second still finds the first running
    const again = page.waitForEvent("download");
    await geojson.evaluate((a) => {
      (a as HTMLElement).click();
      (a as HTMLElement).click();
    });
    await again;
    expect(requests).toBe(2);
  });

  test("each panel's first-time note goes only once its own choice is set", async ({ page }) => {
    const auditNote = auditForm(page).getByRole("note").filter({ hasText: "Using the default assessment choices." });
    const evtolNote = page.getByRole("note").filter({ hasText: "Using the default assessment choices." });
    const openEvtol = async () => {
      await page.goto("/w/view?m=evtol");
      await expect(page.getByRole("form", { name: "Vertiport siting" })).toBeVisible();
    };

    // only the ED-318 choice: neither panel's choice was made
    await page.goto("/settings");
    await page.getByRole("button", { name: "Help me choose" }).click();
    const dialog = page.getByRole("dialog", { name: "Help me choose" });
    await dialog.getByRole("radio", { name: "A national feed with the limits in the properties" }).check();
    await dialog.getByRole("button", { name: "Apply" }).click();
    await expect(dialog).toBeHidden();
    await page.goto("/w/view?m=flight-audit");
    await expect(auditNote).toBeVisible();
    await openEvtol();
    await expect(evtolNote).toBeVisible();

    // the flight audit's own choice, set in Settings: its note goes, eVTOL's stays
    await setChoice(page, "Flight audit: flying above a band's free-flight ceiling", "Permission required (caution)");
    await page.goto("/w/view?m=flight-audit");
    await expect(auditChoice(page)).toBeVisible();
    await expect(auditNote).toHaveCount(0);
    await openEvtol();
    await expect(evtolNote).toBeVisible();

    // older saves, read on each load (the later script wins). No record at all: a choice off its
    // default counts as set
    await page.addInitScript(() => {
      localStorage.setItem("airbrief-settings", JSON.stringify({ state: { bandCeiling: "permission" }, version: 1 }));
    });
    await page.goto("/w/view?m=flight-audit");
    await expect(auditChoice(page).getByRole("radio", { name: "Permission required (caution)" })).toBeChecked();
    await expect(auditNote).toHaveCount(0);
    await openEvtol();
    await expect(evtolNote).toBeVisible();

    // saved before each choice was tracked: `choicesSet: true` means all of them
    await page.addInitScript(() => {
      localStorage.setItem("airbrief-settings", JSON.stringify({ state: { choicesSet: true }, version: 1 }));
    });
    await openEvtol();
    await expect(page.getByRole("form", { name: "Vertiport siting" }).getByRole("group", { name: /^Within 5 km of an airport/ })).toBeVisible();
    await expect(evtolNote).toHaveCount(0);
  });
});
