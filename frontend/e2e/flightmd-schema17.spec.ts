import { expect, test } from "@playwright/test";
import { API_URL, expectNoSeriousA11yViolations } from "./helpers";

/**
 * FlightMD schema 1.6/1.7 in the flight audit: findings with their armed state, placed on the
 * track from the report's per-point GPS times, the parser's warnings, and the planning-brief
 * hash. The report is the 1.5 sample with the newer fields added.
 */

const BRIEF_SHA = "3f".repeat(32);

test("a 1.7 report lists its findings, places them and names its planning brief", async ({ page }) => {
  test.setTimeout(240_000);
  const sample = await page.request.get(`${API_URL}/api/samples/flightmd_report_v15/download/`);
  expect(sample.ok()).toBeTruthy();
  const report = JSON.parse((await sample.body()).toString("utf-8"));
  const n: number = report.metadata.gps_path.length;
  report.schema_version = "1.7";
  Object.assign(report.metadata, {
    armed_windows: [[15, 330]],
    parse_warnings: ["3 GPS samples without a fix were skipped"],
    gps_path_t_s: Array.from({ length: n }, (_, k) => 20 + k),
    planning_brief_sha256: BRIEF_SHA,
  });
  report.findings = [
    { id: "osc", severity: "critical", category: "oscillation", title: "Roll oscillation", t_start_s: 136, t_end_s: 150, params: ["ATC_RAT_RLL_P"] },
    { id: "pre", severity: "warning", category: "ekf", title: "EKF variance before GPS lock", t_start_s: 5, t_end_s: 8, params: [] },
  ];

  await page.goto("/w/view?m=flight-audit");
  const form = page.getByRole("form", { name: "Flight audit" });
  await expect(form).toBeVisible();
  await page.getByRole("tab", { name: "Upload JSON" }).click();
  await page.locator('input[type="file"]').setInputFiles({
    name: "flight_17.json",
    mimeType: "application/json",
    buffer: Buffer.from(JSON.stringify(report)),
  });
  await expect(form.getByText("flight_17.json")).toBeVisible();
  await page.getByRole("button", { name: "Audit flight" }).click();

  const findings = page.getByRole("region", { name: "FlightMD findings (2)" });
  await expect(findings).toBeVisible({ timeout: 90_000 });
  await expect(findings).toContainText("Findings are placed where they started on the track.");
  const placed = findings.getByRole("row", { name: "Zoom to where Roll oscillation started" });
  await expect(placed).toContainText("Critical");
  await expect(placed).toContainText("ATC_RAT_RLL_P");
  await expect(placed).toContainText("Armed");
  await expect(placed).toContainText("above ground at");
  const early = findings.getByRole("row", { name: "Zoom to where EKF variance before GPS lock started" });
  await expect(early).toContainText("Disarmed");
  await expect(early).toContainText("Outside the GPS track's time span");

  await expect(page.getByText("FlightMD parser: 3 GPS samples without a fix were skipped")).toBeVisible();
  await expect(page.getByText("Planned with brief")).toBeVisible();
  await expect(page.getByText(`${BRIEF_SHA.slice(0, 16)}…`)).toBeVisible();
  await expect(page.getByText("(not one of the briefs built in this browser)")).toBeVisible();

  await placed.click();
  await expectNoSeriousA11yViolations(page);
});
