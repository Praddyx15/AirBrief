import type { Metadata } from "next";
import Link from "next/link";
import { ArrowRight, BookOpen, Download, FlaskConical, Github, MapPin, ShieldAlert, Upload, Workflow } from "lucide-react";
import { Button } from "@/components/ui/button";
import { CopyButton } from "@/components/layout/copy-button";
import { RUNBOOK_URL } from "@/components/layout/demo-banner";
import { HeroMap } from "@/components/layout/hero-map";
import { ModuleIcon } from "@/components/layout/module-icon";
import { SiteFooter, DATA_ATTRIBUTIONS, DISCLAIMER } from "@/components/layout/site-footer";
import { SiteHeader } from "@/components/layout/site-header";
import { MODULE_LIST } from "@/lib/modules";

export const metadata: Metadata = {
  title: { absolute: "AirBrief — geospatial checks for drone and eVTOL planning" },
  description:
    "Upload a Shapefile, KML, GeoJSON, GPX or CSV, or drop a pin, and get accurate measurements and evidence for planning: indicative airspace, SORA 2.5 ground risk, daylight and magnetic declination, line of sight, survey and spraying effort, drone-show checks and flight audits. Open source, runs offline.",
};

const QUICK_START = `git clone https://github.com/Praddyx15/AirBrief.git
cd AirBrief
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\\Scripts\\Activate.ps1
pip install -e ".[api,dev]"
cp .env.example .env
python -m seed.samples --dest samples/
python -m seed.datapack --synthetic --name demo
uvicorn api.main:app --reload    # terminal 1: API on :8000

cd frontend && npm install && npm run dev   # terminal 2: web on :3000`;

const STEPS = [
  {
    Icon: Upload,
    title: "Upload",
    text: "Drop a zipped Shapefile, KML/KMZ, GeoJSON, GPX or CSV, start from a sample, or drop a pin, draw or type an operation on the map. The coordinate system is detected and shown.",
  },
  {
    Icon: Workflow,
    title: "Analyse",
    text: "Every feature is measured in a local projection and cross-checked geodesically. Then run a module on the features you select.",
  },
  {
    Icon: Download,
    title: "Export",
    text: "Put several results on a one-page evidence brief with provenance, or export them as JSON, GeoJSON, KML or CSV. Every view has a shareable URL.",
  },
] as const;

const RELATED = [
  {
    name: "FlightMD",
    href: "https://github.com/Praddyx15/FlightMD",
    text: "Post-flight vehicle health from drone logs. AirBrief audits its reports against height limits and zones.",
  },
  {
    name: "UAOP",
    href: "https://github.com/Praddyx15/UAOP",
    text: "The operations platform: fleets, pilots, missions and approvals.",
  },
] as const;

