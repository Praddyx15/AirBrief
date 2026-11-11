/**
 * URL rules shared by Settings and the flight-audit "Open in FlightMD" link.
 *
 * Only absolute http(s) URLs without credentials are ever rendered as links, so a stored value
 * cannot become a `javascript:` or `data:` link.
 */

/** Validate the FlightMD base URL typed in Settings: an error message, or null when valid/empty. */
export function validateFlightmdUrl(raw: string): string | null {
  const v = raw.trim();
  if (!v) return null;
  let url: URL;
  try {
    url = new URL(v);
  } catch {
    return "Enter a full URL including https://, for example https://flightmd.example.org.";
  }
  if (url.protocol !== "https:" && url.protocol !== "http:") return "Only http:// and https:// URLs are supported.";
  if (!url.hostname) return "The URL needs a host name.";
  if (url.username || url.password) return "Do not put a user name or password in the URL.";
  if (url.search || url.hash) return "Enter the base URL only, without ?query or #fragment.";
  return null;
}

/** The value as a normalised absolute http(s) URL with a host and no credentials, else null. */
export function safeHttpUrl(value: string | null | undefined): string | null {
  if (!value) return null;
  try {
    const url = new URL(value.trim());
    if (url.protocol !== "https:" && url.protocol !== "http:") return null;
    if (!url.hostname || url.username || url.password) return null;
    return url.toString();
  } catch {
    return null;
  }
}

/** `<base>/report/<id>` for a valid FlightMD base URL (see `validateFlightmdUrl`), else null. */
export function flightmdReportUrl(base: string | null | undefined, reportId: string | null | undefined): string | null {
  if (!base || !reportId || validateFlightmdUrl(base) !== null) return null;
  const root = safeHttpUrl(base);
  if (!root) return null;
  return `${root.replace(/\/+$/, "")}/report/${encodeURIComponent(reportId)}`;
}
