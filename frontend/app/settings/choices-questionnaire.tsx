"use client";

/**
 * "Help me choose": three or four plain questions that recommend the assessment choices, each with
 * a one-line reason (the mapping is in lib/choices.ts). Nothing changes until Apply.
 */

import { useId, useState } from "react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogClose, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { RadioGroup } from "@/components/ui/radio-group";
import { toast } from "@/components/ui/toaster";
import {
  AIRPORT_LABEL,
  NO_ANSWERS,
  PERMISSION_LABEL,
  READER_LABEL,
  USE_LABEL,
  asked,
  recommend,
  type ChoiceAnswers,
  type Recommendation,
} from "@/lib/choices";
import { BAND_CEILING_LABEL, ED318_LIMITS_LABEL, UNVERIFIED_BAND_LABEL } from "@/lib/labels";
import { useSettings } from "@/lib/store";

function options<T extends string>(labels: Readonly<Record<T, string>>) {
  return (Object.keys(labels) as T[]).map((value) => ({ value, label: labels[value] }));
}

function Recommended<T extends string>({
  term,
  labels,
  rec,
  current,
  isAsked = true,
}: {
  term: string;
  labels: Readonly<Record<T, string>>;
  rec: Recommendation<T> | null;
  current: T;
  isAsked?: boolean;
}) {
  return (
    <div>
      <dt className="text-xs text-fg-muted">{term}</dt>
      {rec ? (
        <dd>
          <span className="font-medium">{labels[rec.value]}</span>. {rec.reason}
        </dd>
      ) : (
        <dd>
          <span className="font-medium">{labels[current]}</span>, unchanged ({isAsked ? "not answered" : "not asked"}).
        </dd>
      )}
    </div>
  );
}

export function ChoicesQuestionnaire({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const id = useId();
  const setChoices = useSettings((s) => s.setChoices);
  const bandCeiling = useSettings((s) => s.bandCeiling);
  const unverifiedBand = useSettings((s) => s.unverifiedBand);
  const ed318Limits = useSettings((s) => s.ed318Limits);
  // kept between opens, so reopening shows the last anwers
  const [answers, setAnswers] = useState<ChoiceAnswers>(NO_ANSWERS);
  const ask = asked(answers.use);
  const rec = recommend(answers);

  const answer =
    <K extends keyof ChoiceAnswers>(key: K) =>
    (value: NonNullable<ChoiceAnswers[K]>) =>
      setAnswers((a) => ({ ...a, [key]: value }));

  const changes = {
    ...(rec.bandCeiling ? { bandCeiling: rec.bandCeiling.value } : {}),
    ...(rec.unverifiedBand ? { unverifiedBand: rec.unverifiedBand.value } : {}),
    ...(rec.ed318Limits ? { ed318Limits: rec.ed318Limits.value } : {}),
  };
  // setChoices marks just these as set (each hides its panel's note); with none, Apply would only say "set"
  const nothing = Object.keys(changes).length === 0;

  const apply = () => {
    setChoices(changes);
    onOpenChange(false);
    toast.success("Assessment choices set", { description: "New flight audits, eVTOL runs and ED-318 exports start from them." });
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle>Help me choose</DialogTitle>
          <DialogDescription>A few questions about how you use AirBrief. Nothing changes until you apply.</DialogDescription>
        </DialogHeader>
        <RadioGroup legend="What do you mostly use AirBrief for?" options={options(USE_LABEL)} value={answers.use} onChange={answer("use")} />
        {ask.permission ? (
          <RadioGroup
            legend="Do you get Digital Sky permission for flights in yellow zones?"
            options={options(PERMISSION_LABEL)}
            value={answers.permission}
            onChange={answer("permission")}
          />
        ) : null}
        {ask.airports ? (
          <RadioGroup
            legend="Near airports, how cautious should eVTOL studies be?"
            options={options(AIRPORT_LABEL)}
            value={answers.airports}
            onChange={answer("airports")}
          />
        ) : null}
        <RadioGroup
          legend="What will read your ED-318 zone exports?"
          options={options(READER_LABEL)}
          value={answers.reader}
          onChange={answer("reader")}
        />
        <section aria-labelledby={`${id}-rec`} className="rounded border border-border bg-elevated/40 p-3">
          <h3 id={`${id}-rec`} className="text-sm font-semibold">
            Recommended
          </h3>
          <dl aria-live="polite" className="mt-2 space-y-2 text-sm">
            <Recommended term="Flight audit, above a band's ceiling" labels={BAND_CEILING_LABEL} rec={rec.bandCeiling} current={bandCeiling} isAsked={ask.permission} />
            <Recommended
              term="eVTOL, within 5 km of an airport"
              labels={UNVERIFIED_BAND_LABEL}
              rec={rec.unverifiedBand}
              current={unverifiedBand}
              isAsked={ask.airports}
            />
            <Recommended term="ED-318 exports, vertical limits" labels={ED318_LIMITS_LABEL} rec={rec.ed318Limits} current={ed318Limits} />
          </dl>
        </section>
        <DialogFooter>
          {/* the footer stacks reversed on phones: order-last puts this on top there */}
          {nothing ? (
            <p className="order-last text-xs text-fg-muted sm:order-first sm:mr-auto sm:self-center">Nothing to apply yet: answer a question above.</p>
          ) : null}
          <DialogClose asChild>
            <Button variant="ghost">Cancel</Button>
          </DialogClose>
          <Button onClick={apply} disabled={nothing}>
            Apply
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
