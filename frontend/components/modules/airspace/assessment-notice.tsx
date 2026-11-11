"use client";

/**
 * Airspace results: the "not assessed" caution banner (no data pack covered the area, so
 * aerodrome zones were not checked and the result is never an all-clear), the red-zones notice
 * and the permanent "Verify on Digital Sky" link to the official DGCA airspace map.
 */

import { ExternalLink } from "lucide-react";
import { useUnits } from "@/lib/store";
import { localizeUnits } from "@/lib/format";
import type { AirspaceResult } from "@/lib/types";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";

/** DGCA Digital Sky: the authoritative Indian drone airspace map and permission portal. */
export const DIGITAL_SKY_URL = "https://digitalsky.dgca.gov.in";

export function DigitalSkyLink() {
  return (
    <p className="text-xs text-fg-muted">
      <a
        href={DIGITAL_SKY_URL}
        target="_blank"
        rel="noopener noreferrer"
        className="inline-flex items-center gap-1 font-medium text-accent-text underline-offset-4 hover:underline"
      >
        Verify on Digital Sky
        <ExternalLink aria-hidden className="size-3.5" />
        <span className="sr-only"> (opens in a new tab)</span>
      </a>{" "}
      — the official DGCA airspace map and permission portal. These zones are indicative only.
    </p>
  );
}

/**
 * Always shown with the airspace check, in the form and the results: red zones are not open
 * data, so a "free" area here is never proof that no red zone exists.
 */
export function RedZonesBanner() {
  return (
    <Alert variant="warn" role="note" aria-label="Red zones notice">
      <AlertTitle>Red zones are not published as open data</AlertTitle>
      <AlertDescription>
        Only red zones from the data pack and your own layers are checked. Temporary red zones (up to 96 hours,
        Drone Rules 2021, rule 24) are published only on Digital Sky, so verify every flight on the{" "}
        <a
          href={DIGITAL_SKY_URL}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1 font-medium text-accent-text underline underline-offset-4"
        >
          Digital Sky map
          <ExternalLink aria-hidden className="size-3.5" />
          <span className="sr-only"> (opens in a new tab)</span>
        </a>
        .
      </AlertDescription>
    </Alert>
  );
}

/** Shown when `assessment_complete` is false (absent in older results: treated as complete). */
export function NotAssessedBanner({ result }: { result: AirspaceResult }) {
  const units = useUnits();
  if (result.assessment_complete !== false) return null;
  const reason = result.issues.find((i) => i.code === "not_assessed")?.message;
  return (
    <Alert variant="warn" role="status">
      <AlertTitle>Airspace not assessed</AlertTitle>
      <AlertDescription>
        {reason ? localizeUnits(reason, units) : "No data pack covers this area: aerodrome zones were not checked."} This is
        not an all-clear.{" "}
        <a
          href={DIGITAL_SKY_URL}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1 font-medium text-accent-text underline underline-offset-4"
        >
          Check the area on Digital Sky
          <ExternalLink aria-hidden className="size-3.5" />
          <span className="sr-only"> (opens in a new tab)</span>
        </a>
        .
      </AlertDescription>
    </Alert>
  );
}
