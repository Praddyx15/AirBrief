import type { Metadata } from "next";
import Link from "next/link";
import { MapPinOff } from "lucide-react";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { SiteFooter } from "@/components/layout/site-footer";
import { SiteHeader } from "@/components/layout/site-header";

export const metadata: Metadata = {
  title: "Page not found",
  robots: { index: false, follow: true },
};

export default function NotFound() {
  return (
    <>
      <SiteHeader />
      <main id="main" tabIndex={-1} className="container flex min-h-[60dvh] items-center justify-center py-12 outline-none">
        <EmptyState
          icon={<MapPinOff />}
          headingLevel="h1"
          title="Page not found"
          description="This address does not exist. Links to datasets use the form /w/view?d=<id>; the dataset may also have been deleted."
          action={
            <>
              <Button asChild>
                <Link href="/w">Go to the workspace</Link>
              </Button>
              <Button variant="ghost" asChild>
                <Link href="/">Home</Link>
              </Button>
            </>
          }
        />
      </main>
      <SiteFooter />
    </>
  );
}
