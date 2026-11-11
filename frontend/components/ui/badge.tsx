import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

/**
 * Badges never carry meaning by colour alone: always include a text label, and for status
 * variants (ok/warn/danger, zone-*) prefer an icon too, e.g.
 *   <Badge variant="warn"><AlertTriangle aria-hidden /> Caution</Badge>
 * Zone variants show a small hatch swatch matching the map pattern.
 */
export const badgeVariants = cva(
  "inline-flex max-w-full items-center gap-1 whitespace-nowrap rounded border px-1.5 py-0.5 text-xs font-medium leading-4 [&_svg]:size-3.5 [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        default: "border-transparent bg-accent-solid text-accent-foreground",
        secondary: "border-border bg-elevated text-fg",
        outline: "border-border bg-transparent text-fg",
        info: "border-accent/40 bg-accent/10 text-fg",
        ok: "border-ok/50 bg-ok/10 text-fg [&_svg]:text-ok",
        warn: "border-warn/50 bg-warn/10 text-fg [&_svg]:text-warn",
        danger: "border-danger/50 bg-danger/10 text-fg [&_svg]:text-danger",
        "zone-green": "border-zone-green/60 bg-zone-green/10 text-fg",
        "zone-yellow": "border-zone-yellow/60 bg-zone-yellow/10 text-fg",
        "zone-red": "border-zone-red/60 bg-zone-red/10 text-fg",
      },
    },
    defaultVariants: { variant: "secondary" },
  },
);

const ZONE_SWATCH: Record<"zone-green" | "zone-yellow" | "zone-red", string> = {
  "zone-green": "bg-zone-green [background-image:radial-gradient(rgb(0_0_0/0.55)_1px,transparent_1.2px)] [background-size:4px_4px]",
  "zone-yellow":
    "bg-zone-yellow [background-image:repeating-linear-gradient(45deg,rgb(0_0_0/0.55)_0_1px,transparent_1px_3px)]",
  "zone-red":
    "bg-zone-red [background-image:repeating-linear-gradient(45deg,rgb(0_0_0/0.55)_0_1px,transparent_1px_3px),repeating-linear-gradient(-45deg,rgb(0_0_0/0.55)_0_1px,transparent_1px_3px)]",
};

export interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement>, VariantProps<typeof badgeVariants> {}

export const Badge = React.forwardRef<HTMLSpanElement, BadgeProps>(({ className, variant, children, ...props }, ref) => {
  const swatch =
    variant === "zone-green" || variant === "zone-yellow" || variant === "zone-red" ? ZONE_SWATCH[variant] : null;
  return (
    <span ref={ref} className={cn(badgeVariants({ variant }), className)} {...props}>
      {swatch ? <span aria-hidden className={cn("inline-block size-2.5 shrink-0 rounded-sm", swatch)} /> : null}
      {children}
    </span>
  );
});
Badge.displayName = "Badge";
