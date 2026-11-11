/**
 * Dataset deletion with an undo window that survives the tab closing.
 *
 * A deletion is first recorded in the persisted history with the time it is due, then sent when
 * the undo window ends. If the page is closed or reloaded before that, `pagehide` sends it at
 * once (a keepalive request), and the next load sends every recorded deletion that is due, so a
 * dataset the user was told is deleted never stays on the server. Undo removes the record.
 */

import type { QueryClient } from "@tanstack/react-query";
import { ApiError, deleteFileOnUnload } from "./api";
import { deleteDataset } from "./queries";
import { useHistory } from "./store";

const timers = new Map<string, number>();

function pendingDueAt(id: string): number | null {
  const raw = useHistory.getState().pendingDeletes[id];
  const due = raw === undefined ? NaN : Number(raw);
  return Number.isFinite(due) ? due : null;
}

async function send(client: QueryClient, id: string): Promise<void> {
  timers.delete(id);
  // Another tab may have undone it (the history is shared through localStorage).
  await useHistory.persist.rehydrate();
  if (pendingDueAt(id) === null) return;
  try {
    await deleteDataset(client, id);
  } catch (error) {
    // Already gone (sent by pagehide or another tab): that is what was asked for.
    if (!(error instanceof ApiError && error.status === 404)) throw error;
    useHistory.getState().forgetDataset(id);
  } finally {
    useHistory.getState().clearPendingDelete(id);
  }
}

function arm(client: QueryClient, id: string, delayMs: number, onError?: (error: unknown) => void): void {
  const existing = timers.get(id);
  if (existing !== undefined) window.clearTimeout(existing);
  const timer = window.setTimeout(() => {
    send(client, id).catch((error: unknown) => onError?.(error));
  }, Math.max(0, delayMs));
  timers.set(id, timer);
}

/** Delete `id` after `delayMs` unless undone. `onError` hears about a failed request. */
export function scheduleDatasetDelete(
  client: QueryClient,
  id: string,
  delayMs: number,
  onError?: (error: unknown) => void,
): void {
  useHistory.getState().markPendingDelete(id, Date.now() + delayMs);
  arm(client, id, delayMs, onError);
}

export function undoDatasetDelete(id: string): void {
  const timer = timers.get(id);
  if (timer !== undefined) window.clearTimeout(timer);
  timers.delete(id);
  useHistory.getState().clearPendingDelete(id);
}

/**
 * Send the deletions armed in this tab now, as the page goes away for good. A page kept in the
 * back/forward cache (`persisted`) may come back and still offer Undo, so it is left alone; if
 * it never comes back, the next load sends the recorded deletion.
 */
function flushOnUnload(event: PageTransitionEvent): void {
  if (event.persisted) return;
  const { pendingDeletes, ownerTokens } = useHistory.getState();
  for (const id of timers.keys()) {
    if (pendingDeletes[id] !== undefined) deleteFileOnUnload(id, ownerTokens[id]);
  }
}

let installed = false;

/**
 * Re-arm the recorded deletions (overdue ones go at once) and send this tab's pending ones when
 * the page is hidden for good. Called once by <Providers> after the history has loaded.
 */
export function resumePendingDeletes(client: QueryClient): void {
  if (typeof window === "undefined") return;
  if (!installed) {
    installed = true;
    window.addEventListener("pagehide", flushOnUnload);
  }
  const { pendingDeletes } = useHistory.getState();
  const now = Date.now();
  for (const id of Object.keys(pendingDeletes)) {
    if (timers.has(id)) continue;
    const due = pendingDueAt(id) ?? now;
    arm(client, id, due - now);
  }
}
