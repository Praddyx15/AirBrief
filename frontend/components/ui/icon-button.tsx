"use client";

import * as React from "react";
import { Button, type ButtonProps } from "./button";
import { Tooltip, TooltipContent, TooltipTrigger } from "./tooltip";
import { Kbd } from "./kbd";
import { cn } from "@/lib/utils";

export interface IconButtonProps extends Omit<ButtonProps, "aria-label" | "children" | "size"> {
  /** Required accessible name; also shown as the tooltip. */
  label: string;
  /** The icon element, e.g. <ZoomIn />. It is marked aria-hidden automatically. */
  icon: React.ReactElement;
  size?: "icon" | "icon-sm";
  /** Tooltip placement. */
  side?: "top" | "right" | "bottom" | "left";
  /** Optional keyboard shortcut shown in the tooltip, e.g. "N". */
  shortcut?: string;
  /** For toggle buttons: sets aria-pressed. */
  pressed?: boolean;
}

/**
 * Icon-only button with a mandatory label (aria-label + tooltip). Never use a bare icon
 * button without one.
 */
export const IconButton = React.forwardRef<HTMLButtonElement, IconButtonProps>(
  ({ label, icon, size = "icon", variant = "ghost", side = "top", shortcut, pressed, className, ...props }, ref) => {
    const iconEl = React.isValidElement<{ "aria-hidden"?: boolean | "true" | "false" }>(icon)
      ? React.cloneElement(icon, { "aria-hidden": true })
      : icon;
    return (
      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            ref={ref}
            size={size}
            variant={variant}
            aria-label={label}
            aria-pressed={pressed}
            className={cn(pressed && "bg-elevated text-fg", className)}
            {...props}
          >
            {iconEl}
          </Button>
        </TooltipTrigger>
        <TooltipContent side={side}>
          <span className="flex items-center gap-2">
            {label}
            {shortcut ? <Kbd>{shortcut}</Kbd> : null}
          </span>
        </TooltipContent>
      </Tooltip>
    );
  },
);
IconButton.displayName = "IconButton";
