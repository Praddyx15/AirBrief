"use client";

import * as React from "react";
import * as SliderPrimitive from "@radix-ui/react-slider";
import { cn } from "@/lib/utils";

export interface SliderProps extends React.ComponentPropsWithoutRef<typeof SliderPrimitive.Root> {
  /**
   * Accessible name for each thumb (one per value). Required for range sliders; for a single
   * thumb you may pass `aria-label` / `aria-labelledby` on the Slider instead.
   */
  thumbLabels?: string[];
  /** Human-readable value for screen readers, e.g. (v) => `${v} m`. */
  getValueText?: (value: number, index: number) => string;
}

/**
 * Slider. WCAG 2.2 "dragging movements": always pair it with a number input (see NumberInput)
 * or rely on the arrow / Page / Home / End keys that Radix provides.
 */
export const Slider = React.forwardRef<React.ElementRef<typeof SliderPrimitive.Root>, SliderProps>(
  (
    {
      className,
      thumbLabels,
      getValueText,
      value,
      defaultValue,
      "aria-label": ariaLabel,
      "aria-labelledby": ariaLabelledby,
      ...props
    },
    ref,
  ) => {
    const values = value ?? defaultValue ?? [props.min ?? 0];
    return (
      <SliderPrimitive.Root
        ref={ref}
        value={value}
        defaultValue={defaultValue}
        className={cn(
          // Touch screens: a 46 px tall band to grab (the track itself is 6 px).
          "relative flex w-full touch-none select-none items-center py-2 pointer-coarse:py-5 data-[disabled]:opacity-50 data-[orientation=vertical]:h-full data-[orientation=vertical]:w-auto data-[orientation=vertical]:flex-col",
          className,
        )}
        {...props}
      >
        <SliderPrimitive.Track className="relative h-1.5 w-full grow overflow-hidden rounded-full bg-border data-[orientation=vertical]:h-full data-[orientation=vertical]:w-1.5">
          <SliderPrimitive.Range className="absolute h-full bg-accent data-[orientation=vertical]:w-full" />
        </SliderPrimitive.Track>
        {values.map((v, i) => (
          <SliderPrimitive.Thumb
            key={i}
            aria-label={thumbLabels?.[i] ?? ariaLabel}
            aria-labelledby={thumbLabels?.[i] ? undefined : ariaLabelledby}
            aria-valuetext={getValueText ? getValueText(v, i) : undefined}
            className={cn(
              "relative block size-4 cursor-grab rounded-full border-2 border-accent bg-bg transition-colors duration-150",
              "hover:bg-elevated active:cursor-grabbing disabled:pointer-events-none",
              // Touch: a larger thumb with an invisible 44 px hit area around it.
              "pointer-coarse:size-7 pointer-coarse:before:absolute pointer-coarse:before:-inset-2.5 pointer-coarse:before:rounded-full pointer-coarse:before:content-['']",
            )}
          />
        ))}
      </SliderPrimitive.Root>
    );
  },
);
Slider.displayName = "Slider";
