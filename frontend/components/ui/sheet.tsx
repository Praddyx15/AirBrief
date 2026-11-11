"use client";

import * as React from "react";
import * as SheetPrimitive from "@radix-ui/react-dialog";
import { cva, type VariantProps } from "class-variance-authority";
import { X } from "lucide-react";
import { cn } from "@/lib/utils";
import { useReturnFocus } from "./use-return-focus";

/**
 * Side panel built on Radix Dialog. Used for the inspector below 1024 px and the bottom
 * sheet below 768 px. Slides become fades under reduced motion (see globals.css).
 * Always render a <SheetTitle> (use className="sr-only" to hide it visually).
 */
export const Sheet = SheetPrimitive.Root;
export const SheetTrigger = SheetPrimitive.Trigger;
export const SheetClose = SheetPrimitive.Close;
export const SheetPortal = SheetPrimitive.Portal;

export const SheetOverlay = React.forwardRef<
  React.ElementRef<typeof SheetPrimitive.Overlay>,
  React.ComponentPropsWithoutRef<typeof SheetPrimitive.Overlay>
>(({ className, ...props }, ref) => (
  <SheetPrimitive.Overlay
    ref={ref}
    className={cn(
      "fixed inset-0 z-overlay bg-[var(--overlay)]",
      "data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=closed]:animate-out data-[state=closed]:fade-out-0",
      className,
    )}
    {...props}
  />
));
SheetOverlay.displayName = SheetPrimitive.Overlay.displayName;

export const sheetVariants = cva(
  [
    "fixed z-overlay flex flex-col gap-3 border-border bg-surface text-fg",
    "data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=open]:fade-in-0 data-[state=closed]:fade-out-0",
    "data-[state=open]:duration-250 data-[state=closed]:duration-200",
  ],
  {
    variants: {
      side: {
        top: "inset-x-0 top-0 max-h-[85dvh] border-b data-[state=closed]:slide-out-to-top data-[state=open]:slide-in-from-top",
        bottom:
          "inset-x-0 bottom-0 max-h-[85dvh] rounded-t-lg border-t pb-[env(safe-area-inset-bottom)] data-[state=closed]:slide-out-to-bottom data-[state=open]:slide-in-from-bottom",
        left: "inset-y-0 left-0 h-full w-[min(340px,90vw)] border-r data-[state=closed]:slide-out-to-left data-[state=open]:slide-in-from-left",
        right:
          "inset-y-0 right-0 h-full w-[min(340px,90vw)] border-l data-[state=closed]:slide-out-to-right data-[state=open]:slide-in-from-right",
      },
    },
    defaultVariants: { side: "right" },
  },
);

export interface SheetContentProps
  extends React.ComponentPropsWithoutRef<typeof SheetPrimitive.Content>,
    VariantProps<typeof sheetVariants> {
  /** Omit the dimming overlay (non-blocking panels; combine with <Sheet modal={false}>). */
  hideOverlay?: boolean;
  hideClose?: boolean;
}

/**
 * Sheet content. Closing it returns focus to whatever opened it (e.g. the "Show inspector"
 * button), also when it was opened from code; a non-modal sheet (no overlay) leaves focus alone
 * after a click outside it.
 */
export const SheetContent = React.forwardRef<React.ElementRef<typeof SheetPrimitive.Content>, SheetContentProps>(
  (
    { side = "right", className, children, hideOverlay = false, hideClose = false, onOpenAutoFocus, onCloseAutoFocus, onInteractOutside, ...props },
    ref,
  ) => {
    const focusHandlers = useReturnFocus(
      { onOpenAutoFocus, onCloseAutoFocus, onInteractOutside },
      { afterOutsideInteraction: !hideOverlay },
    );
    return (
      <SheetPortal>
        {hideOverlay ? null : <SheetOverlay />}
        <SheetPrimitive.Content ref={ref} className={cn(sheetVariants({ side }), className)} {...props} {...focusHandlers}>
          {children}
          {hideClose ? null : (
            <SheetPrimitive.Close
              className="absolute right-3 top-3 inline-flex size-8 cursor-pointer items-center justify-center rounded text-fg-muted transition-colors hover:bg-elevated hover:text-fg pointer-coarse:size-11"
              aria-label="Close panel"
            >
              <X className="size-4" aria-hidden />
            </SheetPrimitive.Close>
          )}
        </SheetPrimitive.Content>
      </SheetPortal>
    );
  },
);
SheetContent.displayName = SheetPrimitive.Content.displayName;

export function SheetHeader({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("flex flex-col gap-1 border-b border-border p-4 pr-12", className)} {...props} />;
}

export function SheetBody({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("min-h-0 flex-1 overflow-y-auto px-4 pb-4", className)} {...props} />;
}

export function SheetFooter({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("flex gap-2 border-t border-border p-4", className)} {...props} />;
}

export const SheetTitle = React.forwardRef<
  React.ElementRef<typeof SheetPrimitive.Title>,
  React.ComponentPropsWithoutRef<typeof SheetPrimitive.Title>
>(({ className, ...props }, ref) => (
  <SheetPrimitive.Title ref={ref} className={cn("text-base font-semibold", className)} {...props} />
));
SheetTitle.displayName = SheetPrimitive.Title.displayName;

export const SheetDescription = React.forwardRef<
  React.ElementRef<typeof SheetPrimitive.Description>,
  React.ComponentPropsWithoutRef<typeof SheetPrimitive.Description>
>(({ className, ...props }, ref) => (
  <SheetPrimitive.Description ref={ref} className={cn("text-sm text-fg-muted", className)} {...props} />
));
SheetDescription.displayName = SheetPrimitive.Description.displayName;
