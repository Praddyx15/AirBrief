"use client";

import { useTheme } from "next-themes";
import { Toaster as Sonner, toast } from "sonner";

/**
 * Global toaster (mounted once in <Providers>). Use `toast()` from this module or "sonner":
 *   toast.success("Analysis finished", { description: "Airspace check: pass" })
 * Toasts are announced politely by sonner; keep important results in the page as well.
 */
export function Toaster() {
  const { resolvedTheme } = useTheme();
  return (
    <Sonner
      theme={resolvedTheme === "light" ? "light" : "dark"}
      // Top centre, below the header: the bottom of the screen holds the results drawer and its
      // KPI tiles, which a bottom toast would cover.
      position="top-center"
      offset={64}
      duration={3500}
      closeButton
      visibleToasts={4}
      toastOptions={{
        classNames: {
          toast: "!rounded !border !border-border !bg-elevated !text-fg !font-sans !text-sm !shadow-none",
          description: "!text-fg-muted",
          actionButton: "!bg-accent-solid !text-accent-foreground",
          cancelButton: "!bg-surface !text-fg",
          closeButton: "!border-border !bg-elevated !text-fg",
        },
      }}
    />
  );
}

export { toast };
