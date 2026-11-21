import * as React from 'react'
import type { ColumnDef } from '@tanstack/react-table'
import { Columns3, Filter, Search, X } from 'lucide-react'
import { ErrorState, PageHeader } from '@/components/common'
import { DataTable, Pager } from '@/components/DataTable'
import { Badge, Card, CardContent, Checkbox, Input, NativeSelect } from '@/components/ui/primitives'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, Sheet } from '@/components/ui/dialog'
import { DEFAULT_COLUMNS, useData, useResource } from '@/lib/api'
import { SOURCE_META } from '@/lib/meta'
import { fmtNum, fmtValue } from '@/lib/utils'

const NAMESPACES: [string, string][] = [
  ['core', 'Core facility'],
  ['gpani_', 'GPANI'],
  ['wpms_', 'WPMS'],
  ['aqua_', 'AquaWatch'],
  ['borelog_', 'Bore Log'],
]
const nsOf = (c: string) => NAMESPACES.find(([p]) => p !== 'core' && c.startsWith(p))?.[0] ?? 'core'
const PAGE = 200

function useDebounced<T>(value: T, ms = 300): T {
  const [v, setV] = React.useState(value)
  React.useEffect(() => {
    const t = setTimeout(() => setV(value), ms)
    return () => clearTimeout(t)
  }, [value, ms])
  return v
}

