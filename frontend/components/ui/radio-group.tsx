"use client";

import * as React from "react";
import { cn } from "@/lib/utils";

export interface RadioOption<T extends string> {
  value: T;
  label: string;
  /** a sentence under the label, read out with the option */
  description?: React.ReactNode;
}

export interface RadioGroupProps<T extends string> {
  legend: React.ReactNode;
  options: readonly RadioOption<T>[];
  value: T | null;
  onChange: (value: T) => void;
  /** under the options, read out with the group */
  hint?: React.ReactNode;
  disabled?: boolean;
  /** options side by side (they wrap) instead of one per line */
  inline?: boolean;
  className?: string;
  legendClassName?: string;
}

/**
 * Native radio buttons in a fieldset: the legend names the group, arrow keys move between the
 * options, and each option's description is tied to its input. For a one-of-two or one-of-three
 * choice that needs a sentence per option (a segmented ToggleGroup has no room for that).
 */
export function RadioGroup<T extends string>({
  legend,
  options,
  value,
  onChange,
  hint,
  disabled,
  inline = false,
  className,
  legendClassName,
}: RadioGroupProps<T>) {
  const id = React.useId();
  return (
    // min-w-0: a fieldset is min-content wide by default and pushes a phone screen sideways
    <fieldset className={cn("min-w-0 space-y-1.5", className)} disabled={disabled} aria-describedby={hint ? `${id}-hint` : undefined}>
      <legend className={cn("mb-1.5 text-sm font-medium", legendClassName)}>{legend}</legend>
      <div className={inline ? "flex flex-wrap gap-x-4 gap-y-1.5" : "space-y-2"}>
        {options.map((o) => {
          const inputId = `${id}-${o.value}`;
          return (
            <div key={o.value} className="flex items-start gap-2">
              <input
                id={inputId}
                type="radio"
                name={id}
                value={o.value}
                checked={value === o.value}
                onChange={() => onChange(o.value)}
                aria-describedby={o.description ? `${inputId}-desc` : undefined}
                // accent-accent reads odd, but it's the accent-color utility with our accent token
                className="mt-0.5 size-4 shrink-0 cursor-pointer accent-accent disabled:cursor-not-allowed pointer-coarse:size-5"
              />
              <div className="min-w-0">
                <label htmlFor={inputId} className="cursor-pointer text-sm">
                  {o.label}
                </label>
                {o.description ? (
                  <p id={`${inputId}-desc`} className="text-xs text-fg-muted">
                    {o.description}
                  </p>
                ) : null}
              </div>
            </div>
          );
        })}
      </div>
      {hint ? (
        <p id={`${id}-hint`} className="text-xs text-fg-muted">
          {hint}
        </p>
      ) : null}
    </fieldset>
  );
}
