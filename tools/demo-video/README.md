# AirBrief demo video

The demo video is rendered by code, never recorded or edited by hand:

- **Playwright** drives the real app in Chromium and captures frames (DevTools screencast).
- **Kokoro** reads the narration: a free 82M-parameter voice model running locally on the CPU (via kokoro-js).
- **FFmpeg** joins the scenes with cross-fades, mixes the voice, click sounds and a generated music bed, and levels the loudness.

One command renders the whole video. The story lives in [`scenes.mjs`](scenes.mjs): one spoken line per scene, plus the steps that show it. Change a line or a step, render again, and everything else (timing, captions, sound) follows.

Nothing here costs money or needs an account:

| Tool | Licence |
|---|---|
| Playwright | Apache-2.0 |
| kokoro-js | Apache-2.0 |
| Kokoro-82M voice weights | Apache-2.0 |
| FFmpeg | LGPL/GPL; used as a separate tool, not bundled |

The music bed and the sound effects are synthesised in [`lib/sound.mjs`](lib/sound.mjs), so there are no third-party audio files to license.

## What the video shows

The video runs about 88 seconds (the length follows the voice):

1. **Hook:** a survey area on the map and four pre-flight questions.
2. **Title card:** "Know before you fly."
3. **Plan without a file:** Ctrl+K "VOBL" moves the map to the airport with its zone rings from the data pack; the "New operation" menu (pin, line, area, coordinates, paste).
4. **Airspace:** on the farm plots, the DGCA 2021 rules (bands measured from each airport's boundary) and your own zone files; then the check: the indicative verdict, its reason, and the aerodrome band with the height flown in it.
5. **One result per feature:** the same plots checked one by one: each plot coloured by its result on the map, a row per plot, and the export menu.
6. **Ground risk:** the SORA 2.5 operational volume on the map and the intrinsic ground risk class as evidence.
7. **Environment:** sunrise, sunset and civil twilight in local time; the power line that crosses the area.
8. **Line of sight:** the pilot's terrain viewshed and the radio path's Fresnel clearance.
9. **Survey:** ground resolution, batteries, flight time, GCPs and storage.
10. **Drone show:** validation finds the D07/D32 conflict and jumps to it.
11. **Flight audit:** the GPX training flight: above the 120 m limit, and outside the training area.
12. **Evidence brief:** every result on one printable page with its sources and hashes.
13. **End card:** what it covers, where to get it, data credits and the "indicative only" note.

Every scene uses the built-in samples. With the synthetic `demo` data pack, the end card says the terrain, population, obstacles and zones are synthetic. With a real pack, it credits that pack's sources instead.

## One-time setup

You need:

- **Node 20 or newer**
- **FFmpeg 5 or newer** on `PATH`, a full build (with libx264). Install it with one of:
  - Windows: `winget install Gyan.FFmpeg`
  - macOS: `brew install ffmpeg`
  - Debian/Ubuntu: `sudo apt install ffmpeg`

Open a new terminal afterwards and check with `ffmpeg -version`. winget, Homebrew and apt check what they download themselves.

A portable copy works too: unzip a build (for example gyan.dev's "essentials") into `tools/demo-video/.bin/` (git-ignored) and put its `bin` folder first on `PATH` for the session. Check the archive before you unzip it: gyan.dev publishes a `.sha256` file next to every build on https://www.gyan.dev/ffmpeg/builds/ (for `ffmpeg-release-essentials.zip`, download `ffmpeg-release-essentials.zip.sha256` too). The two values must match:

```powershell
(Get-FileHash .\ffmpeg-release-essentials.zip -Algorithm SHA256).Hash   # Windows PowerShell
Get-Content .\ffmpeg-release-essentials.zip.sha256
```

```bash
sha256sum ffmpeg-release-essentials.zip          # Git Bash, Linux; on macOS: shasum -a 256
cat ffmpeg-release-essentials.zip.sha256
```

Other builds work the same way (BtbN's GitHub builds list theirs in `checksums.sha256`). If the values differ, delete the download and fetch it again.

Then, in this folder:

```bash
cd tools/demo-video
npm install          # playwright + kokoro-js (+ transformers.js, which loads the model)
npm run setup        # downloads Chromium for Playwright (once)
```

The first full render downloads the Kokoro model from Hugging Face (about 90 MB, cached in `node_modules/@huggingface/transformers/.cache/`). Later renders work offline, apart from the basemap tiles.

**Model revision.** `config.mjs` pins the model to one Hugging Face commit (`voiceRevision`), so the weights cannot change between renders even if the model repository does. To move to a newer one, look up its `sha` at https://huggingface.co/api/models/onnx-community/Kokoro-82M-v1.0-ONNX, put it in `voiceRevision`, and delete `out/cache/voice/` so every line is spoken again with the new weights (the line cache is keyed by model name, voice, speed and text, not the revision).

## Render

1. **Start AirBrief** as in [RUNBOOK §2](../../RUNBOOK.md). Use a production build of the web app, because it records more smoothly than `npm run dev`:

   ```bash
   python -m seed.datapack --synthetic --name demo   # once
   uvicorn api.main:app                              # terminal 1 (repo root)
   cd frontend && npm run build && npm start -- -H 127.0.0.1   # terminal 2
   ```

   Each render creates 4 datasets and 9 analyses through the API. If you render often, set `RATE_LIMIT_PER_HOUR=0` in `.env` for this local instance and restart the API.

2. **Render** (terminal 3, in `tools/demo-video`):

   ```bash
   npm run draft          # fast pass: no voice, line lengths estimated → out/draft.mp4
   OFFSCREEN=1 npm run video   # the real thing → out/demo.mp4 (window kept off screen)
   npm run verify         # numbers + review frames
   ```

   A Chromium window opens and plays through the scenes. Don't touch it while it records, and don't minimise it.
   - `OFFSCREEN=1 npm run video` places that window off the visible screen, so you can keep working. It still renders on the GPU. This is the recommended mode.
   - `HEADLESS=1` runs without any window, but the map then renders in software. On most machines that is too slow to keep up: scenes lag behind the script and their last moments look stale. Use it only on a fast machine, and check the review frames afterwards.

3. **Iterate on one scene:** `npm run scene -- shows` (or `-- plan,shows`) → `out/scene-shows.mp4`.

### Outputs (in `out/`, git-ignored)

| File | What |
|---|---|
| `demo.mp4` | 1920×1080, 30 fps, H.264 + AAC 48 kHz stereo, loudness levelled to −16 LUFS |
| `demo.vtt` | Captions, the same text as the burned-in ones |
| `demo-thumbnail.jpg` | A frame from the title card |
| `demo.manifest.json` | Where every scene, caption and click is in the timeline |
| `demo.seen.json` | The page text of every scene (scanned for error messages by `verify`) |
| `demo.verify.json`, `review/` | The verify report and frames from every scene |
| `failed.png` | The page at the moment a step failed, if one did |
| `cache/voice/` | Synthesised voice lines; only changed lines are re-synthesised |

### Options

| Option | Effect |
|---|---|
| `--no-music` | Voice and clicks only |
| `--music path/to/track.mp3` | Use your own track instead of the generated pad: looped, faded and lowered under the voice. Use only music you may publish (royalty-free or your own) |
| `VOICE=bm_george` | Another Kokoro voice (for example `af_bella`, `am_michael`, `bf_emma`) |
| `VOICE_SPEED=1.05` | Faster or slower narration |
| `MUSIC_DB=-24` | Music quieter (more negative) or louder |
| `REPO_URL=https://github.com/<you>/AirBrief` | Shown on the end card as the next step |
| `BASEMAP=none` | No OpenFreeMap tiles (fully offline; the map has no background) |
| `OFFSCREEN=1` | Record in a real browser window placed off the screen (recommended) |
| `AA_DEBUG_TIMING=1` | Print each step's time and the frame timing per scene (for diagnosing a scene that looks out of step) |
| `APP_URL`, `API_URL` | Where the app runs (defaults `http://localhost:3000`, `http://localhost:8000`) |

On Windows PowerShell, set variables first: `$env:OFFSCREEN="1"; $env:REPO_URL="https://github.com/<you>/AirBrief"; npm run video`.

### The teaser

A second, 30–40 s story for social posts lives in [`teaser.mjs`](teaser.mjs): the hook, a title card, four proof shots (Go to VOBL, airspace on the plots, ground risk, the brief), "it never says go or no-go", and an end card with the repository link.

```bash
OFFSCREEN=1 npm run teaser:draft      # silent pass → out/teaser-draft.mp4
OFFSCREEN=1 npm run teaser            # → out/teaser.mp4 (+ .vtt, thumbnail, manifest)
OFFSCREEN=1 npm run teaser -- --format 4x5   # → out/teaser-4x5.mp4, 1080×1350 portrait
npm run verify -- teaser              # or teaser-4x5; length must be 28–42 s
```

- Its own settings are in `config.mjs` under `teaser` (voice `am_michael` at 1.05, length bounds). `VOICE` and `VOICE_SPEED` still override them.
- `--format 4x5` records a 1080×1350 viewport at 1×: narrower than 1024 px, the app switches to its tablet layout and the scenes no longer fit.
## FlightMD and AirBrief together

Two stories run both apps side by side on one real flight:

| Story | File | Output | What it shows |
|---|---|---|---|
| `flightmd` | [`flightmd.mjs`](flightmd.mjs) | `out/flightmd.mp4`, about 60 s | How the two fit: AirBrief's check of the planned area and its brief, the log in FlightMD, the audit back in AirBrief |
| `rollout` | [`rollout.mjs`](rollout.mjs) | `out/rollout.mp4` (and `-4x5`), about 57 s | The link itself, click by click, for a feature announcement: the brief's hash into FlightMD, **Audit airspace in AirBrief**, **Open in FlightMD**, and how to turn it on |

The flight is `px4-vtol-tiltrotor-1.14` from FlightMD's test corpus: a public PX4 log flown
hardware-in-the-loop, so its position is PX4's simulated home in Zurich. The first render
downloads it into `out/cache/flights/` and checks it against the corpus manifest's SHA-256. The
end card says where it comes from.

Before recording:

1. **The Zurich pack with the Swiss zones** (from the repository root):

   ```bash
   python -m seed.datapack --bbox 8.45,47.33,8.65,47.50 --name zurich --iso3 CHE
   python -m seed.datapack --seal data/datapacks/zurich      --zones https://data.geo.admin.ch/ch.bazl.einschraenkungen-drohnen/einschraenkungen-drohnen/einschraenkungen-drohnen_4326.json      --zones-credit "Federal Office of Civil Aviation FOCA, open use with source"
   ```

   If Overpass is busy, finish with `python -m seed.datapack --refetch data/datapacks/zurich`.
2. **FlightMD** on ports 8100 (API) and 3100 (web), built with
   `NEXT_PUBLIC_API_URL=http://localhost:8100 NEXT_PUBLIC_AIRBRIEF_URL=http://localhost:3000`.
   Raise its `RATE_LIMIT_PER_HOUR`; `FLIGHTMD_OFFLINE=1` keeps it off the network.
3. **AirBrief's API** started with `FLIGHTMD_API_URL=http://localhost:8100`, so it may fetch the
   report.

```bash
OFFSCREEN=1 node record.mjs --story flightmd [--draft]
OFFSCREEN=1 node record.mjs --story rollout [--draft] [--format 4x5]
npm run verify -- flightmd            # or rollout, rollout-4x5
```

- The scenes set the airspace check and the audit to custom rules for Switzerland (120 m, no
  rings around airports: those are FOCA zones in the pack).
- `FLIGHTMD_URL` and `FLIGHTMD_REPO_URL` override FlightMD's address and its repository link.
- A story's `PACKS` names the data packs it shows; only their sources go on the end card.

## How it works

```
scenes.mjs ──► lib/tts.mjs (Kokoro, cached) ──► line lengths
     │
     ▼
record.mjs ── for each scene: prepare (not filmed) → record steps → hold until voice + tail
     │          lib/stage.mjs    clock, captions, pointer, spotlight, zoom, sped-up waits
     │          lib/overlay.mjs  in-page captions, callouts, pointer, cards (shadow DOM)
     │          lib/capture.mjs  DevTools screencast → constant-frame-rate clip
     ▼
lib/assemble.mjs ── xfade join → voice + clicks + whooshes (Node mix) → ducked music
                    → two-pass loudnorm → demo.mp4 + .vtt + thumbnail + manifest
```

- **Timing follows the voice.** Each scene lasts as long as its steps or its line (plus a short tail), whichever is longer, and holds at least 1.6 s after its last spotlight or click so the result can be read. The voice starts 0.35 s after the picture. Captions split the line by sentence and are timed by character count.
- **No loading states on screen.** Page loads happen in `prepare`, before recording starts. Waits for analysis jobs play 4× faster (8× for the long show validation), with a "Sped up" badge once they last more than half a second. The split second after a click on Run, while the results drawer still says "No … results yet", is cut.
- **Frames are placed by when they were painted**, not when they arrived. Chrome can send them late, so each scene keeps recording until a frame painted after its end has arrived.
- **Real states, not sleeps.** Steps wait for real elements: buttons, verdicts, result sections. Holds after that are only for pacing.
- **Motion:** entering elements ease out, leaving elements ease in. Interface moves take 150–300 ms and camera moves 650–760 ms. One thing moves at a time.
- **Sound:** the voice leads. The music sits about 21 dB lower and ducks further while the voice speaks. A soft click plays on every click and a gentle sweep on every cut.

## Changing the video

- **Words:** edit `say` in `scenes.mjs`. Use `speak` when the voice needs a different spelling (for example "Flight M D", "one hundred and twenty").
- **Steps:** `s.click`, `s.pickFile`, `s.waitFor`, `s.spotlight(locator, "why it matters")`, `s.zoom`, `s.hold`, `s.card`, `s.list` (see the top of `lib/stage.mjs`). Use Playwright locators by role and accessible name, as the e2e tests do.
- **A new capability** (another overlay or effect): add it to `lib/overlay.mjs`, expose it in `lib/stage.mjs`, document it here.

## Before sharing

Run through this once:

- [ ] `npm run verify` passes.
- [ ] Watch once with the sound off: the captions alone tell the story.
- [ ] Listen once with your eyes closed: the voice is clear and the music never competes with it.
- [ ] The end card shows your repository link (`REPO_URL`) and the data note.
- [ ] Upload `demo.vtt` with the video.
