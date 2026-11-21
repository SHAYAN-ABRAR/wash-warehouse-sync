import * as React from 'react'
import { flexRender, getCoreRowModel, useReactTable, type ColumnDef } from '@tanstack/react-table'
import { useVirtualizer } from '@tanstack/react-virtual'
import { cn } from '@/lib/utils'

/**
 * Virtualized table: only the visible rows are mounted, so pages with thousands of
 * rows stay fast. Pagination for large datasets happens server-side (local mode).
 */
export function DataTable<T>({
  data,
  columns,
  onRowClick,
  height = 560,
  rowHeight = 44,
  empty,
  getRowId,
  ariaLabel,
  activeRowId,
}: {
  data: T[]
  columns: ColumnDef<T, any>[]
  onRowClick?: (row: T) => void
  height?: number | string
  rowHeight?: number
  empty?: React.ReactNode
  getRowId?: (row: T, index: number) => string
  ariaLabel: string
  activeRowId?: string | null
}) {
  const table = useReactTable({ data, columns, getCoreRowModel: getCoreRowModel(), getRowId })
  const parentRef = React.useRef<HTMLDivElement>(null)
  const rows = table.getRowModel().rows
  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => rowHeight,
    overscan: 12,
  })
  const items = virtualizer.getVirtualItems()
  const padTop = items.length ? items[0].start : 0
  const padBottom = items.length ? virtualizer.getTotalSize() - items[items.length - 1].end : 0
  const colCount = table.getAllLeafColumns().length

  return (
    <div
      ref={parentRef}
      className="relative overflow-auto rounded-lg border scrollbar-thin"
      style={{ maxHeight: height }}
      role="region"
      aria-label={ariaLabel}
      tabIndex={0}
    >
      <table className="w-full caption-bottom border-separate border-spacing-0 text-sm">
        <thead className="sticky top-0 z-10 bg-muted/95 backdrop-blur">
          {table.getHeaderGroups().map((hg) => (
            <tr key={hg.id}>
              {hg.headers.map((h) => (
                <th
                  key={h.id}
                  scope="col"
                  className="whitespace-nowrap border-b px-3 py-2.5 text-left text-xs font-semibold uppercase tracking-wide text-muted-foreground"
                  style={{ width: h.getSize() !== 150 ? h.getSize() : undefined }}
                >
                  {h.isPlaceholder ? null : flexRender(h.column.columnDef.header, h.getContext())}
                </th>
              ))}
            </tr>
          ))}
        </thead>
        <tbody>
          {rows.length === 0 && (
            <tr>
              <td colSpan={colCount} className="px-3 py-10 text-center text-muted-foreground">
                {empty ?? 'No rows'}
              </td>
            </tr>
          )}
          {padTop > 0 && (
            <tr aria-hidden>
              <td colSpan={colCount} style={{ height: padTop }} />
            </tr>
          )}
          {items.map((vi) => {
            const row = rows[vi.index]
            const active = activeRowId != null && row.id === activeRowId
            return (
              <tr
                key={row.id}
                data-index={vi.index}
                onClick={onRowClick ? () => onRowClick(row.original) : undefined}
                onKeyDown={
                  onRowClick
                    ? (e) => {
                        if (e.key === 'Enter' || e.key === ' ') {
                          e.preventDefault()
                          onRowClick(row.original)
                        }
                      }
                    : undefined
                }
                tabIndex={onRowClick ? 0 : undefined}
                className={cn(
                  'group transition-colors',
                  onRowClick && 'cursor-pointer hover:bg-muted/60 focus-visible:bg-muted/60',
                  active && 'bg-accent/60',
                )}
                style={{ height: rowHeight }}
              >
                {row.getVisibleCells().map((cell) => (
                  <td key={cell.id} className="max-w-[340px] truncate border-b px-3 py-2 align-middle">
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </td>
                ))}
              </tr>
            )
          })}
          {padBottom > 0 && (
            <tr aria-hidden>
              <td colSpan={colCount} style={{ height: padBottom }} />
            </tr>
          )}
        </tbody>
      </table>
    </div>
  )
}

export function Pager({
  offset,
  limit,
  total,
  onChange,
}: {
  offset: number
  limit: number
  total: number
  onChange: (offset: number) => void
}) {
  const page = Math.floor(offset / limit) + 1
  const pages = Math.max(1, Math.ceil(total / limit))
  return (
    <div className="flex items-center justify-between gap-3 pt-3 text-sm text-muted-foreground">
      <span className="tabular">
        {total === 0 ? 'No results' : `${(offset + 1).toLocaleString()}–${Math.min(offset + limit, total).toLocaleString()} of ${total.toLocaleString()}`}
      </span>
      <div className="flex items-center gap-1">
        <button
          className="rounded-md border px-2.5 py-1 hover:bg-muted disabled:opacity-40"
          onClick={() => onChange(Math.max(0, offset - limit))}
          disabled={page <= 1}
        >
          Previous
        </button>
        <span className="px-2 tabular">
          {page} / {pages}
        </span>
        <button
          className="rounded-md border px-2.5 py-1 hover:bg-muted disabled:opacity-40"
          onClick={() => onChange(offset + limit)}
          disabled={page >= pages}
        >
          Next
        </button>
      </div>
    </div>
  )
}
