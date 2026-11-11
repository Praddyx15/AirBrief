import type { Metadata } from "next";
import { SiteFooter } from "@/components/layout/site-footer";
import { SiteHeader } from "@/components/layout/site-header";
import { SettingsView } from "./settings-view";

export const metadata: Metadata = {
  title: "Settings",
  description: "Units, theme, motion, basemap, assessment choices, FlightMD URL, data packs and server status.",
};

export default function SettingsPage() {
  return (
    <>
      <SiteHeader />
      <main id="main" tabIndex={-1} className="container max-w-3xl space-y-6 py-8 outline-none md:py-10">
        <div className="space-y-1">
          <h1 className="text-balance text-2xl font-semibold tracking-tight">Settings</h1>
          <p className="text-sm text-fg-muted">
            Preferences are saved in this browser. There are no accounts and nothing is sent to the server.
          </p>
        </div>
        <SettingsView />
      </main>
      <SiteFooter />
    </>
  );
}
