"use client";

import * as React from "react";
import { cn } from "@/lib/utils";

export interface TableProps extends React.TableHTMLAttributes<HTMLTableElement> {
  /**
   * Accessible name of the scroll container, used while the table is wider than its container.
   * Defaults to the caption text.
   */
  scrollLabel?: string;
  /** Classes for the scroll container (e.g. responsive visibility). */
  containerClassName?: string;
}

/**
 * Plain semantic table primitives. For sorting/virtualisation use <DataTable>.
 *
 * The table scrolls sideways inside its container when it is too wide. While it overflows, the
 * container becomes a focusable, labelled region (WCAG 2.1.1) so keyboard users can scroll it
 * with the arrow keys; the global focus ring marks it.
 */
export const Table = React.forwardRef<HTMLTableElement, TableProps>(
  ({ className, scrollLabel, containerClassName, ...props }, ref) => {
    const containerRef = React.useRef<HTMLDivElement>(null);
    const tableRef = React.useRef<HTMLTableElement | null>(null);
    const [regionLabel, setRegionLabel] = React.useState<string | null>(null);

    const setTableRef = React.useCallback(
      (node: HTMLTableElement | null) => {
        tableRef.current = node;
        if (typeof ref === "function") ref(node);
        else if (ref) ref.current = node;
      },
      [ref],
    );

    React.useEffect(() => {
      const container = containerRef.current;
      if (!container) return;
      const update = () => {
        if (container.scrollWidth <= container.clientWidth + 1) {
          setRegionLabel(null);
          return;
        }
        const caption = tableRef.current?.caption?.textContent?.trim();
        setRegionLabel(scrollLabel ?? (caption || "Table"));
      };
      update();
      if (typeof ResizeObserver === "undefined") return;
      const observer = new ResizeObserver(update);
      observer.observe(container);
      if (tableRef.current) observer.observe(tableRef.current);
      return () => observer.disconnect();
    }, [scrollLabel]);

    const scrollable = regionLabel !== null;
    return (
      <div
        ref={containerRef}
        className={cn("relative w-full overflow-x-auto rounded-sm", containerClassName)}
        tabIndex={scrollable ? 0 : undefined}
        role={scrollable ? "region" : undefined}
        aria-label={scrollable ? `${regionLabel} (scrolls sideways)` : undefined}
      >
        <table ref={setTableRef} className={cn("w-full caption-top border-collapse text-sm tabular-nums", className)} {...props} />
      </div>
    );
  },
);
Table.displayName = "Table";

export const TableHeader = React.forwardRef<HTMLTableSectionElement, React.HTMLAttributes<HTMLTableSectionElement>>(
  ({ className, ...props }, ref) => <thead ref={ref} className={cn("[&_tr]:border-b", className)} {...props} />,
);
TableHeader.displayName = "TableHeader";

export const TableBody = React.forwardRef<HTMLTableSectionElement, React.HTMLAttributes<HTMLTableSectionElement>>(
  ({ className, ...props }, ref) => <tbody ref={ref} className={cn("[&_tr:last-child]:border-0", className)} {...props} />,
);
TableBody.displayName = "TableBody";

export const TableFooter = React.forwardRef<HTMLTableSectionElement, React.HTMLAttributes<HTMLTableSectionElement>>(
  ({ className, ...props }, ref) => (
    <tfoot ref={ref} className={cn("border-t bg-elevated font-medium [&>tr]:last:border-b-0", className)} {...props} />
  ),
);
TableFooter.displayName = "TableFooter";

export const TableRow = React.forwardRef<HTMLTableRowElement, React.HTMLAttributes<HTMLTableRowElement>>(
  ({ className, ...props }, ref) => (
    <tr
      ref={ref}
      className={cn(
        "border-b border-border transition-colors duration-150 hover:bg-elevated/60 data-[state=selected]:bg-accent/15",
        className,
      )}
      {...props}
    />
  ),
);
TableRow.displayName = "TableRow";

export const TableHead = React.forwardRef<HTMLTableCellElement, React.ThHTMLAttributes<HTMLTableCellElement>>(
  ({ className, scope = "col", ...props }, ref) => (
    <th
      ref={ref}
      scope={scope}
      className={cn(
        "h-9 whitespace-nowrap px-2 text-left align-middle text-xs font-medium text-fg-muted [&:has([role=checkbox])]:pr-0",
        className,
      )}
      {...props}
    />
  ),
);
TableHead.displayName = "TableHead";

export const TableCell = React.forwardRef<HTMLTableCellElement, React.TdHTMLAttributes<HTMLTableCellElement>>(
  ({ className, ...props }, ref) => (
    <td ref={ref} className={cn("px-2 py-1.5 align-middle", className)} {...props} />
  ),
);
TableCell.displayName = "TableCell";

export const TableCaption = React.forwardRef<HTMLTableCaptionElement, React.HTMLAttributes<HTMLTableCaptionElement>>(
  ({ className, ...props }, ref) => (
    <caption ref={ref} className={cn("mb-2 text-left text-xs text-fg-muted", className)} {...props} />
  ),
);
TableCaption.displayName = "TableCaption";
