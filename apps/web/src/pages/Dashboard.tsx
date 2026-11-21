import { Link } from 'react-router-dom'
import { Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, Legend, ResponsiveContainer, Tooltip as RTooltip, XAxis, YAxis } from 'recharts'
import { AlertTriangle, ArrowRight, CheckCircle2, Columns3, Database, FileStack, GitBranch, Scale, ShieldAlert, UploadCloud } from 'lucide-react'
import { ClassBadge, EmptyState, ErrorState, LoadingBlock, PageHeader, StatCard } from '@/components/common'
import { Badge, Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/primitives'
import { Button } from '@/components/ui/button'
import { useData, useResource } from '@/lib/api'
import { CLASS_META, CLASS_ORDER, SOURCE_META, TONE_CLASSES } from '@/lib/meta'
import type { ChangeClass, HistoryEvent, Source } from '@/lib/types'
import { fmtCompact, fmtDate, fmtNum, fmtRelative } from '@/lib/utils'

const EVENT_LABEL: Record<string, string> = {
  INIT: 'Baseline registered',
  PREVIEW: 'Dry run completed',
  DECISION: 'Review decision recorded',
  APPROVE: 'Plan approved',
  APPLY: 'New version published',
  APPLY_NO_CHANGES: 'Apply: nothing to change',
  APPLY_FAILED: 'Apply failed (warehouse unchanged)',
  APPLY_CANCELLED: 'Apply cancelled (warehouse unchanged)',
  ROLLBACK: 'Rolled back',
  RECOVER: 'Recovered interrupted run',
  SETTINGS_UPDATED: 'Settings updated',
  PUBLISH: 'Copied to private storage',
}

function eventDetail(e: HistoryEvent): string {
  if (e.event === 'APPLY') return `v${String(e.version).padStart(3, '0')} from v${String(e.previous_version).padStart(3, '0')}`
  if (e.event === 'DECISION') return `${e.item_id} → ${e.choice}`
  if (e.event === 'ROLLBACK') return `v${e.from_version} → v${e.to_version}`
  if (e.event === 'PREVIEW') return Array.isArray(e.files) ? (e.files as string[]).join(', ') : ''
  return ''
}

export default function Dashboard() {
  const api = useData()
  const status = useResource(() => api.status(), [api])
  const history = useResource(() => api.history(), [api])
  const quality = useResource(() => api.quality(), [api])

  if (status.error) return <ErrorState error={status.error} onRetry={status.reload} />
  const s = status.data
  const current = s?.current_version

  const growth = (history.data?.versions ?? []).map((v) => ({
    name: `v${String(v.version).padStart(3, '0')}`,
    rows: v.rows,
    added: v.counts?.added_facilities ?? 0,
  }))
  const planBars = (s?.recent_plans ?? [])
    .slice(0, 8)
    .reverse()
    .map((p) => {
      const row: Record<string, string | number> = { name: p.plan_id.split('-').slice(1, 3).join(' ') }
      for (const c of CLASS_ORDER) if (c !== 'UNCHANGED' && p.summary[c]) row[c] = p.summary[c]
      return row
    })
  const sources = Object.entries(quality.data?.quality.sources ?? {})
    .filter(([k]) => ['GPANI', 'WPMS', 'AQUA', 'BORELOG'].includes(k))
    .map(([k, v]) => ({ name: k === 'AQUA' ? 'AquaWatch' : SOURCE_META[k as Source]?.label ?? k, value: v, key: k === 'AQUA' ? 'AQUAWATCH' : k }))
  const q = quality.data?.quality
  const warnings = q ? (q.gps.invalid ?? 0) + (q.gps.reversed ?? 0) + Object.values(q.outliers).reduce((a, o) => a + o.count, 0) : undefined

  return (
    <div className="space-y-8">
      <PageHeader
        eyebrow="Overview"
        title="Warehouse dashboard"
        description="Current verified version of the single-sheet WASH warehouse, recent synchronization activity and items that need attention."
        actions={
          <>
            <Button variant="outline" asChild>
              <Link to="/history">
                <GitBranch /> Version history
              </Link>
            </Button>
            <Button asChild>
              <Link to="/sync/new">
                <UploadCloud /> Start a new sync
              </Link>
            </Button>
          </>
        }
      />

      {status.loading && !s ? (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <LoadingBlock key={i} rows={2} />
          ))}
        </div>
      ) : !current ? (
        <EmptyState
          icon={Database}
          title="No warehouse registered yet"
          description="Register the existing verified workbook once. It is copied into private state as the immutable baseline v000."
          action={
            <Button asChild>
              <Link to="/docs/getting-started">How to register the baseline</Link>
            </Button>
          }
        />
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
          <StatCard
            label="Current warehouse version"
            value={<span>v{String(current.version).padStart(3, '0')}</span>}
            hint={`${current.kind === 'BASELINE' ? 'Baseline' : 'Synchronized'} · ${fmtDate(current.created_at)}`}
            icon={GitBranch}
            tone="teal"
            footer={<span className="font-mono text-[11px]">SHA-256 {current.sha256.slice(0, 16)}…</span>}
          />
          <StatCard
            label="Facility records"
            value={fmtNum(current.rows)}
            hint="Rows in the single worksheet (one facility per row)"
            icon={Database}
            tone="info"
            info="Counted from the verified workbook, excluding the header row."
          />
          <StatCard
            label="Columns"
            value={fmtNum(current.columns)}
            hint={`Exactly one worksheet · ${current.validation?.ok ? 'integrity verified' : 'verification warnings'}`}
            icon={Columns3}
            tone="violet"
          />
          <StatCard
            label="Last verified synchronization"
            value={s?.last_sync ? fmtRelative(s.last_sync.created_at) : 'None yet'}
            hint={
              s?.last_sync
                ? `+${fmtNum(s.last_sync.counts.added_facilities)} facilities · ${fmtNum(s.last_sync.counts.new_observations)} observations`
                : 'Only the baseline is registered'
            }
            icon={CheckCircle2}
            tone="success"
          />
          <StatCard
            label="Pending conflicts"
            value={fmtNum(s?.pending_conflicts ?? 0)}
            hint="Deferred or undecided review items carried between runs"
            icon={Scale}
            tone={(s?.pending_conflicts ?? 0) > 0 ? 'warning' : 'neutral'}
            footer={
              <Link to="/conflicts" className="inline-flex items-center gap-1 font-medium text-primary hover:underline">
                Open Conflicts Center <ArrowRight className="h-3 w-3" />
              </Link>
            }
          />
          <StatCard
            label="Data-quality warnings"
            value={warnings === undefined ? '—' : fmtNum(warnings)}
            hint="Invalid or reversed GPS and implausible values in the current version"
            icon={ShieldAlert}
            tone={warnings ? 'warning' : 'neutral'}
            footer={
              <Link to="/quality" className="inline-flex items-center gap-1 font-medium text-primary hover:underline">
                Data-quality report <ArrowRight className="h-3 w-3" />
              </Link>
            }
          />
        </div>
      )}

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-5">
        <Card className="xl:col-span-3">
          <CardHeader>
            <CardTitle>Recent dry runs</CardTitle>
            <CardDescription>Classified source records per preview (unchanged records omitted)</CardDescription>
          </CardHeader>
          <CardContent className="h-[280px]">
            {planBars.length === 0 ? (
              <EmptyState title="No previews yet" description="Upload a source export to run the first dry run." className="h-full py-6" />
            ) : (
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={planBars} margin={{ left: -12, right: 8 }}>
                  <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="hsl(var(--border))" />
                  <XAxis dataKey="name" tick={{ fontSize: 11, fill: 'hsl(var(--muted-foreground))' }} tickLine={false} axisLine={false} />
                  <YAxis tick={{ fontSize: 11, fill: 'hsl(var(--muted-foreground))' }} tickLine={false} axisLine={false} />
                  <RTooltip contentStyle={{ background: 'hsl(var(--popover))', border: '1px solid hsl(var(--border))', borderRadius: 8, fontSize: 12 }} />
                  {CLASS_ORDER.filter((c) => c !== 'UNCHANGED' && planBars.some((p) => p[c])).map((c) => (
                    <Bar key={c} dataKey={c} name={CLASS_META[c].label} stackId="a" fill={TONE_CLASSES[CLASS_META[c].tone].chart} radius={[2, 2, 0, 0]} />
                  ))}
                  <Legend wrapperStyle={{ fontSize: 11 }} iconType="circle" iconSize={8} />
                </BarChart>
              </ResponsiveContainer>
            )}
          </CardContent>
        </Card>
        <Card className="xl:col-span-2">
          <CardHeader>
            <CardTitle>Facility rows by source</CardTitle>
            <CardDescription>Rows carrying each source system (a merged row counts for each)</CardDescription>
          </CardHeader>
          <CardContent className="h-[280px]">
            {quality.loading && !quality.data ? (
              <LoadingBlock rows={4} />
            ) : (
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={sources} layout="vertical" margin={{ left: 8, right: 24 }}>
                  <XAxis type="number" hide />
                  <YAxis type="category" dataKey="name" width={84} tick={{ fontSize: 12, fill: 'hsl(var(--muted-foreground))' }} axisLine={false} tickLine={false} />
                  <RTooltip formatter={(v: number) => fmtNum(v)} contentStyle={{ background: 'hsl(var(--popover))', border: '1px solid hsl(var(--border))', borderRadius: 8, fontSize: 12 }} />
                  <Bar dataKey="value" radius={[0, 6, 6, 0]} label={{ position: 'right', fontSize: 11, fill: 'hsl(var(--muted-foreground))', formatter: (v: number) => fmtCompact(v) }}>
                    {sources.map((s) => (
                      <Cell key={s.key} fill={SOURCE_META[s.key as Source]?.color} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            )}
          </CardContent>
        </Card>
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-5">
        <Card className="xl:col-span-2">
          <CardHeader>
            <CardTitle>Warehouse growth</CardTitle>
            <CardDescription>Rows per published version</CardDescription>
          </CardHeader>
          <CardContent className="h-[240px]">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={growth} margin={{ left: -4, right: 8 }}>
                <defs>
                  <linearGradient id="growth" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="hsl(var(--primary))" stopOpacity={0.35} />
                    <stop offset="100%" stopColor="hsl(var(--primary))" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="hsl(var(--border))" />
                <XAxis dataKey="name" tick={{ fontSize: 11, fill: 'hsl(var(--muted-foreground))' }} tickLine={false} axisLine={false} />
                <YAxis domain={['dataMin - 5', 'dataMax + 5']} tickFormatter={(v) => fmtCompact(v)} tick={{ fontSize: 11, fill: 'hsl(var(--muted-foreground))' }} tickLine={false} axisLine={false} width={44} />
                <RTooltip formatter={(v: number) => fmtNum(v)} contentStyle={{ background: 'hsl(var(--popover))', border: '1px solid hsl(var(--border))', borderRadius: 8, fontSize: 12 }} />
                <Area type="monotone" dataKey="rows" stroke="hsl(var(--primary))" strokeWidth={2} fill="url(#growth)" />
              </AreaChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>
        <Card className="xl:col-span-3">
          <CardHeader className="flex-row items-center justify-between space-y-0">
            <div>
              <CardTitle>Recent activity</CardTitle>
              <CardDescription>From the append-only change history</CardDescription>
            </div>
            <Button variant="ghost" size="sm" asChild>
              <Link to="/history">
                All history <ArrowRight />
              </Link>
            </Button>
          </CardHeader>
          <CardContent>
            {!s ? (
              <LoadingBlock rows={4} />
            ) : s.recent_events.length === 0 ? (
              <EmptyState title="No activity yet" />
            ) : (
              <ol className="relative space-y-3 border-l pl-5">
                {s.recent_events.slice(0, 7).map((e, i) => (
                  <li key={i} className="relative">
                    <span
                      className={`absolute -left-[26px] top-1 h-3 w-3 rounded-full border-2 border-background ${
                        e.event === 'APPLY' ? 'bg-success' : e.event.startsWith('APPLY_F') ? 'bg-destructive' : e.event === 'DECISION' ? 'bg-warning' : 'bg-primary'
                      }`}
                      aria-hidden
                    />
                    <div className="flex flex-wrap items-baseline justify-between gap-x-3">
                      <span className="text-sm font-medium">{EVENT_LABEL[e.event] ?? e.event}</span>
                      <span className="text-xs text-muted-foreground">{fmtRelative(e.at)}</span>
                    </div>
                    <div className="truncate text-xs text-muted-foreground">
                      {eventDetail(e)}
                      {e.by ? ` · by ${String(e.by)}` : ''}
                    </div>
                  </li>
                ))}
              </ol>
            )}
          </CardContent>
        </Card>
      </div>

      {s && s.recent_plans.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Plans</CardTitle>
            <CardDescription>Dry runs and their outcome. Open one to review and approve.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-2">
            {s.recent_plans.slice(0, 5).map((p) => (
              <Link
                key={p.plan_id}
                to={`/sync/preview/${p.plan_id}`}
                className="flex flex-col gap-2 rounded-lg border p-3 transition-colors hover:bg-muted/50 md:flex-row md:items-center md:justify-between"
              >
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <FileStack className="h-4 w-4 text-muted-foreground" aria-hidden />
                    <span className="font-mono text-xs">{p.plan_id}</span>
                    <Badge tone={p.status === 'APPLIED' ? 'success' : p.status === 'APPROVED' ? 'info' : p.status === 'NO_CHANGES' ? 'neutral' : 'warning'}>
                      {p.status === 'APPLIED' ? `Applied → v${String(p.applied_version).padStart(3, '0')}` : p.status.replace('_', ' ').toLowerCase()}
                    </Badge>
                  </div>
                  <div className="mt-1 truncate text-xs text-muted-foreground">{p.files.join(', ')}</div>
                </div>
                <div className="flex flex-wrap gap-1.5">
                  {CLASS_ORDER.filter((c) => p.summary[c] && c !== 'UNCHANGED')
                    .slice(0, 4)
                    .map((c) => (
                      <ClassBadge key={c} cls={c as ChangeClass} count={p.summary[c]} />
                    ))}
                  {p.undecided > 0 && (
                    <Badge tone="warning">
                      <AlertTriangle className="h-3 w-3" /> {p.undecided} to review
                    </Badge>
                  )}
                </div>
              </Link>
            ))}
          </CardContent>
        </Card>
      )}
    </div>
  )
}
