import { expect, test, type Page } from "@playwright/test";
import { openSample } from "./helpers";

/**
 * The production server (`next start`) sends a Content-Security-Policy with a fresh script nonce
 * per response and no 'unsafe-inline' scripts, plus HSTS, and the app still runs under it:
 * Next's scripts, the theme script and the map.
 */

function scriptSrc(csp: string | undefined): string {
  return (
    (csp ?? "")
      .split(";")
      .map((d) => d.trim())
      .find((d) => d.startsWith("script-src ")) ?? ""
  );
}

async function watchCsp(page: Page): Promise<() => Promise<string[]>> {
  const fromConsole: string[] = [];
  page.on("console", (m) => {
    if (/Content Security Policy/i.test(m.text())) fromConsole.push(m.text());
  });
  await page.addInitScript(() => {
    const w = window as unknown as { __cspViolations: string[] };
    w.__cspViolations = [];
    document.addEventListener("securitypolicyviolation", (e) =>
      w.__cspViolations.push(`${e.violatedDirective} ${e.blockedURI}`),
    );
  });
  return async () => [
    ...fromConsole,
    ...(await page.evaluate(() => (window as unknown as { __cspViolations?: string[] }).__cspViolations ?? [])),
  ];
}

test("every response gets its own script nonce and no inline-script allowance", async ({ request }) => {
  const first = await request.get("/");
  const second = await request.get("/");
  const a = scriptSrc(first.headers()["content-security-policy"]);
  const b = scriptSrc(second.headers()["content-security-policy"]);

  expect(a).toMatch(/'nonce-[A-Za-z0-9+/_-]+={0,2}'/);
  expect(a).not.toContain("'unsafe-inline'");
  expect(b).not.toBe(a);
  expect(first.headers()["strict-transport-security"]).toBe("max-age=31536000; includeSubDomains");

  const nonce = /'nonce-([^']+)'/.exec(a)?.[1];
  const scripts = (await first.text()).match(/<script\b[^>]*>/g) ?? [];
  expect(scripts.length).toBeGreaterThan(0);
  for (const tag of scripts) expect(tag).toContain(`nonce="${nonce}"`);
});

test("pages run under the CSP with the theme applied", async ({ page }) => {
  const violations = await watchCsp(page);
  for (const path of ["/", "/w", "/settings"]) {
    await page.goto(path);
    await expect(page.locator("#main")).toBeAttached();
    await expect(page.locator("html")).toHaveClass(/\bdark\b/);
  }
  expect(await violations()).toEqual([]);
});

test("the light theme from storage is applied by the theme script", async ({ page }) => {
  const violations = await watchCsp(page);
  await page.addInitScript(() => window.localStorage.setItem("theme", "light"));
  await page.goto("/settings");
  await expect(page.locator("html")).toHaveClass(/\blight\b/);
  expect(await violations()).toEqual([]);
});

test("the workspace map loads under the CSP", async ({ page }) => {
  const violations = await watchCsp(page);
  await openSample(page, "Bengaluru survey");
  await expect(page.locator("#map canvas.maplibregl-canvas")).toBeVisible();
  await expect(page.locator("#deckgl-overlay")).toHaveAttribute("tabindex", "-1", { timeout: 30_000 });
  expect(await violations()).toEqual([]);
});
