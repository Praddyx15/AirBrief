#!/usr/bin/env node
/**
 * Render the AirBrief demo video from the running app.
 *
 *   node record.mjs                 full render: voice, music, out/demo.mp4 (+ .vtt, thumbnail, manifest)
 *   node record.mjs --draft         no voice or audio; line lengths estimated; out/draft.mp4
 *   node record.mjs --only shows    one scene (or several: --only measure,shows); out/scene-<ids>.mp4
 *   node record.mjs --no-music      voice and clicks only
 *   node record.mjs --music bed.mp3 your own track (looped, faded, lowered under the voice)
 *   node record.mjs --story teaser  the 30-40 s teaser (teaser.mjs) → out/teaser.mp4; works with --draft and --only
 *   node record.mjs --story flightmd  FlightMD and AirBrief on one flight (flightmd.mjs) → out/flightmd.mp4
 *   node record.mjs --story rollout   the link between them, click by click (rollout.mjs) → out/rollout.mp4
 *   node record.mjs --story tutorial  getting started: load, check, brief (tutorial.mjs) → out/tutorial.mp4
 *   node record.mjs --format 4x5    1080×1350 portrait (config.mjs formats) → out/<name>-4x5.mp4
 *
 * Needs: the API and web app running (RUNBOOK §2) with a data pack, ffmpeg on PATH, and the
 * packages from `npm install` + `npm run setup`. See README.md.
 */

import { existsSync, readFileSync } from "node:fs";
import { mkdir, rm, writeFile } from "node:fs/promises";
import path from "node:path";
import { chromium } from "playwright";
import { CONFIG } from "./config.mjs";
import * as demo from "./scenes.mjs";
import * as flightmd from "./flightmd.mjs";
import * as rollout from "./rollout.mjs";
import * as teaser from "./teaser.mjs";
import * as tutorial from "./tutorial.mjs";
import { dataNote, loadSample, preflight } from "./lib/app.mjs";
import { assemble } from "./lib/assemble.mjs";
import { encodeClip } from "./lib/capture.mjs";
import { ffmpegMajorVersion, hasEncoder, hasFilter } from "./lib/ffmpeg.mjs";
import { installOverlay } from "./lib/overlay.mjs";
import { Stage } from "./lib/stage.mjs";
import { synthesizeAll } from "./lib/tts.mjs";

const STORIES = { demo, teaser, flightmd, rollout, tutorial };

function parseArgs(argv) {
  const args = { draft: false, only: null, music: undefined, story: "demo" };
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    if (arg === "--draft") args.draft = true;
    else if (arg === "--no-music") args.music = false;
    else if (arg === "--music") args.music = argv[++i] ?? "";
    else if (arg.startsWith("--music=")) args.music = arg.slice("--music=".length);
    else if (arg === "--only") args.only = argv[++i];
    else if (arg.startsWith("--only=")) args.only = arg.slice("--only=".length);
    else if (arg === "--story") args.story = argv[++i];
    else if (arg.startsWith("--story=")) args.story = arg.slice("--story=".length);
    else if (arg === "--format") args.format = argv[++i];
    else if (arg.startsWith("--format=")) args.format = arg.slice("--format=".length);
    else if (arg === "--help" || arg === "-h") args.help = true;
    else throw new Error(`Unknown argument: ${arg} (try --help)`);
  }
  if (args.music === "") throw new Error("--music needs a file path");
  if (args.only === "" || args.only === undefined) throw new Error("--only needs one or more scene ids");
  if (!STORIES[args.story]) throw new Error(`Unknown story: ${args.story} (known: ${Object.keys(STORIES).join(", ")})`);
  if (args.format !== undefined && !CONFIG.formats[args.format]) {
    throw new Error(`Unknown format: ${args.format} (known: ${Object.keys(CONFIG.formats).join(", ")})`);
  }
  return args;
}

const started = Date.now();
const log = (message) => console.log(`[${((Date.now() - started) / 1000).toFixed(1).padStart(6)} s] ${message}`);

