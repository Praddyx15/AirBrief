"use client";

import * as React from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { ArrowDown, ArrowUp, ArrowUpDown } from "lucide-react";
import { cn } from "@/lib/utils";

export type SortDirection = "asc" | "desc";

export interface SortState {
  columnId: string;
  direction: SortDirection;
}

export type SortValue = string | number | boolean | null | undefined;

export interface DataTableColumn<T> {
  id: string;
  /** Header content. Use a string where possible (it becomes the sort button label). */
  header: React.ReactNode;
  cell: (row: T, index: number) => React.ReactNode;
  /** Providing this makes the column sortable. Nulls always sort last. */
  sortValue?: (row: T) => SortValue;
  /** Right-aligned monospace tabular cells (numbers, coordinates). */
  numeric?: boolean;
  align?: "left" | "center" | "right";
  /** CSS width of the column, e.g. 120 or "30%". */
  width?: number | string;
  className?: string;
  headerClassName?: string;
  /** Make this column the row header (<th scope="row">), typically the name column. */
  rowHeader?: boolean;
}

export interface DataTableProps<T> {
  data: readonly T[];
  columns: readonly DataTableColumn<T>[];
  getRowId: (row: T, index: number) => string | number;
  /** Accessible name of the table (rendered as <caption>, visually hidden unless captionVisible). */
  caption: string;
  captionVisible?: boolean;
  /** Row activation by click, Enter or Space. Makes rows focusable. */
  onRowClick?: (row: T, index: number) => void;
  /** Hover / keyboard focus on a row (null when leaving). Used for map highlight sync. */
  onRowHover?: (row: T | null) => void;
  /** Selected row ids; the table scrolls a newly selected row into view. */
  selectedRowIds?: readonly (string | number)[];
  /** Accessible label for a clickable row, e.g. (row) => `Select ${row.name}`. */
  getRowLabel?: (row: T) => string;
  getRowClassName?: (row: T, index: number) => string | undefined;
  /** Uncontrolled initial sort. */
  initialSort?: SortState | null;
  /** Controlled sort (pass with onSortChange). */
  sort?: SortState | null;
  onSortChange?: (sort: SortState | null) => void;
  /** Shown in place of rows when `data` is empty. */
  emptyState?: React.ReactNode;
  /** Height limit of the scroll area. Defaults to 420 px when virtualised, none otherwise. */
  maxHeight?: number | string;
  /** Virtualise when there are more rows than this (default 200). */
  virtualizeThreshold?: number;
  /** Estimated row height for virtualisation (default 34 px; 30 px when dense). */
  estimateRowHeight?: number;
  dense?: boolean;
  className?: string;
  /** Extra props for the scroll container (e.g. id for "Skip to results"). */
  containerProps?: React.HTMLAttributes<HTMLDivElement>;
}

function compareValues(a: SortValue, b: SortValue): number {
  const aNull = a === null || a === undefined || (typeof a === "number" && Number.isNaN(a));
  const bNull = b === null || b === undefined || (typeof b === "number" && Number.isNaN(b));
  if (aNull && bNull) return 0;
  if (aNull) return 1;
  if (bNull) return -1;
  if (typeof a === "number" && typeof b === "number") return a - b;
  if (typeof a === "boolean" && typeof b === "boolean") return Number(a) - Number(b);
  return String(a).localeCompare(String(b), undefined, { numeric: true, sensitivity: "base" });
}

const ALIGN: Record<NonNullable<DataTableColumn<unknown>["align"]>, string> = {
  left: "text-left",
  center: "text-center",
  right: "text-right",
};

/** Scroll only `container` (never its ancestors) so `row` shows below the sticky header. */
function scrollWithin(container: HTMLElement | null, row: HTMLElement): void {
  if (!container || container.scrollHeight <= container.clientHeight) return;
  const box = container.getBoundingClientRect();
  const rect = row.getBoundingClientRect();
  const header = container.querySelector("thead")?.getBoundingClientRect().height ?? 0;
  if (rect.top < box.top + header) container.scrollTop -= box.top + header - rect.top;
  else if (rect.bottom > box.bottom) container.scrollTop += rect.bottom - box.bottom;
}

