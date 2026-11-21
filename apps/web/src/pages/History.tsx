import * as React from 'react'
import { Link } from 'react-router-dom'
import { toast } from 'sonner'
import { CheckCircle2, CircleAlert, Download, FileSpreadsheet, GitBranch, RotateCcw, ShieldCheck } from 'lucide-react'
import { EmptyState, ErrorState, LoadingBlock, Mono, PageHeader, SourceBadge } from '@/components/common'
import { Badge, Card, CardContent, CardDescription, CardHeader, CardTitle, Label, Textarea, Tooltip } from '@/components/ui/primitives'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { useData, useResource } from '@/lib/api'
import type { VersionRecord } from '@/lib/types'
import { cn, fmtBytes, fmtDate, fmtNum } from '@/lib/utils'

const COUNT_LABELS: [string, string][] = [
  ['added_facilities', 'Facilities added'],
  ['updated_facilities', 'Rows updated'],
  ['new_observations', 'New observations'],
  ['filled_missing_values', 'Blank values filled'],
  ['updated_measurements', 'Reviewed changes'],
  ['unchanged_skipped', 'Unchanged skipped'],
  ['duplicates_skipped', 'Duplicates skipped'],
  ['conflicts', 'Review items'],
  ['deferred', 'Deferred'],
  ['rejected', 'Rejected'],
]

