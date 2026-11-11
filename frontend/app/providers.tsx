"use client";

import { useEffect, useState, type ReactNode } from "react";
import { QueryClient, QueryClientProvider, useQueryClient } from "@tanstack/react-query";
import { ThemeProvider } from "next-themes";
import { Toaster } from "@/components/ui/toaster";
import { TooltipProvider } from "@/components/ui/tooltip";
import { useMotionAttribute } from "@/lib/motion";
import { resumePendingDeletes } from "@/lib/pending-deletes";
import { createQueryClientOptions } from "@/lib/queries";
import { rehydrateSettings, useHistory } from "@/lib/store";

/**
 * Loads persisted settings and history after mount, resumes dataset deletions that were still
 * waiting out their undo window, and mirrors the motion preference onto <html>.
 */
function ClientSync(): null {
  const queryClient = useQueryClient();
  useEffect(() => {
    void rehydrateSettings();
    void Promise.resolve(useHistory.persist.rehydrate()).then(() => resumePendingDeletes(queryClient));
  }, [queryClient]);
  useMotionAttribute();
  return null;
}

/** `nonce` lets the next-themes script run under the CSP. */
export function Providers({ children, nonce }: { children: ReactNode; nonce?: string }) {
  const [queryClient] = useState(() => new QueryClient(createQueryClientOptions()));

  return (
    <ThemeProvider
      nonce={nonce}
      attribute="class"
      defaultTheme="dark"
      themes={["dark", "light"]}
      enableSystem={false}
      disableTransitionOnChange
    >
      <QueryClientProvider client={queryClient}>
        <TooltipProvider delayDuration={300} skipDelayDuration={150}>
          <ClientSync />
          {children}
          <Toaster />
        </TooltipProvider>
      </QueryClientProvider>
    </ThemeProvider>
  );
}
