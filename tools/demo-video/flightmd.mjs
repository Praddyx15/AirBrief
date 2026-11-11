/**
 * FlightMD and AirBrief on one flight: the airspace check and brief before it, the log review
 * after it, and the audit that ties them together.
 *
 *   node record.mjs --story flightmd [--draft]   → out/flightmd.mp4
 *
 * Needs both apps (README, "FlightMD and AirBrief together"): AirBrief with the zurich pack
 * and FLIGHTMD_API_URL set to FlightMD's API, FlightMD built with NEXT_PUBLIC_AIRBRIEF_URL set
 * to AirBrief. The flight is a real log from FlightMD's test corpus, downloaded once into
 * out/cache and checked against its SHA-256.
 */

import { createHash } from "node:crypto";
import { existsSync } from "node:fs";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { uploadFile } from "./lib/app.mjs";
import { kpi, openWorkspace, view } from "./scenes.mjs";

export const FIXTURES = {};
export const PACKS = ["zurich"];
// later scenes use what earlier ones made (the brief's hash, the report), so no --only
export const WHOLE = true;

// tests/corpus/manifest.json in FlightMD: a tiltrotor VTOL flown hardware-in-the-loop (SYS_HITL=1),
// so the position is PX4's simulated home in Zurich, not anyone's real field
export const FLIGHT = {
  file: "px4-vtol-tiltrotor-1.14.ulg",
  url: "https://cdn.logs.px4.io/f40c084b-189a-4935-b2ab-c68b750b866a.ulg",
  sha256: "0043237adc83669d86f07a5cd783a3001427bc93a735642a1949f7bd3e4b7264",
};

// the planned operation around the take-off point, a little bigger than the flown track
const AREA = {
  type: "FeatureCollection",
  features: [{
    type: "Feature",
    properties: { name: "Zurich test flight area" },
    geometry: { type: "Polygon", coordinates: [[[8.5438, 47.3932], [8.5532, 47.3932], [8.5532, 47.4025], [8.5438, 47.4025], [8.5438, 47.3932]]] },
  }],
};

// Switzerland's open category: 120 m above the ground, and the zones from FOCA's file (in the
// pack). No rings around airports: those are FOCA zones too.
const SWISS_RULES = {
  rules: [],
  default_zone: "green",
  default_ceiling_agl_m: 120,
  default_ceiling_exceeded_zone: "yellow",
  reference: "Open category in Switzerland: 120 m above the ground. Zones from FOCA's ED-269 file in the data pack.",
  distance_from: "perimeter",
  profile: "custom",
};

const run = { log: "", brief: "", audit: "" };

async function flightLog(cacheDir) {
  const dir = path.join(cacheDir, "..", "flights");
  const file = path.join(dir, FLIGHT.file);
  if (!existsSync(file)) {
    await mkdir(dir, { recursive: true });
    const res = await fetch(FLIGHT.url);
    if (!res.ok) throw new Error(`Downloading ${FLIGHT.url} failed (${res.status}).`);
    await writeFile(file, Buffer.from(await res.arrayBuffer()));
  }
  const sha = createHash("sha256").update(await readFile(file)).digest("hex");
  if (sha !== FLIGHT.sha256) throw new Error(`${file} does not match FlightMD's corpus manifest (sha256 ${sha}); delete it and run again.`);
  return file;
}

/**
 * Before recording: the zurich pack and the FlightMD link set up in AirBrief, FlightMD answering,
 * the flight log on disk and the planned area in AirBrief. Returns the dataset ids.
 */
export async function setup(config, { health, meta }) {
  if (!health.datapacks.includes("zurich")) throw new Error("The zurich data pack is not loaded (README, \"FlightMD and AirBrief together\").");
  if (!meta.flightmd?.configured) throw new Error("AirBrief's API has no FLIGHTMD_API_URL, so it may not fetch the report. Restart it with FLIGHTMD_API_URL=<FlightMD's API>.");
  const home = await fetch(new URL("/", config.flightmdUrl)).catch(() => null);
  if (!home?.ok) throw new Error(`FlightMD does not answer at ${config.flightmdUrl}. Start its API and web app first (README).`);
  run.log = await flightLog(config.cacheDir);
  return { area: await uploadFile(config.apiUrl, "zurich_flight_area.geojson", JSON.stringify(AREA)) };
}

/** The downloaded flight log, once `setup` has run. */
export const flightLogFile = () => run.log;

export async function useSwissRules(page) {
  await page.getByRole("combobox", { name: "Rules", exact: true }).click();
  await page.getByRole("option", { name: "Custom", exact: true }).click();
  await page.getByLabel("Custom rules (JSON)").fill(JSON.stringify(SWISS_RULES, null, 2));
}

