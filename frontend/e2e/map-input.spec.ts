import { expect, test, type Page } from "@playwright/test";
import { openSample } from "./helpers";

/**
 * Map overlays and map input: additive selection with Shift / Ctrl+click, the map's tab stops,
 * and overlays (controls, legend, readout, attribution, scale bar) that never cover each other,
 * whatever room the panels and the results drawer leave the map.
 */

const PLOTS = "Farm plots (Shapefile, UTM 43N)";

/** The map has loaded once deck.gl is up (it then takes its canvas out of the tab order). */
async function waitForMap(page: Page): Promise<void> {
  await expect(page.locator("#map canvas.maplibregl-canvas")).toBeVisible();
  await expect(page.locator("#deckgl-overlay")).toHaveAttribute("tabindex", "-1", { timeout: 30_000 });
}

/** Hover over the map until the hover label has named every feature; returns a point on each. */
async function findFeatures(page: Page, names: readonly string[]): Promise<Record<string, [number, number]>> {
  const box = await page.locator("#map canvas.maplibregl-canvas").boundingBox();
  if (!box) throw new Error("The map canvas has no box");
  const label = page.locator("#map div.max-w-64"); // the pointer's hover label
  const hits: Record<string, [number, number]> = {};
  for (let fy = 0.08; fy < 0.95 && Object.keys(hits).length < names.length; fy += 0.05) {
    for (let fx = 0.04; fx < 0.96 && Object.keys(hits).length < names.length; fx += 0.04) {
      const x = box.x + box.width * fx;
      const y = box.y + box.height * fy;
      await page.mouse.move(x, y);
      await page.waitForTimeout(40); // deck.gl picks on the next frame
      if ((await label.count()) === 0) continue;
      const name = (await label.innerText()).split(" ·")[0]?.trim() ?? "";
      if (names.includes(name) && !hits[name]) hits[name] = [x, y];
    }
  }
  expect(Object.keys(hits).sort(), "every feature was found under the pointer").toEqual([...names].sort());
  return hits;
}

async function clickWith(page: Page, key: "Shift" | "Control", point: [number, number]): Promise<void> {
  await page.keyboard.down(key);
  await page.mouse.click(point[0], point[1]);
  await page.keyboard.up(key);
}

/** Problems with the overlay layout: boxes outside the map, overlapping boxes, a covered scale bar. */
async function overlayProblems(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    type Box = { name: string; left: number; top: number; right: number; bottom: number };
    const boxOf = (name: string, el: Element): Box => {
      const r = el.getBoundingClientRect();
      return { name, left: r.left, top: r.top, right: r.right, bottom: r.bottom };
    };
    const problems: string[] = [];
    const mapEl = document.querySelector("#map");
    if (!mapEl) return ["no #map"];
    const map = boxOf("map", mapEl);
    const boxes: Box[] = [];
    document.querySelectorAll("#map [data-map-overlay]").forEach((el) => {
      const b = boxOf(el.getAttribute("data-map-overlay") ?? "?", el);
      if (b.right - b.left > 0 && b.bottom - b.top > 0) boxes.push(b);
    });
    const scale = document.querySelector("#map .maplibregl-ctrl-scale");
    if (scale) boxes.push(boxOf("scale", scale));
    for (const b of boxes) {
      if (b.left < map.left - 1 || b.top < map.top - 1 || b.right > map.right + 1 || b.bottom > map.bottom + 1) {
        problems.push(`${b.name} sticks out of the map`);
      }
    }
    for (let i = 0; i < boxes.length; i++) {
      for (let j = i + 1; j < boxes.length; j++) {
        const a = boxes[i] as Box;
        const c = boxes[j] as Box;
        const w = Math.min(a.right, c.right) - Math.max(a.left, c.left);
        const h = Math.min(a.bottom, c.bottom) - Math.max(a.top, c.top);
        if (w > 1 && h > 1) problems.push(`${a.name} overlaps ${c.name} (${Math.round(w)}x${Math.round(h)})`);
      }
    }
    if (scale) {
      const r = scale.getBoundingClientRect();
      const y = r.top + r.height / 2;
      for (const x of [r.left + 3, (r.left + r.right) / 2, r.right - 3]) {
        const hit = document.elementFromPoint(x, y);
        if (!hit || !scale.contains(hit)) problems.push(`scale covered at x=${Math.round(x)} by ${hit?.tagName ?? "nothing"}`);
      }
    }
    return problems;
  });
}

async function expectOverlaysClear(page: Page): Promise<void> {
  await expect.poll(() => overlayProblems(page), { timeout: 15_000 }).toEqual([]);
}

