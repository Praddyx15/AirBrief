# Evidence integrity: signed bundles, data-pack hashes, honest airspace briefs

Three things make an AirBrief brief trustworthy to someone who did not run it:

- **Signed bundles** (MISS-01). An instance can sign every brief bundle with its own Ed25519 key.
  A recipient checks the bundle with one command and the instance's published public key.
- **Data-pack hashes** (MISS-02). Every data pack records the SHA-256 of each layer file. The API
  checks the files when it opens a pack and refuses a pack that changed. Each analysis records the
  content hash of the pack it read.
- **Non-default safety parameters are flagged** (WEAK-09). An airspace check (or a flight audit)
  run without the data pack's zones, with custom zone rules, user zones or looser search settings
  gets a cross-check warning and a banner that name those parameters.

None of this makes a brief an authorisation. It is still evidence for planning, not approval.

## Data-pack integrity

### What the seed writes

`python -m seed.datapack` (synthetic and real packs) writes into `manifest.json`:

- `layers.<key>.sha256`: the SHA-256 of that layer's file;
- `content_sha256`: the SHA-256 of the lines `"<sha256>  <file>\n"` (the `sha256sum` format, `/`
  separators) sorted by file name. One value then stands for the whole pack;
- `layers.<key>.source_url` for OpenStreetMap layers: the Overpass server that answered. When the
  main server (`overpass-api.de`) is overloaded the seed falls back to a mirror. The mirror then
  shows in the manifest and in the brief's provenance ("osm_overpass via overpass.private.coffee"),
  and the seed prints `[obstacles] served by the mirror …`.

Anyone can recompute `content_sha256` from the layer files. List the files named in the manifest:

    cd data/datapacks/<name>
    sha256sum aerodromes.csv assemblies.geojson dem.tif obstacles.geojson pois.geojson \
        population.tif power.geojson water.geojson zones.geojson | LC_ALL=C sort -k2 | sha256sum

On Windows Git Bash `sha256sum` marks binary files with `*`. Add `| sed 's/ \*/  /'` before
`sort` to get the same result.

`content_sha256` covers the layer files, not the manifest's own fields (name, `synthetic` flag,
bbox, sources). The hashes show that the files are the ones the manifest was built with. They do not
show who built it. To be sure you have a particular builder's pack, compare `content_sha256` with
the value that builder published.

### Integrity states

The API checks every pack when it opens it, and again whenever the manifest or a layer file
changes. The result shows in `GET /api/meta/` (`datapacks[].integrity`, `datapacks[].content_sha256`)
and on the Settings page (Data packs and limits):

| `integrity` | Settings badge | Meaning | What happens |
|---|---|---|---|
| `verified` | Files match their hashes | Every layer file matches its SHA-256 and the hashes match `content_sha256` | Normal use. Each analysis records the pack's `content_sha256` |
| `unverified` | No hashes to check | An older pack built before packs carried hashes | The pack is used, but every analysis that reads it gets the warning `datapack_unverified`. Rebuild the pack to remove it |
| `mismatch` | Files changed: not used | A layer file was edited or deleted, a layer was dropped from the manifest, or a hash was changed | The pack is unusable. Analyses that read it fail with `datapack_integrity` (HTTP 503 on synchronous endpoints such as `/api/context/`). The Settings page shows a red alert. The server log names the files |

Hashing a large pack takes a while, so each file's hash is cached per (path, size, modification
time). A pack opened again, or reopened after another file changed, re-hashes only the changed
files. A file rewritten with the same size and modification time is re-hashed only after the API
restarts. A change is noticed the next time an analysis or request picks the pack. An analysis that
is already running keeps the pack it started with.

### After editing a layer on purpose

Real packs ship an empty `zones.geojson` for you to fill with restricted areas. After editing a
layer, record the new hashes or the API will refuse the pack:

    python -m seed.datapack --seal data/datapacks/<name>

The command prints the new `content_sha256`. Give that value to anyone who compares briefs against
your pack.

### In the analysis and the brief

