import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

export const buttonVariants = cva(
  [
    "inline-flex shrink-0 cursor-pointer select-none items-center justify-center gap-2 whitespace-nowrap rounded font-medium no-underline",
    "transition-colors duration-200",
    "disabled:pointer-events-none disabled:cursor-not-allowed disabled:opacity-50",
    "aria-disabled:pointer-events-none aria-disabled:opacity-50",
    "[&_svg]:pointer-events-none [&_svg]:size-4 [&_svg]:shrink-0",
  ],
  {
    variants: {
      variant: {
        default: "bg-accent-solid text-accent-foreground hover:bg-accent-solid/90 active:bg-accent-solid/80",
        secondary: "border border-border bg-elevated text-fg hover:bg-border/60",
        ghost: "text-fg hover:bg-elevated data-[state=on]:bg-elevated aria-pressed:bg-elevated",
        outline: "border border-border bg-transparent text-fg hover:bg-elevated",
        destructive: "bg-danger-solid text-white hover:bg-danger-solid/90 active:bg-danger-solid/80",
        link: "h-auto px-0 text-accent-text underline-offset-4 hover:underline",
      },
      size: {
        sm: "h-8 px-3 text-xs pointer-coarse:min-h-11",
        default: "h-9 px-4 text-sm pointer-coarse:min-h-11",
        lg: "h-11 px-6 text-base",
        icon: "size-9 p-0 pointer-coarse:size-11",
        "icon-sm": "size-8 p-0 pointer-coarse:size-11",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  },
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  /** Render the child element (e.g. a Next <Link>) with button styling. */
  asChild?: boolean;
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, type, ...props }, ref) => {
    const Comp = asChild ? Slot : "button";
    return (
      <Comp
        ref={ref}
        className={cn(buttonVariants({ variant, size }), className)}
        {...(asChild ? {} : { type: type ?? "button" })}
        {...props}
      />
    );
  },
);
Button.displayName = "Button";
