import * as React from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import type { ColumnDef } from '@tanstack/react-table'
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip as RTooltip, XAxis, YAxis } from 'recharts'
import { toast } from 'sonner'
import { CheckCircle2, Columns3, Download, FileSpreadsheet, GitCompareArrows, Play, Scale, ShieldCheck } from 'lucide-react'
import { ClassBadge, EmptyState, ErrorState, LoadingBlock, Mono, PageHeader, SourceBadge } from '@/components/common'
import { DataTable, Pager } from '@/components/DataTable'
import { CellChanges, EvidenceList, IssueList, MatchSummary, OperationSummary, Section, SourceFields } from '@/components/RecordParts'
import { Badge, Card, CardContent, CardDescription, CardHeader, CardTitle, InfoTip, Input, NativeSelect, Progress, Tooltip } from '@/components/ui/primitives'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, Sheet } from '@/components/ui/dialog'
import { DemoUnavailableError, useData, useJob, useRefreshAll, useResource } from '@/lib/api'
import { CLASS_META, CLASS_ORDER, SOURCE_META, TONE_CLASSES } from '@/lib/meta'
import type { ChangeClass, Plan, RecordResult, Source } from '@/lib/types'
import { cn, fmtBytes, fmtDate, fmtNum, periodLabel } from '@/lib/utils'

const AUDIT_FILES = [
  ['report.html', 'Human-readable report'],
  ['sync_summary.json', 'Summary (JSON)'],
  ['record_changes.csv', 'Every record and action'],
  ['field_change_history.csv', 'Cell-level changes'],
  ['conflicts.csv', 'Review items'],
  ['validation_errors.csv', 'Validation issues'],
  ['new_facilities.csv', 'New facilities'],
  ['matched_resources.csv', 'Matched resources'],
  ['skipped_duplicates.csv', 'Unchanged & duplicates'],
] as const

function useActivePlan(): { planId: string | null; plans: ReturnType<typeof useResource<import('@/lib/types').PlanListItem[]>> } {
  const api = useData()
  const { planId } = useParams()
  const plans = useResource(() => api.plans(), [api])
  const fallback = plans.data?.find((p) => p.status !== 'APPLIED' && p.status !== 'NO_CHANGES')?.plan_id ?? plans.data?.[0]?.plan_id ?? null
  return { planId: planId ?? fallback, plans }
}

