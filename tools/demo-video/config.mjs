/**
 * Render settings. Environment variables override the ones marked "env".
 */

import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const env = process.env;

function number(name, fallback) {
  const value = Number.parseFloat(env[name] ?? "");
  return Number.isFinite(value) ? value : fallback;
}

function withFlightmd() {
  return {
    flightmdUrl: env.FLIGHTMD_URL || "http://localhost:3100", // env; FlightMD's web app
    flightmdRepoUrl: env.FLIGHTMD_REPO_URL || "https://github.com/Praddyx15/FlightMD", // env
  };
}

export const CONFIG = {
  // env: where the running app is (RUNBOOK §2)
  appUrl: env.APP_URL || "http://localhost:3000",
  apiUrl: env.API_URL || "http://localhost:8000",

  // Browser: a 1536×864 layout rendered at 1.25× gives 1920×1080 frames with UI text 25 % larger
  // than a plain 1080p screen recording.
  viewport: { width: 1536, height: 864 },
  deviceScaleFactor: 1.25,
  output: { width: 1920, height: 1080, fps: 30 },
  jpegQuality: 92,
  // env HEADLESS=1: no window, but software WebGL: too slow to paint the map in real time on
  // most machines, so scenes lag behind the script. Prefer OFFSCREEN=1: a real (GPU) window
  // placed off the screen, so it does not cover your desktop while it records.
  headless: env.HEADLESS === "1",
  offscreen: env.OFFSCREEN === "1",
  // env BASEMAP=none: no OpenFreeMap tiles (fully offline)
  basemap: env.BASEMAP === "none" ? "none" : "openfreemap",
  settleMs: 600,

  // Voice: Kokoro-82M through kokoro-js (Apache-2.0), on the CPU
  voice: env.VOICE || "af_heart", // env; e.g. af_bella, am_michael, bf_emma, bm_george
  voiceSpeed: number("VOICE_SPEED", 1.0), // env
  voiceModel: "onnx-community/Kokoro-82M-v1.0-ONNX",
  // a Hugging Face commit, so the weights can't change under us (README: "Model revision")
  voiceRevision: "1939ad2a8e416c0acfeecc08a694d14ef25f2231",
  voiceDtype: "q8",

  // Timing (seconds)
  leadSeconds: 0.35, // the voice starts this long after the picture
  tailSeconds: 1.0, // the picture holds this long after the voice
  finalHoldSeconds: 1.6, // ...and at least this long after the scene's last visible action
  crossfadeSeconds: 0.45,
  transition: "fade", // ffmpeg xfade transition
  fastFactor: 4, // waits play this many times faster
  zoomScale: 1.5,

  // Sound
  clickGain: 0.35,
  whooshGain: 0.18,
  musicGainDb: number("MUSIC_DB", -21), // env; music level before ducking, relative
  duck: { threshold: 0.025, ratio: 6, attackMs: 20, releaseMs: 400 },
  loudness: { I: -16, TP: -1.5, LRA: 11 },

  // End card
  repoUrl: env.REPO_URL || "https://github.com/Praddyx15/AirBrief", // env; same link as the app's footer

  // Brand colours (frontend/app/globals.css, dark theme)
  colors: {
    bg: "#0b0e11",
    surface: "#12161b",
    border: "#2a323c",
    text: "#e6eaee",
    textMuted: "#9aa5b1",
    accent: "#3b82f6",
    accentSolid: "#2563eb",
    accentText: "#60a5fa",
    warn: "#f5a524",
  },

  // Target length of the finished video (checked by verify.mjs)
  minSeconds: 60,
  maxSeconds: 120,

  // --story teaser (teaser.mjs): these replace the settings above
  teaser: {
    minSeconds: 28,
    maxSeconds: 42,
    voice: env.VOICE || "am_michael", // env
    voiceSpeed: number("VOICE_SPEED", 1.05), // env
    tailSeconds: 0.6,
  },

  // --story flightmd (flightmd.mjs): FlightMD and AirBrief on one flight; --story rollout
  // (rollout.mjs): the link between them, click by click, for a feature announcement
  flightmd: { minSeconds: 50, maxSeconds: 110, ...withFlightmd() },
  rollout: { minSeconds: 50, maxSeconds: 100, ...withFlightmd() },
  // --story tutorial (tutorial.mjs): the first two minutes, for a new user
  tutorial: { minSeconds: 60, maxSeconds: 150 },

  // --format 4x5: portrait for the LinkedIn feed. 1080 CSS px at 1x keeps the desktop layout;
  // below 1024 px the app switches to its tablet layout, which hides the module panel.
  formats: {
    "4x5": { viewport: { width: 1080, height: 1350 }, deviceScaleFactor: 1, output: { width: 1080, height: 1350, fps: 30 } },
  },

  outDir: path.join(here, "out"),
  workDir: path.join(here, "out", "work"),
  cacheDir: path.join(here, "out", "cache", "voice"),
};
