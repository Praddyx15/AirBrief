/**
 * Local date and time helpers for the environment form. The API computes everything in UTC;
 * the form sends the local date, local HH:MM window times and the zone's offset in minutes east
 * of UTC (`utc_offset_min`), which only labels the times shown back.
 */

import { formatUtcOffset } from "@/lib/format";

const ISO_DATE = /^(\d{4})-(\d{2})-(\d{2})$/;
const HHMM = /^([01]\d|2[0-3]):([0-5]\d)$/;

/** "YYYY-MM-DD" with a real calendar date. */
export function isIsoDate(text: string): boolean {
  const m = ISO_DATE.exec(text);
  if (!m) return false;
  const [y, mo, d] = [Number(m[1]), Number(m[2]), Number(m[3])];
  const date = new Date(Date.UTC(y, mo - 1, d));
  return date.getUTCFullYear() === y && date.getUTCMonth() === mo - 1 && date.getUTCDate() === d;
}

/** "HH:MM" on a 24 h clock. */
export function isHhmm(text: string): boolean {
  return HHMM.test(text);
}

function pad(n: number): string {
  return String(n).padStart(2, "0");
}

/** Today in the browser's zone, "YYYY-MM-DD". */
export function todayLocal(now: Date = new Date()): string {
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}

/** Minutes after midnight → "HH:MM" (wrapping at 24 h). */
export function minutesToHhmm(minutes: number): string {
  const m = ((Math.round(minutes) % 1440) + 1440) % 1440;
  return `${pad(Math.floor(m / 60))}:${pad(m % 60)}`;
}

/** The default planned window: from the next quarter hour, one hour long. */
export function defaultWindow(now: Date = new Date()): { start: string; end: string } {
  const start = Math.ceil((now.getHours() * 60 + now.getMinutes() + 1) / 15) * 15;
  return { start: minutesToHhmm(start), end: minutesToHhmm(start + 60) };
}

/**
 * The default date and window together: today from the next quarter hour, or tomorrow when that
 * quarter hour is midnight (after 23:45 the window would otherwise start at today's 00:00, in the past).
 */
export function defaultPlan(now: Date = new Date()): { date: string; start: string; end: string } {
  const startMinute = Math.ceil((now.getHours() * 60 + now.getMinutes() + 1) / 15) * 15;
  const day = startMinute >= 1440 ? new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1) : now;
  return { date: todayLocal(day), ...defaultWindow(now) };
}

/**
 * The browser zone's offset (minutes east of UTC) at a local date and time, so a window in
 * summer time gets the summer offset. Falls back to the current offset for invalid input.
 */
export function browserOffsetMinutes(date: string, time: string | null): number {
  const d = ISO_DATE.exec(date);
  if (!d || !isIsoDate(date)) return -new Date().getTimezoneOffset();
  const t = time && HHMM.exec(time);
  const hours = t ? Number(t[1]) : 12;
  const minutes = t ? Number(t[2]) : 0;
  return -new Date(Number(d[1]), Number(d[2]) - 1, Number(d[3]), hours, minutes).getTimezoneOffset();
}

/** IANA name of the browser's zone (e.g. "Asia/Kolkata"), or null when unavailable. */
export function browserZoneName(): string | null {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || null;
  } catch {
    return null;
  }
}

/** Offsets in use around the world (minutes east of UTC), for choosing another zone. */
export const UTC_OFFSETS: readonly number[] = [
  -720, -660, -600, -570, -540, -480, -420, -360, -300, -240, -210, -180, -120, -60, 0, 60, 120, 180, 210, 240, 270, 300,
  330, 345, 360, 390, 420, 480, 525, 540, 570, 600, 630, 660, 720, 765, 780, 840,
];

/** Choice value of the zone select: the browser's zone, or a fixed offset in minutes. */
export const BROWSER_ZONE = "browser";

export function offsetChoices(extra: number | null): number[] {
  const list = new Set(UTC_OFFSETS);
  if (extra !== null && Number.isInteger(extra) && extra >= -720 && extra <= 840) list.add(extra);
  return [...list].sort((a, b) => a - b);
}

/** "UTC+05:30" (re-exported so the module imports one place). */
export const offsetLabel = formatUtcOffset;

/** A local wall-clock time with its day relative to the analysed date, e.g. "00:31 (+1 d)". */
export function localWithDay(local: string, dayOffset: number): string {
  if (!dayOffset) return local;
  return `${local} (${dayOffset > 0 ? "+" : "−"}${Math.abs(dayOffset)} d)`;
}
