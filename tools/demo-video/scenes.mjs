/**
 * The script of the demo video: one spoken line per scene, and the steps that show it.
 *
 * - `say`      caption text (also what is spoken, unless `speak` is set)
 * - `speak`    the same sentence spelled for the voice (abbreviations, numbers)
 * - `prepare`  runs before recording starts (page loads, so no loading states are filmed)
 * - `run`      runs while recording; the scene then holds until the voice ends + `tail`
 * - `thumbnail` marks the scene the thumbnail frame is taken from
 *
 * Story: one promise (know before you fly, from a pin or a file, for free), problem first, then
 * the interactions that prove it, then one next step. Scenes only use the built-in samples and the
 * loaded data pack; they create datasets and analyses on the local instance, nothing else.
 */

/** Built-in samples loaded through the API before recording: key → sample name. */
export const FIXTURES = {
  survey: "bengaluru_survey",
  show: "show_50_drones",
  gpx: "training_flight_gpx",
  plots: "plots_shapefile",
};

/** Data packs the scenes show, for the end card's credits. */
export const PACKS = ["bengaluru"];

export const modules = (page) => page.getByRole("navigation", { name: "Workspace modules", exact: true });
export const view = (ids, key, module) => `/w/view?d=${encodeURIComponent(ids[key])}&m=${module}`;
export const kpi = (page, label) => page.locator("dl").filter({ has: page.getByText(label, { exact: true }) }).first();
export const mapCanvas = (page) => page.locator("canvas.maplibregl-canvas").first();

/** A check for `s.waitFor`: true once `locator` has stayed visible for `seconds`. */
export function steady(locator, seconds) {
  let since = null;
  return async () => {
    if (!(await locator.isVisible())) since = null;
    else since ??= Date.now();
    return since !== null && Date.now() - since >= seconds * 1000;
  };
}

/**
 * Click a module's run button. Until the new job is listed, the results drawer opens on "No …
 * results yet" for a fraction of a second: that moment is cut, not filmed.
 */
export async function startRun(s, page, button, done) {
  await s.click(button, { pause: 0.1 });
  const listed = page.getByRole("region", { name: "Results" }).getByRole("progressbar").or(done).first();
  await s.waitFor(listed, { factor: 100 });
}

/** Load a workspace page off camera, with the map legend folded away (it covers the map). */
export async function openWorkspace(s, page, url, ready) {
  await s.goto(url, ready);
  const legend = page.getByRole("button", { name: "Legend", exact: true });
  if ((await legend.count()) > 0 && (await legend.first().getAttribute("aria-expanded")) === "true") {
    await legend.first().click();
  }
  await s.tilesQuiet();
}

/**
 * @param {{ cta: string, dataNote: string }} endCard
 * @returns {Array<{ id: string, say: string, speak?: string, tail?: number, thumbnail?: boolean,
 *   prepare?: (ctx: any) => Promise<void>, run: (ctx: any) => Promise<void> }>}
 */
