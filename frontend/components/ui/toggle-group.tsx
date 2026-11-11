"use client";

import * as React from "react";
import * as ToggleGroupPrimitive from "@radix-ui/react-toggle-group";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

export const toggleVariants = cva(
  [
    "inline-flex cursor-pointer items-center justify-center gap-1.5 whitespace-nowrap rounded-sm text-sm font-medium text-fg-muted",
    "transition-colors duration-200 hover:bg-elevated hover:text-fg",
    "disabled:pointer-events-none disabled:opacity-50",
    "data-[state=on]:bg-accent/15 data-[state=on]:text-fg data-[state=on]:shadow-[inset_0_0_0_1px_rgb(var(--accent-rgb)/0.6)]",
    "[&_svg]:size-4 [&_svg]:shrink-0",
  ],
  {
    variants: {
      variant: {
        default: "",
        outline: "border border-border",
      },
      // Touch screens: every item is at least 44 × 44 px.
      size: {
        sm: "h-7 px-2 text-xs pointer-coarse:min-h-11 pointer-coarse:min-w-11",
        default: "h-8 px-3 pointer-coarse:min-h-11 pointer-coarse:min-w-11",
        icon: "size-8 p-0 pointer-coarse:size-11",
      },
    },
    defaultVariants: { variant: "default", size: "default" },
  },
);

const ToggleGroupContext = React.createContext<VariantProps<typeof toggleVariants>>({
  variant: "default",
  size: "default",
});

type ToggleGroupProps = React.ComponentPropsWithoutRef<typeof ToggleGroupPrimitive.Root> &
  VariantProps<typeof toggleVariants>;

/**
 * Segmented control (single or multiple). Give the group an aria-label, and each icon-only
 * item an aria-label. Example: 2D / 3D switch, metric / imperial.
 */
export const ToggleGroup = React.forwardRef<React.ElementRef<typeof ToggleGroupPrimitive.Root>, ToggleGroupProps>(
  ({ className, variant, size, children, ...props }, ref) => (
    <ToggleGroupPrimitive.Root
      ref={ref}
      className={cn("inline-flex items-center gap-0.5 rounded border border-border bg-surface p-0.5", className)}
      {...props}
    >
      <ToggleGroupContext.Provider value={{ variant, size }}>{children}</ToggleGroupContext.Provider>
    </ToggleGroupPrimitive.Root>
  ),
);
ToggleGroup.displayName = ToggleGroupPrimitive.Root.displayName;

export const ToggleGroupItem = React.forwardRef<
  React.ElementRef<typeof ToggleGroupPrimitive.Item>,
  React.ComponentPropsWithoutRef<typeof ToggleGroupPrimitive.Item> & VariantProps<typeof toggleVariants>
>(({ className, children, variant, size, ...props }, ref) => {
  const context = React.useContext(ToggleGroupContext);
  return (
    <ToggleGroupPrimitive.Item
      ref={ref}
      className={cn(
        toggleVariants({ variant: context.variant ?? variant, size: context.size ?? size }),
        className,
      )}
      {...props}
    >
      {children}
    </ToggleGroupPrimitive.Item>
  );
});
ToggleGroupItem.displayName = ToggleGroupPrimitive.Item.displayName;