/**
 * @param {{ dataNote: string, config: any }} info
 */
export function buildScenes({ dataNote, config }) {
  const fmd = (p) => new URL(p, config.flightmdUrl).toString();
  const repos = [config.flightmdRepoUrl, config.repoUrl].map((u) => u.replace(/^https?:\/\//, "").replace(/\/+$/, "")).join("  ·  ");
  return [
    {
      id: "hook",
      say: "Every flight raises questions before take-off, and again after landing.",
      tail: 0.8,
      prepare: async ({ s, page }) => {
        await s.goto(fmd("/"), page.getByRole("heading", { level: 1, name: "Find out why your drone flew badly." }));
      },
      run: async ({ s }) => {
        await s.hold(0.3);
        await s.list({
          title: "One flight",
          items: ["Before: may it fly there, and over whom?", "After: was the aircraft healthy?", "And did it fly the way it was planned?"],
          delayMs: 300,
          staggerMs: 700,
        });
      },
    },
    {
      id: "title",
      say: "AirBrief gathers the evidence before take-off. FlightMD reads the log after landing. Each one links to the other.",
      speak: "Air Brief gathers the evidence before take-off. Flight M D reads the log after landing. Each one links to the other.",
      thumbnail: true,
      tail: 0.8,
      prepare: async ({ s }) => {
        await s.card({});
      },
      run: async ({ s }) => {
        await s.card({
          wordmark: "FlightMD + AirBrief",
          title: "Before and after every flight.",
          subtitle: "Airspace, terrain and ground-risk evidence before take-off; the flight log reviewed after landing.",
          chips: ["Free and open source (MIT)", "Runs on your own machine", "No accounts"],
        });
      },
    },
    {
      id: "plan",
      say: "Before the flight, AirBrief checks the planned area in Zurich against the Swiss zone file and the terrain.",
      speak: "Before the flight, Air Brief checks the planned area in Zurich, against the Swiss zone file and the terrain.",
      prepare: async ({ s, page, ids }) => {
        await openWorkspace(s, page, view(ids, "area", "airspace"), page.getByRole("button", { name: "Check airspace", exact: true }));
        await useSwissRules(page);
        await page.getByRole("button", { name: "Check airspace", exact: true }).scrollIntoViewIfNeeded();
      },
      run: async ({ s, page }) => {
        const verdict = page.getByRole("status").filter({ hasText: /^Airspace: (Pass|Caution|Fail)/ }).first();
        await s.hold(0.3);
        await s.click(page.getByRole("button", { name: "Check airspace", exact: true }));
        await s.waitFor(verdict);
        await s.hold(0.4);
        await s.spotlight(verdict, "Indicative, with the reason spelled out");
        await s.hold(1.4);
        await s.spotlight(page.getByRole("region", { name: /^Zones \(\d+\)/ }), "Control zones from FOCA's official file");
      },
    },
    {
      id: "brief",
      say: "The checks go on a one-page brief, and the brief's hash travels with the flight.",
      prepare: async ({ s, page, ids }) => {
        await s.goto(`/w/report?d=${encodeURIComponent(ids.area)}&mode=brief`, page.getByRole("button", { name: "Create brief", exact: true }));
      },
      run: async ({ s, page }) => {
        await s.click(page.getByRole("button", { name: "Create brief", exact: true }));
        const preview = page.getByRole("region", { name: "Brief preview" });
        await s.waitFor(preview);
        const line = preview.locator("p").filter({ hasText: /^\s*Brief SHA-256 [0-9a-f]{64}/ }).first();
        run.brief = (await line.innerText()).match(/Brief SHA-256 ([0-9a-f]{64})/)[1];
        await line.evaluate((el) => el.scrollIntoView({ behavior: "smooth", block: "center" }));
        await s.hold(0.8);
        await s.spotlight(line, "The brief's fingerprint: SHA-256");
      },
    },
    {
      id: "upload",
      say: "After landing, the log goes into FlightMD, with that hash pasted in.",
      speak: "After landing, the log goes into Flight M D, with that hash pasted in.",
      prepare: async ({ s, page }) => {
        await s.goto(fmd("/"), page.getByRole("heading", { level: 1, name: "Find out why your drone flew badly." }));
      },
      run: async ({ s, page }) => {
        await s.hold(0.3);
        await s.click(page.getByText("Planned in AirBrief? Link the planning brief", { exact: true }));
        const field = page.getByLabel("Planning brief hash (SHA-256)");
        await s.click(field);
        await field.fill(run.brief);
        await s.spotlight(field, "The brief this flight was planned with");
        await s.hold(1.2);
        await s.clearSpotlight();
        const drop = page.getByText("Drop a flight log here, or click to choose one", { exact: true }).locator("..");
        await s.pickFile(drop, run.log, page.getByLabel("Flight log file"));
        await s.waitFor(page.getByRole("heading", { level: 1, name: FLIGHT.file, exact: true }), { factor: 8 });
      },
    },
    {
      id: "report",
      say: "FlightMD scores the aircraft's health, explains every finding, and keeps the brief it was planned with.",
      speak: "Flight M D scores the aircraft's health, explains every finding, and keeps the brief it was planned with.",
      run: async ({ s, page }) => {
        run.audit = await page.getByRole("link", { name: "Audit airspace in AirBrief" }).getAttribute("href");
        await s.hold(0.3);
        await s.spotlight(page.locator("dt", { hasText: "Planning brief" }).locator(".."), "The AirBrief brief, carried with the report");
        await s.hold(1.4);
        await s.spotlight(page.getByRole("figure", { name: /^Health score/ }), "A real PX4 tiltrotor log, scored out of 100");
        await s.hold(1.6);
        await s.spotlight(page.locator("#findings article").first(), "Each finding in plain words, with what to do");
      },
    },
    {
      id: "handoff",
      say: "One click sends the flight back to AirBrief.",
      speak: "One click sends the flight back to Air Brief.",
      tail: 0.4,
      run: async ({ s, page }) => {
        const button = page.getByRole("link", { name: "Audit airspace in AirBrief" });
        await button.scrollIntoViewIfNeeded();
        await s.hold(0.2);
        await s.spotlight(button, "Audit airspace in AirBrief");
        await s.hold(1.0);
        // it opens a new tab; the next scene loads the same address in this one
        const popup = page.context().waitForEvent("page", { timeout: 5000 }).catch(() => null);
        await s.click(button);
        (await popup)?.close().catch(() => {});
      },
    },
    {
      id: "audit",
      say: "AirBrief fetches the report and audits the track: height above the ground, the people under it, and the zones above it.",
      speak: "Air Brief fetches the report, and audits the track. Height above the ground, the people under it, and the zones above it.",
      prepare: async ({ s, page }) => {
        await s.goto(run.audit, page.getByRole("button", { name: "Audit flight", exact: true }));
        await s.tilesQuiet();
        await useSwissRules(page);
        await page.getByLabel("FlightMD report link").scrollIntoViewIfNeeded();
      },
      run: async ({ s, page }) => {
        await s.hold(0.3);
        await s.spotlight(page.getByLabel("FlightMD report link"), "Filled in by FlightMD");
        await s.hold(1.2);
        await s.clearSpotlight();
        const height = kpi(page, "Max height above ground");
        await s.click(page.getByRole("button", { name: "Audit flight", exact: true }));
        await s.waitFor(height);
        await s.hold(0.4);
        await s.spotlight(height, "Height above the terrain, not above take-off");
        await s.hold(1.4);
        await s.spotlight(kpi(page, "People overflown"), "People under the track, from WorldPop");
        await s.hold(1.4);
        await s.spotlight(page.getByText(/^Not counted, the flight never reached their heights/), "Under both control zones: they start at 120 m");
      },
    },
    {
      id: "links",
      say: "The audit names the brief the flight was planned with, and links straight back to the log.",
      run: async ({ s, page }) => {
        await s.hold(0.3);
        await s.spotlight(page.getByText("Planned with brief", { exact: true }).locator(".."), "The brief from before the flight");
        await s.hold(1.6);
        await s.spotlight(page.getByRole("link", { name: /Open in FlightMD/ }).first(), "And back to the log in FlightMD");
      },
    },
    {
      id: "end",
      say: "FlightMD and AirBrief are free and open source. Both are on GitHub.",
      speak: "Flight M D and Air Brief are free and open source. Both are on GitHub.",
      tail: 2.0,
      prepare: async ({ s }) => {
        await s.card({});
      },
      run: async ({ s }) => {
        await s.card({
          wordmark: "FlightMD + AirBrief",
          title: "Before and after every flight.",
          chips: ["FlightMD: flight log review", "AirBrief: airspace, terrain, ground risk", "MIT licence"],
          cta: repos,
          footnote:
            "Flight: a public PX4 log from FlightMD's test corpus, flown hardware-in-the-loop at PX4's simulated Zurich home. " +
            `${dataNote} Results are indicative and do not replace official authorisation.`,
          staggerMs: 90,
        });
      },
    },
  ];
}
