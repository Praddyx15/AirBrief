"use client";

import * as React from "react";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Kbd } from "@/components/ui/kbd";

interface Shortcut {
  keys: string[][];
  description: string;
}

interface ShortcutGroup {
  title: string;
  items: Shortcut[];
}

const GROUPS: ShortcutGroup[] = [
  {
    title: "Map (when the map has focus)",
    items: [
      { keys: [["←"], ["→"], ["↑"], ["↓"]], description: "Pan the map" },
      { keys: [["+"], ["-"]], description: "Zoom in / out" },
      { keys: [["Shift", "←"], ["Shift", "→"]], description: "Rotate (3D)" },
      { keys: [["Shift", "↑"], ["Shift", "↓"]], description: "Tilt (3D)" },
      { keys: [["N"]], description: "Focus the next feature" },
      { keys: [["Shift", "N"]], description: "Focus the previous feature" },
      { keys: [["Enter"]], description: "Select the focused feature" },
      { keys: [["Shift", "Click"], ["Ctrl", "Click"]], description: "Add a feature to the selection, or take it out (⌘+click on macOS)" },
      { keys: [["Esc"]], description: "Clear the keyboard focus, then the selection" },
      { keys: [["F"]], description: "Fit the map to the data" },
      { keys: [["?"]], description: "Show this help" },
    ],
  },
  {
    title: "Tables and lists",
    items: [
      { keys: [["↑"], ["↓"]], description: "Move between rows" },
      { keys: [["Home"], ["End"]], description: "First / last row" },
      { keys: [["Enter"], ["Space"]], description: "Select the row (and the feature on the map)" },
    ],
  },
  {
    title: "Playback (when the transport bar has focus)",
    items: [
      { keys: [["Space"]], description: "Play / pause" },
      { keys: [["←"], ["→"]], description: "Step one frame" },
      { keys: [["Shift", "←"], ["Shift", "→"]], description: "Jump 5 seconds" },
    ],
  },
  {
    title: "Everywhere",
    items: [{ keys: [["Ctrl", "K"]], description: "Command palette (⌘K on macOS)" }],
  },
];

export interface ShortcutsDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/** Keyboard shortcut help, opened with `?` on the map or from the map controls. */
export function ShortcutsDialog({ open, onOpenChange }: ShortcutsDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle>Keyboard shortcuts</DialogTitle>
          <DialogDescription>Everything on the map and in the workspace works without a mouse.</DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          {GROUPS.map((group) => (
            <section key={group.title} aria-labelledby={`sc-${group.title.replace(/\W+/g, "-")}`}>
              <h3 id={`sc-${group.title.replace(/\W+/g, "-")}`} className="mb-1.5 text-sm font-medium text-fg">
                {group.title}
              </h3>
              <dl className="divide-y divide-border rounded border border-border text-sm">
                {group.items.map((item) => (
                  <div key={item.description} className="flex items-center justify-between gap-4 px-3 py-1.5">
                    <dt className="text-fg-muted">{item.description}</dt>
                    <dd className="flex shrink-0 flex-wrap items-center justify-end gap-1">
                      {item.keys.map((combo, i) => (
                        <React.Fragment key={combo.join("+")}>
                          {i > 0 ? <span className="text-2xs text-fg-muted">or</span> : null}
                          <span className="inline-flex items-center gap-0.5">
                            {combo.map((k) => (
                              <Kbd key={k}>{k}</Kbd>
                            ))}
                          </span>
                        </React.Fragment>
                      ))}
                    </dd>
                  </div>
                ))}
              </dl>
            </section>
          ))}
        </div>
      </DialogContent>
    </Dialog>
  );
}
