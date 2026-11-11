/**
 * The teaser: a 30–40 s cut for a LinkedIn post.
 *
 * Same shape as scenes.mjs (`say`, `speak`, `prepare`, `run`, `thumbnail`), fewer and shorter
 * lines: the three questions, the promise, four proof shots from the real app, the stance
 * (evidence, never a go/no-go), and one next step.
 *
 *   node record.mjs --story teaser [--draft]   → out/teaser.mp4 (or out/teaser-draft.mp4)
 */

import { kpi, mapCanvas, modules, openWorkspace, startRun, steady, view } from "./scenes.mjs";

export const FIXTURES = {
  survey: "bengaluru_survey",
  plots: "plots_shapefile",
};
export const PACKS = ["bengaluru"];

/**
 * @param {{ cta: string, dataNote: string }} info
 */
export function buildScenes({ cta, dataNote }) {
  return [
    {
      id: "hook",
      say: "Before a drone takes off: may it fly here? Who is underneath? Can the pilot see it?",
      tail: 0.4,
      prepare: async ({ s, page, ids }) => {
        await openWorkspace(s, page, view(ids, "survey", "measure"), modules(page));
      },
      run: async ({ s }) => {
        await s.hold(0.3);
        await s.list({
          title: "Before take-off",
          items: ["May it fly here?", "Who is underneath?", "Can the pilot see it?"],
          delayMs: 1500,
          staggerMs: 1050,
        });
      },
    },
    {
      id: "title",
      say: "AirBrief: pre-flight evidence, from a pin on a map.",
      speak: "Air Brief. Pre-flight evidence, from a pin on a map.",
      thumbnail: true,
      tail: 0.6,
      prepare: async ({ s }) => {
        await s.goto("/");
        await s.card({});
      },
      run: async ({ s }) => {
        await s.card({
          wordmark: "AirBrief",
          title: "Pre-flight evidence, from a pin on a map.",
          subtitle: "Airspace, ground risk and line of sight for drones and eVTOL, on one brief.",
        });
      },
    },
    {
      id: "goto",
      say: "Type an airport, and its zone rings appear.",
      prepare: async ({ s, page }) => {
        await openWorkspace(s, page, "/w/view?m=airspace", page.getByRole("button", { name: "New operation", exact: true }));
      },
      run: async ({ s, page }) => {
        await page.keyboard.press("Control+k");
        const palette = page.getByRole("dialog", { name: "Command palette" });
        await s.waitFor(palette, { speedUp: false });
        await page.keyboard.type("VOBL", { delay: 90 });
        const place = palette.getByRole("option", { name: /VOBL · Kempegowda International Airport/ });
        await s.waitFor(place, { speedUp: false });
        await s.hold(0.2);
        await page.keyboard.press("Enter");
        await s.waitFor(page.getByRole("region", { name: "Go to result" }));
        const rings = page.getByRole("region", { name: "Context layers" }).getByRole("switch", { name: /^Aerodrome zone rings .*\d+ features?$/ });
        await s.waitFor(steady(rings, 1.2));
        await s.spotlight(mapCanvas(page), "Aerodrome zone rings around VOBL");
      },
    },
    {
      id: "airspace",
      say: "Every plot is checked against the DGCA 2021 bands.",
      speak: "Every plot is checked against the D G C A twenty twenty-one bands.",
      tail: 0.3,
      prepare: async ({ s, page, ids }) => {
        await openWorkspace(s, page, view(ids, "plots", "airspace"), page.getByRole("button", { name: "Check airspace", exact: true }));
        await page.getByRole("region", { name: /^Zones from layers/ }).scrollIntoViewIfNeeded();
      },
      run: async ({ s, page }) => {
        const bands = page.getByRole("region", { name: "Aerodrome bands", exact: true });
        await s.hold(0.2);
        await startRun(s, page, page.getByRole("button", { name: "Check airspace", exact: true }), bands);
        await s.waitFor(bands);
        await s.hold(0.3);
        await s.spotlight(bands, "DGCA 2021 band, its ceiling and the height flown");
      },
    },
    {
      id: "ground-risk",
      say: "Ground risk: the SORA volume, and who is underneath it.",
      speak: "Ground risk. The Sora volume, and who is underneath it.",
      tail: 0.3,
      prepare: async ({ s, page, ids }) => {
        await openWorkspace(s, page, view(ids, "survey", "ground-risk"), page.getByRole("button", { name: "Assess ground risk", exact: true }));
      },
      run: async ({ s, page }) => {
        const igrc = kpi(page, "Intrinsic ground risk class");
        await startRun(s, page, page.getByRole("button", { name: "Assess ground risk", exact: true }), igrc);
        await s.waitFor(igrc, { factor: 8 }); // the population count takes a few seconds
        await s.hold(0.3);
        await s.spotlight(mapCanvas(page), "SORA 2.5 operational volume over the population");
      },
    },
    {
      id: "brief",
      say: "Then it all goes on one A4 brief, with sources and hashes.",
      tail: 0.3,
      prepare: async ({ s, page, ids }) => {
        // the survey needs an airspace check of its own for the brief (the scene above checked the plots)
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
        await s.hidePointer();
        // sheet top just under the sticky header, so most of the page is in the picture
        await preview.getByRole("article").first().evaluate((el) => window.scrollBy({ top: el.getBoundingClientRect().top - 76, behavior: "smooth" }));
        await s.hold(0.8);
        await s.spotlight(preview.getByRole("article").first().locator("header"), "One A4 page, with its sources and hashes");
      },
    },
    {
      id: "stance",
      // no prepare: the brief from the scene above stays on screen
      say: "It never says go or no-go. It gathers the evidence; the pilot and the authority decide.",
      speak: "It never says go, or no-go. It gathers the evidence. The pilot and the authority decide.",
      tail: 0.3,
      run: async ({ s, page }) => {
        const notice = page.getByRole("region", { name: "Brief preview" }).getByRole("note").filter({ hasText: /^Evidence, not authorisation/ }).first();
        await s.hold(0.3);
        await s.spotlight(notice, "Evidence, not authorisation");
      },
    },
    {
      id: "end",
      say: "AirBrief is free and open source. Find it on GitHub.",
      speak: "Air Brief is free and open source. Find it on GitHub.",
      tail: 1.1,
      prepare: async ({ s }) => {
        await s.goto("/");
        await s.card({});
      },
      run: async ({ s }) => {
        await s.card({
          wordmark: "AirBrief",
          title: "Free and open source",
          subtitle: "MIT licence. Runs on your own machine, offline, with no account.",
          chips: ["Pairs with FlightMD"],
          cta,
          footnote: `${dataNote} Results are indicative and do not replace official authorisation.`,
        });
      },
    },
  ];
}
