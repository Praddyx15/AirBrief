import { expect, test, type APIRequestContext } from "@playwright/test";
import { API_URL } from "./helpers";

/**
 * WEAK-08: "Open in FlightMD" is a button only for the viewer's own FlightMD (the address in
 * Settings); a link to another host that came with the audit is shown as text. The API takes only
 * https links (plain http only to localhost, outside production). The audits are created through
 * the API with an explicit flightmd_url and the sample FlightMD report.
 */

interface Info {
  id: string;
  status: string;
}

async function sampleReport(request: APIRequestContext): Promise<Record<string, unknown>> {
  const response = await request.get(`${API_URL}/api/samples/flightmd_report_v15/download/`);
  expect(response.ok()).toBe(true);
  return (await response.json()) as Record<string, unknown>;
}

function audit(request: APIRequestContext, report: Record<string, unknown>, flightmdUrl: string) {
  return request.post(`${API_URL}/api/analyses/`, {
    data: { kind: "flight_audit", params: { report, flightmd_url: flightmdUrl, use_terrain: false } },
  });
}

test.describe("flight audit link", () => {
  const link = "https://flightmd.example.com/report/abc-123";

  async function completedAudit(request: APIRequestContext): Promise<string> {
    const created = await audit(request, await sampleReport(request), link);
    expect(created.ok(), await created.text()).toBe(true);
    const info = (await created.json()) as Info;
    await expect
      .poll(async () => ((await (await request.get(`${API_URL}/api/analyses/${info.id}/`)).json()) as Info).status, { timeout: 90_000 })
      .toBe("COMPLETED");
    return info.id;
  }

  test("a link to your own FlightMD is a button that names its host", async ({ page, request }) => {
    test.setTimeout(180_000);
    const id = await completedAudit(request);
    await page.addInitScript(() => {
      localStorage.setItem("airbrief-settings", JSON.stringify({ state: { flightmdUrl: "https://flightmd.example.com" }, version: 1 }));
    });
    await page.goto(`/w/view?m=flight-audit&a=${id}`);
    const open = page.getByRole("link", { name: "Open in FlightMD" });
    await expect(open).toBeVisible({ timeout: 60_000 });
    await expect(open).toHaveAttribute("href", link);
    await expect(open).toHaveAttribute("target", "_blank");
    const rel = ((await open.getAttribute("rel")) ?? "").split(/\s+/);
    expect(rel).toEqual(expect.arrayContaining(["noopener", "noreferrer"]));
    await expect(open).toHaveAccessibleDescription("flightmd.example.com");
  });

  test("a link to another host is shown as text, never as a button", async ({ page, request }) => {
    test.setTimeout(180_000);
    const id = await completedAudit(request);
    await page.goto(`/w/view?m=flight-audit&a=${id}`);
    await expect(page.getByText(link, { exact: true })).toBeVisible({ timeout: 60_000 });
    await expect(page.getByRole("link", { name: "Open in FlightMD" })).toHaveCount(0);
  });

  test("a link from FlightMD opens the audit with the report link filled in, not yet run", async ({ page }) => {
    const report = "https://flightmd.example.com/report/abc-123";
    await page.goto(`/w/view?m=flight-audit&report_url=${encodeURIComponent(report)}`);
    await expect(page.getByLabel("FlightMD report link")).toHaveValue(report, { timeout: 60_000 });
    await expect(page.getByRole("tab", { name: /link/i, selected: true })).toBeVisible();
    // taken once, then dropped from the address
    await expect(page).not.toHaveURL(/report_url=/);
    await expect(page.getByLabel("FlightMD report link")).toHaveValue(report);
  });

  test("a share link from a local FlightMD can be audited once a FlightMD server is configured", async ({ page }) => {
    await page.route("**/api/meta/", async (route) => {
      const res = await route.fetch();
      const meta = (await res.json()) as Record<string, unknown>;
      await route.fulfill({ response: res, json: { ...meta, flightmd: { configured: true, url: "http://localhost:8100" } } });
    });
    const report = "http://localhost:3100/report/abc-123";
    await page.goto(`/w/view?m=flight-audit&report_url=${encodeURIComponent(report)}`);
    await expect(page.getByLabel("FlightMD report link")).toHaveValue(report, { timeout: 60_000 });
    await expect(page.getByRole("button", { name: "Audit flight", exact: true })).toBeEnabled();
    await expect(page.getByText("Use an https link, or a link to the configured FlightMD server.")).toHaveCount(0);
  });

  test("the API refuses a plain-http link to another computer", async ({ request }) => {
    const refused = await audit(request, await sampleReport(request), "http://flightmd.example.com/report/abc-123");
    expect(refused.status()).toBe(422);
    const body = (await refused.json()) as { error: { code: string; details: Record<string, unknown> } };
    expect(body.error.code).toBe("invalid_params");
    expect(body.error.details).toEqual({ field: "flightmd_url" });
  });
});
