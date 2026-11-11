import AxeBuilder from "@axe-core/playwright";
import { expect, type Page } from "@playwright/test";

export const API_URL = process.env.E2E_API_URL ?? "http://localhost:8000";

/** Fail on serious or critical WCAG 2.x A/AA violations. */
export async function expectNoSeriousA11yViolations(page: Page): Promise<void> {
  // Let entrance transitions (toasts, drawers) finish: mid-fade text would be measured at partial
  // opacity and fail contrast although the settled UI passes. Finite animations only (spinners loop).
  await page
    .waitForFunction(
      () =>
        document.getAnimations().every((a) => {
          const iterations = a.effect?.getComputedTiming().iterations;
          return a.playState !== "running" || iterations === Infinity;
        }),
      undefined,
      { timeout: 5_000 },
    )
    .catch(() => undefined);
  const results = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"])
    // The WebGL map canvas is not inspectable by axe; its content has a table alternative.
    .exclude("canvas")
    .analyze();
  const serious = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  const summary = serious.map((v) => `${v.id} (${v.impact}): ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`);
  expect(summary, `Accessibility violations:\n${summary.join("\n")}`).toEqual([]);
}

export async function openSample(page: Page, title: string): Promise<void> {
  await page.goto("/w");
  // exact: "50-drone show" must not also match "50-drone show (clean)" / "(CSV)".
  await page.getByRole("button", { name: `Open sample ${title}`, exact: true }).click();
  await page.waitForURL(/\/w\/view/);
  await expect(page.getByRole("navigation", { name: "Workspace modules" })).toBeVisible();
}

export async function hasHorizontalScroll(page: Page): Promise<boolean> {
  return page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
}
