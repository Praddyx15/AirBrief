/**
 * The feature announcement: FlightMD and AirBrief link both ways, shown click by click.
 *
 *   node record.mjs --story rollout [--draft] [--format 4x5]   → out/rollout.mp4
 *
 * AirBrief → FlightMD: the brief's hash goes in with the log, and the audit's "Open in FlightMD"
 * lands on the report. FlightMD → AirBrief: "Audit airspace in AirBrief" opens the audit with
 * the report's link filled in. Same apps, log and pack as flightmd.mjs (see its header).
 */

import { FLIGHT, flightLogFile, setup as flightmdSetup, useSwissRules } from "./flightmd.mjs";
import { kpi, openWorkspace, view } from "./scenes.mjs";

export const FIXTURES = {};
export const PACKS = ["zurich"];
export const WHOLE = true;

const run = { log: "", brief: "", audit: "", back: "" };

export async function setup(config, info) {
  const ids = await flightmdSetup(config, info);
  run.log = flightLogFile();
  return ids;
}

const fmdHome = (page) => page.getByRole("heading", { level: 1, name: "Find out why your drone flew badly." });

/**
 * @param {{ dataNote: string, config: any }} info
 */
export function buildScenes({ dataNote, config }) {
  const fmd = (p) => new URL(p, config.flightmdUrl).toString();
  const repos = [config.flightmdRepoUrl, config.repoUrl].map((u) => u.replace(/^https?:\/\//, "").replace(/\/+$/, "")).join("  ·  ");
  return [
    {
      id: "title",
      say: "The checks before a flight and the log after it used to live apart. FlightMD and AirBrief now link both ways.",
      speak: "The checks before a flight, and the log after it, used to live apart. Flight M D and Air Brief now link both ways.",
      thumbnail: true,
      tail: 0.8,
      prepare: async ({ s, page }) => {
        await s.goto(fmd("/"), fmdHome(page));
        await s.card({});
      },
      run: async ({ s }) => {
        await s.card({
          wordmark: "FlightMD + AirBrief",
          title: "Now linked both ways.",
          subtitle: "The brief from before the flight travels with the log, and every report opens in AirBrief's flight audit.",
          chips: ["AirBrief → FlightMD", "FlightMD → AirBrief", "Free and open source"],
        });
      },
    },
    {
      id: "brief",
      say: "In AirBrief, every pre-flight brief has a SHA-256 hash.",
      speak: "In Air Brief, every pre-flight brief has a S H A two fifty-six hash.",
      prepare: async ({ s, page, ids }) => {
        // the brief needs a check to report: the airspace check of the planned area, off camera
        const check = page.getByRole("button", { name: "Check airspace", exact: true });
        await openWorkspace(s, page, view(ids, "area", "airspace"), check);
        await useSwissRules(page);
        await check.click();
        await page.getByRole("group", { name: "Airspace summary", exact: true }).waitFor({ timeout: 180_000 });
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
        await s.spotlight(line, "The brief's fingerprint");
      },
    },
    {
      id: "paste",
      say: "Paste it into FlightMD when you upload the flight log.",
      speak: "Paste it into Flight M D when you upload the flight log.",
      prepare: async ({ s, page }) => {
        await s.goto(fmd("/"), fmdHome(page));
      },
      run: async ({ s, page }) => {
        await s.hold(0.3);
        await s.click(page.getByText("Planned in AirBrief? Link the planning brief", { exact: true }));
        const field = page.getByLabel("Planning brief hash (SHA-256)");
        await s.click(field);
        await field.fill(run.brief);
        await s.spotlight(field, "New: link the planning brief");
        await s.hold(1.2);
        await s.clearSpotlight();
        const drop = page.getByText("Drop a flight log here, or click to choose one", { exact: true }).locator("..");
        await s.pickFile(drop, run.log, page.getByLabel("Flight log file"));
        await s.waitFor(page.getByRole("heading", { level: 1, name: FLIGHT.file, exact: true }), { factor: 8 });
      },
    },
    {
      id: "report",
      say: "The report keeps the brief, so the flight traces back to its plan.",
      run: async ({ s, page }) => {
        await s.hold(0.3);
        await s.spotlight(page.locator("dt", { hasText: "Planning brief" }).locator(".."), "The brief this flight was planned with");
      },
    },
    {
      id: "to-airbrief",
      say: "Going the other way, any report with a GPS track has a new button: Audit airspace in AirBrief.",
      speak: "Going the other way, any report with a GPS track has a new button. Audit airspace in Air Brief.",
      tail: 0.4,
      run: async ({ s, page }) => {
        const button = page.getByRole("link", { name: "Audit airspace in AirBrief" });
        run.audit = await button.getAttribute("href");
        await s.hold(0.3);
        await s.spotlight(button, "New in FlightMD");
        await s.hold(1.4);
        // it opens a new tab; the next scene loads the same address in this one
        const popup = page.context().waitForEvent("page", { timeout: 5000 }).catch(() => null);
        await s.click(button);
        (await popup)?.close().catch(() => {});
      },
    },
    {
      id: "audit",
      say: "AirBrief opens with the report's link filled in. One click audits the flight against the terrain, the zones and the people below.",
      speak: "Air Brief opens with the report's link filled in. One click audits the flight, against the terrain, the zones, and the people below.",
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
        await s.spotlight(page.getByRole("status").filter({ hasText: /^Flight audit: / }).first(), "The audit's verdict, with the reason");
        await s.hold(1.4);
        await s.spotlight(height, "Height above the terrain, along the whole track");
      },
    },
    {
      id: "planned",
      say: "The audit names the brief the flight was planned with.",
      speak: "The audit names the brief the flight was planned with.",
      run: async ({ s, page }) => {
        await s.hold(0.3);
        await s.spotlight(page.getByText("Planned with brief", { exact: true }).locator(".."), "The same hash, recognised");
      },
    },
    {
      id: "back",
      say: "And Open in FlightMD goes straight back to the report.",
      speak: "And, Open in Flight M D, goes straight back to the report.",
      tail: 0.4,
      run: async ({ s, page }) => {
        const open = page.getByRole("link", { name: /Open in FlightMD/ }).first();
        run.back = await open.getAttribute("href");
        await s.hold(0.3);
        await s.spotlight(open, "Back to FlightMD");
        await s.hold(1.0);
        const popup = page.context().waitForEvent("page", { timeout: 5000 }).catch(() => null);
        await s.click(open);
        (await popup)?.close().catch(() => {});
      },
    },
    {
      id: "landed",
      say: "Same flight, same report: two tools, one trail of evidence.",
      prepare: async ({ s, page }) => {
        await s.goto(run.back, page.getByRole("heading", { level: 1, name: FLIGHT.file, exact: true }));
      },
      run: async ({ s, page }) => {
        await s.hold(0.4);
        await s.spotlight(page.getByRole("heading", { level: 1, name: FLIGHT.file, exact: true }).locator(".."), "The report the audit came from");
      },
    },
    {
      id: "setup",
      say: "To turn it on, give each app the other's address.",
      speak: "To turn it on, give each app the other's address.",
      tail: 0.8,
      prepare: async ({ s }) => {
        await s.card({});
      },
      run: async ({ s }) => {
        await s.card({
          wordmark: "Turn it on",
          title: "Give each app the other's address.",
          chips: ["FlightMD: NEXT_PUBLIC_AIRBRIEF_URL", "AirBrief: FLIGHTMD_API_URL", "AirBrief Settings: FlightMD address"],
          staggerMs: 300,
        });
      },
    },
    {
      id: "end",
      say: "Both are free and open source, on GitHub.",
      tail: 2.0,
      prepare: async ({ s }) => {
        await s.card({});
      },
      run: async ({ s }) => {
        await s.card({
          wordmark: "FlightMD + AirBrief",
          title: "Now linked both ways.",
          cta: repos,
          footnote:
            "Flight: a public PX4 log from FlightMD's test corpus, flown hardware-in-the-loop at PX4's simulated Zurich home. " +
            `${dataNote} Results are indicative and do not replace official authorisation.`,
        });
      },
    },
  ];
}
