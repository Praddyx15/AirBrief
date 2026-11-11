# Security

## Reporting a problem

Please report it privately through **Security → Report a vulnerability** on this repository, not
in a public issue. Say what's affected, how to reproduce it and what an attacker could do. You'll
hear back within a week, and fixes ship as soon as they're ready (with credit if you like).

## How AirBrief is meant to be run

Visitors are anonymous: there are no accounts. Every upload, link and parameter is treated as
hostile, and the person running the server (who controls `.env`, the data packs and the signing
key) is the only one trusted.

Datasets and analyses get random ids. Knowing an id lets you *see* that item, which is how sharing
works; deleting or cancelling it needs the owner token the browser got when it was created.

## What it guards against

- Oversized or malicious uploads: size limits checked twice, zip-slip and zip-bomb guards, XML
  entity attacks blocked, caps on features, vertices and nesting.
- Expensive requests: per-IP rate limits, bounded map and search queries, job time limits, a disk
  quota with a daily upload budget per address.
- Server-side request forgery: FlightMD links are fetched over https from public addresses only
  (or from the one FlightMD server you configure), with every redirect re-checked.
- Leaks: no stack traces, paths or resolver details in responses; logs hash client addresses.
- Tampering: data packs carry SHA-256 hashes, and brief bundles can be signed with Ed25519.
- The usual web issues: strict CSP, no framing, no cookies, CORS limited to your own origin, CSV
  exports protected against formula injection.

`APP_ENV=production` switches all of this to safe defaults; [`DEPLOYMENT.md`](DEPLOYMENT.md) has
the settings.

## Privacy

The web app has no accounts, cookies, analytics or third-party scripts. The only outside request is
for basemap tiles, and you can turn the basemap off in Settings. The server keeps what you upload
and the analyses you run, and deletes them after 72 hours in production (`UPLOAD_TTL_HOURS`) or
when you delete them. Backups and logs need their own retention.

## Known limits

- Anyone with a link can read that item, so don't put personal or sensitive data on a public
  instance.
- Rate limits and quotas are counted per API process. Behind several processes, use your reverse
  proxy's limiter too.
- Development mode trusts the local network. Use production mode for anything others can reach.
- Results are evidence for planning, not authorisation, and not safety-of-life outputs.
