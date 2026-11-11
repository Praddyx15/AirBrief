/**
 * Colour helpers for UI, charts and deck.gl layers (PLAN §7 "Map colours").
 *
 * - Zones: green / yellow / red, ALWAYS combined with a hatch pattern and a text label.
 * - Risk (iGRC 1–10): ordinal ramp, `riskColor(level)`.
 * - Continuous rasters/scores: viridis, `viridis(t)`. Diverging data: `rdbu(t)`.
 * - Status: `--ok`, `--warn`, `--danger` (+ icon + label), see `toneForVerdict` etc.
 *
 * deck.gl wants `[r, g, b, a]` arrays with 0–255 channels: everything here can return that.
 */

import type { AglBand, AnalysisStatus, FileStatus, FlyabilityStatus, MeasurementStatus, Severity, Verdict, ZoneKind } from "./types";

export type RGB = [number, number, number];
export type RGBA = [number, number, number, number];

/** "#3b82f6" / "#38f" / "#3b82f680" → [r, g, b, a] (alpha 0–255). Invalid input → opaque grey. */
export function hexToRgba(hex: string, alpha?: number): RGBA {
  let h = hex.trim().replace(/^#/, "");
  if (h.length === 3 || h.length === 4) h = h.split("").map((c) => c + c).join("");
  if (!/^[0-9a-fA-F]{6}([0-9a-fA-F]{2})?$/.test(h)) return [128, 128, 128, alpha ?? 255];
  const r = parseInt(h.slice(0, 2), 16);
  const g = parseInt(h.slice(2, 4), 16);
  const b = parseInt(h.slice(4, 6), 16);
  const a = h.length === 8 ? parseInt(h.slice(6, 8), 16) : 255;
  return [r, g, b, alpha ?? a];
}

export function rgbToHex([r, g, b]: RGB | RGBA): string {
  const to = (v: number) => Math.round(Math.min(255, Math.max(0, v))).toString(16).padStart(2, "0");
  return `#${to(r)}${to(g)}${to(b)}`;
}

/** Return a copy with a different alpha (0–255). */
export function withAlpha(color: RGB | RGBA, alpha: number): RGBA {
  return [color[0], color[1], color[2], Math.round(Math.min(255, Math.max(0, alpha)))];
}

/** CSS `rgb()` string for charts (recharts) and inline SVG. */
export function rgbaCss(color: RGB | RGBA, alpha01?: number): string {
  const a = alpha01 ?? (color.length === 4 ? color[3] / 255 : 1);
  return `rgb(${color[0]} ${color[1]} ${color[2]} / ${a})`;
}

function lerp(a: number, b: number, t: number): number {
  return a + (b - a) * t;
}

function interpolateStops(stops: readonly RGB[], t: number): RGB {
  const x = Number.isFinite(t) ? Math.min(1, Math.max(0, t)) : 0;
  const scaled = x * (stops.length - 1);
  const i = Math.min(stops.length - 2, Math.floor(scaled));
  const f = scaled - i;
  const a = stops[i] as RGB;
  const b = stops[i + 1] as RGB;
  return [Math.round(lerp(a[0], b[0], f)), Math.round(lerp(a[1], b[1], f)), Math.round(lerp(a[2], b[2], f))];
}

/** Semantic token names defined in app/globals.css. */
export type TokenName =
  | "bg"
  | "surface"
  | "elevated"
  | "border"
  | "text"
  | "text-muted"
  | "text-subtle"
  | "accent"
  | "accent-solid"
  | "accent-text"
  | "on-accent"
  | "warn"
  | "danger"
  | "danger-solid"
  | "ok"
  | "zone-green"
  | "zone-yellow"
  | "zone-red";

/** Fallbacks (dark theme) used during SSR or before styles load. */
const TOKEN_FALLBACK: Record<TokenName, string> = {
  bg: "#0b0e11",
  surface: "#12161b",
  elevated: "#1a2027",
  border: "#2a323c",
  text: "#e6eaee",
  "text-muted": "#9aa5b1",
  "text-subtle": "#5c6773",
  accent: "#3b82f6",
  "accent-solid": "#2563eb",
  "accent-text": "#60a5fa",
  "on-accent": "#ffffff",
  warn: "#f5a524",
  danger: "#f79a9d",
  "danger-solid": "#dc2626",
  ok: "#3dbb7c",
  "zone-green": "#22c55e",
  "zone-yellow": "#eab308",
  "zone-red": "#ef4444",
};

/**
 * Read a CSS custom property from `<html>` (or `element`). Returns the trimmed value, or the
 * dark-theme fallback for known tokens when unavailable (SSR). Re-read after theme changes:
 * depend on `resolvedTheme` from next-themes in your memo/effect.
 */
export function readCssVar(name: TokenName | `--${string}`, element?: Element): string {
  const prop = name.startsWith("--") ? name : `--${name}`;
  if (typeof window !== "undefined" && typeof document !== "undefined") {
    const el = element ?? document.documentElement;
    const value = getComputedStyle(el).getPropertyValue(prop).trim();
    if (value) return value;
  }
  const key = prop.slice(2) as TokenName;
  return TOKEN_FALLBACK[key] ?? "";
}

/** A token as `[r, g, b, a]` for deck.gl. */
export function tokenRgba(name: TokenName, alpha = 255): RGBA {
  return hexToRgba(readCssVar(name), alpha);
}

/** `var(--name)` reference for inline styles and recharts props (follows the theme live). */
export function cssVarRef(name: TokenName): string {
  return `var(--${name})`;
}

/** Zone colours, fixed across themes so maps read the same in print and both themes. */
export const ZONE_HEX: Readonly<Record<ZoneKind, string>> = {
  green: "#22c55e",
  yellow: "#eab308",
  red: "#ef4444",
};

export const ZONE_RGB: Readonly<Record<ZoneKind, RGB>> = {
  green: [34, 197, 94],
  yellow: [234, 179, 8],
  red: [239, 68, 68],
};

export const ZONE_LABEL: Readonly<Record<ZoneKind, string>> = {
  green: "Green zone",
  yellow: "Yellow zone",
  red: "Red zone",
};

/** Hatch pattern ids in the atlas created by `createHatchAtlas()`. */
export type HatchPatternId = "solid" | "dots" | "diagonal" | "cross";

/** Pattern used for each zone so zones never rely on colour alone. */
export const ZONE_PATTERN: Readonly<Record<ZoneKind, HatchPatternId>> = {
  green: "dots",
  yellow: "diagonal",
  red: "cross",
};

/** Zone fill colour for deck.gl (`alpha` 0–255, default semi-transparent). */
export function zoneColor(zone: ZoneKind, alpha = 140): RGBA {
  return withAlpha(ZONE_RGB[zone], alpha);
}

export interface HatchMappingEntry {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface HatchAtlas {
  /**
   * The atlas canvas: white patterns on transparent, use with `fillPatternMask: true`. A canvas
   * (not a data URL) so deck.gl uploads it at once instead of drawing a frame before it loads.
   */
  atlas: HTMLCanvasElement;
  mapping: Record<HatchPatternId, HatchMappingEntry>;
  width: number;
  height: number;
}

export const HATCH_TILE = 32;
const HATCH_ORDER: readonly HatchPatternId[] = ["solid", "dots", "diagonal", "cross"];

let hatchCache: HatchAtlas | null = null;

/**
 * Build (once) the hatch pattern atlas for deck.gl's `FillStyleExtension({pattern: true})`:
 *
 *   new GeoJsonLayer({
 *     extensions: [new FillStyleExtension({ pattern: true })],
 *     fillPatternAtlas: hatch.atlas,
 *     fillPatternMapping: hatch.mapping,
 *     fillPatternMask: true,                       // tint with getFillColor
 *     getFillPattern: (f) => ZONE_PATTERN[f.properties.zone],
 *     getFillPatternScale: 1,
 *   })
 *
 * Browser only (uses a canvas); throws when called on the server.
 */
export function createHatchAtlas(): HatchAtlas {
  if (hatchCache) return hatchCache;
  if (typeof document === "undefined") throw new Error("createHatchAtlas() must run in the browser");
  const size = HATCH_TILE;
  const canvas = document.createElement("canvas");
  canvas.width = size * HATCH_ORDER.length;
  canvas.height = size;
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("Canvas 2D context is not available");
  ctx.fillStyle = "#ffffff";
  ctx.strokeStyle = "#ffffff";
  ctx.lineCap = "square";

  const mapping = {} as Record<HatchPatternId, HatchMappingEntry>;

  HATCH_ORDER.forEach((id, i) => {
    const x0 = i * size;
    mapping[id] = { x: x0, y: 0, width: size, height: size };
    ctx.save();
    ctx.beginPath();
    ctx.rect(x0, 0, size, size);
    ctx.clip();
    if (id === "solid") {
      ctx.fillRect(x0, 0, size, size);
    } else if (id === "dots") {
      const r = 2.5;
      for (const [cx, cy] of [
        [8, 8],
        [24, 24],
      ] as const) {
        ctx.beginPath();
        ctx.arc(x0 + cx, cy, r, 0, Math.PI * 2);
        ctx.fill();
      }
    } else {
      // 45° lines that tile seamlessly: draw x + y = c for c in {0, size, 2*size}.
      ctx.lineWidth = 3;
      const diag = (c: number) => {
        ctx.beginPath();
        ctx.moveTo(x0 + c - size, size);
        ctx.lineTo(x0 + c + size, -size);
        ctx.stroke();
      };
      [0, size, 2 * size].forEach(diag);
      if (id === "cross") {
        // and x - y = c for c in {-size, 0, size}
        const anti = (c: number) => {
          ctx.beginPath();
          ctx.moveTo(x0 + c - size, -size);
          ctx.lineTo(x0 + c + 2 * size, 2 * size);
          ctx.stroke();
        };
        [-size, 0, size].forEach(anti);
      }
    }
    ctx.restore();
  });

  hatchCache = { atlas: canvas, mapping, width: canvas.width, height: canvas.height };
  return hatchCache;
}

/** CSS background for a legend swatch matching the zone hatch (colour + pattern). */
export function zoneSwatchStyle(zone: ZoneKind): { backgroundColor: string; backgroundImage: string; backgroundSize?: string } {
  const c = ZONE_HEX[zone];
  const pattern = ZONE_PATTERN[zone];
  const line = "rgb(0 0 0 / 0.55)";
  if (pattern === "dots") {
    return {
      backgroundColor: c,
      backgroundImage: `radial-gradient(${line} 1.2px, transparent 1.4px)`,
      backgroundSize: "6px 6px",
    };
  }
  if (pattern === "diagonal") {
    return {
      backgroundColor: c,
      backgroundImage: `repeating-linear-gradient(45deg, ${line} 0 1.5px, transparent 1.5px 5px)`,
    };
  }
  return {
    backgroundColor: c,
    backgroundImage: `repeating-linear-gradient(45deg, ${line} 0 1.5px, transparent 1.5px 5px), repeating-linear-gradient(-45deg, ${line} 0 1.5px, transparent 1.5px 5px)`,
  };
}

/** Index 0 = level 1 (lowest risk) … index 9 = level 10. Light-to-dark, yellow → dark red. */
export const RISK_RAMP: readonly string[] = [
  "#fff7bc",
  "#fee391",
  "#fec44f",
  "#fe9929",
  "#ec7014",
  "#cc4c02",
  "#a63603",
  "#8c2d04",
  "#67000d",
  "#3f0007",
];

/** Colour for an iGRC / risk level 1–10 (clamped). */
export function riskColor(level: number, alpha = 255): RGBA {
  const i = Math.min(10, Math.max(1, Math.round(level))) - 1;
  return hexToRgba(RISK_RAMP[i] as string, alpha);
}

export function riskHex(level: number): string {
  const i = Math.min(10, Math.max(1, Math.round(level))) - 1;
  return RISK_RAMP[i] as string;
}

/** Text colour (black/white) that stays readable on a risk swatch. */
export function riskTextColor(level: number): "#000000" | "#ffffff" {
  return Math.round(level) <= 5 ? "#000000" : "#ffffff";
}

/** Viridis, 9 stops (matplotlib). */
export const VIRIDIS_STOPS: readonly string[] = [
  "#440154",
  "#472d7b",
  "#3b528b",
  "#2c728e",
  "#21918c",
  "#28ae80",
  "#5ec962",
  "#addc30",
  "#fde725",
];

/** RdBu diverging, 9 stops (ColorBrewer): t=0 red, t=0.5 neutral, t=1 blue. */
export const RDBU_STOPS: readonly string[] = [
  "#b2182b",
  "#d6604d",
  "#f4a582",
  "#fddbc7",
  "#f7f7f7",
  "#d1e5f0",
  "#92c5de",
  "#4393c3",
  "#2166ac",
];

const VIRIDIS_RGB: readonly RGB[] = VIRIDIS_STOPS.map((h) => hexToRgba(h).slice(0, 3) as RGB);
const RDBU_RGB: readonly RGB[] = RDBU_STOPS.map((h) => hexToRgba(h).slice(0, 3) as RGB);

/** Viridis colour for t in 0..1 (clamped; NaN → t=0), as `[r, g, b, a]`. */
export function viridis(t: number, alpha = 255): RGBA {
  return withAlpha(interpolateStops(VIRIDIS_RGB, t), alpha);
}

export function viridisHex(t: number): string {
  return rgbToHex(interpolateStops(VIRIDIS_RGB, t));
}

/** RdBu diverging colour for t in 0..1. */
export function rdbu(t: number, alpha = 255): RGBA {
  return withAlpha(interpolateStops(RDBU_RGB, t), alpha);
}

export function rdbuHex(t: number): string {
  return rgbToHex(interpolateStops(RDBU_RGB, t));
}

/** Map a value in [min, max] to 0..1 (0 when the range is empty). */
export function normalize(value: number, min: number, max: number): number {
  if (!Number.isFinite(value) || max <= min) return 0;
  return Math.min(1, Math.max(0, (value - min) / (max - min)));
}

/** Evenly spaced legend breaks: `count` values from min to max inclusive. */
export function legendBreaks(min: number, max: number, count = 5): number[] {
  if (count < 2 || !Number.isFinite(min) || !Number.isFinite(max)) return [min];
  return Array.from({ length: count }, (_, i) => min + ((max - min) * i) / (count - 1));
}

export type Tone = "ok" | "warn" | "danger" | "info" | "neutral";

export const TONE_TOKEN: Readonly<Record<Tone, TokenName>> = {
  ok: "ok",
  warn: "warn",
  danger: "danger",
  info: "accent",
  neutral: "text-muted",
};

export function toneForVerdict(verdict: Verdict): Tone {
  return verdict === "pass" ? "ok" : verdict === "caution" ? "warn" : "danger";
}

export const VERDICT_LABEL: Readonly<Record<Verdict, string>> = {
  pass: "Pass",
  caution: "Caution",
  fail: "Fail",
};

export function toneForZone(zone: ZoneKind): Tone {
  return zone === "green" ? "ok" : zone === "yellow" ? "warn" : "danger";
}

export function toneForSeverity(severity: Severity): Tone {
  return severity === "info" ? "info" : severity === "warning" ? "warn" : "danger";
}

export function toneForAnalysisStatus(status: AnalysisStatus): Tone {
  switch (status) {
    case "COMPLETED":
      return "ok";
    case "FAILED":
      return "danger";
    case "CANCELLED":
      return "neutral";
    default:
      return "info";
  }
}

export function toneForFileStatus(status: FileStatus): Tone {
  return status === "COMPLETED" ? "ok" : status === "FAILED" ? "danger" : "info";
}

export function toneForMeasurementStatus(status: MeasurementStatus): Tone {
  switch (status) {
    case "measured":
      return "ok";
    case "not_applicable":
      return "neutral";
    case "unsupported":
    case "skipped":
      return "warn";
    default:
      return "danger";
  }
}

export const MEASUREMENT_STATUS_LABEL: Readonly<Record<MeasurementStatus, string>> = {
  measured: "Measured",
  not_applicable: "Not applicable",
  unsupported: "Unsupported",
  skipped: "Skipped",
  error: "Error",
};

export function toneForFlyability(status: FlyabilityStatus): Tone {
  switch (status) {
    case "free":
      return "ok";
    case "limited":
    case "permission":
      return "warn";
    default:
      return "danger";
  }
}

/** Colours for flight-audit path segments (`agl_band`). */
export function toneForAglBand(band: AglBand): Tone {
  return band === "ok" ? "ok" : band === "near" ? "warn" : "danger";
}

/** A tone's colour as `[r, g, b, a]` from the current theme. */
export function toneRgba(tone: Tone, alpha = 255): RGBA {
  return tokenRgba(TONE_TOKEN[tone], alpha);
}

export function toneVar(tone: Tone): string {
  return cssVarRef(TONE_TOKEN[tone]);
}

/** Categorical palette for series (drones, layers): distinguishable in both themes. */
export const CATEGORICAL: readonly string[] = [
  "#3b82f6",
  "#f59e0b",
  "#10b981",
  "#ec4899",
  "#8b5cf6",
  "#14b8a6",
  "#f97316",
  "#84cc16",
];

export function categorical(i: number, alpha = 255): RGBA {
  const n = CATEGORICAL.length;
  return hexToRgba(CATEGORICAL[((i % n) + n) % n] as string, alpha);
}
