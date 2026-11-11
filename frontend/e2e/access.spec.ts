import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import { API_URL, openSample } from "./helpers";

/**
 * Anyone with a dataset's link can run an analysis on it, but only runs sent with the dataset's
 * owner token are the owner's evidence. Someone else's run is labelled "Added by another
 * visitor" in the History tab and in the brief, and the brief never ticks it by default; a
 * visitor's own runs are not labelled for that visitor.
 *
 * The other visitor's analyses are created through the API without a token (`request.post`).
 */

const VISITOR = "Added by another visitor";

interface Info {
  id: string;
  status: string;
  owner_token?: string | null;
  by_dataset_owner?: boolean;
}

async function waitForAnalysis(request: APIRequestContext, id: string): Promise<void> {
  await expect
    .poll(async () => ((await (await request.get(`${API_URL}/api/analyses/${id}/`)).json()) as Info).status, { timeout: 90_000 })
    .toBe("COMPLETED");
}

/** An airspace check through the API; `token` is the dataset's owner token, if any. */
async function runAirspace(request: APIRequestContext, datasetId: string, params: Record<string, unknown>, token?: string): Promise<Info> {
  const response = await request.post(`${API_URL}/api/analyses/`, {
    data: { kind: "airspace", dataset_id: datasetId, params },
    headers: token ? { "X-Owner-Token": token } : undefined,
  });
  expect(response.ok(), await response.text()).toBe(true);
  const info = (await response.json()) as Info;
  await waitForAnalysis(request, info.id);
  return info;
}

/** The dataset's owner token, as this browser stored it when it opened the sample. */
async function storedOwnerToken(page: Page, datasetId: string): Promise<string> {
  const token = await page.evaluate((id) => {
    const raw = window.localStorage.getItem("airbrief-history");
    const state = raw ? (JSON.parse(raw) as { state?: { ownerTokens?: Record<string, string> } }).state : undefined;
    return state?.ownerTokens?.[id] ?? null;
  }, datasetId);
  expect(token, "the browser keeps the owner token of a dataset it opened").toBeTruthy();
  return token as string;
}

const results = (page: Page) => page.getByRole("region", { name: "Results" });
const briefForm = (page: Page) => page.getByRole("form", { name: "Brief contents" });

test.describe("analyses added by other visitors", () => {
  test("the owner sees another visitor's run labelled, and the brief keeps the owner's own run ticked", async ({ page, request }) => {
    await openSample(page, "Bengaluru survey");
    const datasetId = new URL(page.url()).searchParams.get("d");
    expect(datasetId).toBeTruthy();
    const token = await storedOwnerToken(page, datasetId as string);

    const owner = await runAirspace(request, datasetId as string, { altitude_agl_m: 120 }, token);
    // Newer, same kind, data-pack zones switched off: the run an attacker would want ticked.
    const visitor = await runAirspace(request, datasetId as string, { altitude_agl_m: 120, include_datapack_zones: false });
    expect(owner.by_dataset_owner).toBe(true);
    expect(visitor.by_dataset_owner).toBe(false);

    await page.reload();
    await results(page).getByRole("tab", { name: "History" }).click();
    await expect(results(page).getByRole("row", { name: /added by another visitor/ })).toHaveCount(1);
    await expect(results(page).getByText(VISITOR, { exact: true })).toHaveCount(1);
    await expect(results(page).getByRole("row", { name: /^Open Airspace check/ })).toHaveCount(2);

    await page.goto(`/w/report?mode=brief&d=${datasetId}`);
    const form = briefForm(page);
    const theirs = form.getByRole("listitem").filter({ hasText: VISITOR }).getByRole("checkbox", { name: "Airspace check" });
    const ours = form.getByRole("listitem").filter({ hasNotText: VISITOR }).getByRole("checkbox", { name: "Airspace check" });
    await expect(ours).toBeChecked();
    await expect(theirs).not.toBeChecked();
    await expect(theirs).toHaveAccessibleDescription(new RegExp(VISITOR));
    await expect(form.getByText(/They are not ticked by\s+default/)).toBeVisible();

    // Still selectable by hand.
    await theirs.check();
    await expect(theirs).toBeChecked();
  });

  test("a visitor's own run on a shared dataset is not labelled for them and is ticked in their brief", async ({ page, request }) => {
    // A dataset this browser did not create (no owner token here), with another visitor's run on it.
    const created = await request.post(`${API_URL}/api/samples/bengaluru_survey/`);
    expect(created.ok()).toBe(true);
    const datasetId = ((await created.json()) as Info).id;
    await expect
      .poll(async () => ((await (await request.get(`${API_URL}/api/files/${datasetId}/`)).json()) as Info).status, { timeout: 60_000 })
      .toBe("COMPLETED");
    await runAirspace(request, datasetId, { altitude_agl_m: 120, include_datapack_zones: false });

    await page.goto(`/w/view?d=${datasetId}&m=airspace`);
    await page.getByRole("button", { name: "Check airspace" }).click();
    await expect(page.getByText(/Airspace: (Pass|Caution|Fail)/)).toBeVisible({ timeout: 90_000 });

    await results(page).getByRole("tab", { name: "History" }).click();
    await expect(results(page).getByRole("row", { name: /^Open Airspace check/ })).toHaveCount(2);
    await expect(results(page).getByRole("row", { name: /added by another visitor/ })).toHaveCount(1);

    await page.goto(`/w/report?mode=brief&d=${datasetId}`);
    const form = briefForm(page);
    await expect(form.getByRole("listitem").filter({ hasNotText: VISITOR }).getByRole("checkbox", { name: "Airspace check" })).toBeChecked();
    await expect(form.getByRole("listitem").filter({ hasText: VISITOR }).getByRole("checkbox", { name: "Airspace check" })).not.toBeChecked();
  });
});