async function checkTools() {
  const major = await ffmpegMajorVersion();
  if (major !== null && major < 5) throw new Error(`ffmpeg ${major}.x is too old; install ffmpeg 5 or newer.`);
  for (const filter of ["xfade", "loudnorm", "sidechaincompress", "amix"]) {
    if (!(await hasFilter(filter))) throw new Error(`This ffmpeg build has no '${filter}' filter; install a full build (see README.md).`);
  }
  for (const encoder of ["libx264", "aac"]) {
    if (!(await hasEncoder(encoder))) throw new Error(`This ffmpeg build has no '${encoder}' encoder; install a full build (see README.md).`);
  }
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  if (args.help) {
    console.log(readFileSync(new URL(import.meta.url), "utf8").split("*/")[0]);
    return;
  }
  if (typeof args.music === "string" && !existsSync(args.music)) throw new Error(`Music file not found: ${args.music}`);
  const story = STORIES[args.story];
  const config = { ...CONFIG, ...CONFIG[args.story], ...CONFIG.formats[args.format] };

  log("Checking ffmpeg and the running app");
  await checkTools();
  const { health, meta } = await preflight(config);
  log(`API ${health.version}, data packs: ${health.datapacks.join(", ")}`);

  const repo = config.repoUrl.replace(/^https?:\/\//, "").replace(/\/+$/, "");
  const cta = repo ? repo : "Free on GitHub · MIT licence";
  // credit only the packs the story shows (none loaded: the synthetic pack stood in for them)
  const shown = story.PACKS ? { ...health, datapacks: health.datapacks.filter((p) => story.PACKS.includes(p)) } : health;
  let scenes = story.buildScenes({ cta, dataNote: dataNote(meta, shown, { basemap: config.basemap }), config });
  if (args.only) {
    const wanted = args.only.split(",").map((x) => x.trim()).filter(Boolean);
    if (story.WHOLE) throw new Error(`The ${args.story} story's scenes build on each other; record it whole (use --draft for a quick pass).`);
    const unknown = wanted.filter((id) => !scenes.some((s) => s.id === id));
    if (unknown.length) throw new Error(`Unknown scene id(s): ${unknown.join(", ")}. Known: ${scenes.map((s) => s.id).join(", ")}`);
    scenes = scenes.filter((s) => wanted.includes(s.id));
  }
  // the demo keeps its old names (draft, scene-<ids>); other stories get theirs as a prefix
  const prefix = args.story === "demo" ? "" : `${args.story}-`;
  const base = args.only ? `${prefix}scene-${scenes.map((s) => s.id).join("+")}` : args.draft ? `${prefix}draft` : args.story;
  const name = args.format ? `${base}-${args.format}` : base;

  await mkdir(config.outDir, { recursive: true });
  // Fresh frames and clips; other renders' voice tracks (work/<name>-voice.wav) stay for verify.
  await rm(path.join(config.workDir, "frames"), { recursive: true, force: true });
  await rm(path.join(config.workDir, "clips"), { recursive: true, force: true });
  await mkdir(path.join(config.workDir, "clips"), { recursive: true });

  log(args.draft ? "Draft: estimating line lengths (no voice)" : `Voice: Kokoro '${config.voice}'`);
  const voices = await synthesizeAll(scenes, {
    voice: config.voice,
    speed: config.voiceSpeed,
    model: config.voiceModel,
    revision: config.voiceRevision,
    dtype: config.voiceDtype,
    cacheDir: config.cacheDir,
    draft: args.draft,
    log,
  });

  log("Loading the sample datasets");
  const ids = {};
  for (const [key, sample] of Object.entries(story.FIXTURES)) ids[key] = await loadSample(config.apiUrl, sample);
  if (story.setup) Object.assign(ids, await story.setup(config, { health, meta }));

  const browser = await chromium.launch({
    headless: config.headless,
    args: [
      "--hide-scrollbars",
      "--force-color-profile=srgb",
      // Headless Chrome has no GPU: software WebGL, or the map stays blank.
      ...(config.headless ? ["--use-angle=swiftshader", "--enable-unsafe-swiftshader"] : []),
      // A real window off the visible screen; keep painting although nobody can see it.
      ...(!config.headless && config.offscreen
        ? ["--window-position=-4000,0", "--disable-backgrounding-occluded-windows", "--disable-renderer-backgrounding"]
        : []),
    ],
  });
  const context = await browser.newContext({
    viewport: config.viewport,
    deviceScaleFactor: config.deviceScaleFactor,
    colorScheme: "dark",
    reducedMotion: "no-preference",
    locale: "en-GB",
  });
  await context.addInitScript(
    ({ basemap, flightmdUrl }) => {
      try {
        // choicesHintDismissed: the first-run note on assessment choices would sit in the audit panel
        localStorage.setItem(
          "airbrief-settings",
          JSON.stringify({ state: { units: "metric", reduceMotion: "system", basemap, flightmdUrl, choicesHintDismissed: true }, version: 1 }),
        );
        localStorage.setItem("theme", "dark");
      } catch {
        // storage blocked: the app falls back to its defaults (metric, dark)
      }
    },
    { basemap: config.basemap, flightmdUrl: config.flightmdUrl ?? "" },
  );
  await context.addInitScript(installOverlay, {
    width: config.viewport.width,
    height: config.viewport.height,
    colors: config.colors,
  });
  const page = await context.newPage();
  page.setDefaultTimeout(60_000);
  const stage = new Stage({ page, config, workDir: config.workDir, log });

  const results = [];
  const seen = {};
  try {
    for (const [index, scene] of scenes.entries()) {
      const voice = voices.get(scene.id);
      const ctx = { s: stage, page, ids, info: { health, meta } };
      log(`Scene ${index + 1}/${scenes.length}: ${scene.id}`);
      if (scene.prepare) await scene.prepare(ctx);
      await stage.begin(scene, voice, index);
      await scene.run(ctx);
      const take = await stage.end();
      seen[scene.id] = await page.evaluate(() => document.body.innerText.slice(0, 20000)).catch(() => "");
      const clip = path.join(config.workDir, "clips", `${String(index + 1).padStart(2, "0")}-${scene.id}.mp4`);
      await encodeClip({
        frames: take.frames,
        dir: take.frameDir,
        mapTime: take.mapTime,
        duration: take.duration,
        fps: config.output.fps,
        width: config.output.width,
        height: config.output.height,
        file: clip,
      });
      log(`  ${take.duration.toFixed(2)} s, ${take.frames.length} frames, ${take.clicks.length} click(s)`);
      results.push({ id: scene.id, say: scene.say, thumbnail: Boolean(scene.thumbnail), clip, duration: take.duration, voice, clicks: take.clicks, cues: take.cues });
    }
  } catch (error) {
    const shot = path.join(config.outDir, "failed.png");
    await page.screenshot({ path: shot }).catch(() => {});
    await browser.close().catch(() => {});
    error.message += `\nA screenshot of the page at the failure is in ${shot}.`;
    throw error;
  }
  await browser.close();

  const music = args.draft ? false : args.music;
  const output = await assemble({
    scenes: results, config, outDir: config.outDir, workDir: config.workDir, draft: args.draft, music, name, log,
    story: args.story,
    format: args.format ?? null,
  });
  await writeFile(path.join(config.outDir, `${name}.seen.json`), JSON.stringify(seen, null, 2));

  log(`Done: ${output.file} (${output.total.toFixed(1)} s)`);
  console.log(`  captions   ${output.vttFile}`);
  console.log(`  thumbnail  ${output.thumbnail}`);
  console.log(`  manifest   ${output.manifestFile}`);
  if (!args.draft) {
    console.log("Next: npm run verify, then watch it once with the sound on and once muted before sharing.");
  }
}

main().catch((error) => {
  console.error(`\n${error.message}`);
  process.exit(1);
});
