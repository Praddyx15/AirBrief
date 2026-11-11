# Web app, end-to-end tests and the static demo

Everything here runs in `frontend/` with Node 20. Use the tools from `node_modules/.bin` (what
`npm run` does), not a bare `npx`, which may fetch a different version from the internet.

## Lint, types, build

```bash
cd frontend
npm ci
npm run lint          # ESLint, no warnings allowed
npm run typecheck     # next typegen, then tsc --noEmit
npm run build         # production build; must end with the route table and no errors
```

`app/globals.css` is generated from the suite tokens (`design/suite-tokens.json`) by
`node scripts/gen-tokens.mjs`; `pytest tests/frontend` checks their contrast.

## End to end: Playwright and axe-core

25 spec files in `frontend/e2e/`, run one at a time (`workers: 1`) against a real API and a
production build of the web app, with the synthetic `demo` pack. Most of them also run axe-core
and fail on serious or critical accessibility violations.

**1. The API** (repository root, terminal 1):

```bash
python -m seed.datapack --synthetic --name demo     # once, and after every upgrade
python -m seed.samples --dest samples/
APP_ENV=development DEFAULT_DATAPACK=demo RATE_LIMIT_PER_HOUR=0 \
  uvicorn api.main:app --host 127.0.0.1 --port 8000
```

`RATE_LIMIT_PER_HOUR=0` matters: the run creates hundreds of datasets and analyses, far more than
the default 120 POSTs an hour.

**2. The web app** (terminal 2):

```bash
cd frontend
NEXT_PUBLIC_API_URL=http://127.0.0.1:8000 npm run build
npm start -- -H 127.0.0.1 -p 3000
```

**3. The tests** (terminal 3):

```bash
cd frontend
PLAYWRIGHT_BROWSERS_PATH=0 ./node_modules/.bin/playwright install chromium   # once
PLAYWRIGHT_BROWSERS_PATH=0 PLAYWRIGHT_BASE_URL=http://127.0.0.1:3000 E2E_API_URL=http://127.0.0.1:8000 npm run e2e
```

`PLAYWRIGHT_BROWSERS_PATH=0` keeps the browser inside `frontend/node_modules`, matched to this
Playwright version, instead of the user-wide cache. A full run takes 40 to 60 minutes; one file:
`npm run e2e -- e2e/zones.spec.ts`; one test: `npm run e2e -- -g "Open in FlightMD"`.

Expected: every test passes (152 on 2026-10-30, about 45 minutes in all). On a machine short of memory a test can fail with "Target
crashed" (Chromium was killed); close other programs and run that file again. If a test fails
on a fresh run, open its trace: `./node_modules/.bin/playwright show-trace test-results/<test>/trace.zip`.
The HTML report is in `frontend/playwright-report/`.

| Spec | Covers |
|---|---|
| `smoke`, `workflows` | Upload → map → measurements → analysis; screenshots at 375, 768, 1024 and 1440 px |
| `shell`, `map-input`, `shared-ui`, `pages`, `module-forms`, `evtol-shows-audit` | Layout, keyboard use, map input, forms, every page |
| `plan`, `environment`, `line-of-sight`, `groundrisk`, `tracks`, `heights`, `brief` | One per feature |
| `access`, `abuse`, `fetch`, `evidence`, `deploy` | Owner tokens, limits, FlightMD links, signed evidence, headers |
| `zones`, `zones-followups`, `batch`, `choices`, `flightmd-schema17` | Zone files and engine, one result per feature, assessment choices, FlightMD 1.7 reports |

Stale data pack: failures in `environment`, `groundrisk` or `plan` that mention power lines,
assemblies or IATA codes, or `zones` showing circles instead of bands, mean the `demo` pack is
older than the code. Build it again (step 1) and restart the API.

## The static demo (what GitHub Pages serves)

```bash
python -m seed.datapack --synthetic --name demo
python -m seed.demo_export --out frontend/public/demo --datapack-dir data/datapacks
cd frontend
NEXT_OUTPUT=export NEXT_PUBLIC_DEMO=1 NEXT_PUBLIC_BASE_PATH=/AirBrief npm run export
```

Git Bash rewrites `/AirBrief` into a Windows path; prefix that last line with
`MSYS_NO_PATHCONV=1`. The site lands in `frontend/out/`. Serve it under the same base path to
look at it:

```bash
mkdir -p ../.site && rm -rf ../.site/AirBrief && cp -r out ../.site/AirBrief
python -m http.server 3100 --bind 127.0.0.1 --directory ../.site
```

Open http://127.0.0.1:3100/AirBrief/: the landing page, then **Open workspace** shows the
sample datasets with their analyses, read-only, and the map with the context layers. Delete
`.site/` afterwards. `make demo` runs the first three steps with the repository name from git.

## The demo video

`tools/demo-video/README.md` has the setup (FFmpeg, Kokoro voice) and the render commands.
After a render, `npm run verify` (and `npm run verify -- teaser`, `-- teaser-4x5`) checks the
length, loudness, captions and every scene's page text, including that nothing on screen says
"AeroAtlas" any more, and writes review frames to `out/review/`.
