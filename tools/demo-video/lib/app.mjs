/**
 * Talking to the running AirBrief instance before recording: check that the web app and the
 * API answer, read which data packs are loaded, and create the sample datasets the scenes use.
 */

async function getJson(url, init) {
  let response;
  try {
    response = await fetch(url, init);
  } catch (error) {
    throw new Error(`No answer from ${url} (${error.cause?.code ?? error.message}).`);
  }
  const text = await response.text();
  let body = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = null;
  }
  return { status: response.status, ok: response.ok, body, text };
}

/** Throws with a fix when the app is not ready to be recorded. Returns { health, meta }. */
export async function preflight({ appUrl, apiUrl }) {
  const web = await fetch(new URL("/w", appUrl)).catch(() => null);
  if (!web || !web.ok) {
    throw new Error(
      `The web app does not answer at ${appUrl}/w. Start it first: in frontend/, "npm run build && npm start" ` +
        `(smoother than "npm run dev"). Set APP_URL if it runs elsewhere.`,
    );
  }
  const health = await getJson(new URL("/health", apiUrl));
  if (!health.ok || !health.body) {
    throw new Error(`The API does not answer at ${apiUrl}/health. Start it with "uvicorn api.main:app" (RUNBOOK §2). Set API_URL if it runs elsewhere.`);
  }
  if (health.body.db !== "ok") throw new Error(`The API reports db="${health.body.db}". Fix the database first (RUNBOOK §7).`);
  if (!Array.isArray(health.body.datapacks) || health.body.datapacks.length === 0) {
    throw new Error('No data pack is loaded. Build the synthetic one: "python -m seed.datapack --synthetic --name demo", then restart the API.');
  }
  const meta = await getJson(new URL("/api/meta/", apiUrl));
  if (!meta.ok || !meta.body) throw new Error(`GET ${apiUrl}/api/meta/ failed with ${meta.status}.`);
  return { health: health.body, meta: meta.body };
}

/** Create a dataset from a built-in sample and wait until it is processed. Returns its id. */
export async function loadSample(apiUrl, name, { timeoutMs = 120_000 } = {}) {
  const created = await getJson(new URL(`/api/samples/${encodeURIComponent(name)}/`, apiUrl), { method: "POST" });
  return processed(apiUrl, created, `Sample "${name}"`, timeoutMs);
}

/** Upload a file the scenes need (text, e.g. a GeoJSON area) and wait until it is processed. Returns its id. */
export async function uploadFile(apiUrl, filename, content, { timeoutMs = 120_000 } = {}) {
  const form = new FormData();
  form.append("file", new Blob([content]), filename);
  const created = await getJson(new URL("/api/files/", apiUrl), { method: "POST", body: form });
  return processed(apiUrl, created, filename, timeoutMs);
}

/** Wait until a newly created dataset is processed; returns its id. */
async function processed(apiUrl, created, what, timeoutMs) {
  if (created.status === 429) {
    throw new Error("The API rate limit was reached. For local renders set RATE_LIMIT_PER_HOUR=0 in .env and restart the API.");
  }
  if (!created.ok || !created.body?.id) {
    const message = created.body?.error?.message ?? created.text.slice(0, 300);
    throw new Error(`${what} could not be created (${created.status}): ${message}`);
  }
  let info = created.body;
  const deadline = Date.now() + timeoutMs;
  while (info.status !== "COMPLETED") {
    if (info.status === "FAILED") throw new Error(`${what} failed to process: ${info.error?.message ?? "unknown error"}`);
    if (Date.now() > deadline) throw new Error(`${what} was still ${info.status} after ${timeoutMs / 1000} s.`);
    await new Promise((resolve) => setTimeout(resolve, 500));
    const next = await getJson(new URL(`/api/files/${info.id}/`, apiUrl));
    if (!next.ok || !next.body) throw new Error(`Reading dataset ${info.id} failed (${next.status}).`);
    info = next.body;
  }
  return info.id;
}

/**
 * The data note for the end card: synthetic packs are labelled as such; real packs are
 * credited with their attributions.
 */
export function dataNote(meta, health, { basemap }) {
  const loaded = new Set(health.datapacks);
  const packs = (meta.datapacks ?? []).filter((p) => loaded.has(p.name));
  const map = basemap === "none" ? "" : " Basemap © OpenFreeMap, © OpenStreetMap contributors.";
  if (packs.length === 0 || packs.every((p) => p.synthetic)) {
    return `Terrain, population, obstacles and zones in this video come from the synthetic demo data pack.${map}`;
  }
  const credits = [...new Set(packs.flatMap((p) => p.attribution ?? []))];
  return `Data: ${credits.length ? credits.join("; ") : "see DATA_LICENSES.md"}.${map}`;
}