export function buildScenes({ cta, dataNote }) {
  return [
    {
      id: "hook",
      say: "Before take-off, four questions decide whether you fly.",
      tail: 1.2,
      prepare: async ({ s, page, ids }) => {
        await openWorkspace(s, page, view(ids, "survey", "measure"), modules(page));
      },
      run: async ({ s }) => {
        await s.hold(0.4);
        await s.list({
          title: "Before take-off",
          items: ["May I fly here, and how high?", "Who is on the ground below?", "Can the pilot see it, and the radio reach it?", "How long will the job take?"],
          delayMs: 260,
          staggerMs: 480,
        });
      },
    },
    {
      id: "title",
      say: "AirBrief answers them in seconds: free, open source, and offline on your own machine.",
      speak: "Air Brief answers them in seconds. Free, open source, and offline, on your own machine.",
      thumbnail: true,
      tail: 1.0,
      prepare: async ({ s }) => {
        await s.goto("/");
        await s.card({});
      },
      run: async ({ s }) => {
        await s.card({
          wordmark: "AirBrief",
          title: "Know before you fly.",
          subtitle: "Pre-flight evidence for drones and air taxis, from a pin, a drawing or a file.",
          chips: ["Free and open source (MIT)", "Runs offline", "No accounts, no API keys"],
        });
      },
    },
    {
      id: "plan",
      say: "No file needed: type an airport and its zone rings appear. Or drop a pin, or draw the area.",
      speak: "No file needed. Type an airport, and its zone rings appear. Or drop a pin, or draw the area.",
      prepare: async ({ s, page }) => {
        await openWorkspace(s, page, "/w/view?m=airspace", page.getByRole("button", { name: "New operation", exact: true }));
      },
      run: async ({ s, page }) => {
        await s.hold(0.3);
        await page.keyboard.press("Control+k");
        const palette = page.getByRole("dialog", { name: "Command palette" });
        await s.waitFor(palette, { speedUp: false });
        await page.keyboard.type("VOBL", { delay: 140 });
        const place = palette.getByRole("option", { name: /VOBL · Kempegowda International Airport/ });
        await s.waitFor(place, { speedUp: false });
        await s.hold(0.4);
        await page.keyboard.press("Enter");
        await s.waitFor(page.getByRole("region", { name: "Go to result" }));
        const rings = page.getByRole("region", { name: "Context layers" }).getByRole("switch", { name: /^Aerodrome zone rings .*\d+ features?$/ });
        // the map refits around the growing legend and each new view reloads the rings for a moment
        await s.waitFor(steady(rings, 1.5));
        await s.hold(0.6);
        await s.spotlight(mapCanvas(page), "Aerodrome zone rings, from the data pack, before any file");
        await s.hold(1.6);
        await s.clearSpotlight();
        await s.spotlight(page.getByRole("button", { name: "New operation", exact: true }), "Pin, line, area, coordinates or paste");
      },
    },
    {
      id: "airspace",
      say: "Airspace applies the DGCA 2021 bands, your own zone files, terrain and obstacles, and explains its verdict.",
      speak: "Airspace applies the D G C A twenty twenty-one bands, your own zone files, terrain and obstacles, and explains its verdict.",
      prepare: async ({ s, page, ids }) => {
        await openWorkspace(s, page, view(ids, "plots", "airspace"), page.getByRole("button", { name: "Check airspace", exact: true }));
        // rules, zone files and the button in one view, so nothing scrolls on camera
        await page.getByRole("region", { name: /^Zones from layers/ }).scrollIntoViewIfNeeded();
      },
      run: async ({ s, page }) => {
        await s.hold(0.3);
        await s.spotlight(page.getByRole("combobox", { name: "Rules", exact: true }), "DGCA 2021 bands, measured from each airport's boundary");
        await s.hold(1.8);
        await s.spotlight(page.getByRole("region", { name: /^Zones from layers/ }), "Your own zone files, ED-269 or ED-318 too");
        await s.hold(1.2);
        await s.clearSpotlight();
        const verdict = page.getByRole("status").filter({ hasText: /^Airspace: (Pass|Caution|Fail)/ }).first();
        await startRun(s, page, page.getByRole("button", { name: "Check airspace", exact: true }), verdict);
        await s.waitFor(verdict);
        await s.hold(0.4);
        await s.spotlight(verdict, "Indicative only, with the reason spelled out");
        await s.hold(1.4);
        await s.spotlight(page.getByRole("region", { name: "Aerodrome bands", exact: true }), "The band's ceiling, and the height flown in it");
      },
    },
    {
      id: "batch",
      say: "Got many plots or towers? Check them all at once: one result per feature, ready to export.",
      prepare: async ({ s, page, ids }) => {
        await openWorkspace(s, page, view(ids, "plots", "airspace"), page.getByRole("button", { name: "Check airspace", exact: true }));
        await page.getByRole("region", { name: /^Zones from layers/ }).scrollIntoViewIfNeeded();
      },
      run: async ({ s, page }) => {
        const form = page.getByRole("form", { name: "Airspace check" });
        await s.hold(0.3);
        await s.click(form.getByRole("switch", { name: /^One result per feature/ }));
        const check = form.getByRole("button", { name: "Check airspace", exact: true });
        await check.evaluate((el) => el.scrollIntoView({ behavior: "smooth", block: "nearest" })); // the toggle's fields pushed it down
        await s.hold(0.5);
        const results = page.getByRole("region", { name: "Results" });
        const table = results.getByRole("table", { name: "One result per feature" });
        await startRun(s, page, check, table);
        await s.waitFor(table);
        await s.hold(0.4);
        await s.spotlight(mapCanvas(page), "Each plot coloured by its own result");
        await s.hold(1.3);
        await s.clearSpotlight();
        await s.spotlight(table, "A row per plot: result, band and ceiling");
        await s.hold(1.3);
        await s.spotlight(results.getByRole("button", { name: "Export" }).first(), "Ready to export: CSV, GeoJSON or KML");
      },
    },
    {
      id: "ground-risk",
      say: "Ground risk builds the SORA 2.5 operational volume and counts the people under it, as evidence.",
      speak: "Ground risk builds the Sora two point five operational volume, and counts the people under it, as evidence.",
      prepare: async ({ s, page, ids }) => {
        await openWorkspace(s, page, view(ids, "survey", "ground-risk"), page.getByRole("button", { name: "Assess ground risk", exact: true }));
      },
      run: async ({ s, page }) => {
        const igrc = kpi(page, "Intrinsic ground risk class");
        await startRun(s, page, page.getByRole("button", { name: "Assess ground risk", exact: true }), igrc);
        await s.waitFor(igrc);
        await s.hold(0.5);
        await s.spotlight(mapCanvas(page), "Flight geography, contingency volume and ground risk buffer");
        await s.hold(1.8);
        await s.clearSpotlight();
        await s.spotlight(igrc, "Evidence for SORA, not the final class");
      },
    },
    {
      id: "environment",
      say: "Environment adds daylight, the magnetic field and nearby power lines for your flight window.",
      prepare: async ({ s, page, ids }) => {
        await openWorkspace(s, page, view(ids, "survey", "environment"), page.getByRole("button", { name: "Check environment", exact: true }));
      },
      run: async ({ s, page }) => {
        const dusk = kpi(page, "Civil dusk");
        await startRun(s, page, page.getByRole("button", { name: "Check environment", exact: true }), dusk);
        await s.waitFor(dusk);
        await s.hold(0.5);
        await s.spotlight(dusk, "Sunrise, sunset and civil twilight in local time");
        await s.hold(1.8);
        await s.spotlight(kpi(page, "Nearest power line"), "A 220 kV line crosses the area");
      },
    },
    {
      id: "line-of-sight",
      say: "Line of sight shows what the pilot can see, and whether the radio clears the terrain.",
      prepare: async ({ s, page, ids }) => {
        await openWorkspace(s, page, view(ids, "survey", "line-of-sight"), page.getByRole("button", { name: "Check line of sight", exact: true }));
      },
      run: async ({ s, page }) => {
        const seen = kpi(page, "Area in visual line of sight");
        await startRun(s, page, page.getByRole("button", { name: "Check line of sight", exact: true }), seen);
        await s.waitFor(seen);
        await s.hold(0.5);
        await s.spotlight(seen, "Terrain viewshed from the pilot's eye");
        await s.hold(1.8);
        await s.spotlight(kpi(page, "Worst Fresnel clearance"), "Radio path clearance at 2.4 GHz");
      },
    },
    {
      id: "survey",
      say: "Survey and spraying estimates give resolution, flight time and batteries.",
      prepare: async ({ s, page, ids }) => {
        await openWorkspace(s, page, view(ids, "survey", "survey"), page.getByRole("button", { name: "Estimate survey", exact: true }));
      },
      run: async ({ s, page }) => {
        const gsd = kpi(page, "Ground sample distance");
        await startRun(s, page, page.getByRole("button", { name: "Estimate survey", exact: true }), gsd);
        await s.waitFor(gsd);
        await s.hold(0.5);
        await s.spotlight(gsd, "Centimetres per pixel, over the terrain");
        await s.hold(1.6);
        await s.spotlight(kpi(page, "Batteries"), "Batteries, flight time, GCPs and storage");
      },
    },
    {
      id: "shows",
      say: "Drone shows are checked pair by pair, frame by frame, down to the one conflict.",
      prepare: async ({ s, page, ids }) => {
        await openWorkspace(s, page, view(ids, "show", "shows"), page.getByRole("button", { name: "Validate show", exact: true }));
      },
      run: async ({ s, page }) => {
        const conflict = page.getByRole("row", { name: "Go to conflict between D07 and D32", exact: true });
        await startRun(s, page, page.getByRole("button", { name: "Validate show", exact: true }), conflict);
        await s.waitFor(conflict, { factor: 8 }); // checking 50 drones frame by frame takes a while
        await s.hold(0.4);
        await s.spotlight(conflict, "D07 and D32: 0.8 m apart, limit 1.5 m");
        await s.hold(1.2);
        // clicked under the spotlight: clearing it first brings the caption down over the row
        await s.click(conflict);
        await s.hold(0.4);
        await s.spotlight(mapCanvas(page), "The show jumps to that moment, both drones marked");
      },
    },
    {
      id: "flight-audit",
      say: "After the flight, a GPX or FlightMD track is audited against the ground and the training area.",
      speak: "After the flight, a G P X or Flight M D track is audited, against the ground and the training area.",
      prepare: async ({ s, page, ids }) => {
        await openWorkspace(s, page, view(ids, "gpx", "flight-audit"), page.getByRole("button", { name: "Audit flight", exact: true }));
      },
      run: async ({ s, page }) => {
        const height = kpi(page, "Max height above ground");
        await startRun(s, page, page.getByRole("button", { name: "Audit flight", exact: true }), height);
        await s.waitFor(height);
        await s.hold(0.5);
        await s.spotlight(height, "Above the 120 m limit for 33 s");
        await s.hold(1.4);
        await s.spotlight(mapCanvas(page), "Over the limit in red, outside the training area in amber");
      },
    },
    {
      id: "brief",
      say: "It all becomes a one-page evidence brief, with sources and hashes to reproduce it.",
      prepare: async ({ s, page, ids }) => {
        // the airspace scenes check the plots, so the survey gets its own check for the brief
        const check = page.getByRole("button", { name: "Check airspace", exact: true });
        await s.goto(view(ids, "survey", "airspace"), check);
        await check.click();
        await page.getByRole("group", { name: "Airspace summary", exact: true }).waitFor({ timeout: 180_000 });
        await s.goto(`/w/report?d=${encodeURIComponent(ids.survey)}&mode=brief`, page.getByRole("button", { name: "Create brief", exact: true }));
      },
      run: async ({ s, page }) => {
        await s.click(page.getByRole("button", { name: "Create brief", exact: true }));
        const preview = page.getByRole("region", { name: "Brief preview" });
        await s.waitFor(preview);
        await preview.scrollIntoViewIfNeeded();
        await s.hold(0.6);
        await s.spotlight(preview, "One page: evidence, not authorisation");
      },
    },
    {
      id: "end",
      say: "AirBrief is free and open source. Run it on your own machine today.",
      speak: "Air Brief is free and open source. Run it on your own machine today.",
      tail: 2.0,
      prepare: async ({ s }) => {
        await s.goto("/");
        await s.card({});
      },
      run: async ({ s }) => {
        await s.card({
          wordmark: "AirBrief",
          title: "Know before you fly.",
          chips: ["Measure", "Airspace", "Ground risk", "Environment", "Line of sight", "Survey and spraying", "eVTOL", "Drone shows", "Flight audit", "Evidence brief"],
          cta,
          footnote: `${dataNote} Results are indicative and do not replace official authorisation.`,
          staggerMs: 70,
        });
      },
    },
  ];
}