export default function History() {
  const api = useData()
  const history = useResource(() => api.history(), [api])
  const [rollbackTo, setRollbackTo] = React.useState<VersionRecord | null>(null)
  const [reason, setReason] = React.useState('')

  async function doRollback() {
    if (!rollbackTo) return
    try {
      await api.rollback(rollbackTo.version, reason)
      toast.success(`Version v${String(rollbackTo.version).padStart(3, '0')} is current again`)
      setRollbackTo(null)
      setReason('')
      history.reload()
    } catch (e) {
      toast.error((e as Error).message)
    }
  }

  if (history.error) return <ErrorState error={history.error} onRetry={history.reload} />
  if (!history.data) return <LoadingBlock rows={6} />
  const versions = [...history.data.versions].reverse()
  const current = history.data.current_version

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Warehouse"
        title="Sync history"
        description="Every published version is immutable, verified and traceable to the plan, source files (with SHA-256) and decisions that produced it. Rollback only moves the current pointer — nothing is deleted."
      />
      {versions.length === 0 ? (
        <EmptyState icon={GitBranch} title="No versions yet" description="Register the baseline warehouse to start the version history." />
      ) : (
        <ol className="relative space-y-4 border-l-2 border-border pl-6">
          {versions.map((v) => {
            const isCurrent = v.version === current
            const url = api.downloadVersionUrl(v.version)
            return (
              <li key={v.version} className="relative">
                <span
                  className={cn(
                    'absolute -left-[33px] top-5 flex h-4 w-4 items-center justify-center rounded-full border-2 border-background',
                    isCurrent ? 'bg-primary ring-4 ring-primary/20' : 'bg-muted-foreground/40',
                  )}
                  aria-hidden
                />
                <Card className={cn(isCurrent && 'ring-1 ring-primary/50')}>
                  <CardHeader className="gap-3 md:flex-row md:items-start md:justify-between">
                    <div className="space-y-1.5">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="text-lg font-semibold">v{String(v.version).padStart(3, '0')}</span>
                        <Badge tone={v.kind === 'BASELINE' ? 'violet' : 'info'}>{v.kind === 'BASELINE' ? 'Baseline' : 'Synchronized'}</Badge>
                        {isCurrent && (
                          <Badge tone="success" dot>
                            current
                          </Badge>
                        )}
                        {v.validation?.ok ? (
                          <Badge tone="success">
                            <ShieldCheck className="h-3 w-3" /> verified
                          </Badge>
                        ) : (
                          <Badge tone="danger">
                            <CircleAlert className="h-3 w-3" /> verification warnings
                          </Badge>
                        )}
                      </div>
                      <CardDescription>
                        {fmtDate(v.created_at)} · {fmtNum(v.rows)} rows × {v.columns} columns · {fmtBytes(v.size)}
                        {v.previous_version !== null && ` · from v${String(v.previous_version).padStart(3, '0')}`}
                        {v.applied_by && ` · by ${v.applied_by}`}
                      </CardDescription>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      {url ? (
                        <Button variant="outline" size="sm" asChild>
                          <a href={url} download>
                            <Download /> Download workbook
                          </a>
                        </Button>
                      ) : (
                        <Tooltip content="Workbook downloads are available in Local Production Mode only">
                          <span>
                            <Button variant="outline" size="sm" disabled>
                              <Download /> Download workbook
                            </Button>
                          </span>
                        </Tooltip>
                      )}
                      {!isCurrent && (
                        <Button variant="outline" size="sm" onClick={() => setRollbackTo(v)} disabled={api.mode === 'demo'}>
                          <RotateCcw /> Make current
                        </Button>
                      )}
                      {v.plan_id && (
                        <Button variant="ghost" size="sm" asChild>
                          <Link to={`/sync/preview/${v.plan_id}`}>Plan & audit</Link>
                        </Button>
                      )}
                    </div>
                  </CardHeader>
                  <CardContent className="space-y-4">
                    <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                      <FileSpreadsheet className="h-3.5 w-3.5" />
                      <Mono>{v.file}</Mono>
                      <span className="font-mono">SHA-256 {v.sha256.slice(0, 20)}…</span>
                      <span>· engine {v.engine_version} · schema {v.schema_version}</span>
                    </div>
                    {v.kind === 'SYNC' && (
                      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-5">
                        {COUNT_LABELS.filter(([k]) => v.counts[k] !== undefined).map(([k, label]) => (
                          <div key={k} className="rounded-lg border bg-muted/30 px-3 py-2">
                            <div className="text-[11px] uppercase tracking-wide text-muted-foreground">{label}</div>
                            <div className="text-base font-semibold tabular">{fmtNum(v.counts[k])}</div>
                          </div>
                        ))}
                      </div>
                    )}
                    {v.sources.length > 0 && (
                      <div className="space-y-1.5">
                        <div className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Source files</div>
                        {v.sources.map((s) => (
                          <div key={s.sha256 + s.file_name} className="flex flex-wrap items-center gap-2 text-sm">
                            <SourceBadge source={s.source} />
                            <span className="font-medium">{s.file_name}</span>
                            <span className="text-xs text-muted-foreground">
                              {fmtNum(s.records)} records · SHA-256 {s.sha256.slice(0, 12)}…
                            </span>
                          </div>
                        ))}
                      </div>
                    )}
                    {v.new_columns && v.new_columns.length > 0 && (
                      <div className="flex flex-wrap items-center gap-1.5 text-xs">
                        <span className="text-muted-foreground">New columns:</span>
                        {v.new_columns.map((c) => (
                          <Mono key={c}>{c}</Mono>
                        ))}
                      </div>
                    )}
                    <details>
                      <summary className="cursor-pointer text-xs font-medium text-primary">Integrity checks ({v.validation?.checks?.length ?? 0})</summary>
                      <ul className="mt-2 grid grid-cols-1 gap-1 text-xs md:grid-cols-2">
                        {(v.validation?.checks ?? []).map((c) => (
                          <li key={c.name} className="flex items-start gap-1.5">
                            {c.status === 'PASS' ? <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-success" /> : <CircleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0 text-warning" />}
                            <span>
                              <span className="font-mono">{c.name}</span> <span className="text-muted-foreground">{c.detail}</span>
                            </span>
                          </li>
                        ))}
                      </ul>
                      {v.validation?.preservation && (
                        <p className="mt-2 text-xs text-muted-foreground">
                          Preservation proof: {fmtNum(v.validation.preservation.compared_rows)} base rows compared,{' '}
                          {fmtNum(v.validation.preservation.patched_rows_checked)} changed rows checked cell by cell,{' '}
                          {fmtNum(v.validation.preservation.inserted_rows)} inserted rows.
                        </p>
                      )}
                    </details>
                  </CardContent>
                </Card>
              </li>
            )
          })}
        </ol>
      )}

      <Card>
        <CardHeader>
          <CardTitle>Event log</CardTitle>
          <CardDescription>change_history.jsonl — append-only</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="max-h-[420px] overflow-y-auto rounded-lg border">
            <table className="w-full text-sm">
              <thead className="sticky top-0 bg-muted text-xs uppercase tracking-wide text-muted-foreground">
                <tr>
                  <th className="px-3 py-2 text-left">When</th>
                  <th className="px-3 py-2 text-left">Event</th>
                  <th className="px-3 py-2 text-left">By</th>
                  <th className="px-3 py-2 text-left">Details</th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {[...history.data.events].reverse().map((e, i) => {
                  const { at, event, by, ...rest } = e
                  return (
                    <tr key={i}>
                      <td className="whitespace-nowrap px-3 py-1.5 text-xs text-muted-foreground">{fmtDate(at)}</td>
                      <td className="px-3 py-1.5 font-medium">{event}</td>
                      <td className="px-3 py-1.5 text-xs">{by ? String(by) : '—'}</td>
                      <td className="max-w-[520px] truncate px-3 py-1.5 font-mono text-[11px] text-muted-foreground" title={JSON.stringify(rest)}>
                        {JSON.stringify(rest)}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>

      <Dialog open={!!rollbackTo} onOpenChange={(o) => !o && setRollbackTo(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Make v{String(rollbackTo?.version ?? 0).padStart(3, '0')} current?</DialogTitle>
            <DialogDescription>
              Rollback only changes which verified version is current. Later versions stay on disk and can be restored again. Future syncs build on the
              restored version.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-1.5">
            <Label htmlFor="rb-reason">Reason (required)</Label>
            <Textarea id="rb-reason" value={reason} onChange={(e) => setReason(e.target.value)} />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setRollbackTo(null)}>
              Cancel
            </Button>
            <Button onClick={doRollback} disabled={!reason.trim()}>
              Roll back
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
