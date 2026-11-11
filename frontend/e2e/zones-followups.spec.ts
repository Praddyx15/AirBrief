import { expect, test, type Page, type Request } from "@playwright/test";
import { expectNoSeriousA11yViolations, openSample } from "./helpers";

/**
 * Zone rule profiles beyond the airspace check: the flight audit and eVTOL forms have the same
 * Rules select (DGCA 2021 by default), reopened runs keep their own rules, and the map's context
 * rings are drawn with the Rules of the open panel.
 */

async function pickRules(page: Page, form: string, label: string): Promise<void> {
  const select = page.getByRole("form", { name: form }).getByRole("combobox", { name: "Rules" });
  await select.click();
  await page.getByRole("option", { name: label }).click();
  await expect(select).toHaveText(label);
}

const ringsRequest = (profile: string) => (r: Request) => {
  const url = new URL(r.url());
  return url.pathname === "/api/context/" && url.searchParams.get("layers") === "rings" && url.searchParams.get("profile") === profile;
};

test.describe("zone rules beyond airspace", () => {
  test("the context rings follow the Rules of the airspace panel", async ({ page }) => {
    await page.goto("/w/view?m=airspace");
    await expect(page.getByRole("button", { name: "New operation" })).toBeVisible();
    const dgca = page.waitForRequest(ringsRequest("dgca_2021"));
    // Going to an aerodrome switches its rings on.
    await page.keyboard.press("Control+k");
    const palette = page.getByRole("dialog", { name: "Command palette" });
    await palette.getByRole("combobox", { name: "Search commands" }).fill("VOBL");
    await palette.getByRole("option", { name: /VOBL · Kempegowda International Airport/ }).click();
    await dgca;
    const context = page.getByRole("complementary", { name: "Layers and features" }).getByRole("region", { name: "Context layers" });
    await expect(context.getByRole("switch", { name: /^Aerodrome zone rings/ })).toBeChecked();

    const conservative = page.waitForRequest(ringsRequest("conservative_illustrative"));
    await pickRules(page, "Airspace check", "Conservative (illustrative)");
    await conservative;
  });

  test("flight audit: the Rules select, the bands under the chosen rules, a reopened audit keeps them", async ({ page }) => {
    test.setTimeout(240_000);
    await page.goto("/w/view?m=flight-audit&sample=flightmd_report_v12");
    await expect(page.getByText("GPS points")).toBeVisible();
    const form = page.getByRole("form", { name: "Flight audit" });
    await expect(form.getByRole("region", { name: "Zone rules" })).toBeVisible();
    await expect(form.getByRole("combobox", { name: "Rules" })).toHaveText("DGCA 2021");

    await pickRules(page, "Flight audit", "Conservative (illustrative)");
    await page.getByRole("button", { name: "Audit flight" }).click();
    await expect(page.getByRole("group", { name: "Flight audit summary" })).toBeVisible({ timeout: 90_000 });
    const bands = page.getByRole("list", { name: "Aerodrome bands reached by the flight" });
    await expect(bands).toContainText("Elsewhere");
    await expect(page.getByText("Rules: Conservative (illustrative).")).toBeVisible();
    await expect(page.getByText(/Highest height above ground flown among the track points/)).toBeVisible();
    await page.waitForURL(/[?&]a=/);
    await expectNoSeriousA11yViolations(page);

    await page.reload();
    await expect(page.getByRole("group", { name: "Flight audit summary" })).toBeVisible({ timeout: 60_000 });
    await expect(page.getByRole("form", { name: "Flight audit" }).getByRole("combobox", { name: "Rules" })).toHaveText(
      "Conservative (illustrative)",
    );

    // Custom rules open a JSON box that must hold rules before the audit runs.
    await pickRules(page, "Flight audit", "Custom");
    await expect(form.getByRole("textbox", { name: /Custom rules \(JSON\)/ })).toHaveValue(/"dgca_2021"/);
  });

  test("eVTOL: one Rules select for siting and corridor, named in the results", async ({ page }) => {
    test.setTimeout(360_000);
    await openSample(page, "Bengaluru eVTOL");
    await page.getByRole("navigation", { name: "Workspace modules" }).getByRole("link", { name: /eVTOL/ }).click();
    const siting = page.getByRole("form", { name: "Vertiport siting" });
    await expect(siting.getByRole("combobox", { name: "Rules" })).toHaveText("DGCA 2021");
    await expect(siting.getByText(/marked “Verify on Digital Sky”/)).toBeVisible();

    await page.getByRole("button", { name: "Score sites" }).click();
    await expect(page.getByRole("region", { name: "Ranking", exact: true })).toBeVisible({ timeout: 120_000 });
    const criteria = page.getByRole("region", { name: "Criteria", exact: true });
    await expect(criteria).toContainText("Zone rules: DGCA 2021.");
    await expect(criteria).toContainText("verify on Digital Sky");

    await page.getByRole("tab", { name: "Corridor" }).click();
    await expect(page.getByRole("form", { name: "Corridor routing" }).getByRole("combobox", { name: "Rules" })).toHaveText("DGCA 2021");
    await pickRules(page, "Corridor routing", "Conservative (illustrative)");
    await page.getByRole("button", { name: "Route corridor" }).click();
    await expect(page.getByRole("group", { name: "Corridor summary" })).toBeVisible({ timeout: 120_000 });
    await expect(page.getByText("Zone rules: Conservative (illustrative).")).toBeVisible();

    // the siting tab shares the choice
    await page.getByRole("tab", { name: "Vertiport siting" }).click();
    await expect(siting.getByRole("combobox", { name: "Rules" })).toHaveText("Conservative (illustrative)");
  });
});
