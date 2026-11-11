"use client";

/**
 * Date and time inputs for the environment form, validated like `NumField`: the error shows after
 * the field is left (or after a blocked Run) and is registered with the form's error summary.
 */

import * as React from "react";
import { Field } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import type { FormErrors } from "../shared";

export interface DateTimeFieldProps {
  label: string;
  type: "date" | "time";
  /** "YYYY-MM-DD" (date) or "HH:MM" (time); "" while empty */
  value: string;
  onChange: (value: string) => void;
  validate: (value: string) => string | null;
  hint?: React.ReactNode;
  form: FormErrors;
}

export function DateTimeField({ label, type, value, onChange, validate, hint, form }: DateTimeFieldProps) {
  const id = React.useId();
  const [touched, setTouched] = React.useState(false);
  const error = validate(value);
  const report = form.report;
  React.useEffect(() => {
    report(id, error ? `${label}: ${error}` : null);
  }, [id, error, label, report]);
  React.useEffect(() => () => report(id, null), [id, report]);
  const showError = touched || form.attempted;

  return (
    <Field id={id} label={label} hint={hint} error={showError ? error : null} required>
      {(p) => (
        <Input
          {...p}
          type={type}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onBlur={() => setTouched(true)}
          className="num"
        />
      )}
    </Field>
  );
}
