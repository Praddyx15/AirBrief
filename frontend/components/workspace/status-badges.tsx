"use client";

import * as React from "react";
import { AlertTriangle, Ban, CheckCircle2, Clock, Info, Loader2, MinusCircle, OctagonAlert, Users, type LucideIcon } from "lucide-react";
import {
  MEASUREMENT_STATUS_LABEL,
  toneForAnalysisStatus,
  toneForFileStatus,
  toneForMeasurementStatus,
  type Tone,
} from "@/lib/colors";
import type { AnalysisStatus, FileStatus, MeasurementStatus } from "@/lib/types";
import { cn } from "@/lib/utils";
import { Badge, type BadgeProps } from "@/components/ui/badge";
import { SimpleTooltip } from "@/components/ui/tooltip";

const TONE_VARIANT: Record<Tone, NonNullable<BadgeProps["variant"]>> = {
  ok: "ok",
  warn: "warn",
  danger: "danger",
  info: "info",
  neutral: "secondary",
};

const TONE_ICON: Record<Tone, LucideIcon> = {
  ok: CheckCircle2,
  warn: AlertTriangle,
  danger: OctagonAlert,
  info: Info,
  neutral: MinusCircle,
};

/** Badge with icon + text for any tone (status never relies on colour alone). */
export function ToneBadge({
  tone,
  label,
  icon,
  spin = false,
  className,
}: {
  tone: Tone;
  label: string;
  icon?: LucideIcon;
  spin?: boolean;
  className?: string;
}) {
  const Icon = icon ?? TONE_ICON[tone];
  return (
    <Badge variant={TONE_VARIANT[tone]} className={className}>
      <Icon aria-hidden className={cn(spin && "animate-spin motion-reduce:animate-none")} />
      {label}
    </Badge>
  );
}

/**
 * Measurement status. `compact` shows only the status icon (the label is in a tooltip and for
 * screen readers), for narrow tables such as the feature list.
 */
export function MeasurementStatusBadge({
  status,
  className,
  compact = false,
}: {
  status: MeasurementStatus;
  className?: string;
  compact?: boolean;
}) {
  const tone = toneForMeasurementStatus(status);
  const label = MEASUREMENT_STATUS_LABEL[status];
  if (!compact) return <ToneBadge tone={tone} label={label} className={className} />;
  const Icon = TONE_ICON[tone];
  return (
    <SimpleTooltip content={label}>
      <Badge variant={TONE_VARIANT[tone]} className={cn("px-1", className)}>
        <Icon aria-hidden />
        <span className="sr-only">{label}</span>
      </Badge>
    </SimpleTooltip>
  );
}

const FILE_STATUS_LABEL: Record<FileStatus, string> = {
  PENDING: "Queued",
  PROCESSING: "Processing",
  COMPLETED: "Ready",
  FAILED: "Failed",
};

export function FileStatusBadge({ status, className }: { status: FileStatus; className?: string }) {
  const active = status === "PENDING" || status === "PROCESSING";
  return (
    <ToneBadge
      tone={toneForFileStatus(status)}
      label={FILE_STATUS_LABEL[status]}
      icon={status === "PENDING" ? Clock : active ? Loader2 : undefined}
      spin={status === "PROCESSING"}
      className={className}
    />
  );
}

export const ANALYSIS_STATUS_LABEL: Record<AnalysisStatus, string> = {
  PENDING: "Queued",
  RUNNING: "Running",
  COMPLETED: "Completed",
  FAILED: "Failed",
  CANCELLED: "Cancelled",
};

export function AnalysisStatusBadge({ status, className }: { status: AnalysisStatus; className?: string }) {
  return (
    <ToneBadge
      tone={toneForAnalysisStatus(status)}
      label={ANALYSIS_STATUS_LABEL[status]}
      icon={status === "PENDING" ? Clock : status === "RUNNING" ? Loader2 : status === "CANCELLED" ? Ban : undefined}
      spin={status === "RUNNING"}
      className={className}
    />
  );
}

/** An analysis someone else ran on this dataset, without its owner token (`useAddedByVisitor`). */
export function VisitorBadge({ className }: { className?: string }) {
  return <ToneBadge tone="warn" icon={Users} label="Added by another visitor" className={className} />;
}
