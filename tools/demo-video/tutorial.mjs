/**
 * Getting started: the first two minutes with AirBrief, for someone who has never opened it.
 * Upload a file, read the measurements, run two checks, print the brief, and the no-file way in.
 *
 *   node record.mjs --story tutorial [--draft]   → out/tutorial.mp4
 *
 * Needs the API and web app (README) with the Bengaluru pack, and samples/modules/ from
 * `python -m seed.samples --dest samples/`.
 */

import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { kpi, mapCanvas, modules, openWorkspace, startRun, steady } from "./scenes.mjs";

export const FIXTURES = {};
export const PACKS = ["bengaluru"];
// the upload scene makes the dataset every later scene works on
export const WHOLE = true;

const SURVEY = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..", "samples", "modules", "bengaluru_survey.kml");

export async function setup() {
  if (!existsSync(SURVEY)) throw new Error(`${SURVEY} is missing; run "python -m seed.samples --dest samples/" first.`);
  return {};
}

const moduleLink = (page, name) => modules(page).getByRole("link", { name, exact: true });

/**
 * @param {{ cta: string, dataNote: string }} info
 */
export function buildScenes({ cta, dataNote }) {
  return [
    {
      id: "intro",
      say: "This is AirBrief. In the next two minutes: load a file, check the airspace and the ground below, and print a one-page brief.",
      speak: "This is Air Brief. In the next two minutes: load a file, check the airspace and the ground below, and print a one-page brief.",
      thumbnail: true,
      tail: 0.8,
      prepare: async ({ s }) => {
        await s.goto("/");
        await s.card({});
      },
      run: async ({ s }) => {
        await s.card({
          wordmark: "AirBrief",
          title: "Getting started",
          subtitle: "Load a file, check the airspace and the ground below, print a brief.",
          chips: ["1  Load", "2  Check", "3  Brief"],
          staggerMs: 300,
        });
      },
    },
    {
      id: "upload",
      say: "Start on the workspace page and drop in a KML, Shapefile, GeoJSON, GPX or CSV. Here, a survey area near Bengaluru.",
      prepare: async ({ s, page }) => {
        await s.goto("/w", page.getByRole("heading", { name: "Open a dataset" }));
      },
      run: async ({ s, page }) => {
        const browse = page.getByRole("button", { name: "Browse…" });
        await s.hold(0.3);
        await s.spotlight(browse.locator(".."), "Drag a file here, or browse for one");
        await s.hold(1.6);
        await s.clearSpotlight();
        await s.pickFile(browse, SURVEY, page.getByLabel("Choose a file to upload"));
        await s.waitFor(modules(page));
      },
    },
    {
      id: "measure",
      say: "AirBrief reads every feature and measures it. The map and the table always show the same thing.",
      prepare: async ({ s }) => {
        await s.tilesQuiet();
      },
      run: async ({ s, page }) => {
        await s.hold(0.3);
        await s.spotlight(kpi(page, "Area"), "Measured on the ground, not on the map");
        await s.hold(1.6);
        await s.spotlight(mapCanvas(page), "The same features on the map");
      },
    },
    {
      id: "airspace",
      say: "Pick a check on the left. Airspace looks at the airport zones around the area, at the height you plan to fly, and says why.",
      speak: "Pick a check on the left. Airspace looks at the airport zones around the area, at the height you plan to fly, and says why.",
      run: async ({ s, page }) => {
        await s.clearSpotlight();
        await s.hold(0.3);
        await s.click(moduleLink(page, "Airspace"));
        const check = page.getByRole("button", { name: "Check airspace", exact: true });
        await s.waitFor(check, { speedUp: false });
        await s.hold(0.6);
        const verdict = page.getByRole("status").filter({ hasText: /^Airspace: (Pass|Caution|Fail)/ }).first();
        await startRun(s, page, check, verdict);
        await s.waitFor(verdict);
        await s.hold(0.4);
        await s.spotlight(verdict, "The verdict, with its reason");
        await s.hold(1.4);
        await s.spotlight(page.getByRole("region", { name: "Aerodrome bands", exact: true }), "Each airport band, its ceiling and your height");
      },
    },
    {
      id: "ground-risk",
      say: "Ground risk draws the operational volume around the flight and counts the people underneath.",
      run: async ({ s, page }) => {
        await s.clearSpotlight();
        await s.hold(0.3);
        await s.click(moduleLink(page, "Ground risk"));
        const assess = page.getByRole("button", { name: "Assess ground risk", exact: true });
        await s.waitFor(assess, { speedUp: false });
        await s.hold(0.4);
        const igrc = kpi(page, "Intrinsic ground risk class");
        await startRun(s, page, assess, igrc);
        await s.waitFor(igrc, { factor: 8 });
        await s.hold(0.5);
        await s.spotlight(mapCanvas(page), "Flight area, contingency and buffer, over the population");
      },
    },
    {
      id: "brief",
      say: "When you're done: Export, then Create brief. Every result goes on one A4 page with its sources, ready to print or share.",
      run: async ({ s, page }) => {
        await s.clearSpotlight();
        await s.hold(0.3);
        await s.click(page.getByRole("button", { name: "Export", exact: true }).first());
        const link = page.getByRole("link", { name: /Create brief/ });
        await s.waitFor(link, { speedUp: false });
        await s.hold(0.5);
        await s.click(link);
        const create = page.getByRole("button", { name: "Create brief", exact: true });
        await s.waitFor(create);
        await s.hold(0.6);
        await s.click(create);
        const preview = page.getByRole("region", { name: "Brief preview" });
        await s.waitFor(preview);
        await preview.scrollIntoViewIfNeeded();
        await s.hold(0.6);
        await s.spotlight(preview, "One page: evidence, never a permission");
      },
    },
    {
      id: "no-file",
      say: "No file? Press Control K, type an airport, and start from a pin or a drawing instead.",
      speak: "No file? Press Control K, type an airport, and start from a pin, or a drawing, instead.",
      prepare: async ({ s, page }) => {
        await openWorkspace(s, page, "/w/view?m=airspace", page.getByRole("button", { name: "New operation", exact: true }));
      },
      run: async ({ s, page }) => {
        await s.hold(0.3);
        await page.keyboard.press("Control+k");
        const palette = page.getByRole("dialog", { name: "Command palette" });
        await s.waitFor(palette, { speedUp: false });
        await page.keyboard.type("VOBL", { delay: 140 });
        await s.waitFor(palette.getByRole("option", { name: /VOBL · Kempegowda International Airport/ }), { speedUp: false });
        await s.hold(0.4);
        await page.keyboard.press("Enter");
        const rings = page.getByRole("region", { name: "Context layers" }).getByRole("switch", { name: /^Aerodrome zone rings .*\d+ features?$/ });
        await s.waitFor(steady(rings, 1.5));
        await s.hold(0.4);
        await s.spotlight(page.getByRole("button", { name: "New operation", exact: true }), "Pin, line, area, coordinates or paste");
      },
    },
    {
      id: "end",
      say: "That's the basics. There are nine checks in all; try them on the samples, then on your own data.",
      tail: 2.0,
      prepare: async ({ s }) => {
        await s.goto("/");
        await s.card({});
      },
      run: async ({ s }) => {
        await s.card({
          wordmark: "AirBrief",
          title: "That's the basics.",
          subtitle: "Also: line of sight, environment, survey and spraying, eVTOL, drone shows and flight audits.",
          cta,
          footnote: `${dataNote} Results are indicative and do not replace official authorisation.`,
        });
      },
    },
  ];
}
