# Real data: the Bengaluru pack and a national zone file

The tests and the demo use a synthetic pack, so they never depend on the internet. This file is
about the real thing: a data pack built from public sources, and an official zone file, both
checked end to end. Network tests never run in CI (their sources change and rate-limit), so run
them here before a release.

## The sources

Checked on 2026-10-30, in a browser and from the command line. All free, no account, no key.

| Layer | Source | Licence | Notes |
|---|---|---|---|
| Aerodromes, runways | OurAirports `airports.csv`, `runways.csv` (https://ourairports.com/data/) | Public domain | Rebuilt every night, mirrored on GitHub |
| Terrain | Copernicus DEM GLO-30 on the AWS Open Data registry (https://registry.opendata.aws/copernicus-dem/) | Copernicus DEM licence (free, with attribution) | Cloud-optimised GeoTIFF tiles, read over HTTPS without an AWS account |
| Population | WorldPop 2020, 1 km, UN-adjusted, India (https://hub.worldpop.org/geodata/summary?id=36811, DOI 10.5258/SOTON/WP00671) | CC BY 4.0 | 17.5 MB for the whole country |
| Obstacles, stations, water, venues, power, aerodrome boundaries | OpenStreetMap through the Overpass API | ODbL 1.0 | The main server and two mirrors, tried in turn; all three were overloaded at times on 2026-10-30 |
| Zone file (test only) | FOCA, Swiss UAS geographical zones in ED-269 (https://opendata.swiss/en/dataset/geografische-uas-gebiete-der-schweiz) | Open use, source to be named | About 17 MB, 1,256 zones; not part of any pack |

## Build the Bengaluru pack

```bash
python -m seed.datapack --region bengaluru        # about 80 km × 78 km around the city
```

It takes 15 to 45 minutes, mostly waiting for Overpass. Each layer is tried on its own; when the
Overpass servers are busy a layer can fail (`[pois] FAILED: All Overpass mirrors failed`) and the
command exits 1 with the rest of the pack written. Fetch only what is missing later:

```bash
python -m seed.datapack --refetch data/datapacks/bengaluru            # the layers its build missed
python -m seed.datapack --refetch data/datapacks/bengaluru --layers pois,water   # or name them
```

The pack is sealed again afterwards. Downloads are cached in `data/datapacks/.cache/`, so a
second full build only repeats the Overpass queries.

What the 2026-10-30 build held (the OSM counts change as people map):

| Layer | Content |
|---|---|
| `aerodromes` | 8, including VOBL (Kempegowda), VOBG (HAL) and VOYK (Yelahanka) |
| `aerodrome_perimeters`, `runways` | 5 and 5 |
| `dem` | 2,700 × 2,520 cells of 1 arc-second, 635 to 1,215 m |
| `population` | 90 × 84 cells of 1 km, 15.4 million people in all, at most 54,092 in one cell |
| `obstacles` | 12,869 (masts, towers, chimneys, tall buildings) |
| `pois` | 245 (rail and metro stations, bus stations); fetched with `--refetch` after the first build lost it |
| `water` | 2,028 |
| `assemblies` | 102 (stadiums, sports centres with a capacity, markets) |
| `power` | 1,073 |
| `zones` | 0 (add your own) |

The manifest records, for every OSM layer, which server answered and the date of its data
(`osm_base`). On 2026-10-30 one mirror served data from 2026-07-15 for the aerodrome
boundaries; `--refetch` cannot redo that layer (it is matched to the aerodromes), so rebuild the
pack when the main server answers if that matters.

## Check it

```bash
python -c "from airbrief_core.data.datapack import DataPack; p = DataPack('data/datapacks/bengaluru'); print(p.integrity, p.manifest.content_sha256)"
```

`verified` and the content hash. Then use it in the app:

```bash
DEFAULT_DATAPACK=bengaluru uvicorn api.main:app --host 127.0.0.1 --port 8000
```

On a 600 m square of farmland about 8 km south-west of Kempegowda airport
(`77.640,13.140` to `77.6455,13.1455`), the 2026-10-30 pack gave:

| Check | Result |
|---|---|
| Airspace at 100 m | Verdict "caution"; nearest aerodromes VOYK 3.7 km, VOBL 8.8 km |
| Ground risk | Intrinsic GRC 6, density band below 50,000 people/km² |
| Environment, 2026-11-02, UTC+05:30 | Sunrise 06:13, sunset 17:52; magnetic declination −1.05°; nearest power line 286 m |
| Survey, line of sight | Complete, with the pack's hash on the result |

Every result carries `datapack_sha256`, the pack's content hash, and the brief's sources list
credits OurAirports, Copernicus, WorldPop and OpenStreetMap instead of "synthetic".

## Network tests

```bash
pytest -m network --run-network       # or: make test-network
```

Six tests: OurAirports downloads and holds VOBL, and five national zone files go through the
whole upload pipeline, every zone measured: Switzerland (FOCA, ED-269, about 1,300 zones, five
self-crossing rings repaired and flagged), Spain (ENAIRE's aeronautical zones, ED-318, 1,672),
Ireland (IAA, ED-318, found on its page because the file name changes with each issue),
Luxembourg (DAC, ED-269) and the German DFS ED-318 sample. `docs/features/zones.md` has what each
file looks like and why the reader accepts all of them. The Swiss file once had a zone with 63
volumes, which is why the per-zone cap is 100.

To try the zone file in the app: download
`https://data.geo.admin.ch/ch.bazl.einschraenkungen-drohnen/einschraenkungen-drohnen/einschraenkungen-drohnen_4326.json`,
upload it on the home page, and it opens as a zone dataset of about 1,300 yellow zones over
Switzerland. For a check against real terrain there, build a pack around it, for example
`python -m seed.datapack --bbox 8.4,47.3,8.7,47.5 --name zurich --iso3 CHE`, and run the
airspace check with that zone dataset under *Zones from datasets*. The DGCA aerodrome bands are
Indian rules; for Switzerland pick **Custom rules** as the zone profile.

## Another area

```bash
python -m seed.datapack --bbox 85.1,27.5,85.6,27.9 --name kathmandu --iso3 NPL
```

At most 2° × 2°; `--iso3` picks the country's WorldPop file (without it the pack has no
population layer). `--lite` uses the 90 m DEM instead of 30 m.
