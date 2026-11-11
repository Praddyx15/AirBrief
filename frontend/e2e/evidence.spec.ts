import { expect, test, type APIRequestContext } from "@playwright/test";
import { API_URL } from "./helpers";

/**
 * Data-pack integrity on the settings page, the brief's warning for an airspace run with
 * non-default safety parameters (threat-model AS-1), and the provenance line that says whether
 * the bundle is signed. Analyses are created through the API as the dataset's owner (as in
 * brief.spec.ts).
 */

interface Info {
  id: string;
  status: string;
  owner_token?: string | null;
}

interface MetaLike {
  datapacks: { name: string; integrity?: string }[];
  signing?: { key_id: string } | null;
}

const INTEGRITY_LABEL: Record<string, string> = {
  verified: "Files match their hashes",
  unverified: "No hashes to check",
  mismatch: "Files changed: not used",
};

async function meta(request: APIRequestContext): Promise<MetaLike> {
  const response = await request.get(`${API_URL}/api/meta/`);
  expect(response.ok()).toBe(true);
  return (await response.json()) as MetaLike;
}

test("settings show each data pack's integrity and whether bundles are signed", async ({ page, request }) => {
  const m = await meta(request);
  await page.goto("/settings");
  const table = page.getByRole("table", { name: "Installed data packs" });
  for (const pack of m.datapacks) {
    const row = table.getByRole("row").filter({ has: page.getByRole("rowheader", { name: pack.name, exact: true }) });
    await expect(row.getByText(INTEGRITY_LABEL[pack.integrity ?? "unverified"], { exact: true })).toBeVisible();
  }
  const signing = m.signing
    ? page.getByText(`Brief bundles from this server are signed (Ed25519, key ${m.signing.key_id})`, { exact: false })
    : page.getByText("Brief bundles from this server are not signed", { exact: false });
  await expect(signing).toBeVisible();
});

test("an airspace run without the data pack's zones is flagged in the brief", async ({ page, request }) => {
  const sample = await request.post(`${API_URL}/api/samples/bengaluru_survey/`);
  expect(sample.ok()).toBe(true);
  const file = (await sample.json()) as Info;
  await expect
    .poll(async () => ((await (await request.get(`${API_URL}/api/files/${file.id}/`)).json()) as Info).status, { timeout: 60_000 })
    .toBe("COMPLETED");
  const created = await request.post(`${API_URL}/api/analyses/`, {
    data: { kind: "airspace", dataset_id: file.id, params: { altitude_agl_m: 60, include_datapack_zones: false } },
    headers: file.owner_token ? { "X-Owner-Token": file.owner_token } : undefined,
  });
  expect(created.ok(), await created.text()).toBe(true);
  const analysis = (await created.json()) as Info;
  await expect
    .poll(async () => ((await (await request.get(`${API_URL}/api/analyses/${analysis.id}/`)).json()) as Info).status, { timeout: 90_000 })
    .toBe("COMPLETED");

  await page.goto(`/w/report?d=${file.id}&a=${analysis.id}&mode=brief`);
  const contents = page.getByRole("form", { name: "Brief contents" });
  await expect(contents.getByRole("checkbox", { name: "Airspace check" })).toBeChecked();
  await contents.getByRole("button", { name: "Create brief" }).click();
  const sheet = page.getByRole("region", { name: "Brief preview" }).getByRole("article");
  await expect(sheet).toBeVisible({ timeout: 60_000 });

  const checks = sheet.getByRole("list", { name: "Cross-checks" });
  await expect(checks.getByText(/ran with non-default safety parameters: data-pack zones left out \(include_datapack_zones: false\)/)).toBeVisible();
  await expect(
    sheet.getByRole("note").filter({ hasText: /^Run with non-default safety parameters: data-pack zones left out/ }),
  ).toBeVisible();

  const m = await meta(request);
  const signature = m.signing
    ? `Bundles from this instance are signed (key ${m.signing.key_id})`
    : "Not signed: these SHA-256 values detect accidental corruption, not deliberate edits";
  await expect(sheet.getByText(signature, { exact: false })).toBeVisible();
});
