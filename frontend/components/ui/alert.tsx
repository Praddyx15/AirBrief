import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { AlertTriangle, CheckCircle2, Info, OctagonAlert, type LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";

export const alertVariants = cva(
  "relative flex w-full gap-3 rounded border p-3 text-sm text-fg [&>svg]:mt-0.5 [&>svg]:size-4 [&>svg]:shrink-0",
  {
    variants: {
      variant: {
        default: "border-border bg-surface [&>svg]:text-fg-muted",
        info: "border-accent/40 bg-accent/10 [&>svg]:text-accent",
        ok: "border-ok/40 bg-ok/10 [&>svg]:text-ok",
        warn: "border-warn/50 bg-warn/10 [&>svg]:text-warn",
        danger: "border-danger/50 bg-danger/10 [&>svg]:text-danger",
      },
    },
    defaultVariants: { variant: "default" },
  },
);

type AlertVariant = NonNullable<VariantProps<typeof alertVariants>["variant"]>;

const DEFAULT_ICON: Record<AlertVariant, LucideIcon> = {
  default: Info,
  info: Info,
  ok: CheckCircle2,
  warn: AlertTriangle,
  danger: OctagonAlert,
};

export interface AlertProps extends React.HTMLAttributes<HTMLDivElement>, VariantProps<typeof alertVariants> {
  /** Custom icon element; pass `null` to hide. Defaults to an icon matching the variant. */
  icon?: React.ReactNode;
}

/**
 * Inline message. Status is conveyed by icon + text, not colour alone. Defaults to
 * role="alert" for danger and role="status" otherwise; override with `role`.
 */
export const Alert = React.forwardRef<HTMLDivElement, AlertProps>(
  ({ className, variant, icon, role, children, ...props }, ref) => {
    const v: AlertVariant = variant ?? "default";
    const Icon = DEFAULT_ICON[v];
    return (
      <div
        ref={ref}
        role={role ?? (v === "danger" ? "alert" : "status")}
        className={cn(alertVariants({ variant: v }), className)}
        {...props}
      >
        {icon === undefined ? <Icon aria-hidden /> : icon}
        <div className="min-w-0 flex-1 space-y-1">{children}</div>
      </div>
    );
  },
);
Alert.displayName = "Alert";

export const AlertTitle = React.forwardRef<HTMLParagraphElement, React.HTMLAttributes<HTMLParagraphElement>>(
  ({ className, ...props }, ref) => (
    <p ref={ref} className={cn("font-medium leading-tight", className)} {...props} />
  ),
);
AlertTitle.displayName = "AlertTitle";

export const AlertDescription = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => (
    <div ref={ref} className={cn("text-sm text-fg-muted [&_p]:leading-relaxed", className)} {...props} />
  ),
);
AlertDescription.displayName = "AlertDescription";
