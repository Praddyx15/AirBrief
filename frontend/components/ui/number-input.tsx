"use client";

import * as React from "react";
import { parseNumberText } from "@/lib/format";
import { cn } from "@/lib/utils";
import { inputClassName } from "./input";

export interface NumberInputProps
  extends Omit<
    React.InputHTMLAttributes<HTMLInputElement>,
    "value" | "defaultValue" | "onChange" | "type" | "min" | "max" | "step" | "inputMode"
  > {
  value: number | null;
  /** Called with every valid number typed, on arrow-key steps, and on blur. */
  onValueChange: (value: number | null) => void;
  min?: number;
  max?: number;
  /** Arrow-key step (Shift multiplies by 10). Default 1. */
  step?: number;
  /** Unit suffix shown inside the input, e.g. "m". Decorative: put the unit in the label too. */
  unit?: string;
  /** Decimals used when formatting the value for display (default: as typed / up to 6). */
  decimals?: number;
  /** Allow clearing the input (emits null). Default false: an empty input reverts on blur. */
  allowEmpty?: boolean;
  /** Clamp to [min, max] on blur and arrow keys. Default true for arrows, false for typing. */
  clampOnBlur?: boolean;
  /**
   * Called with a message when the text is not a number ("0,500" as soon as it is typed;
   * unfinished text such as "1,2," when the field is left or on Enter) and with null once it
   * is. With this callback the text is kept on blur so the surrounding field can show the error
   * and block submitting; without it, such text reverts to the last valid value. Either way,
   * Enter on such text does not submit the form.
   */
  onInvalidText?: (message: string | null) => void;
  className?: string;
  inputClassName?: string;
}

function formatValue(value: number | null, decimals?: number): string {
  if (value === null || !Number.isFinite(value)) return "";
  if (decimals !== undefined) return value.toFixed(decimals);
  return String(Number(value.toFixed(6)));
}

function parse(text: string): number | null {
  const parsed = parseNumberText(text);
  return parsed.kind === "number" ? parsed.value : null;
}

/** Why the text is not a number, once the user is done typing (null when it is one or is empty). */
function textError(text: string): string | null {
  const parsed = parseNumberText(text);
  return parsed.kind === "invalid" || parsed.kind === "partial" ? parsed.message : null;
}

function clampTo(n: number, min?: number, max?: number): number {
  let v = n;
  if (min !== undefined) v = Math.max(min, v);
  if (max !== undefined) v = Math.min(max, v);
  return v;
}

/** Validation messages belong to the surrounding <Field> (validate on blur). */
export const NumberInput = React.forwardRef<HTMLInputElement, NumberInputProps>(
  (
    {
      value,
      onValueChange,
      min,
      max,
      step = 1,
      unit,
      decimals,
      allowEmpty = false,
      clampOnBlur = false,
      onInvalidText,
      className,
      inputClassName: inputClass,
      onBlur,
      onFocus,
      onKeyDown,
      disabled,
      style,
      ...props
    },
    ref,
  ) => {
    const [draft, setDraft] = React.useState<string>(() => formatValue(value, decimals));
    const focused = React.useRef(false);
    // Read through a ref: an inline callback must not re-run the sync effect below.
    const invalidRef = React.useRef(onInvalidText);
    invalidRef.current = onInvalidText;

    // Follow external changes (e.g. a paired slider or a preset) while the user is not typing.
    React.useEffect(() => {
      if (focused.current) return;
      setDraft(formatValue(value, decimals));
      invalidRef.current?.(null);
    }, [value, decimals]);

    const precision = React.useMemo(() => {
      const s = String(step);
      const dot = s.indexOf(".");
      return dot === -1 ? 0 : s.length - dot - 1;
    }, [step]);

    // The stored value is rounded to `decimals`, exactly as displayed.
    const commit = (next: number | null) => {
      const value = next !== null && decimals !== undefined ? Number(next.toFixed(decimals)) : next;
      onValueChange(value);
      setDraft(formatValue(value, decimals));
      invalidRef.current?.(null);
    };

    const stepBy = (direction: 1 | -1, multiplier: number) => {
      const base = value ?? parse(draft) ?? min ?? 0;
      const raw = base + direction * step * multiplier;
      const rounded = Number(raw.toFixed(Math.max(precision, decimals ?? 0)));
      commit(clampTo(rounded, min, max));
    };

    return (
      <div className={cn("relative flex w-full items-center", className)}>
        <input
          ref={ref}
          type="text"
          inputMode="decimal"
          autoComplete="off"
          spellCheck={false}
          role="spinbutton"
          aria-valuenow={value ?? undefined}
          aria-valuemin={min}
          aria-valuemax={max}
          aria-valuetext={value !== null && unit ? `${formatValue(value, decimals)} ${unit}` : undefined}
          disabled={disabled}
          value={draft}
          className={cn(inputClassName, "num text-right", inputClass)}
          // Room for the unit suffix (monospace, so its width is its length in ch).
          style={unit ? { paddingRight: `calc(${unit.length}ch + 1.25rem)`, ...style } : style}
          onFocus={(event) => {
            focused.current = true;
            onFocus?.(event);
          }}
          onChange={(event) => {
            const text = event.target.value;
            setDraft(text);
            const parsed = parseNumberText(text);
            // Unfinished text ("12,") is not an error until the user leaves the field.
            onInvalidText?.(parsed.kind === "invalid" ? parsed.message : null);
            if (parsed.kind === "number") onValueChange(parsed.value);
            else if (parsed.kind === "empty" && allowEmpty) onValueChange(null);
          }}
          onBlur={(event) => {
            focused.current = false;
            const parsed = parse(draft);
            const empty = draft.trim() === "";
            if (parsed !== null) {
              commit(clampOnBlur ? clampTo(parsed, min, max) : parsed);
            } else if (empty && allowEmpty) {
              commit(null);
            } else if (empty || !onInvalidText) {
              // Nothing usable typed: show the last valid value again.
              setDraft(formatValue(value, decimals));
              onInvalidText?.(null);
            } else {
              // Keep the text so the user can see and fix it; the field shows why it is invalid.
              onInvalidText(textError(draft));
            }
            onBlur?.(event);
          }}
          onKeyDown={(event) => {
            onKeyDown?.(event);
            if (event.defaultPrevented || disabled) return;
            const mult = event.shiftKey ? 10 : 1;
            if (event.key === "ArrowUp") {
              event.preventDefault();
              stepBy(1, mult);
            } else if (event.key === "ArrowDown") {
              event.preventDefault();
              stepBy(-1, mult);
            } else if (event.key === "PageUp") {
              event.preventDefault();
              stepBy(1, 10);
            } else if (event.key === "PageDown") {
              event.preventDefault();
              stepBy(-1, 10);
            } else if (event.key === "Home" && min !== undefined) {
              event.preventDefault();
              commit(min);
            } else if (event.key === "End" && max !== undefined) {
              event.preventDefault();
              commit(max);
            } else if (event.key === "Enter") {
              const parsed = parse(draft);
              if (parsed !== null) {
                commit(clampOnBlur ? clampTo(parsed, min, max) : parsed);
              } else if (draft.trim() !== "") {
                // Text that is not a number never submits the form with the last valid value.
                event.preventDefault();
                onInvalidText?.(textError(draft));
              }
            }
          }}
          {...props}
        />
        {unit ? (
          <span
            aria-hidden
            className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 select-none font-mono text-sm text-fg-muted"
          >
            {unit}
          </span>
        ) : null}
      </div>
    );
  },
);
NumberInput.displayName = "NumberInput";
