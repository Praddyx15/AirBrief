import * as React from "react";
import { cn } from "@/lib/utils";

export const Kbd = React.forwardRef<HTMLElement, React.HTMLAttributes<HTMLElement>>(({ className, ...props }, ref) => (
  <kbd
    ref={ref}
    className={cn(
      "inline-flex h-5 min-w-5 items-center justify-center rounded-sm border border-border bg-elevated px-1 font-mono text-2xs font-medium text-fg-muted",
      className,
    )}
    {...props}
  />
));
Kbd.displayName = "Kbd";