/**
 * Accessible data table:
 * - sortable headers (buttons inside <th aria-sort>), Enter/Space toggles via the button;
 * - rows activate on click / Enter / Space and move focus with ArrowUp / ArrowDown;
 * - hover and focus both report the row (map sync);
 * - virtualised with @tanstack/react-virtual above `virtualizeThreshold` rows, keeping real
 *   <table> semantics (spacer rows, aria-rowcount / aria-rowindex).
 */
export function DataTable<T>({
  data,
  columns,
  getRowId,
  caption,
  captionVisible = false,
  onRowClick,
  onRowHover,
  selectedRowIds,
  getRowLabel,
  getRowClassName,
  initialSort = null,
  sort: controlledSort,
  onSortChange,
  emptyState,
  maxHeight,
  virtualizeThreshold = 200,
  estimateRowHeight,
  dense = false,
  className,
  containerProps,
}: DataTableProps<T>) {
  const [uncontrolledSort, setUncontrolledSort] = React.useState<SortState | null>(initialSort);
  const sort = controlledSort !== undefined ? controlledSort : uncontrolledSort;
  const scrollRef = React.useRef<HTMLDivElement>(null);

  const setSort = React.useCallback(
    (next: SortState | null) => {
      if (controlledSort === undefined) setUncontrolledSort(next);
      onSortChange?.(next);
    },
    [controlledSort, onSortChange],
  );

  const rows = React.useMemo(() => {
    const indexed = data.map((row, index) => ({ row, index }));
    if (!sort) return indexed;
    const column = columns.find((c) => c.id === sort.columnId);
    if (!column?.sortValue) return indexed;
    const get = column.sortValue;
    const dir = sort.direction === "asc" ? 1 : -1;
    return indexed
      .map((item) => ({ ...item, key: get(item.row) }))
      .sort((a, b) => {
        const aNull = a.key === null || a.key === undefined;
        const bNull = b.key === null || b.key === undefined;
        // Nulls last in both directions.
        if (aNull !== bNull) return aNull ? 1 : -1;
        const c = compareValues(a.key, b.key) * dir;
        return c !== 0 ? c : a.index - b.index;
      })
      .map(({ row, index }) => ({ row, index }));
  }, [data, columns, sort]);

  const virtualized = rows.length > virtualizeThreshold;
  const rowHeight = estimateRowHeight ?? (dense ? 30 : 34);

  const virtualizer = useVirtualizer({
    count: virtualized ? rows.length : 0,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => rowHeight,
    overscan: 12,
  });

  const selectedSet = React.useMemo(() => new Set(selectedRowIds ?? []), [selectedRowIds]);

  // Scroll a newly selected row into view (e.g. after a click on the map). The selection the
  // table opens with (top-ranked site, or one from the URL) only scrolls the table's own area:
  // scrolling the drawer or page would push the summary tiles above it out of view.
  const lastSelectedRef = React.useRef<string | number | null>(null);
  // Set once the rows of the current `data` were shown: later selections are the user's picks.
  const rowsShownRef = React.useRef(false);
  // New data (e.g. a re-run's result) starts over: its default selection is not scrolled to either.
  React.useEffect(() => {
    rowsShownRef.current = false;
  }, [data]);
  React.useEffect(() => {
    const initial = !rowsShownRef.current;
    if (rows.length > 0) rowsShownRef.current = true;
    const first = selectedRowIds?.[0];
    if (first === undefined || first === lastSelectedRef.current) {
      if (first === undefined) lastSelectedRef.current = null;
      return;
    }
    const pos = rows.findIndex(({ row, index }) => getRowId(row, index) === first);
    if (pos < 0) return; // not among the rows (yet): handled when they arrive
    lastSelectedRef.current = first;
    if (virtualized) {
      virtualizer.scrollToIndex(pos, { align: "auto" });
      return;
    }
    const el = scrollRef.current?.querySelector<HTMLElement>(`[data-row-pos="${pos}"]`);
    if (!el) return;
    if (initial) scrollWithin(scrollRef.current, el);
    else el.scrollIntoView({ block: "nearest" });
  }, [selectedRowIds, rows, getRowId, virtualized, virtualizer]);

  const focusRow = (pos: number) => {
    if (pos < 0 || pos >= rows.length) return;
    const tryFocus = () => {
      const el = scrollRef.current?.querySelector<HTMLElement>(`[data-row-pos="${pos}"]`);
      if (el) {
        el.focus();
        return true;
      }
      return false;
    };
    if (tryFocus()) return;
    if (virtualized) {
      virtualizer.scrollToIndex(pos, { align: "auto" });
      window.requestAnimationFrame(() => {
        if (!tryFocus()) window.requestAnimationFrame(() => void tryFocus());
      });
    }
  };

  const toggleSort = (column: DataTableColumn<T>) => {
    if (!column.sortValue) return;
    if (sort?.columnId !== column.id) setSort({ columnId: column.id, direction: "asc" });
    else setSort({ columnId: column.id, direction: sort.direction === "asc" ? "desc" : "asc" });
  };

  const interactive = Boolean(onRowClick);
  const cellPad = dense ? "px-2 py-1" : "px-2 py-1.5";
  const resolvedMaxHeight = maxHeight ?? (virtualized ? 420 : undefined);

  const renderRow = (row: T, index: number, pos: number, measureRef?: (el: Element | null) => void) => {
    const id = getRowId(row, index);
    const selected = selectedSet.has(id);
    return (
      <tr
        key={id}
        ref={measureRef}
        data-index={virtualized ? pos : undefined}
        data-row-pos={pos}
        data-state={selected ? "selected" : undefined}
        aria-selected={interactive ? selected : undefined}
        aria-rowindex={virtualized ? pos + 2 : undefined}
        aria-label={interactive && getRowLabel ? getRowLabel(row) : undefined}
        tabIndex={interactive ? 0 : undefined}
        className={cn(
          // scroll-mt: a row scrolled into view is not hidden under the sticky header.
          "scroll-mt-9 border-b border-border transition-colors duration-150",
          "hover:bg-elevated/70 focus-visible:bg-elevated/70 focus-visible:outline-offset-[-2px]",
          selected && "bg-accent/15 hover:bg-accent/20",
          // Clickable rows are at least 44 px tall on touch screens (a row's height is its minimum).
          interactive && "cursor-pointer pointer-coarse:h-11",
          getRowClassName?.(row, index),
        )}
        onClick={interactive ? () => onRowClick?.(row, index) : undefined}
        onKeyDown={
          interactive
            ? (event) => {
                if (event.target !== event.currentTarget) return;
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  onRowClick?.(row, index);
                } else if (event.key === "ArrowDown") {
                  event.preventDefault();
                  focusRow(pos + 1);
                } else if (event.key === "ArrowUp") {
                  event.preventDefault();
                  focusRow(pos - 1);
                } else if (event.key === "Home") {
                  event.preventDefault();
                  focusRow(0);
                } else if (event.key === "End") {
                  event.preventDefault();
                  focusRow(rows.length - 1);
                }
              }
            : undefined
        }
        onMouseEnter={onRowHover ? () => onRowHover(row) : undefined}
        onFocus={onRowHover ? () => onRowHover(row) : undefined}
      >
        {columns.map((column) => {
          const content = column.cell(row, index);
          const cls = cn(
            cellPad,
            "align-middle",
            column.numeric ? "num text-right" : ALIGN[column.align ?? "left"],
            column.className,
          );
          return column.rowHeader ? (
            <th key={column.id} scope="row" className={cn(cls, "font-normal")}>
              {content}
            </th>
          ) : (
            <td key={column.id} className={cls}>
              {content}
            </td>
          );
        })}
      </tr>
    );
  };

  const virtualItems = virtualized ? virtualizer.getVirtualItems() : [];
  const totalSize = virtualized ? virtualizer.getTotalSize() : 0;
  const firstItem = virtualItems[0];
  const lastItem = virtualItems[virtualItems.length - 1];
  const paddingTop = firstItem ? firstItem.start : 0;
  const paddingBottom = lastItem ? totalSize - lastItem.end : 0;

  return (
    <div
      {...containerProps}
      ref={scrollRef}
      className={cn(
        "relative w-full overflow-auto rounded border border-border scrollbar-thin",
        className,
        containerProps?.className,
      )}
      style={{ maxHeight: resolvedMaxHeight, ...containerProps?.style }}
      onMouseLeave={onRowHover ? () => onRowHover(null) : undefined}
      onBlur={
        onRowHover
          ? (event) => {
              if (!event.currentTarget.contains(event.relatedTarget as Node | null)) onRowHover(null);
            }
          : undefined
      }
    >
      <table
        className={cn("w-full border-collapse text-sm tabular-nums", dense && "text-xs")}
        aria-rowcount={virtualized ? rows.length + 1 : undefined}
      >
        <caption className={cn(captionVisible ? "px-2 py-2 text-left text-xs text-fg-muted" : "sr-only")}>
          {caption}
        </caption>
        <colgroup>
          {columns.map((column) => (
            <col key={column.id} style={column.width !== undefined ? { width: column.width } : undefined} />
          ))}
        </colgroup>
        <thead className="sticky top-0 z-[1] bg-surface shadow-[inset_0_-1px_0_rgb(var(--border-rgb))]">
          <tr aria-rowindex={virtualized ? 1 : undefined}>
            {columns.map((column) => {
              const sortable = Boolean(column.sortValue);
              const active = sort?.columnId === column.id;
              const ariaSort = sortable
                ? active
                  ? sort?.direction === "asc"
                    ? "ascending"
                    : "descending"
                  : "none"
                : undefined;
              const align = column.numeric ? "text-right" : ALIGN[column.align ?? "left"];
              return (
                <th
                  key={column.id}
                  scope="col"
                  aria-sort={ariaSort}
                  className={cn(
                    "h-9 whitespace-nowrap px-2 align-middle text-xs font-medium text-fg-muted",
                    align,
                    column.headerClassName,
                  )}
                >
                  {sortable ? (
                    <button
                      type="button"
                      onClick={() => toggleSort(column)}
                      className={cn(
                        "-mx-1 inline-flex cursor-pointer items-center gap-1 rounded px-1 py-0.5 transition-colors hover:text-fg pointer-coarse:min-h-11",
                        active && "text-fg",
                        column.numeric && "flex-row-reverse",
                      )}
                    >
                      <span>{column.header}</span>
                      {active ? (
                        sort?.direction === "asc" ? (
                          <ArrowUp className="size-3.5" aria-hidden />
                        ) : (
                          <ArrowDown className="size-3.5" aria-hidden />
                        )
                      ) : (
                        <ArrowUpDown className="size-3.5 opacity-50" aria-hidden />
                      )}
                    </button>
                  ) : (
                    column.header
                  )}
                </th>
              );
            })}
          </tr>
        </thead>
        <tbody>
          {rows.length === 0 ? (
            <tr>
              <td colSpan={columns.length} className="p-0">
                {emptyState ?? <p className="px-3 py-6 text-center text-sm text-fg-muted">No rows to show.</p>}
              </td>
            </tr>
          ) : virtualized ? (
            <>
              {paddingTop > 0 ? (
                <tr aria-hidden="true">
                  <td colSpan={columns.length} style={{ height: paddingTop, padding: 0, border: 0 }} />
                </tr>
              ) : null}
              {virtualItems.map((item) => {
                const entry = rows[item.index];
                if (!entry) return null;
                return renderRow(entry.row, entry.index, item.index, virtualizer.measureElement);
              })}
              {paddingBottom > 0 ? (
                <tr aria-hidden="true">
                  <td colSpan={columns.length} style={{ height: paddingBottom, padding: 0, border: 0 }} />
                </tr>
              ) : null}
            </>
          ) : (
            rows.map((entry, pos) => renderRow(entry.row, entry.index, pos))
          )}
        </tbody>
      </table>
    </div>
  );
}
