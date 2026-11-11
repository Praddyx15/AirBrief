"use client";

/**
 * Workspace live region (`aria-live="polite"`) for background events that show no toast
 * (dataset processing, a job starting, show conflict seeks). Events that toast are not also
 * announced here: the toaster is a polite live region itself, so they would be read twice.
 * Modules call `announce("...")` from `useAnnouncer` or the non-hook `announce()`.
 */

import * as React from "react";
import { create } from "zustand";

interface AnnouncerState {
  message: string;
  /** increments on each announcement so repeating the same text is still read out */
  seq: number;
  announce: (message: string) => void;
  clear: () => void;
}

export const useAnnouncer = create<AnnouncerState>()((set) => ({
  message: "",
  seq: 0,
  announce: (message) => set((s) => ({ message: message.trim(), seq: s.seq + 1 })),
  clear: () => set({ message: "" }),
}));

/** Announce a message politely (usable outside React components). */
export function announce(message: string): void {
  useAnnouncer.getState().announce(message);
}

/** Mount once per page (the workspace does). */
export function LiveRegion() {
  const message = useAnnouncer((s) => s.message);
  const seq = useAnnouncer((s) => s.seq);
  return (
    <div role="status" aria-live="polite" aria-atomic="true" className="sr-only" data-testid="workspace-live-region">
      {/* The key forces a fresh text node so identical consecutive messages are re-read. */}
      <span key={seq}>{message}</span>
    </div>
  );
}
