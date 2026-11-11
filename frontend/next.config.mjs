/**
 * Next.js configuration for AirBrief.
 *
 * - `NEXT_OUTPUT=export` produces a fully static site in `out/` (used for the public demo,
 *   together with `NEXT_PUBLIC_DEMO=1`). Routes are query-param based, so no dynamic segments
 *   need `generateStaticParams`.
 * - `NEXT_PUBLIC_BASE_PATH` serves the app under a sub-path (for example `/airbrief` on
 *   GitHub Pages). Leave it empty to serve from the domain root.
 * - Security headers (no framing, no referrer, no MIME sniffing, HSTS) are sent by `next start`;
 *   the nonce-based Content-Security-Policy comes from `middleware.ts`. A static export cannot
 *   set headers: configure them on the web server (DEPLOYMENT.md).
 */

const isExport = process.env.NEXT_OUTPUT === "export";
const isProduction = process.env.NODE_ENV === "production";
const basePath = (process.env.NEXT_PUBLIC_BASE_PATH ?? "").trim().replace(/\/+$/, "");

const securityHeaders = [
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Referrer-Policy", value: "no-referrer" },
  // geolocation=(self): "My location" in the plan toolbar (the fix stays in the browser).
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=(self), payment=()" },
  // browsers ignore it over plain http, so local `next start` is unaffected
  ...(isProduction ? [{ key: "Strict-Transport-Security", value: "max-age=31536000; includeSubDomains" }] : []),
];

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  basePath,
  images: { unoptimized: true },
  ...(isExport
    ? { output: "export", trailingSlash: true }
    : {
        async headers() {
          return [{ source: "/:path*", headers: securityHeaders }];
        },
      }),
};

export default nextConfig;