test.describe("map input", () => {
  test("Shift+click and Ctrl+click add features to the selection", async ({ page }) => {
    await openSample(page, PLOTS);
    await waitForMap(page);
    const at = await findFeatures(page, ["Plot A", "Plot B", "Plot C"]);
    const selected = () => (new URL(page.url()).searchParams.get("f") ?? "").split(",").filter(Boolean).sort();

    await page.mouse.click(...at["Plot B"]);
    await expect.poll(selected).toEqual(["1"]);
    await clickWith(page, "Shift", at["Plot C"]);
    await expect.poll(selected).toEqual(["1", "2"]);
    await clickWith(page, "Control", at["Plot A"]);
    await expect.poll(selected).toEqual(["0", "1", "2"]);
    // A modified click on a selected feature takes it out again.
    await clickWith(page, "Shift", at["Plot C"]);
    await expect.poll(selected).toEqual(["0", "1"]);
  });

  test("the map is one tab stop, followed by the overlays in visual order", async ({ page }) => {
    await openSample(page, PLOTS);
    await waitForMap(page);
    await expect(page.locator("#deckgl-overlay")).toHaveAttribute("aria-hidden", "true");

    // Reading order, top row then bottom row: the plan toolbar (top left), then the map controls
    // (top right). With no plan step open, "New operation" is the toolbar's only tab stop.
    const map = page.locator("#map");
    await map.locator("canvas.maplibregl-canvas").focus();
    await page.keyboard.press("Tab");
    await expect(map.getByRole("button", { name: "New operation" })).toBeFocused();
    await page.keyboard.press("Tab");
    await expect(map.getByRole("button", { name: "Zoom in" })).toBeFocused();

    // DOM order is the tab order: plan toolbar (top left), controls (top right), legend (bottom
    // left), readout and attribution (bottom right). (The "basemap unavailable" notice, shown only
    // offline above the plan toolbar, is a status with no tab stop.)
    const overlays = await page.evaluate(() => {
      const frame = document.querySelector("#map")?.getBoundingClientRect();
      if (!frame) return [];
      return Array.from(document.querySelectorAll("#map [data-map-overlay]"), (el) => {
        const r = el.getBoundingClientRect();
        return {
          name: el.getAttribute("data-map-overlay") ?? "?",
          row: r.top + r.height / 2 < frame.top + frame.height / 2 ? 0 : 1,
          column: r.left + r.width / 2 < frame.left + frame.width / 2 ? 0 : 1,
          top: r.top,
        };
      }).filter((o) => o.name !== "notice");
    });
    const domOrder = overlays.map((o) => o.name);
    expect(domOrder).toEqual(["plan", "controls", "legend", "readout", "attribution"]);
    // ... which is the visual order: by row, then by column, then top to bottom within a corner.
    const visualOrder = [...overlays].sort((a, b) => a.row - b.row || a.column - b.column || a.top - b.top).map((o) => o.name);
    expect(visualOrder).toEqual(domOrder);
  });

  test("Reset north in 3D keeps the 3D view", async ({ page }) => {
    await openSample(page, PLOTS);
    await waitForMap(page);
    const view3d = page.getByRole("radio", { name: /3D map view/ });
    await view3d.click();
    await page.getByRole("button", { name: "Reset north and tilt to 50°" }).click();
    await expect(view3d).toHaveAttribute("data-state", "on");
  });
});

test.describe("map overlays on small maps", () => {
  test("1024 px: the legend starts collapsed and nothing overlaps", async ({ page }) => {
    await page.setViewportSize({ width: 1024, height: 768 });
    await openSample(page, PLOTS);
    await waitForMap(page);
    const map = page.locator("#map");
    // Rail, layers panel and inspector leave a map about 330 px wide.
    await expect(map.getByRole("button", { name: "Legend", exact: true })).toHaveAttribute("aria-expanded", "false");
    // The attribution is shown on load, then folds behind its (i) button.
    await expect(map.getByRole("button", { name: "Map attribution" })).toHaveAttribute("aria-expanded", "false", { timeout: 15_000 });
    await expectOverlaysClear(page);
    await map.getByRole("button", { name: "Zoom in" }).click({ trial: true });
    await map.getByRole("button", { name: "Keyboard shortcuts" }).click({ trial: true });
  });

  test("1280x720 with the results drawer open: controls stay clear of the readout", async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 720 });
    await openSample(page, PLOTS);
    await waitForMap(page);
    const skip = page.getByRole("link", { name: "Skip to results" });
    await skip.focus();
    await skip.press("Enter");
    await expect(page.getByRole("separator", { name: "Resize results drawer" })).toBeVisible();
    const map = page.locator("#map");
    // A short map starts with the legend collapsed.
    await expect(map.getByRole("button", { name: "Legend", exact: true })).toHaveAttribute("aria-expanded", "false");
    await expectOverlaysClear(page);
    // Not obscured: the trial click fails if anything covers the button.
    await map.getByRole("button", { name: "Keyboard shortcuts" }).click({ trial: true });
    await map.getByRole("radio", { name: "2D map view" }).click({ trial: true });
  });
});

test.describe("map overlays on a phone", () => {
  test.use({ viewport: { width: 375, height: 812 }, hasTouch: true, isMobile: true });

  test("expanded sheet: the controls fit the smaller map and the scale stays readable", async ({ page }) => {
    await page.goto("/w");
    await page.getByRole("button", { name: `Open sample ${PLOTS}`, exact: true }).click();
    await page.waitForURL(/\/w\/view/);
    await waitForMap(page);
    await page.getByRole("button", { name: "Expand the details sheet" }).click();
    const map = page.locator("#map");
    await expect(map.getByRole("button", { name: "Map attribution" })).toHaveAttribute("aria-expanded", "false", { timeout: 15_000 });
    await expectOverlaysClear(page);
    await map.getByRole("button", { name: "Zoom in" }).click({ trial: true });
    await map.getByRole("radio", { name: "2D map view" }).click({ trial: true });
    await map.getByRole("radio", { name: /3D map view/ }).click({ trial: true });
    // Touch-only devices have no keyboard to show shortcuts for.
    if (await page.evaluate(() => window.matchMedia("(hover: none) and (pointer: coarse)").matches)) {
      await expect(map.getByRole("button", { name: "Keyboard shortcuts" })).toHaveCount(0);
    }
  });
});
