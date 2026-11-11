#!/usr/bin/env node
/**
 * The README's screenshots (docs/images/*.jpg), taken from the running app with the Bengaluru
 * pack, as for the videos (README.md, "Render"). Re-run it after a visible UI change.
 *
 *   node screenshots.mjs
 */

import { mkdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";
import { CONFIG } from "./config.mjs";
import { loadSample } from "./lib/app.mjs";
import { kpi, view } from "./scenes.mjs";

const out = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..", "docs", "images");
await mkdir(out, { recursive: true });

const ids = {
  survey: await loadSample(CONFIG.apiUrl, "bengaluru_survey"),
  gpx: await loadSample(CONFIG.apiUrl, "training_flight_gpx"),
};

// a real window off the screen: headless Chrome draws the map in software, too slowly
const browser = await chromium.launch({
  headless: false,
  args: ["--window-position=-4000,0", "--disable-backgrounding-occluded-windows", "--disable-renderer-backgrounding", "--hide-scrollbars"],
});
const context = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1.25, colorScheme: "dark", locale: "en-GB" });
await context.addInitScript(() => {
  localStorage.setItem("airbrief-settings", JSON.stringify({ state: { units: "metric", basemap: "openfreemap", choicesHintDismissed: true }, version: 1 }));
  localStorage.setItem("theme", "dark");
});
const page = await context.newPage();

async function run(url, button, done) {
  await page.goto(new URL(url, CONFIG.appUrl).toString());
  const start = page.getByRole("button", { name: button, exact: true });
  await start.waitFor({ timeout: 60_000 });
  await start.click();
  await done.waitFor({ timeout: 180_000 });
  // the "finished" toast lasts 3.5 s, and the legend would cover the map
  await page.locator("[data-sonner-toast]").first().waitFor({ state: "detached", timeout: 15_000 }).catch(() => {});
  const legend = page.getByRole("button", { name: "Legend", exact: true });
  if ((await legend.count()) > 0 && (await legend.first().getAttribute("aria-expanded")) === "true") await legend.first().click();
  await page.waitForLoadState("networkidle").catch(() => {});
  await page.waitForTimeout(1500);
}

async function shot(name) {
  await page.screenshot({ path: path.join(out, `${name}.jpg`), type: "jpeg", quality: 82 });
  console.log(`docs/images/${name}.jpg`);
}

await run(view(ids, "survey", "airspace"), "Check airspace", page.getByRole("region", { name: "Aerodrome bands", exact: true }));
await shot("airspace");
await run(view(ids, "survey", "ground-risk"), "Assess ground risk", kpi(page, "Intrinsic ground risk class"));
await shot("ground-risk");
await run(view(ids, "gpx", "flight-audit"), "Audit flight", kpi(page, "Max height above ground"));
await shot("flight-audit");

await page.goto(new URL(`/w/report?d=${encodeURIComponent(ids.survey)}&mode=brief`, CONFIG.appUrl).toString());
await page.getByRole("button", { name: "Create brief", exact: true }).click();
const preview = page.getByRole("region", { name: "Brief preview" });
await preview.waitFor({ timeout: 60_000 });
await preview.scrollIntoViewIfNeeded();
await page.waitForTimeout(1000);
await shot("brief");

await browser.close();