export default function Explorer() {
  const api = useData()
  const schema = useResource(() => api.schema(), [api])
  const facets = useResource(() => api.facets(), [api])
  const [search, setSearch] = React.useState('')
  const q = useDebounced(search)
  const [filters, setFilters] = React.useState({ division: '', district: '', source: '', match_status: '', gps: '', flag: '' })
  const flag = useDebounced(filters.flag)
  const district = useDebounced(filters.district)
  const [offset, setOffset] = React.useState(0)
  const [columns, setColumns] = React.useState<string[]>(() => {
    try {
      return JSON.parse(localStorage.getItem('wash-explorer-columns') ?? 'null') ?? DEFAULT_COLUMNS
    } catch {
      return DEFAULT_COLUMNS
    }
  })
  const [pickOpen, setPickOpen] = React.useState(false)
  const [detailRow, setDetailRow] = React.useState<number | null>(null)
  React.useEffect(() => setOffset(0), [q, filters.division, district, filters.source, filters.match_status, filters.gps, flag])
  const page = useResource(
    () => api.rows({ q, ...filters, district, flag, columns, offset, limit: PAGE }),
    [api, q, filters.division, district, filters.source, filters.match_status, filters.gps, flag, columns.join(','), offset],
  )
  const detail = useResource(() => (detailRow ? api.row(detailRow) : Promise.resolve(null)), [api, detailRow])
  const activeFilters = Object.entries(filters).filter(([, v]) => v)

  const tableCols = React.useMemo<ColumnDef<Record<string, unknown>>[]>(
    () => [
      { id: 'row', header: 'Row', size: 70, cell: ({ row }) => <span className="tabular text-xs text-muted-foreground">{String(row.original.row)}</span> },
      ...columns.map(
        (c): ColumnDef<Record<string, unknown>> => ({
          id: c,
          header: c,
          cell: ({ row }) => {
            const v = row.original[c]
            return c === 'candidate_id' ? <span className="font-mono text-xs">{fmtValue(v)}</span> : <span title={fmtValue(v)}>{fmtValue(v)}</span>
          },
        }),
      ),
    ],
    [columns],
  )

  function saveColumns(next: string[]) {
    setColumns(next)
    try {
      localStorage.setItem('wash-explorer-columns', JSON.stringify(next))
    } catch {
      /* ignore */
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Warehouse"
        title="Warehouse Explorer"
        description={
          schema.data
            ? `Search and filter ${fmtNum(schema.data.rows)} facility rows × ${schema.data.headers.length} columns of the current version. Results are paginated server-side — the browser never loads the whole warehouse.`
            : 'Search and filter the current warehouse version.'
        }
        actions={
          <Button variant="outline" onClick={() => setPickOpen(true)}>
            <Columns3 /> Columns ({columns.length})
          </Button>
        }
      />
      <Card>
        <CardContent className="space-y-3 p-4">
          <div className="flex flex-col gap-3 lg:flex-row">
            <div className="relative flex-1">
              <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                className="pl-9"
                placeholder="Search facility ID, source ID, geocode or location name…"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                aria-label="Search the warehouse"
              />
            </div>
            <div className="flex flex-wrap gap-2">
              <NativeSelect value={filters.division} onChange={(e) => setFilters((f) => ({ ...f, division: e.target.value }))} aria-label="Division">
                <option value="">All divisions</option>
                {(facets.data?.division ?? []).map(([d, n]) => (
                  <option key={d} value={d}>
                    {d} ({fmtNum(n)})
                  </option>
                ))}
              </NativeSelect>
              <Input
                className="w-40"
                placeholder="District…"
                value={filters.district}
                onChange={(e) => setFilters((f) => ({ ...f, district: e.target.value }))}
                aria-label="District"
              />
              <NativeSelect value={filters.source} onChange={(e) => setFilters((f) => ({ ...f, source: e.target.value }))} aria-label="Source">
                <option value="">All sources</option>
                {Object.entries(SOURCE_META).map(([k, v]) => (
                  <option key={k} value={k}>
                    {v.label}
                  </option>
                ))}
              </NativeSelect>
              <NativeSelect value={filters.match_status} onChange={(e) => setFilters((f) => ({ ...f, match_status: e.target.value }))} aria-label="Match status" className="max-w-[220px]">
                <option value="">Any match status</option>
                {(facets.data?.match_status ?? []).map(([m, n]) => (
                  <option key={m} value={m}>
                    {m} ({fmtNum(n)})
                  </option>
                ))}
              </NativeSelect>
              <NativeSelect value={filters.gps} onChange={(e) => setFilters((f) => ({ ...f, gps: e.target.value }))} aria-label="GPS validity">
                <option value="">Any GPS</option>
                <option value="valid">Valid GPS</option>
                <option value="invalid">Missing / invalid GPS</option>
              </NativeSelect>
              <Input
                className="w-48"
                placeholder="Quality flag contains…"
                value={filters.flag}
                onChange={(e) => setFilters((f) => ({ ...f, flag: e.target.value.toUpperCase() }))}
                aria-label="Quality flag"
              />
            </div>
          </div>
          {activeFilters.length > 0 && (
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <Filter className="h-3.5 w-3.5 text-muted-foreground" />
              {activeFilters.map(([k, v]) => (
                <button key={k} onClick={() => setFilters((f) => ({ ...f, [k]: '' }))} className="inline-flex items-center gap-1">
                  <Badge tone="info">
                    {k.replace('_', ' ')}: {v} <X className="h-3 w-3" />
                  </Badge>
                </button>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      {page.error ? (
        <ErrorState error={page.error} onRetry={page.reload} />
      ) : (
        <div>
          <DataTable
            ariaLabel="Warehouse rows"
            data={(page.data?.items ?? []) as Record<string, unknown>[]}
            columns={tableCols}
            onRowClick={(r) => setDetailRow(Number(r.row))}
            getRowId={(r) => String(r.row)}
            activeRowId={detailRow ? String(detailRow) : null}
            height={620}
            empty={page.loading ? 'Loading…' : 'No rows match the search and filters.'}
          />
          {page.data && <Pager offset={offset} limit={PAGE} total={page.data.total} onChange={setOffset} />}
        </div>
      )}

      <Dialog open={pickOpen} onOpenChange={setPickOpen}>
        <DialogContent wide>
          <DialogHeader>
            <DialogTitle>Choose columns</DialogTitle>
            <DialogDescription>Displayed columns are remembered in this browser.</DialogDescription>
          </DialogHeader>
          <div className="max-h-[60vh] space-y-5 overflow-y-auto pr-1">
            {NAMESPACES.map(([ns, label]) => {
              const cols = (schema.data?.headers ?? []).filter((c) => nsOf(c) === ns)
              if (!cols.length) return null
              return (
                <div key={ns}>
                  <div className="mb-2 flex items-center justify-between">
                    <h3 className="text-sm font-semibold">{label}</h3>
                    <span className="text-xs text-muted-foreground">{cols.length} columns</span>
                  </div>
                  <div className="grid grid-cols-1 gap-1.5 sm:grid-cols-2 lg:grid-cols-3">
                    {cols.map((c) => (
                      <label key={c} className="flex items-center gap-2 rounded-md px-2 py-1 text-xs hover:bg-muted">
                        <Checkbox
                          checked={columns.includes(c)}
                          onCheckedChange={(v) => saveColumns(v ? [...columns, c] : columns.filter((x) => x !== c))}
                          aria-label={c}
                        />
                        <span className="truncate font-mono">{c}</span>
                      </label>
                    ))}
                  </div>
                </div>
              )
            })}
          </div>
          <div className="flex justify-between">
            <Button variant="ghost" onClick={() => saveColumns(DEFAULT_COLUMNS)}>
              Reset to defaults
            </Button>
            <Button onClick={() => setPickOpen(false)}>Done</Button>
          </div>
        </DialogContent>
      </Dialog>

      <Sheet open={detailRow !== null} onOpenChange={(o) => !o && setDetailRow(null)} title={`Row ${detailRow ?? ''}`} description={String(detail.data?.values.candidate_id ?? '')}>
        {detail.data && (
          <div className="space-y-6">
            {NAMESPACES.map(([ns, label]) => {
              const entries = Object.entries(detail.data!.values).filter(([k]) => nsOf(k) === ns)
              if (!entries.length) return null
              return (
                <section key={ns}>
                  <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">{label}</h3>
                  <dl className="divide-y rounded-lg border">
                    {entries.map(([k, v]) => (
                      <div key={k} className="grid grid-cols-[minmax(0,2fr)_minmax(0,3fr)] gap-3 px-3 py-1.5 text-sm">
                        <dt className="truncate font-mono text-xs text-muted-foreground" title={k}>
                          {k}
                        </dt>
                        <dd className="break-words">{fmtValue(v)}</dd>
                      </div>
                    ))}
                  </dl>
                </section>
              )
            })}
          </div>
        )}
      </Sheet>
    </div>
  )
}
