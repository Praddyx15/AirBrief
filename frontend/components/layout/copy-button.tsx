"use client";

import { useEffect, useRef, useState } from "react";
import { Check, Copy } from "lucide-react";
import { IconButton } from "@/components/ui/icon-button";
import { toast } from "@/components/ui/toaster";

/**
 * Fallback for origins without the async Clipboard API (plain-http LAN installs) or without
 * permission: copy from a temporary, off-screen text area.
 */
function copyWithTextArea(text: string): boolean {
  const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
  const area = document.createElement("textarea");
  area.value = text;
  area.setAttribute("readonly", "");
  area.setAttribute("aria-hidden", "true");
  area.style.position = "fixed";
  area.style.top = "0";
  area.style.left = "-9999px";
  document.body.appendChild(area);
  area.select();
  let copied = false;
  try {
    copied = document.execCommand("copy");
  } catch {
    copied = false;
  }
  area.remove();
  previous?.focus();
  return copied;
}

function selectElementText(id: string): boolean {
  const element = document.getElementById(id);
  const selection = window.getSelection();
  if (!element || !selection) return false;
  const range = document.createRange();
  range.selectNodeContents(element);
  selection.removeAllRanges();
  selection.addRange(range);
  return true;
}

/**
 * Copies `text` to the clipboard and confirms with a check icon and a live announcement. When
 * copying is not possible, it says so and selects `targetId` (the visible text) for Ctrl+C.
 */
export function CopyButton({
  text,
  label = "Copy to clipboard",
  targetId,
  className,
}: {
  text: string;
  label?: string;
  /** id of the element that shows `text`, selected when copying fails */
  targetId?: string;
  className?: string;
}) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<number | null>(null);

  useEffect(
    () => () => {
      if (timer.current !== null) window.clearTimeout(timer.current);
    },
    [],
  );

  const copy = async () => {
    let ok = false;
    try {
      if (!navigator.clipboard?.writeText) throw new Error("Clipboard API unavailable");
      await navigator.clipboard.writeText(text);
      ok = true;
    } catch {
      ok = copyWithTextArea(text);
    }
    if (timer.current !== null) window.clearTimeout(timer.current);
    setCopied(ok);
    if (ok) {
      timer.current = window.setTimeout(() => setCopied(false), 2000);
      return;
    }
    const selected = targetId ? selectElementText(targetId) : false;
    toast.error("Could not copy", {
      description: selected
        ? "The text is selected: press Ctrl+C (⌘C on a Mac) to copy it."
        : "Select the text and press Ctrl+C (⌘C on a Mac).",
    });
  };

  return (
    <>
      <IconButton
        label={copied ? "Copied" : label}
        icon={copied ? <Check /> : <Copy />}
        size="icon-sm"
        onClick={() => void copy()}
        className={className}
      />
      {/* A failure is announced by its toast (the toaster is a live region): not twice. */}
      <span className="sr-only" aria-live="polite">
        {copied ? "Copied to clipboard" : ""}
      </span>
    </>
  );
}
