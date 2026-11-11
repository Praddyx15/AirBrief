"use client";

import * as React from "react";
import type * as DialogPrimitive from "@radix-ui/react-dialog";

type ContentProps = React.ComponentPropsWithoutRef<typeof DialogPrimitive.Content>;
type InteractOutsideEvent = Parameters<NonNullable<ContentProps["onInteractOutside"]>>[0];

export interface ReturnFocusHandlers {
  onOpenAutoFocus: (event: Event) => void;
  onCloseAutoFocus: (event: Event) => void;
  onInteractOutside: (event: InteractOutsideEvent) => void;
}

function canFocus(el: HTMLElement): boolean {
  if (!el.isConnected || el.getClientRects().length === 0) return false;
  return !(el.hasAttribute("disabled") || el.closest("[inert]"));
}

/**
 * Returns keyboard focus to the element that had it when a Radix dialog or sheet opened.
 *
 * Radix only restores focus to a `<DialogTrigger>`. Most of our dialogs open from code (the
 * Ctrl+K palette, "?" on the map, header buttons that set state), so without this, closing one
 * left focus on `<body>` and sent keyboard users back to the top of the page (WCAG 2.4.3).
 *
 * - The opener is recorded in `onOpenAutoFocus`, which Radix fires before it moves focus in.
 * - On close, focus goes back only when it was lost (on `<body>`); if the user already moved it
 *   elsewhere, it stays there.
 * - `afterOutsideInteraction: false` (non-modal panels) leaves focus alone after a click outside,
 *   as Radix does.
 * Handlers passed by the caller run first; calling `preventDefault()` in them opts out.
 */
export function useReturnFocus(
  props: Partial<Pick<ContentProps, "onOpenAutoFocus" | "onCloseAutoFocus" | "onInteractOutside">>,
  options: { afterOutsideInteraction: boolean },
): ReturnFocusHandlers {
  const { onOpenAutoFocus, onCloseAutoFocus, onInteractOutside } = props;
  const openerRef = React.useRef<HTMLElement | null>(null);
  const interactedOutsideRef = React.useRef(false);
  const { afterOutsideInteraction } = options;

  return React.useMemo<ReturnFocusHandlers>(
    () => ({
      onOpenAutoFocus: (event) => {
        const active = document.activeElement;
        openerRef.current = active instanceof HTMLElement && active !== document.body ? active : null;
        interactedOutsideRef.current = false;
        onOpenAutoFocus?.(event);
      },
      onInteractOutside: (event) => {
        onInteractOutside?.(event);
        if (!event.defaultPrevented) interactedOutsideRef.current = true;
      },
      onCloseAutoFocus: (event) => {
        onCloseAutoFocus?.(event);
        const content = event.currentTarget;
        if (content instanceof Node && content.isConnected && content.contains(document.activeElement)) {
          // Still open and focused: React StrictMode's extra unmount in development. Keep the
          // opener for the real close, and leave focus where it is.
          event.preventDefault();
          return;
        }
        const opener = openerRef.current;
        openerRef.current = null;
        if (event.defaultPrevented) return;
        const active = document.activeElement;
        if (active && active !== document.body) {
          // Focus has already moved on (e.g. into the map): keep it there.
          event.preventDefault();
          return;
        }
        if (interactedOutsideRef.current && !afterOutsideInteraction) return;
        if (opener && canFocus(opener)) {
          event.preventDefault();
          opener.focus();
        }
      },
    }),
    [onOpenAutoFocus, onCloseAutoFocus, onInteractOutside, afterOutsideInteraction],
  );
}
