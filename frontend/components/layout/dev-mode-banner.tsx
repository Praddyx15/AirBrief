"use client";

import { useEffect, useState } from "react";
import { AlertTriangle } from "lucide-react";
import { DEMO_MODE } from "@/lib/api";
import { useMeta } from "@/lib/queries";
import { cn } from "@/lib/utils";

const LOOPBACK_HOSTS = new Set(["localhost", "127.0.0.1", "[::1]"]);

/**
 * Persistent warning when the API runs with APP_ENV=development but the page is opened from
 * another machine: development mode has no owner tokens and lists everything. Never shown on a
 * loopback host or in the static demo.
 */
export function DevModeBanner({ className }: { className?: string }) {
  const meta = useMeta();
  // window only exists in the browser; decide after hydration
  const [remote, setRemote] = useState(false);
  useEffect(() => {
    setRemote(!LOOPBACK_HOSTS.has(window.location.hostname));
  }, []);

  if (DEMO_MODE || !remote || meta.data?.app_env !== "development") return null;
  return (
    <div
      role="status"
      className={cn("border-b border-warn/50 bg-warn/10 text-sm text-fg", className)}
      data-print="hide"
    >
      <div className="container flex items-start gap-2 py-2">
        <AlertTriangle className="mt-0.5 size-4 shrink-0 text-warn" aria-hidden />
        <p className="font-medium">
          Development mode: anyone who can reach this server can see and delete everything on it.
        </p>
      </div>
    </div>
  );
}
