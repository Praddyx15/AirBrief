"use client";

/**
 * Entries of an export menu (the analysis and the dataset exports): each menu saves one file at a
 * time, the entry being fetched shows a spinner and the others say they are waiting.
 */

import * as React from "react";
import { Loader2 } from "lucide-react";
import { toast } from "@/components/ui/toaster";
import { describeError, downloadExport } from "@/lib/api";
import { EXPORT_BUSY_NOTE } from "@/lib/labels";
import { downloadBlob, isPlainClick } from "@/lib/utils";

export const EXPORT_ITEM_CLASS =
  "flex w-full cursor-pointer flex-col items-start gap-0.5 rounded px-2 py-1.5 text-left text-sm text-fg no-underline hover:bg-elevated focus-visible:bg-elevated disabled:cursor-not-allowed disabled:opacity-50 aria-disabled:cursor-not-allowed aria-disabled:opacity-50 pointer-coarse:min-h-11";

export function ExportItemContent({ icon, label, description }: { icon: React.ReactNode; label: string; description: string }) {
  return (
    <>
      <span className="flex items-center gap-2 font-medium">
        {icon}
        {label}
      </span>
      <span className="pl-6 text-xs text-fg-muted">{description}</span>
    </>
  );
}

/**
 * One export at a time for a menu. `saving` (the busy entry's key) is for display; the ref is the
 * guard, since two clicks before a re-render would both still see `saving` as null.
 */
export function useExportSaver() {
  const [saving, setSaving] = React.useState<string | null>(null);
  const running = React.useRef(false);
  const run = async (key: string, job: () => void | Promise<void>) => {
    if (running.current) return;
    running.current = true;
    setSaving(key);
    try {
      await job();
    } catch (error) {
      toast.error("Export failed", { description: describeError(error).message });
    } finally {
      running.current = false;
      setSaving(null);
    }
  };
  return { saving, running, run };
}

export type ExportSaver = ReturnType<typeof useExportSaver>;

/**
 * One entry: a server export (`href`) or a file built in the browser (`build`, the demo). The API
 * is another origin, where `download` is ignored and an error answer would replace the page, so
 * `href` is fetched and saved under the API's file name (`name` if it gives none). A modified
 * click or a middle click (new tab etc.) is left to the browser, except while the menu is exporting.
 * With neither `href` nor `build` the entry is shown disabled.
 */
export function ExportItem({
  saver,
  id,
  href,
  build,
  name,
  icon,
  label,
  description,
}: {
  saver: ExportSaver;
  /** unique in the menu: marks which entry is busy */
  id: string;
  href?: string | null;
  build?: () => void | Promise<void>;
  name: string;
  icon: React.ReactNode;
  label: string;
  description: string;
}) {
  const busy = saver.saving === id;
  const waiting = saver.saving !== null && !busy;
  const content = (
    <ExportItemContent
      icon={busy ? <Loader2 aria-hidden className="size-4 animate-spin motion-reduce:animate-none" /> : icon}
      label={label}
      description={busy ? EXPORT_BUSY_NOTE.self : waiting ? EXPORT_BUSY_NOTE.other : description}
    />
  );

  // "" is no link either
  if (!href) {
    return (
      <button
        type="button"
        className={EXPORT_ITEM_CLASS}
        disabled={!build}
        aria-busy={busy || undefined}
        aria-disabled={waiting || undefined}
        onClick={() => build && void saver.run(id, build)}
      >
        {content}
      </button>
    );
  }
  return (
    <a
      href={href}
      download={name}
      className={EXPORT_ITEM_CLASS}
      aria-busy={busy || undefined}
      aria-disabled={waiting || undefined}
      onAuxClick={(e) => {
        if (saver.running.current) e.preventDefault();
      }}
      onClick={(e) => {
        if (saver.running.current) {
          e.preventDefault();
          return;
        }
        if (!isPlainClick(e)) return;
        e.preventDefault();
        void saver.run(id, async () => {
          const res = await downloadExport(href);
          downloadBlob(res.blob, res.filename ?? name, res.blob.type || "application/octet-stream");
        });
      }}
    >
      {content}
    </a>
  );
}
