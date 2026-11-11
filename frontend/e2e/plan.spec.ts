import { expect, test, type Page } from "@playwright/test";
import { expectNoSeriousA11yViolations, hasHorizontalScroll } from "./helpers";

/**
 * An operation without a file. Pin by typed coordinates and run airspace, paste a WKT polygon
 * and run survey, draw an area on the map, Go to an aerodrome from the command palette with its
 * context layers, the landing page's "Check a location", and an axe check of the plan menu.
 * Runs against the synthetic demo pack (VOBL at 13.1986 N 77.7066 E).
 */

const SURVEY_WKT = "POLYGON ((77.48 12.90, 77.50 12.90, 77.50 12.91, 77.48 12.91, 77.48 12.90))";

async function openPlanMenu(page: Page) {
  await page.getByRole("button", { name: "New operation" }).click();
  const menu = page.getByRole("dialog", { name: "New operation" });
  await expect(menu).toBeVisible();
  return menu;
}

/** The unsaved-draft card (a region named by its heading). */
function draftCard(page: Page, name: string) {
  return page.getByRole("region", { name, exact: true });
}

test.describe("plan without a file", () => {
  test("typed coordinates near VOBL become a pin, and airspace runs on it without an upload", async ({ page }) => {
    await page.goto("/w/view?m=airspace");
    const menu = await openPlanMenu(page);
    await menu.getByRole("button", { name: "Coordinates…" }).click();

    const dialog = page.getByRole("dialog", { name: "Type coordinates" });
    await expect(dialog).toBeVisible();
    await expect(dialog.getByRole("radio", { name: "Pin" })).toBeChecked();
    await dialog.getByLabel("Coordinates", { exact: true }).fill("13.1979, 77.7063");
    // Both values fit either order, so the dialog says how it read them (latitude first).
    await expect(dialog.getByText("Reads as 13.19790° N, 77.70630° E.")).toBeVisible();
    await dialog.getByRole("button", { name: "Show on the map" }).click();
    await expect(dialog).toBeHidden();

    const draft = draftCard(page, "Pin");
    await expect(draft).toBeVisible();
    await expect(draft.getByText("13.19790° N, 77.70630° E")).toBeVisible();
    await expect(draft.getByText("Not saved yet")).toBeVisible();
    await draft.getByRole("radiogroup", { name: "Radius presets" }).getByRole("radio", { name: "500 m" }).click();

    await draft.getByRole("button", { name: "Use this operation" }).click();
    await page.waitForURL(/[?&]d=/);
    await expect(page).toHaveURL(/[?&]m=airspace/);
    // The saved pin is an ordinary dataset: the point and its radius polygon.
    await expect(page.getByRole("heading", { level: 1, name: "Pin 13.1979 N 77.7063 E.geojson" })).toBeVisible();
    const features = page.getByRole("complementary", { name: "Layers and features" });
    await expect(features.getByRole("row", { name: "Select Pin radius" })).toBeVisible();

    await page.getByRole("button", { name: "Check airspace" }).click();
    await expect(page.getByText(/Airspace: (Pass|Caution|Fail)/)).toBeVisible({ timeout: 90_000 });
  });

  test("a pasted WKT polygon becomes the survey area and the survey runs", async ({ page }) => {
    await page.goto("/w/view?m=survey");
    // The survey form says what it needs instead of "Open a dataset first".
    await expect(page.getByText("Draw, pin or open a dataset.").first()).toBeVisible();

    const menu = await openPlanMenu(page);
    await menu.getByRole("button", { name: "Paste…" }).click();
    const dialog = page.getByRole("dialog", { name: "Paste a geometry" });
    await dialog.getByLabel("GeoJSON, WKT or coordinates").fill(SURVEY_WKT);
    await dialog.getByRole("button", { name: "Read geometry" }).click();
    await expect(dialog).toBeHidden();

    const draft = draftCard(page, "Pasted geometry");
    await expect(draft.getByText("Polygon", { exact: true })).toBeVisible();
    await draft.getByRole("button", { name: "Use this operation" }).click();
    await page.waitForURL(/[?&]d=/);

    await page.getByRole("button", { name: "Estimate survey" }).click();
    await expect(page.getByRole("heading", { name: /^Battery blocks \(\d+\)$/ })).toBeVisible({ timeout: 90_000 });
  });

  test("invalid pasted text says what is wrong, in the dialog", async ({ page }) => {
    await page.goto("/w/view?m=airspace");
    const menu = await openPlanMenu(page);
    await menu.getByRole("button", { name: "Paste…" }).click();
    const dialog = page.getByRole("dialog", { name: "Paste a geometry" });
    await dialog.getByLabel("GeoJSON, WKT or coordinates").fill("POLYGON ((0 0, 1 1");
    await dialog.getByRole("button", { name: "Read geometry" }).click();
    await expect(dialog.getByRole("alert")).toContainText("The geometry could not be read");
    await expect(dialog).toBeVisible();
  });

  test("an area drawn with clicks finishes with Enter, and Backspace removes a corner", async ({ page }) => {
    await page.goto("/w/view?m=measure");
    const menu = await openPlanMenu(page);
    await menu.getByRole("button", { name: "Area" }).click();
    const drawing = page.getByRole("region", { name: "Draw an area: drawing" });
    await expect(drawing).toBeVisible();

    const canvas = page.locator("#map canvas.maplibregl-canvas");
    const box = await canvas.boundingBox();
    if (!box) throw new Error("map canvas not found");
    const cx = box.width / 2;
    const cy = box.height / 2;
    const corners: [number, number][] = [
      [-60, -40],
      [60, -40],
      [60, 50],
      [-60, 50],
    ];
    for (const [i, [dx, dy]] of corners.entries()) {
      // Clicking the canvas (not page coordinates) waits until nothing covers the map: until the
      // map has loaded, its placeholder takes the clicks.
      await canvas.click({ position: { x: cx + dx, y: cy + dy } });
      // Each corner is counted before the next click, so these stay separate clicks, not a
      // double-click (which would finish the area).
      await expect(drawing.getByText(i === 0 ? "1 point" : `${i + 1} points`, { exact: true })).toBeVisible();
    }
    await page.keyboard.press("Backspace");
    await expect(drawing.getByText("3 points")).toBeVisible();
    await page.keyboard.press("Enter");

    const draft = draftCard(page, "Drawn area");
    await expect(draft).toBeVisible();
    await expect(draft.getByText("Corners")).toBeVisible();
    await draft.getByRole("button", { name: "Discard" }).click();
    await expect(draft).toBeHidden();
  });

  test("Ctrl+K with the keyboard only: the first place found is highlighted, so Enter goes there", async ({ page }) => {
    await page.goto("/w/view?m=airspace");
    await expect(page.getByRole("button", { name: "New operation" })).toBeVisible();
    await page.keyboard.press("Control+k");
    const palette = page.getByRole("dialog", { name: "Command palette" });
    await page.keyboard.type("VOBL");
    // Places come from the API after the commands are filtered; the first one is still highlighted.
    await expect(palette.getByRole("option", { name: /VOBL · Kempegowda International Airport/ })).toHaveAttribute("aria-selected", "true");
    await page.keyboard.press("Enter");
    await expect(palette).toBeHidden();
    await expect(page.getByTestId("workspace-live-region")).toContainText("Map moved to VOBL · Kempegowda International Airport.");
  });

  test("Ctrl+K: VOBL moves the map there and switches on the aerodrome context layers", async ({ page }) => {
    await page.goto("/w/view?m=airspace");
    await expect(page.getByRole("button", { name: "New operation" })).toBeVisible();
    await page.keyboard.press("Control+k");
    const palette = page.getByRole("dialog", { name: "Command palette" });
    await palette.getByRole("combobox", { name: "Search commands" }).fill("VOBL");
    await palette.getByRole("option", { name: /VOBL · Kempegowda International Airport/ }).click();
    await expect(palette).toBeHidden();
    // The map moved (and screen readers hear it).
    await expect(page.getByTestId("workspace-live-region")).toContainText("Map moved to VOBL · Kempegowda International Airport.");

    const result = page.getByRole("region", { name: "Go to result" });
    await expect(result).toContainText("VOBL · Kempegowda International Airport");
    await expect(result).toContainText("Large airport · Bengaluru · IATA BLR");

    const context = page.getByRole("complementary", { name: "Layers and features" }).getByRole("region", { name: "Context layers" });
    const rings = context.getByRole("switch", { name: /^Aerodrome zone rings/ });
    await expect(rings).toBeChecked();
    await expect(context.getByRole("switch", { name: /^Aerodromes/ })).toBeChecked();
    // Obstacles can be switched on too; each layer says how many features the view has.
    const obstacles = context.getByRole("switch", { name: /^Obstacles/ });
    await obstacles.click();
    await expect(obstacles).toBeChecked();
    await expect(context.getByText(/^\d[\d,]* features?$|^First /).first()).toBeVisible({ timeout: 30_000 });
    await expect(context.getByRole("button", { name: "View Aerodrome zone rings as table" })).toBeVisible();
  });

  test("typed coordinates in the palette come first and Pin here makes a pin", async ({ page }) => {
    await page.goto("/w/view?m=airspace");
    await expect(page.getByRole("button", { name: "New operation" })).toBeVisible();
    await page.keyboard.press("Control+k");
    const palette = page.getByRole("dialog", { name: "Command palette" });
    await palette.getByRole("combobox", { name: "Search commands" }).fill("13.2500, 77.6500");
    const first = palette.getByRole("option").first();
    await expect(first).toContainText("13.25000° N, 77.65000° E");
    await first.click();
    const result = page.getByRole("region", { name: "Go to result" });
    await result.getByRole("button", { name: "Pin here" }).click();
    await expect(draftCard(page, "Pin")).toContainText("13.25000° N, 77.65000° E");
  });

  test("the landing page's Check a location opens pin mode on the airspace module", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("link", { name: "Check a location" }).click();
    await page.waitForURL(/\/w\/view\?m=airspace/);
    const drawing = page.getByRole("region", { name: "Drop a pin: drawing" });
    await expect(drawing).toBeVisible();
    // ?plan=pin is removed from the URL once used.
    await expect(page).not.toHaveURL(/plan=/);
    // Keyboard alternative to clicking: pin the map centre.
    await drawing.getByRole("button", { name: "Pin the map centre" }).click();
    await expect(draftCard(page, "Pin")).toBeVisible();
  });

  test("keyboard only: Space places points at the map centre, Enter finishes, Escape cancels", async ({ page }) => {
    await page.goto("/w/view?m=measure");
    const menu = await openPlanMenu(page);
    await menu.getByRole("button", { name: "Line" }).click();
    const drawing = page.getByRole("region", { name: "Draw a line: drawing" });
    await expect(drawing).toBeVisible();
    // Starting a tool puts focus on the map (WCAG 2.5.7: no pointer needed).
    await expect(page.locator("#map canvas.maplibregl-canvas")).toBeFocused();

    await page.keyboard.press("Space");
    await expect(drawing.getByText("1 point")).toBeVisible();
    await page.keyboard.press("ArrowRight"); // pans the map, so the centre moves
    await page.waitForTimeout(600);
    await page.keyboard.press("Space");
    await expect(drawing.getByText("2 points")).toBeVisible();
    await page.keyboard.press("Enter");
    await expect(draftCard(page, "Drawn line")).toBeVisible();
    await draftCard(page, "Drawn line").getByRole("button", { name: "Discard" }).click();

    const again = await openPlanMenu(page);
    await again.getByRole("button", { name: "Area" }).click();
    await expect(page.getByRole("region", { name: "Draw an area: drawing" })).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.getByRole("region", { name: "Draw an area: drawing" })).toBeHidden();
    await expect(page.getByTestId("workspace-live-region")).toContainText("Drawing cancelled.");
  });

  test("My location with permission becomes a pin at the device position", async ({ page, context }) => {
    await context.grantPermissions(["geolocation"]);
    await context.setGeolocation({ latitude: 13.1979, longitude: 77.7063, accuracy: 12 });
    await page.goto("/w/view?m=airspace");
    const menu = await openPlanMenu(page);
    await menu.getByRole("button", { name: "My location" }).click();
    const draft = draftCard(page, "My location");
    await expect(draft).toContainText("13.19790° N, 77.70630° E");
    await expect(draft.getByText("Accuracy")).toBeVisible();
    await expect(draft.getByRole("button", { name: "Use this operation" })).toBeEnabled();
  });

  test("on a phone: Pin, then Check airspace saves the pin and runs the check", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/w/view?m=airspace");
    const toolbar = page.getByRole("group", { name: "Plan an operation" });
    await expect(toolbar.getByRole("button", { name: "My location" })).toBeVisible();
    await toolbar.getByRole("button", { name: "Pin" }).click();
    const drawing = page.getByRole("region", { name: "Drop a pin: drawing" });
    await expect(drawing.getByText("Tap the map where you want the pin.")).toBeVisible();
    await drawing.getByRole("button", { name: "Cancel" }).click();
    await expect(drawing).toBeHidden();
    // Phones offer no line, area or typed coordinates.
    await expect(page.getByRole("button", { name: "New operation" })).toHaveCount(0);

    // A known spot next to VOBL (inside the demo pack), then Pin here.
    await page.keyboard.press("Control+k");
    const palette = page.getByRole("dialog", { name: "Command palette" });
    await palette.getByRole("combobox", { name: "Search commands" }).fill("13.1979, 77.7063");
    // Typed coordinates come first once the place search has answered; until then a saved pin
    // whose name holds the same numbers (a recent dataset) can be the first option.
    const first = palette.getByRole("option").first();
    await expect(first).toContainText("13.19790° N, 77.70630° E");
    await first.click();
    await page.getByRole("region", { name: "Go to result" }).getByRole("button", { name: "Pin here" }).click();
    const draft = draftCard(page, "Pin");
    await expect(draft).toContainText("13.19790° N, 77.70630° E");
    await draft.getByRole("button", { name: "Check airspace" }).click();
    // Saved as a dataset (d=) and the airspace analysis started on it (a=).
    await page.waitForURL(/[?&]a=/, { timeout: 60_000 });
    await expect(page).toHaveURL(/[?&]d=/);
    await expect(page).toHaveURL(/[?&]m=airspace/);
  });

  test("at 1024 px the plan toolbar stays clear of the map controls", async ({ page }) => {
    await page.setViewportSize({ width: 1024, height: 768 });
    await page.goto("/w/view?m=airspace");
    const plan = page.getByRole("button", { name: "New operation" });
    const controls = page.getByRole("toolbar", { name: "Map view" });
    await expect(plan).toBeVisible();
    await expect(controls).toBeVisible();
    const a = await plan.boundingBox();
    const b = await controls.boundingBox();
    if (!a || !b) throw new Error("toolbar not laid out");
    expect(a.x + a.width <= b.x || b.x + b.width <= a.x || a.y + a.height <= b.y || b.y + b.height <= a.y).toBe(true);
    expect(await hasHorizontalScroll(page)).toBe(false);
  });

  test("the plan menu and a draft card have no serious accessibility violations", async ({ page }) => {
    await page.goto("/w/view?m=airspace");
    await openPlanMenu(page);
    await expectNoSeriousA11yViolations(page);
    await page.keyboard.press("Escape");

    const menu = await openPlanMenu(page);
    await menu.getByRole("button", { name: "Coordinates…" }).click();
    const dialog = page.getByRole("dialog", { name: "Type coordinates" });
    await dialog.getByLabel("Coordinates", { exact: true }).fill("13.1979 N 77.7063 E");
    await expectNoSeriousA11yViolations(page);
    await dialog.getByRole("button", { name: "Show on the map" }).click();
    await expect(draftCard(page, "Pin")).toBeVisible();
    await expectNoSeriousA11yViolations(page);
  });
});
