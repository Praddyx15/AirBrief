import { NextResponse, type NextRequest } from "next/server";

/**
 * Content-Security-Policy for `next start`, with a fresh script nonce per request. Inline scripts
 * run only with the nonce: Next adds it to its own scripts (it reads it from the request's CSP
 * header) and the root layout passes `x-nonce` to the theme script. Styles keep 'unsafe-inline'
 * for inline style attributes. A static export never runs this (see DEPLOYMENT.md §8).
 */

const BASEMAP_ORIGIN = "https://tiles.openfreemap.org";

function apiOrigin() {
  try {
    return new URL(process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").origin;
  } catch {
    return "";
  }
}

function contentSecurityPolicy(nonce: string) {
  return [
    "default-src 'self'",
    // 'self' is only a fallback for browsers without 'strict-dynamic'
    `script-src 'self' 'nonce-${nonce}' 'strict-dynamic'`,
    "style-src 'self' 'unsafe-inline'",
    `img-src 'self' data: blob: ${BASEMAP_ORIGIN}`,
    "font-src 'self' data:",
    // data:/blob: — deck.gl fetches the zone hatch atlas (a data: URL); MapLibre uses blob: URLs.
    `connect-src 'self' data: blob: ${apiOrigin()} ${BASEMAP_ORIGIN}`.replace(/\s+/g, " "),
    "worker-src 'self' blob:",
    "child-src blob:",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
  ].join("; ");
}

export function middleware(request: NextRequest) {
  // the dev server needs eval for hot reloading, so only production builds get a CSP
  if (process.env.NODE_ENV !== "production") return NextResponse.next();

  const nonce = btoa(crypto.randomUUID());
  const csp = contentSecurityPolicy(nonce);
  const requestHeaders = new Headers(request.headers);
  requestHeaders.set("x-nonce", nonce);
  requestHeaders.set("content-security-policy", csp);

  const response = NextResponse.next({ request: { headers: requestHeaders } });
  response.headers.set("Content-Security-Policy", csp);
  return response;
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
