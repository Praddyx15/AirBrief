import { expect, test, type Locator, type Page, type Request } from "@playwright/test";
import { expectNoSeriousA11yViolations, openSample } from "./helpers";

/**
 * Shared UI: focus returning from dialogs, the command palette (axe, strict filter, module
 * switching), number fields (thousands separators, imperial units, presets) and the Run button
 * (no duplicate jobs, focus kept while it runs).
 */

const rail = (page: Page) => page.getByRole("navigation", { name: "Workspace modules" });

/** A dialog listens for Escape once it holds the focus; on a busy CI runner that comes a moment
 * after it is visible, and an Escape pressed in between is lost. */
async function focusedInside(dialog: Locator): Promise<void> {
  await expect.poll(() => dialog.evaluate((el) => el.contains(document.activeElement))).toBe(true);
}
/** The Airspace module's form (the Survey panel shown before it has its own "Height above ground"). */
const airspaceForm = (page: Page) => page.getByRole("form", { name: "Airspace check" });
const heightField = (page: Page) => airspaceForm(page).getByRole("spinbutton", { name: /^Height above ground/ });

async function openAirspace(page: Page): Promise<void> {
  await rail(page).getByRole("link", { name: /Airspace/ }).click();
  await expect(page).toHaveURL(/[?&]m=airspace/);
  await expect(airspaceForm(page)).toBeVisible();
}

/** /api/analyses/: the collection an analysis is created in (POST), as lib/api.ts calls it. */
const ANALYSES_PATH = /^\/api\/analyses\/?$/;

function isCreateAnalysis(request: Request): boolean {
  return request.method() === "POST" && ANALYSES_PATH.test(new URL(request.url()).pathname);
}

test.describe("dialogs", () => {
  test("closing a dialog opened from code returns focus to what opened it", async ({ page }) => {
    await openSample(page, "Farm plots (Shapefile, UTM 43N)");

    const shortcutsButton = page.getByRole("button", { name: "Keyboard shortcuts" }).first();
    await shortcutsButton.focus();
    await page.keyboard.press("Enter");
    const shortcuts = page.getByRole("dialog", { name: "Keyboard shortcuts" });
    await expect(shortcuts).toBeVisible();
    await focusedInside(shortcuts);
    await page.keyboard.press("Escape");
    await expect(shortcuts).toBeHidden();
    await expect(shortcutsButton).toBeFocused();

    const zoomIn = page.getByRole("button", { name: "Zoom in" }).first();
    await zoomIn.focus();
    await page.keyboard.press("Control+K");
    const palette = page.getByRole("dialog", { name: "Command palette" });
    await expect(palette).toBeVisible();
    await focusedInside(palette);
    await page.keyboard.press("Escape");
    await expect(palette).toBeHidden();
    await expect(zoomIn).toBeFocused();
  });
});

test.describe("command palette", () => {
  test("the listbox has no separator elements, passes axe and filters strictly", async ({ page }) => {
    await page.goto("/w");
    await page.getByRole("button", { name: "Search and commands" }).click();
    const palette = page.getByRole("dialog", { name: "Command palette" });
    await expect(palette).toBeVisible();
    await expect(palette.getByRole("option").first()).toBeVisible();
    await expect(palette.locator("[cmdk-list] [role='separator']")).toHaveCount(0);
    await expectNoSeriousA11yViolations(page);

    // Unrelated words no longer fuzzy-match the long module descriptions.
    await palette.getByRole("combobox").fill("delete");
    await expect(palette.getByText("No matching commands.")).toBeVisible();
    await palette.getByRole("combobox").fill("airsp");
    await expect(palette.getByRole("option", { name: "Airspace", exact: true })).toBeVisible();
  });

  test("switching module from the palette replaces the history entry, like the rail", async ({ page }) => {
    await openSample(page, "Farm plots (Shapefile, UTM 43N)");
    await page.keyboard.press("Control+K");
    const palette = page.getByRole("dialog", { name: "Command palette" });
    await expect(palette).toBeVisible();
    await expect(palette.getByRole("group", { name: "Map and dataset" })).toBeVisible();
    await palette.getByRole("option", { name: "Airspace", exact: true }).click();
    await expect(page).toHaveURL(/[?&]m=airspace/);

    // Back leaves the workspace instead of returning to the previous module.
    await page.goBack();
    await expect(page).not.toHaveURL(/\/w\/view/);
  });
});

