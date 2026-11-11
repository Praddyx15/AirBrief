"use client";

/**
 * The assessment choices in the flight audit and eVTOL panels (docs/features/choices.md): a run
 * starts from the choice made in Settings and may differ from it, and while the choices are
 * untouched a small note offers the questionnaire. Never a modal, so no flow is interrupted.
 */

import * as React from "react";
import Link from "next/link";
import { SlidersHorizontal, X } from "lucide-react";
import { useSettings, useSettingsHydrated, type ChoiceKey } from "@/lib/store";
import { IconButton } from "@/components/ui/icon-button";
import { RadioGroup } from "@/components/ui/radio-group";

/** Settings → Assessment choices, and the same with "Help me choose" open. */
export const CHOICES_HREF = "/settings#assessment-choices";
export const QUESTIONNAIRE_HREF = "/settings#help-me-choose";

const FOCUSABLE = "a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex='-1'])";

/**
 * "Using the default assessment choices", until this panel's own choice is set or the note is
 * dismissed (per browser, for every panel).
 */
export function ChoicesHint({ choice }: { choice: ChoiceKey }) {
  const ref = React.useRef<HTMLDivElement>(null);
  const hydrated = useSettingsHydrated();
  const hidden = useSettings((s) => s.choicesSet.includes(choice) || s.choicesHintDismissed);
  const dismiss = useSettings((s) => s.dismissChoicesHint);
  // Nothing before the stored settings are read, so it never flashes for someone who set them.
  if (!hydrated || hidden) return null;

  const close = () => {
    // the button goes away with the note: carry on at the next control, not on <body>
    const note = ref.current;
    const next = Array.from(note?.parentElement?.querySelectorAll<HTMLElement>(FOCUSABLE) ?? []).find(
      (el) =>
        note !== null && el.tabIndex >= 0 && !note.contains(el) && Boolean(note.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING),
    );
    dismiss();
    next?.focus();
  };

  return (
    <div ref={ref} role="note" className="flex items-start gap-2 rounded border border-dashed border-border p-2.5 text-xs text-fg-muted">
      <SlidersHorizontal aria-hidden className="mt-0.5 size-3.5 shrink-0" />
      <p className="min-w-0 flex-1">
        Using the default assessment choices.{" "}
        <Link href={QUESTIONNAIRE_HREF} className="text-accent-text underline underline-offset-4">
          Answer 3 questions to set yours
        </Link>
      </p>
      <IconButton label="Dismiss the note on assessment choices" icon={<X />} size="icon-sm" className="-my-1 -mr-1 shrink-0" onClick={close} />
    </div>
  );
}

/**
 * One choice for this run: it starts from the user's setting, says so (with a link to it), and
 * says when the run differs.
 */
export function ChoiceField<T extends string>({
  legend,
  choices,
  labels,
  hints,
  value,
  setting,
  onChange,
}: {
  legend: string;
  choices: readonly T[];
  labels: Readonly<Record<T, string>>;
  hints: Readonly<Record<T, string>>;
  value: T;
  setting: T;
  onChange: (value: T) => void;
}) {
  const link = (
    <Link href={CHOICES_HREF} className="text-accent-text underline underline-offset-4">
      assessment choices
    </Link>
  );
  return (
    <RadioGroup
      legend={legend}
      inline
      options={choices.map((c) => ({ value: c, label: labels[c] }))}
      value={value}
      onChange={onChange}
      hint={
        <>
          {hints[value]}{" "}
          {value === setting ? (
            <>From your {link}; this run can differ.</>
          ) : (
            <>
              This run differs from your {link} ({labels[setting]}).
            </>
          )}
        </>
      }
    />
  );
}
