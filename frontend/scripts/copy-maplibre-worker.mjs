/**
 * MapLibre GL 6 runs its tile parsing in a Web Worker loaded from a separate file
 * (`maplibre-gl-worker.mjs`). Bundlers do not emit that file, so it is copied into
 * `public/vendor/` (git-ignored) before every dev, build and export run, and the map points
 * MapLibre at it with `setWorkerUrl` (components/map/map-view.tsx). Same origin, no CDN.
 */
import { copyFileSync, mkdirSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const require = createRequire(import.meta.url);
const source = path.join(path.dirname(require.resolve("maplibre-gl/package.json")), "dist", "maplibre-gl-worker.mjs");
const targetDir = path.join(here, "..", "public", "vendor");
mkdirSync(targetDir, { recursive: true });
copyFileSync(source, path.join(targetDir, "maplibre-gl-worker.mjs"));
console.log("Copied the MapLibre worker to public/vendor/maplibre-gl-worker.mjs");