test.describe("number fields", () => {
  test("a thousands comma is read as thousands and ambiguous text is an error, never a guess", async ({ page }) => {
    await openSample(page, "Bengaluru survey");
    await openAirspace(page);
    const height = heightField(page);

    await height.fill("1,200");
    await height.press("Tab");
    await expect(height).toHaveValue("1200");
    await expect(height).toHaveAttribute("aria-valuenow", "1200");

    // Typing a grouped decimal key by key never flashes an error on the way ("1,234." → "1,234.5").
    await height.fill("");
    await height.pressSequentially("1,234.");
    await expect(height).not.toHaveAttribute("aria-invalid", "true");
    await height.pressSequentially("5");
    await height.press("Tab");
    await expect(height).toHaveValue("1234.5");

    let creates = 0;
    page.on("request", (request) => {
      if (isCreateAnalysis(request)) creates += 1;
    });
    await height.fill("1,2,3");
    await height.press("Tab");
    await expect(height).toHaveValue("1,2,3");
    await expect(height).toHaveAttribute("aria-invalid", "true");
    // The error is shown under the field and announced with it.
    await expect(height).toHaveAccessibleDescription(
      /Use a dot for decimals \(2\.5\) and commas only between thousands \(1,200\)\./,
    );

    // Run is blocked by the error summary: the last valid value is not sent behind the user's back.
    await airspaceForm(page).getByRole("button", { name: "Check airspace" }).click();
    const summary = airspaceForm(page).getByRole("alert").filter({ hasText: "Please fix the following" });
    await expect(summary).toBeVisible();
    await expect(summary).toContainText("Height above ground");
    expect(creates).toBe(0);
  });

  test("imperial units apply to inputs, presets and range messages; the API still gets metres", async ({ page }) => {
    await page.goto("/settings");
    await page.getByRole("radio", { name: "Imperial (ft, mi²)" }).click();
    await expect(page.getByRole("radio", { name: "Imperial (ft, mi²)" })).toHaveAttribute("aria-checked", "true");

    await openSample(page, "Bengaluru survey");
    await openAirspace(page);
    const form = airspaceForm(page);
    const height = heightField(page);
    // The 120 m default, shown in feet, with the unit in the field's name.
    await expect(height).toHaveValue("394");
    await expect(height).toHaveAccessibleName(/^Height above ground \(ft\)/);

    // Range messages are in the unit shown (1,500 m is 4,921 ft).
    await height.fill("5000");
    await height.press("Tab");
    await expect(height).toHaveAttribute("aria-invalid", "true");
    await expect(height).toHaveAccessibleDescription(/Must be at most 4,921 ft\./);

    // Presets are the metric zone ceilings shown in whole feet; they keep their exact metres, so
    // "197 ft" checks exactly the 60 m ceiling (not 60.05 m, which would read as above it).
    const presets = form.getByRole("radiogroup", { name: /^Height .*presets$/ });
    await expect(presets.getByRole("radio")).toHaveText(["98 ft", "197 ft", "394 ft"]);
    await presets.getByRole("radio", { name: "197 ft" }).click();
    await expect(height).toHaveValue("197");
    await expect(height).not.toHaveAttribute("aria-invalid", "true");

    const created = page.waitForRequest(isCreateAnalysis);
    await form.getByRole("button", { name: "Check airspace" }).click();
    const body = (await created).postDataJSON() as { params: { altitude_agl_m: number } };
    expect(body.params.altitude_agl_m).toBe(60);
  });
});

test.describe("run button", () => {
  test("keeps keyboard focus while the job starts and runs, and never starts a duplicate", async ({ page }) => {
    let creates = 0;
    // Simulate a slow network for the create request, as on the public demo.
    await page.route(
      (url) => ANALYSES_PATH.test(url.pathname),
      async (route) => {
        if (route.request().method() === "POST") {
          creates += 1;
          await new Promise((resolve) => setTimeout(resolve, 800));
        }
        await route.fallback();
      },
    );

    await openSample(page, "Bengaluru survey");
    await openAirspace(page);
    const run = airspaceForm(page).getByRole("button", { name: /^(Check airspace|Starting…|Running…)$/ });
    // Enabled once the dataset has finished processing.
    await expect(run).toBeEnabled();
    await run.focus();
    await page.keyboard.press("Enter");

    await expect(run).toHaveAttribute("aria-disabled", "true");
    await expect(run).toBeFocused();
    // A second press while busy does nothing.
    await page.keyboard.press("Enter");
    await page.keyboard.press("Space");

    await expect(page.getByText(/Airspace: (Pass|Caution|Fail)/)).toBeVisible({ timeout: 90_000 });
    await expect(run).toHaveAccessibleName("Check airspace");
    await expect(run).not.toHaveAttribute("aria-disabled", "true");
    // Focus stayed on the button through "Starting…" and "Running…" (it is never moved back to it).
    await expect(run).toBeFocused();
    expect(creates).toBe(1);
  });
});

test.describe("chrome details", () => {
  test("search fields show only their own clear button", async ({ page }) => {
    await openSample(page, "Farm plots (Shapefile, UTM 43N)");
    await expect(page.locator("input[type='search']").first()).toBeVisible();
    // The browser's own clear (X) is hidden by a style rule (pseudo-elements cannot be queried).
    const nativeClearHidden = await page.evaluate(() => {
      const hides = (rules: CSSRuleList): boolean =>
        Array.from(rules).some((rule) => {
          if (rule instanceof CSSStyleRule && rule.selectorText.includes("::-webkit-search-cancel-button")) {
            return rule.style.getPropertyValue("appearance") === "none" || rule.style.getPropertyValue("-webkit-appearance") === "none";
          }
          return "cssRules" in rule ? hides((rule as CSSGroupingRule).cssRules) : false;
        });
      return Array.from(document.styleSheets).some((sheet) => {
        try {
          return hides(sheet.cssRules);
        } catch {
          return false;
        }
      });
    });
    expect(nativeClearHidden).toBe(true);
  });

  test("KPI values wrap instead of being cut off with an ellipsis", async ({ page }) => {
    await openSample(page, "Farm plots (Shapefile, UTM 43N)");
    const d = new URL(page.url()).searchParams.get("d");
    expect(d).toBeTruthy();
    await page.goto(`/w/report?d=${d}`);
    const values = page.locator("dd > span.num");
    await expect(values.first()).toBeVisible();
    const truncated = await values.evaluateAll((els) =>
      els.filter((el) => {
        const style = getComputedStyle(el);
        return style.textOverflow === "ellipsis" || style.whiteSpace === "nowrap";
      }).length,
    );
    expect(truncated).toBe(0);
  });
});
