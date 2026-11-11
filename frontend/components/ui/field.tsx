"use client";

import * as React from "react";
import { AlertCircle } from "lucide-react";
import { cn } from "@/lib/utils";
import { Label } from "./label";

/** Props to spread on the control rendered inside <Field>. */
export interface FieldControlProps {
  id: string;
  "aria-describedby"?: string;
  "aria-invalid"?: true;
  "aria-required"?: true;
  required?: boolean;
}

export interface FieldProps {
  /** Visible label text (units are appended: "Altitude (m)"). */
  label: React.ReactNode;
  /** Unit shown in the label, e.g. "m" or "%". */
  unit?: string;
  /** Help text under the control. */
  hint?: React.ReactNode;
  /** Inline error (validate on blur). When set, the control gets aria-invalid. */
  error?: React.ReactNode;
  required?: boolean;
  /** Control id; generated when omitted. */
  id?: string;
  className?: string;
  /** Hide the label visually (still read by screen readers). */
  hideLabel?: boolean;
  /** Extra element shown at the right of the label row (e.g. a reset link). */
  labelAside?: React.ReactNode;
  /** Render the control with the wiring props: `{(p) => <Input {...p} />}`. */
  children: (props: FieldControlProps) => React.ReactNode;
}

/**
 * Label + control + hint + inline error, with `aria-describedby` and `aria-invalid` wired.
 *
 *   <Field label="Altitude" unit="m" hint="Above ground level" error={errors.alt}>
 *     {(p) => <NumberInput {...p} value={alt} onValueChange={setAlt} unit="m" />}
 *   </Field>
 *
 * Error ids follow `${id}-error` so an error summary can link to `#${id}`.
 */
export function Field({
  label,
  unit,
  hint,
  error,
  required = false,
  id,
  className,
  hideLabel = false,
  labelAside,
  children,
}: FieldProps) {
  const autoId = React.useId();
  const controlId = id ?? `field-${autoId.replace(/:/g, "")}`;
  const hintId = hint ? `${controlId}-hint` : undefined;
  const errorId = error ? `${controlId}-error` : undefined;
  const describedBy = [errorId, hintId].filter(Boolean).join(" ") || undefined;

  const controlProps: FieldControlProps = {
    id: controlId,
    "aria-describedby": describedBy,
    ...(error ? { "aria-invalid": true as const } : {}),
    ...(required ? { "aria-required": true as const, required: true } : {}),
  };

  return (
    <div className={cn("flex flex-col gap-1.5", className)}>
      <div className={cn("flex items-baseline justify-between gap-2", hideLabel && "sr-only")}>
        <Label htmlFor={controlId}>
          {label}
          {unit ? <span className="font-normal text-fg-muted"> ({unit})</span> : null}
          {required ? (
            <>
              <span aria-hidden className="ml-0.5 text-danger">
                *
              </span>
              <span className="sr-only"> (required)</span>
            </>
          ) : null}
        </Label>
        {labelAside}
      </div>
      {children(controlProps)}
      {error ? (
        <p id={errorId} className="flex items-start gap-1.5 text-xs text-danger">
          <AlertCircle className="mt-px size-3.5 shrink-0" aria-hidden />
          <span>{error}</span>
        </p>
      ) : null}
      {hint ? (
        <p id={hintId} className="text-xs text-fg-muted">
          {hint}
        </p>
      ) : null}
    </div>
  );
}

export interface ErrorSummaryItem {
  /** id of the control (Field `id`) */
  id: string;
  message: string;
}

/**
 * Focusable error summary for forms (shown after a failed submit). Focus it with the ref
 * after validation so screen readers read it, and link each error to its control.
 */
export const ErrorSummary = React.forwardRef<
  HTMLDivElement,
  { errors: ErrorSummaryItem[]; title?: string; className?: string }
>(({ errors, title = "Please fix the following", className }, ref) => {
  const titleId = `error-summary-${React.useId().replace(/:/g, "")}`;
  if (errors.length === 0) return null;
  return (
    <div
      ref={ref}
      tabIndex={-1}
      role="alert"
      aria-labelledby={titleId}
      className={cn("rounded border border-danger/50 bg-danger/10 p-3 text-sm", className)}
    >
      <p id={titleId} className="flex items-center gap-1.5 font-medium text-fg">
        <AlertCircle className="size-4 text-danger" aria-hidden />
        {title}
      </p>
      <ul className="mt-1.5 list-disc space-y-0.5 pl-6">
        {errors.map((e) => (
          <li key={e.id}>
            <a
              href={`#${e.id}`}
              className="text-fg underline"
              onClick={(event) => {
                const target = document.getElementById(e.id);
                if (target) {
                  event.preventDefault();
                  target.focus();
                  target.scrollIntoView({ block: "center" });
                }
              }}
            >
              {e.message}
            </a>
          </li>
        ))}
      </ul>
    </div>
  );
});
ErrorSummary.displayName = "ErrorSummary";
