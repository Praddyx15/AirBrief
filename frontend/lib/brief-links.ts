"use client";

/**
 * Briefs built in this browser, by `provenance.brief_sha256`. The API keeps no briefs
 * (`/api/briefs/` is stateless), so this list is what "a local brief" means: when a FlightMD
 * report carries `metadata.planning_brief_sha256` (pasted into FlightMD's upload form from the
 * brief), the flight audit links back to the brief it was planned with.
 */

import * as React from "react";
import { briefHref } from "@/components/report/brief-href";

const KEY = "airbrief-briefs";
const LIMIT = 50;
const SHA256 = /^[0-9a-f]{64}$/;

export interface BriefLink {
  sha256: string;
  title: string;
  datasetId: string | null;
  analysisIds: string[];
  generatedUtc: string;
}

function isLink(value: unknown): value is BriefLink {
  if (!value || typeof value !== "object") return false;
  const v = value as Record<string, unknown>;
  return (
    typeof v.sha256 === "string" &&
    SHA256.test(v.sha256) &&
    typeof v.title === "string" &&
    (v.datasetId === null || typeof v.datasetId === "string") &&
    Array.isArray(v.analysisIds) &&
    v.analysisIds.every((id) => typeof id === "string") &&
    typeof v.generatedUtc === "string"
  );
}

/** The remembered briefs, newest first (empty when storage is blocked or the entry is corrupt). */
export function loadBriefLinks(): BriefLink[] {
  try {
    const raw = window.localStorage.getItem(KEY);
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed.filter(isLink).slice(0, LIMIT) : [];
  } catch {
    return [];
  }
}

export function rememberBrief(link: BriefLink): void {
  if (!SHA256.test(link.sha256)) return;
  const links = [link, ...loadBriefLinks().filter((l) => l.sha256 !== link.sha256)].slice(0, LIMIT);
  try {
    window.localStorage.setItem(KEY, JSON.stringify(links));
  } catch {
    // storage full or blocked: the link just isn't remembered
  }
}

export function findBriefLink(sha256: string | null | undefined): BriefLink | null {
  if (!sha256) return null;
  const want = sha256.toLowerCase();
  return loadBriefLinks().find((l) => l.sha256 === want) ?? null;
}

/** Where to reopen a remembered brief: brief mode with its dataset and first analysis ticked. */
export function briefLinkHref(link: BriefLink): string {
  return briefHref(link.datasetId, link.analysisIds[0] ?? null);
}

/** The remembered brief with this hash; read after mount, as localStorage is client-only. */
export function useBriefLink(sha256: string | null | undefined): BriefLink | null {
  const [link, setLink] = React.useState<BriefLink | null>(null);
  React.useEffect(() => {
    setLink(findBriefLink(sha256));
  }, [sha256]);
  return link;
}