- Every analysis envelope (export JSON, the bundle's `analyses/NN_<kind>.json`) has
  `datapack_sha256`: the `content_sha256` of the pack it read. It is `null` without a pack or with an
  unverified one. The API stores it in `analyses.datapack_sha256` (migration
  `0004_analysis_datapack_sha256`).
- The brief's provenance lists, for each data pack, `content_sha256` and each layer's `sha256` and
  `source_url`. For each analysis it lists `datapack_sha256`.
- If the installed pack's content changed after an analysis read it, the brief adds the warning
  cross-check `datapack_changed` ("Run the analysis again").

## Signed brief bundles

### Create a signing key (operator)

The API needs the `cryptography` package. It is in the `api` extra, or install the `signing` extra
on its own:

    pip install "airbrief-core[signing]"
    python -m airbrief_core.verify --new-key /etc/airbrief/brief-signing.pem

The command writes an unencrypted PEM (PKCS#8) Ed25519 private key with owner-only permissions
(on POSIX) and never overwrites an existing file. It prints:

    wrote an Ed25519 private key to /etc/airbrief/brief-signing.pem: keep it secret, set BRIEF_SIGNING_KEY_FILE to it
    key id      3f9c0e5b1a7d2c48
    public key  q0n6...base64...=

Then set the key in `.env` (or the service environment) and restart the API:

    BRIEF_SIGNING_KEY_FILE=/etc/airbrief/brief-signing.pem

| Setting | Default (development) | Default (production) | Effect |
|---|---|---|---|
| `BRIEF_SIGNING_KEY_FILE` | empty: bundles not signed | empty: bundles not signed | Path to the PEM Ed25519 private key. The API refuses to start if the file is missing, not an Ed25519 key, or `cryptography` is not installed. It never falls back to unsigned bundles in silence |

Rules for the key:

- Only the API's user may read it.
- Never commit it, and never put it in the `data/` directory you back up with uploads.
- Back it up separately.
- One key per instance. The key id is the first 16 hex characters of the SHA-256 of the raw
  32-byte public key.

To rotate the key, create a new one and publish its public key next to the old one. Old bundles
stay verifiable with the old public key.

### What a signed bundle holds

- `signature.json`: `{"algorithm": "Ed25519", "key_id": "...", "signed_file": "provenance.json",
  "signature": "<base64>"}`. The signature covers the exact bytes of `provenance.json`.
- `provenance.json` lists the size and SHA-256 of every other entry except `signature.json`. The
  signature therefore covers every file in the bundle.
- The brief (`brief.html`, `brief.json`, and the page in the app) says **"Bundles from this
  instance are signed (key 3f9c0e5b1a7d2c48)"** and that the brief on its own carries no signature:
  only the downloaded bundle is signed, and the brief hash covers this text, so it reads the same
  everywhere. Without a key it says **"Not signed: these SHA-256 values detect
  accidental corruption, not deliberate edits; anyone who changes a file can recompute them."**

### Publish the public key

- `GET /api/meta/` publishes `signing: {"algorithm": "Ed25519", "key_id", "public_key"}` (raw key,
  base64). It is `null` when the instance does not sign.
- The Settings page (Data packs and limits) shows the key id and the public key for copying.
- Recipients must get the key from a channel they already trust: your website, an email you sign,
  or a contract annex. A key read from a bundle, or from a server the bundle points to, proves
  nothing. Anyone who edits a bundle can sign it with a key of their own.

### Verify a bundle (recipient)

    pip install "airbrief-core[signing]"
    python -m airbrief_core.verify airbrief_brief_1a2b3c4d5e6f.zip --public-key q0n6...base64...=

`--public-key` also accepts a file that holds the base64 key or a PEM public key. The command reads
the zip in memory with the upload code's archive guards (no absolute paths, no `..`, no symlinks, no
encrypted entries, no duplicate names, at most 1,000 files and 256 MB uncompressed). It extracts
nothing. It then:

1. re-hashes every entry listed in `provenance.json` and compares size and SHA-256;
2. refuses entries that are missing or not listed;
3. checks that `signature.json` is an Ed25519 signature of `provenance.json` by the given key.

Output of a good bundle:

    bundle  airbrief_brief_1a2b3c4d5e6f.zip
    files   14 match provenance.json
    signed  yes, Ed25519 signature valid for key 3f9c0e5b1a7d2c48
    result  OK

The exit status is 0 when every check passed and 1 otherwise. Each failure is printed as a `FAIL`
line:

| Line | Meaning |
|---|---|
| `brief.json: content differs from provenance.json (SHA-256 or size)` | The file was changed after the bundle was made |
| `the signature does not match provenance.json: it was changed after signing` | Someone edited a file and recomputed its hash |
| `signed with key …, not with the given key …` | Another instance (or someone else) signed it, or you have the wrong key |
| `not signed: no signature.json, but a public key was given` | The signature is missing. It may have been removed |
| `x: in the bundle but not listed in provenance.json` | A file was added |

Without `--public-key` only the hashes are checked. A signed bundle then prints `signed  by key …,
NOT checked`, and an unsigned one prints `signed  no: the SHA-256 values detect accidental
corruption, not deliberate edits`.

A valid signature proves that the holder of the instance key produced this bundle and that nothing
in it changed since. It does not prove the evidence is right: the brief's cross-checks and banners
still apply.

## Non-default safety parameters (WEAK-09)

A brief flags an analysis whose parameters weaken its zone or airspace evidence compared with the
defaults. This covers honest mistakes and analyses another visitor added to a shared dataset
(threat-model AS-1). Flagged parameters:

| Kinds | Parameter | Flagged when |
|---|---|---|
| airspace, flight audit | `include_datapack_zones` | `false` (the data pack's restricted zones were left out) |
| airspace, flight audit, eVTOL siting and corridor | `zone_rules` | given and different from every named rule set of `zone_profile` (DGCA 2021, conservative); a preset `zone_profile` alone is not flagged |
| airspace, flight audit | `user_zones` | any zone drawn by the user |
| airspace | `obstacle_search_m` | below 300 m |
| airspace | `vertical_clearance_m` | below 30 m |
| airspace | `min_agl_m` | below 30 m |
| airspace | `profile_spacing_m` | above 30 m (coarser terrain profile) |
| airspace | `takeoff_elevation_m` | given (replaces the DEM's take-off elevation) |

Each flagged analysis gets:

- a **warning cross-check** `non_default_parameters`, for example "Airspace & terrain ran with
  non-default safety parameters: data-pack zones left out (include_datapack_zones: false); custom
  aerodrome zone rules instead of the defaults (zone_rules). Check they were intended…";
- a **banner** in its airspace (or flight audit) part: "Run with non-default safety parameters: …".

Both show in `brief.json`, in `brief.html` and on the brief page in the app.

## Manual acceptance check

1. Build a pack: `python -m seed.datapack --synthetic --name demo --dest <tmp>`. Its manifest has a
   `sha256` per layer and a `content_sha256`. The `sha256sum` recipe above gives the same value.
2. Start the API on that pack and open Settings → Data packs and limits. The demo pack shows
   "Files match their hashes", and the page says bundles are not signed.
3. Edit `<tmp>/demo/zones.geojson` (delete a feature). The badge turns to "Files changed: not used"
   with a red alert, and an airspace check fails with `datapack_integrity`. Run
   `python -m seed.datapack --seal <tmp>/demo` and the pack is verified again.
4. Create a key with `python -m airbrief_core.verify --new-key <tmp>/key.pem`, set
   `BRIEF_SIGNING_KEY_FILE`, and restart. Settings shows the key id and public key.
5. Load the Bengaluru survey sample, run an airspace check, create a brief and download the bundle.
   The provenance says "Bundles from this instance are signed (key …)". Running `python -m airbrief_core.verify
   <bundle.zip> --public-key <public key>` prints `result  OK`.
6. Change one byte of `brief.json` in the zip. The verifier prints `FAIL brief.json: content differs
   from provenance.json` and exits with 1.
7. Through the API, run an airspace check of the same dataset with
   `{"include_datapack_zones": false}` and put it in a brief. The cross-checks list "…ran with
   non-default safety parameters: data-pack zones left out (include_datapack_zones: false)" and
   the airspace card shows the same parameters in a banner.