export default function SyncPreview() {
  const api = useData()
  const navigate = useNavigate()
  const { planId, plans } = useActivePlan()
  const plan = useResource(() => (planId ? api.plan(planId) : Promise.resolve(null)), [api, planId])
  const [filter, setFilter] = React.useState<{ classification: string; source: string; q: string; offset: number }>({
    classification: '',
    source: '',
    q: '',
    offset: 0,
  })
  const records = useResource(
    () => (planId ? api.records(planId, { ...filter, limit: 200 }) : Promise.resolve(null)),
    [api, planId, filter.classification, filter.source, filter.q, filter.offset],
  )
  const [selected, setSelected] = React.useState<RecordResult | null>(null)
  const [confirm, setConfirm] = React.useState<'approve' | 'apply' | null>(null)
  const [jobId, setJobId] = React.useState<string | null>(null)
  const refreshAll = useRefreshAll()
  const job = useJob(api, jobId, (j) => {
    if (j.status === 'succeeded') {
      const v = j.result?.new_version
      toast.success(v != null ? `Published version v${String(v).padStart(3, '0')}` : j.result?.message ?? 'Nothing to apply')
      refreshAll() // the plan, explorer, dashboard and history all show the new version
    } else toast.error(j.error ?? `Apply ${j.status}`)
  })

  if (plans.error) return <ErrorState error={plans.error} onRetry={plans.reload} />
  if (!plans.loading && !planId)
    return (
      <>
        <PageHeader eyebrow="Synchronize" title="Sync preview" />
        <EmptyState
          title="No dry runs yet"
          description="Upload a source export to compare it with the current warehouse."
          action={
            <Button asChild>
              <Link to="/sync/new">Start a new sync</Link>
            </Button>
          }
        />
      </>
    )
  const p = plan.data

  async function doApprove() {
    try {
      await api.approve(p!.plan_id)
      toast.success('Plan approved. Undecided review items will be deferred, never auto-approved.')
      plan.reload()
    } catch (e) {
      toast.error((e as Error).message)
    } finally {
      setConfirm(null)
    }
  }
  async function doApply() {
    try {
      const j = await api.apply(p!.plan_id, p!.status !== 'APPROVED')
      setJobId(j.job_id)
    } catch (e) {
      toast.error((e as Error).message)
    } finally {
      setConfirm(null)
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Synchronize · dry run"
        title="Sync preview"
        description="Every incoming record is classified against the exact base version below. Nothing changes until the plan is approved and applied."
        actions={
          <>
            <label className="sr-only" htmlFor="plan-select">
              Plan
            </label>
            <NativeSelect id="plan-select" value={planId ?? ''} onChange={(e) => navigate(`/sync/preview/${e.target.value}`)} className="w-full sm:w-[340px]">
              {(plans.data ?? []).map((x) => (
                <option key={x.plan_id} value={x.plan_id}>
                  {x.plan_id} · {x.status.toLowerCase()}
                </option>
              ))}
            </NativeSelect>
            {p && (
              <>
                <Button variant="outline" onClick={() => setConfirm('approve')} disabled={p.status !== 'DRAFT'}>
                  <ShieldCheck /> Approve plan
                </Button>
                <Tooltip content={api.mode === 'demo' ? 'Applying runs only in Local Production Mode' : 'Write → verify → publish a new version'}>
                  <span>
                    <Button
                      onClick={() => setConfirm('apply')}
                      disabled={['APPLIED', 'NO_CHANGES'].includes(p.status) || !!(job && job.status === 'running')}
                    >
                      <Play /> Apply plan
                    </Button>
                  </span>
                </Tooltip>
              </>
            )}
          </>
        }
      />

      {plan.error && <ErrorState error={plan.error} onRetry={plan.reload} />}
      {!p ? (
        <LoadingBlock rows={5} />
      ) : (
        <>
          <PlanMeta plan={p} />
          {job && (
            <Card className="p-4">
              <div className="mb-2 flex items-center justify-between text-sm">
                <span className="font-medium">
                  {job.status === 'running' ? job.message || 'Working…' : `Apply ${job.status}`}{' '}
                  <span className="text-muted-foreground">({job.phase})</span>
                </span>
                <span className="tabular text-muted-foreground">{Math.round(job.fraction * 100)}%</span>
              </div>
              <Progress value={job.fraction * 100} label="Apply progress" />
              {job.error && <p className="mt-2 text-sm text-destructive">{job.error}</p>}
            </Card>
          )}
          <ClassificationGrid plan={p} active={filter.classification} onPick={(c) => setFilter((f) => ({ ...f, classification: f.classification === c ? '' : c, offset: 0 }))} />
          <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
            <SourceBreakdown plan={p} />
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <Columns3 className="h-4 w-4 text-muted-foreground" /> New columns
                </CardTitle>
                <CardDescription>Created only when a new survey year/season is written</CardDescription>
              </CardHeader>
              <CardContent>
                {p.new_columns.length === 0 ? (
                  <p className="text-sm text-muted-foreground">No new columns — every period already exists.</p>
                ) : (
                  <ul className="space-y-1.5">
                    {p.new_columns.map((c) => (
                      <li key={c.name} className="flex items-center justify-between gap-2 text-sm">
                        <Mono>{c.name}</Mono>
                        <span className="truncate text-xs text-muted-foreground">after {c.insert_after}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </CardContent>
            </Card>
          </div>

          <Card>
            <CardHeader className="gap-3 md:flex-row md:items-end md:justify-between">
              <div>
                <CardTitle>Classified records</CardTitle>
                <CardDescription>
                  Select a record for its evidence and exact cell changes.{' '}
                  {p.summary.by_classification.UNCHANGED > 200 && 'Unchanged records beyond the first 200 are listed in the audit file.'}
                </CardDescription>
              </div>
              <div className="flex flex-wrap gap-2">
                <Input
                  placeholder="Search ID or file…"
                  value={filter.q}
                  onChange={(e) => setFilter((f) => ({ ...f, q: e.target.value, offset: 0 }))}
                  className="w-56"
                  aria-label="Search records"
                />
                <NativeSelect value={filter.classification} onChange={(e) => setFilter((f) => ({ ...f, classification: e.target.value, offset: 0 }))} aria-label="Classification">
                  <option value="">All classifications</option>
                  {CLASS_ORDER.map((c) => (
                    <option key={c} value={c}>
                      {CLASS_META[c].label}
                    </option>
                  ))}
                </NativeSelect>
                <NativeSelect value={filter.source} onChange={(e) => setFilter((f) => ({ ...f, source: e.target.value, offset: 0 }))} aria-label="Source">
                  <option value="">All sources</option>
                  {Object.entries(SOURCE_META).map(([k, v]) => (
                    <option key={k} value={k}>
                      {v.label}
                    </option>
                  ))}
                </NativeSelect>
              </div>
            </CardHeader>
            <CardContent>
              {records.error ? (
                <ErrorState error={records.error} onRetry={records.reload} />
              ) : (
                <>
                  <RecordTable data={records.data?.items ?? []} onSelect={setSelected} activeId={selected?.rec_id ?? null} />
                  {records.data && <Pager offset={filter.offset} limit={200} total={records.data.total} onChange={(o) => setFilter((f) => ({ ...f, offset: o }))} />}
                </>
              )}
            </CardContent>
          </Card>

          <AuditDownloads plan={p} />
        </>
      )}

      <RecordSheet record={selected} onClose={() => setSelected(null)} />

      <Dialog open={confirm !== null} onOpenChange={(o) => !o && setConfirm(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{confirm === 'approve' ? 'Approve this plan?' : 'Apply this plan?'}</DialogTitle>
            <DialogDescription>
              {api.mode === 'demo'
                ? new DemoUnavailableError(confirm === 'approve' ? 'Approving a plan' : 'Applying a plan').message
                : confirm === 'approve'
                  ? `Approval locks the current decisions (plan hash). ${p?.review_counts?.undecided ?? 0} undecided review item(s) will be deferred — ambiguous items are never auto-approved.`
                  : `The engine writes a new workbook to a temporary file, verifies integrity and preservation of every untouched cell, and only then publishes v${String((p?.base_version ?? 0) + 1).padStart(3, '0')}. ${p?.status !== 'APPROVED' ? 'This plan is not approved: applying now counts as explicit authorization.' : ''}`}
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirm(null)}>
              Cancel
            </Button>
            {api.mode === 'local' && (
              <Button onClick={confirm === 'approve' ? doApprove : doApply}>{confirm === 'approve' ? 'Approve plan' : 'Apply and publish'}</Button>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}

function PlanMeta({ plan }: { plan: Plan }) {
  return (
    <Card className="p-5">
      <div className="flex flex-wrap items-center gap-x-6 gap-y-3 text-sm">
        <div className="flex items-center gap-2">
          <Badge tone={plan.status === 'APPLIED' ? 'success' : plan.status === 'APPROVED' ? 'info' : plan.status === 'NO_CHANGES' ? 'neutral' : 'warning'} dot>
            {plan.status === 'APPLIED' ? `Applied → v${String(plan.applied_version).padStart(3, '0')}` : plan.status.replace('_', ' ')}
          </Badge>
          <Mono>{plan.plan_id}</Mono>
        </div>
        <div>
          <span className="text-muted-foreground">Base version </span>
          <span className="font-medium">v{String(plan.base_version).padStart(3, '0')}</span>
          <span className="text-muted-foreground"> · {fmtNum(plan.base_rows)} rows × {plan.base_columns} columns</span>
        </div>
        <div className="text-muted-foreground">
          Created {fmtDate(plan.created_at)}
          {plan.elapsed_seconds != null && ` · ${plan.elapsed_seconds}s`}
        </div>
        {plan.approval && (
          <div className="flex items-center gap-1.5 text-success">
            <CheckCircle2 className="h-4 w-4" /> Approved by {plan.approval.approved_by}
          </div>
        )}
      </div>
      <div className="mt-4 grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
        {plan.sources.map((s) => (
          <div key={s.sha256 + s.file_name} className="rounded-lg border bg-muted/30 p-3 text-sm">
            <div className="flex items-center gap-2">
              <FileSpreadsheet className="h-4 w-4 shrink-0 text-muted-foreground" />
              <span className="truncate font-medium" title={s.file_name}>
                {s.file_name}
              </span>
            </div>
            <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
              <SourceBadge source={s.source} />
              <span>{s.format}</span>
              <span>· {fmtNum(s.records)} records</span>
              <span>· {fmtBytes(s.size)}</span>
            </div>
            {s.periods.length > 0 && <div className="mt-1.5 text-xs">Periods: {s.periods.map(periodLabel).join(', ')}</div>}
            {s.personal_data_columns_ignored.length > 0 && (
              <div className="mt-1.5 text-xs text-muted-foreground">Personal-data columns never read: {s.personal_data_columns_ignored.join(', ')}</div>
            )}
          </div>
        ))}
      </div>
    </Card>
  )
}

function ClassificationGrid({ plan, active, onPick }: { plan: Plan; active: string; onPick: (c: string) => void }) {
  const counts = plan.summary.by_classification
  const total = plan.summary.records_total || 1
  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-4">
      {CLASS_ORDER.map((c) => {
        const meta = CLASS_META[c]
        const n = counts[c] ?? 0
        const Icon = meta.icon
        return (
          <button
            key={c}
            onClick={() => onPick(c)}
            className={cn(
              'group rounded-xl border bg-card p-4 text-left shadow-card transition-all hover:-translate-y-0.5 hover:shadow-lift focus-visible:ring-2 focus-visible:ring-ring',
              active === c && 'ring-2 ring-primary',
              n === 0 && 'opacity-60',
            )}
            aria-pressed={active === c}
          >
            <div className="flex items-center justify-between">
              <span className={cn('flex h-8 w-8 items-center justify-center rounded-lg', TONE_CLASSES[meta.tone].soft)}>
                <Icon className={cn('h-4 w-4', TONE_CLASSES[meta.tone].badge.split(' ').find((x) => x.startsWith('text-')))} />
              </span>
              {meta.auto ? (
                <Badge tone="neutral">automatic</Badge>
              ) : (
                <Badge tone="warning">
                  <Scale className="h-3 w-3" /> review
                </Badge>
              )}
            </div>
            <div className="mt-3 text-2xl font-semibold tabular">{fmtNum(n)}</div>
            <div className="flex items-center gap-1.5 text-sm font-medium">
              {meta.label} <InfoTip text={meta.description} />
            </div>
            <div className="mt-2 h-1 overflow-hidden rounded-full bg-muted">
              <div className="h-full rounded-full" style={{ width: `${(n / total) * 100}%`, background: TONE_CLASSES[meta.tone].chart }} />
            </div>
          </button>
        )
      })}
    </div>
  )
}

function SourceBreakdown({ plan }: { plan: Plan }) {
  const data = Object.entries(plan.summary.by_source).map(([src, counts]) => ({ name: SOURCE_META[src as Source]?.label ?? src, ...counts }))
  const classes = CLASS_ORDER.filter((c) => data.some((d) => (d as Record<string, unknown>)[c]))
  return (
    <Card className="xl:col-span-2">
      <CardHeader>
        <CardTitle>By source</CardTitle>
        <CardDescription>Classification of records per source system</CardDescription>
      </CardHeader>
      <CardContent className="h-[230px]">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} layout="vertical" margin={{ left: 8, right: 16 }}>
            <CartesianGrid strokeDasharray="3 3" horizontal={false} stroke="hsl(var(--border))" />
            <XAxis type="number" tick={{ fontSize: 11, fill: 'hsl(var(--muted-foreground))' }} axisLine={false} tickLine={false} />
            <YAxis type="category" dataKey="name" width={84} tick={{ fontSize: 12, fill: 'hsl(var(--muted-foreground))' }} axisLine={false} tickLine={false} />
            <RTooltip contentStyle={{ background: 'hsl(var(--popover))', border: '1px solid hsl(var(--border))', borderRadius: 8, fontSize: 12 }} />
            {classes.map((c) => (
              <Bar key={c} dataKey={c} name={CLASS_META[c].label} stackId="s" fill={TONE_CLASSES[CLASS_META[c].tone].chart} />
            ))}
            <Legend wrapperStyle={{ fontSize: 11 }} iconType="circle" iconSize={8} />
          </BarChart>
        </ResponsiveContainer>
      </CardContent>
    </Card>
  )
}

function RecordTable({ data, onSelect, activeId }: { data: RecordResult[]; onSelect: (r: RecordResult) => void; activeId: string | null }) {
  const columns = React.useMemo<ColumnDef<RecordResult>[]>(
    () => [
      { header: 'Classification', size: 190, cell: ({ row }) => <ClassBadge cls={row.original.classification} /> },
      { header: 'Source', size: 110, cell: ({ row }) => <SourceBadge source={row.original.source} /> },
      { header: 'Source ID', size: 170, cell: ({ row }) => <span className="font-mono text-xs">{row.original.source_record_id ?? '—'}</span> },
      { header: 'Period', size: 110, cell: ({ row }) => <span className="text-xs">{row.original.period ? periodLabel(row.original.period) : '—'}</span> },
      { header: 'Row', size: 60, cell: ({ row }) => <span className="tabular text-xs text-muted-foreground">{row.original.source_row}</span> },
      { header: 'Proposed action', cell: ({ row }) => <span className="text-sm text-muted-foreground">{row.original.proposed_action}</span> },
    ],
    [],
  )
  return (
    <DataTable
      ariaLabel="Classified source records"
      data={data}
      columns={columns}
      onRowClick={onSelect}
      getRowId={(r) => r.rec_id}
      activeRowId={activeId}
      height={520}
      empty="No records match these filters."
    />
  )
}

export function RecordSheet({ record, onClose }: { record: RecordResult | null; onClose: () => void }) {
  const [tab, setTab] = React.useState<'changes' | 'evidence' | 'source'>('changes')
  return (
    <Sheet
      open={!!record}
      onOpenChange={(o) => !o && onClose()}
      title={record ? `${record.source} · ${record.source_record_id ?? 'no ID'}` : ''}
      description={record ? `${record.source_file} — row ${record.source_row}` : undefined}
    >
      {record && (
        <div className="space-y-6">
          <div className="flex flex-wrap items-center gap-2">
            <ClassBadge cls={record.classification as ChangeClass} />
            {record.period && <Badge tone="info">{periodLabel(record.period)}</Badge>}
            {record.review_item && <Badge tone="warning">Review item {record.review_item}</Badge>}
          </div>
          <p className="text-sm">{record.proposed_action}</p>
          {record.review_item && (
            <Button asChild variant="outline" size="sm">
              <Link to={`/inspect?item=${record.review_item}`}>
                <GitCompareArrows /> Open in Match Inspector
              </Link>
            </Button>
          )}
          <div className="flex gap-1 rounded-lg bg-muted p-1 text-sm" role="tablist">
            {(['changes', 'evidence', 'source'] as const).map((t) => (
              <button
                key={t}
                role="tab"
                aria-selected={tab === t}
                onClick={() => setTab(t)}
                className={cn('flex-1 rounded-md px-3 py-1.5 font-medium capitalize', tab === t ? 'bg-card shadow-sm' : 'text-muted-foreground')}
              >
                {t === 'changes' ? 'Cell changes' : t === 'evidence' ? 'Match evidence' : 'Source record'}
              </button>
            ))}
          </div>
          {tab === 'changes' && (
            <Section title="Operations">
              {record.operations.length ? (
                <>
                  <OperationSummary operations={record.operations} />
                  <CellChanges operations={record.operations} />
                </>
              ) : (
                <p className="text-sm text-muted-foreground">
                  {record.review_item ? 'Operations depend on the review decision — see the Conflicts Center.' : 'No warehouse change for this record.'}
                </p>
              )}
              {record.notes.length > 0 && (
                <ul className="list-disc space-y-1 pl-5 text-sm text-muted-foreground">
                  {record.notes.map((n, i) => (
                    <li key={i}>{n}</li>
                  ))}
                </ul>
              )}
            </Section>
          )}
          {tab === 'evidence' && (
            <div className="space-y-5">
              <MatchSummary match={record.match} />
              <Section title="Evidence">
                <EvidenceList evidence={record.match.evidence} />
              </Section>
              <Section title="Validation">
                <IssueList issues={record.issues} />
              </Section>
              <Section title="Fingerprint">
                <p className="break-all font-mono text-xs text-muted-foreground">{record.content_hash}</p>
              </Section>
            </div>
          )}
          {tab === 'source' && (
            <Section title="Mapped source fields (personal data never read)">
              <SourceFields preview={record.preview} />
            </Section>
          )}
        </div>
      )}
    </Sheet>
  )
}

function AuditDownloads({ plan }: { plan: Plan }) {
  const api = useData()
  const stages: ('preview' | 'apply')[] = plan.status === 'APPLIED' ? ['apply', 'preview'] : ['preview']
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Download className="h-4 w-4 text-muted-foreground" /> Audit files
        </CardTitle>
        <CardDescription>
          {api.mode === 'demo'
            ? 'Synthetic audit produced by the engine for this demo plan.'
            : 'Private to this computer — share only with authorized colleagues.'}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {stages.map((stage) => (
          <div key={stage}>
            <div className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">{stage === 'apply' ? 'Applied run' : 'Dry run'}</div>
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
              {AUDIT_FILES.map(([file, label]) => (
                <a
                  key={file}
                  href={api.auditUrl(plan.plan_id, stage, file)}
                  download={file !== 'report.html' ? `${plan.plan_id}_${stage}_${file}` : undefined}
                  target={file === 'report.html' ? '_blank' : undefined}
                  rel="noreferrer"
                  className="flex items-center justify-between gap-2 rounded-lg border px-3 py-2 text-sm transition-colors hover:bg-muted/60"
                >
                  <span className="min-w-0">
                    <span className="block truncate font-medium">{label}</span>
                    <span className="block truncate font-mono text-[11px] text-muted-foreground">{file}</span>
                  </span>
                  <Download className="h-4 w-4 shrink-0 text-muted-foreground" />
                </a>
              ))}
            </div>
          </div>
        ))}
      </CardContent>
    </Card>
  )
}
