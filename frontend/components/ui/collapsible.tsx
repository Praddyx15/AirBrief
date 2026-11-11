"use client";

import * as React from "react";
import * as CollapsiblePrimitive from "@radix-ui/react-collapsible";
import { ChevronRight } from "lucide-react";
import { cn } from "@/lib/utils";

export const Collapsible = CollapsiblePrimitive.Root;

/**
 * Trigger with a rotating chevron. Height is never animated (PLAN §7); the content fades in.
 */
export const CollapsibleTrigger = React.forwardRef<
  React.ElementRef<typeof CollapsiblePrimitive.Trigger>,
  React.ComponentPropsWithoutRef<typeof CollapsiblePrimitive.Trigger> & { hideChevron?: boolean }
>(({ className, children, hideChevron = false, asChild, ...props }, ref) => (
  <CollapsiblePrimitive.Trigger
    ref={ref}
    asChild={asChild}
    className={cn(
      "group flex w-full cursor-pointer items-center gap-1.5 rounded py-1.5 text-left text-sm font-medium text-fg transition-colors hover:text-fg pointer-coarse:min-h-11",
      className,
    )}
    {...props}
  >
    {asChild ? (
      children
    ) : (
      <>
        {hideChevron ? null : (
          <ChevronRight
            className="size-4 shrink-0 text-fg-muted transition-transform duration-200 group-data-[state=open]:rotate-90"
            aria-hidden
          />
        )}
        {children}
      </>
    )}
  </CollapsiblePrimitive.Trigger>
));
CollapsibleTrigger.displayName = CollapsiblePrimitive.Trigger.displayName;

export const CollapsibleContent = React.forwardRef<
  React.ElementRef<typeof CollapsiblePrimitive.Content>,
  React.ComponentPropsWithoutRef<typeof CollapsiblePrimitive.Content>
>(({ className, ...props }, ref) => (
  <CollapsiblePrimitive.Content
    ref={ref}
    className={cn("data-[state=open]:animate-in data-[state=open]:fade-in-0", className)}
    {...props}
  />
));
CollapsibleContent.displayName = CollapsiblePrimitive.Content.displayName;
