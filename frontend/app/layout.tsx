import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";
import { GeistMono } from "geist/font/mono";
import { GeistSans } from "geist/font/sans";
import { headers } from "next/headers";
import { cn } from "@/lib/utils";
import { Providers } from "./providers";
import "./globals.css";

export const metadata: Metadata = {
  title: {
    default: "AirBrief",
    template: "%s · AirBrief",
  },
  description:
    "Open-source geospatial workspace for drone and eVTOL planning: measure uploaded geometry, check indicative airspace, ground risk, survey effort, drone shows and flight logs.",
  applicationName: "AirBrief",
  robots: { index: true, follow: true },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  colorScheme: "dark light",
  themeColor: [
    { media: "(prefers-color-scheme: dark)", color: "#0b0e11" },
    { media: "(prefers-color-scheme: light)", color: "#f8fafc" },
  ],
};

/**
 * Root layout. Every page must render its primary content in an element with `id="main"`
 * (for example `<main id="main" tabIndex={-1}>`) so the skip link has a target.
 *
 * Under `next start` it reads the CSP nonce set by `middleware.ts` (which makes every page
 * render per request). A static export has no middleware and no request, so it skips that.
 */
export default async function RootLayout({ children }: { children: ReactNode }) {
  const nonce = process.env.NEXT_OUTPUT === "export" ? undefined : ((await headers()).get("x-nonce") ?? undefined);
  return (
    <html lang="en" className={cn(GeistSans.variable, GeistMono.variable)} suppressHydrationWarning>
      <body className="min-h-dvh bg-bg font-sans text-fg antialiased">
        <a href="#main" className="skip-link">
          Skip to main content
        </a>
        <Providers nonce={nonce}>{children}</Providers>
      </body>
    </html>
  );
}