export default function LandingPage() {
  return (
    <>
      <SiteHeader />
      <main id="main" tabIndex={-1} className="outline-none">
        <section aria-labelledby="hero-title" className="border-b border-border">
          <div className="container grid items-center gap-10 py-14 md:py-20 lg:grid-cols-[1fr_1.05fr]">
            <div className="space-y-6">
              <p className="inline-flex items-center gap-2 rounded border border-border bg-surface px-2 py-1 text-xs font-medium text-fg-muted">
                <FlaskConical className="size-3.5 text-accent" aria-hidden />
                Open source · MIT · runs offline
              </p>
              <h1
                id="hero-title"
                className="max-w-[20ch] text-balance text-4xl font-semibold leading-[1.1] tracking-tight text-fg md:text-5xl"
              >
                Know your ground and airspace before you fly.
              </h1>
              <p className="max-w-[60ch] text-pretty text-lg leading-relaxed text-fg-muted">
                Upload a Shapefile, KML or GPX and get measurements you can trust, then gather evidence on indicative
                airspace, ground risk, daylight and wind, line of sight, survey effort, drone shows and recorded flights
                in one workspace.
              </p>
              <div className="flex flex-wrap items-center gap-3">
                <Button asChild size="lg">
                  <Link href="/w">
                    Open workspace
                    <ArrowRight aria-hidden />
                  </Link>
                </Button>
                <Button asChild size="lg" variant="outline">
                  <Link href="/w/view?m=airspace&plan=pin">
                    <MapPin aria-hidden />
                    Check a location
                  </Link>
                </Button>
                <Button asChild size="lg" variant="outline">
                  <Link href="/w#samples">Try a sample</Link>
                </Button>
              </div>
              <p className="max-w-[60ch] text-sm text-fg-muted">
                No file? Drop a pin, draw an area or type coordinates, and get indicative airspace evidence for it.
              </p>
            </div>
            <div className="mx-auto w-full max-w-[560px]">
              <HeroMap />
            </div>
          </div>
        </section>

        <section aria-labelledby="modules-title" className="border-b border-border">
          <div className="container py-14">
            <h2 id="modules-title" className="max-w-[30ch] text-balance text-2xl font-semibold tracking-tight">
              What you can check
            </h2>
            <p className="mt-2 max-w-[65ch] text-fg-muted">
              Nine modules share one map, one feature table and one URL. Results are indicative evidence for planning,
              not authorisation.
            </p>
            <ul className="mt-8 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              {MODULE_LIST.map((m) => (
                <li key={m.id} className="flex flex-col gap-2 rounded border border-border bg-surface p-4">
                  <span className="flex size-9 items-center justify-center rounded border border-border bg-elevated">
                    <ModuleIcon module={m.id} className="size-4 text-accent" />
                  </span>
                  <h3 className="text-sm font-semibold">{m.label}</h3>
                  <p className="text-sm leading-relaxed text-fg-muted">{m.description}</p>
                </li>
              ))}
              <li className="flex flex-col justify-between gap-3 rounded border border-dashed border-border p-4">
                <p className="text-sm text-fg-muted">Have a FlightMD report? Audit its height and zones directly.</p>
                <Link
                  href="/w/view?m=flight-audit"
                  className="inline-flex items-center gap-1.5 text-sm font-medium text-accent-text underline-offset-4 hover:underline"
                >
                  Audit a FlightMD flight
                  <ArrowRight className="size-4" aria-hidden />
                </Link>
              </li>
            </ul>
          </div>
        </section>

        <section aria-labelledby="how-title" className="border-b border-border bg-surface">
          <div className="container py-14">
            <h2 id="how-title" className="text-balance text-2xl font-semibold tracking-tight">
              How it works
            </h2>
            <ol className="mt-8 grid gap-6 md:grid-cols-3">
              {STEPS.map(({ Icon, title, text }, i) => (
                <li key={title} className="flex gap-4">
                  <span
                    aria-hidden
                    className="num flex size-8 shrink-0 items-center justify-center rounded-full border border-accent/60 bg-accent/10 text-sm font-semibold text-fg"
                  >
                    {i + 1}
                  </span>
                  <div className="space-y-1.5">
                    <h3 className="flex items-center gap-2 font-semibold">
                      <Icon className="size-4 text-accent" aria-hidden />
                      {title}
                    </h3>
                    <p className="max-w-[45ch] text-sm leading-relaxed text-fg-muted">{text}</p>
                  </div>
                </li>
              ))}
            </ol>
          </div>
        </section>

        <section aria-labelledby="related-title" className="border-b border-border">
          <div className="container py-14">
            <h2 id="related-title" className="text-balance text-2xl font-semibold tracking-tight">
              Works with FlightMD and UAOP
            </h2>
            <p className="mt-2 max-w-[65ch] text-fg-muted">
              Open-source tools for uncrewed aviation that hand data to each other.
            </p>
            <ul className="mt-8 grid gap-3 md:grid-cols-2">
              {RELATED.map((p) => (
                <li key={p.name} className="rounded border border-border bg-surface p-4">
                  <h3 className="font-semibold">
                    <a
                      href={p.href}
                      target="_blank"
                      rel="noreferrer"
                      className="inline-flex items-center gap-1.5 text-accent-text underline-offset-4 hover:underline"
                    >
                      <Github className="size-4" aria-hidden />
                      {p.name}
                      <span className="sr-only"> on GitHub (opens in a new tab)</span>
                    </a>
                  </h3>
                  <p className="mt-2 text-sm leading-relaxed text-fg-muted">{p.text}</p>
                </li>
              ))}
            </ul>
          </div>
        </section>

        <section aria-labelledby="run-title" className="border-b border-border bg-surface">
          <div className="container grid gap-8 py-14 lg:grid-cols-[1fr_1.6fr]">
            <div className="space-y-3">
              <h2 id="run-title" className="text-balance text-2xl font-semibold tracking-tight">
                Run it yourself
              </h2>
              <p className="max-w-[55ch] text-fg-muted">
                Python 3.11+ and Node 18+. No API keys and no accounts: SQLite, local files and an offline synthetic
                data pack are the defaults. Docker works too.
              </p>
              <a
                href={RUNBOOK_URL}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-1.5 font-medium text-accent-text underline-offset-4 hover:underline"
              >
                <BookOpen className="size-4" aria-hidden />
                Read the RUNBOOK
                <span className="sr-only"> (opens in a new tab)</span>
              </a>
            </div>
            <figure className="min-w-0 rounded border border-border bg-bg">
              <figcaption className="flex items-center justify-between border-b border-border px-3 py-1.5 text-xs text-fg-muted">
                <span>Native quick start (bash)</span>
                <CopyButton text={QUICK_START} label="Copy quick-start commands" targetId="quick-start-commands" />
              </figcaption>
              <pre
                id="quick-start-commands"
                className="overflow-x-auto p-4 font-mono text-[13px] leading-relaxed text-fg"
                tabIndex={0}
              >
                <code>{QUICK_START}</code>
              </pre>
            </figure>
          </div>
        </section>

        <section aria-labelledby="data-title">
          <div className="container py-14">
            <h2 id="data-title" className="text-balance text-2xl font-semibold tracking-tight">
              Open data &amp; licences
            </h2>
            <div className="mt-6 grid gap-6 lg:grid-cols-[1.6fr_1fr]">
              <ul className="space-y-2 text-sm">
                {DATA_ATTRIBUTIONS.map((a) => (
                  <li key={a.label} className="max-w-[75ch] text-fg-muted">
                    <span className="font-medium text-fg">{a.label}: </span>
                    {a.text}
                  </li>
                ))}
                <li className="max-w-[75ch] text-fg-muted">
                  <span className="font-medium text-fg">Power lines, assembly venues: </span>© OpenStreetMap contributors,
                  ODbL
                </li>
                <li className="max-w-[75ch] text-fg-muted">
                  <span className="font-medium text-fg">Magnetic field: </span>World Magnetic Model 2025, NOAA NCEI / BGS
                  (public domain), shipped with the code
                </li>
                <li className="max-w-[75ch] text-fg-muted">
                  <span className="font-medium text-fg">Code: </span>
                  MIT licence. Samples and the synthetic demo pack are generated by AirBrief and contain no third-party
                  data.
                </li>
              </ul>
              <div className="flex gap-3 rounded border border-warn/50 bg-warn/10 p-4 text-sm" role="note">
                <ShieldAlert className="mt-0.5 size-5 shrink-0 text-warn" aria-hidden />
                <div className="space-y-1.5">
                  <p className="font-semibold text-fg">{DISCLAIMER}.</p>
                  <p className="text-fg-muted">
                    Airspace zones are illustrative rules applied around aerodrome reference points. Always obtain the
                    official permission for your flight.
                  </p>
                </div>
              </div>
            </div>
          </div>
        </section>
      </main>
      <SiteFooter />
    </>
  );
}
