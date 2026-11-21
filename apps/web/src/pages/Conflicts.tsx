import * as React from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { toast } from 'sonner'
import { CheckCircle2, Clock3, GitCompareArrows, Layers, Scale, Sparkles } from 'lucide-react'
import { ClassBadge, EmptyState, ErrorState, LoadingBlock, PageHeader } from '@/components/common'
import { Pager } from '@/components/DataTable'
import { CellChanges, MatchSummary, Section } from '@/components/RecordParts'
import { Badge, Card, CardContent, CardDescription, CardHeader, CardTitle, Input, Label, NativeSelect, Textarea } from '@/components/ui/primitives'
import { Button } from '@/components/ui/button'
import { Sheet } from '@/components/ui/dialog'
import { useData, useResource } from '@/lib/api'
import { useMediaQuery } from '@/lib/hooks'
import { CLASS_META, DECISION_META } from '@/lib/meta'
import type { ChangeClass, PlanListItem, ReviewItem } from '@/lib/types'
import { cn, fmtDate, fmtNum } from '@/lib/utils'

export function usePlanChoice() {
  const api = useData()
  const [params, setParams] = useSearchParams()
  const plans = useResource(() => api.plans(), [api])
  const open = (plans.data ?? []).filter((p) => p.review_items > 0)
  const planId = params.get('plan') ?? open.find((p) => p.status === 'DRAFT' || p.status === 'APPROVED')?.plan_id ?? open[0]?.plan_id ?? null
  const setPlan = (id: string) => {
    const next = new URLSearchParams(params)
    next.set('plan', id)
    next.delete('item')
    setParams(next)
  }
  return { planId, plans, setPlan, params, setParams }
}

export function PlanPicker({ plans, planId, onChange }: { plans: PlanListItem[]; planId: string | null; onChange: (id: string) => void }) {
  return (
    <>
      <label className="sr-only" htmlFor="plan-pick">
        Plan
      </label>
      <NativeSelect id="plan-pick" value={planId ?? ''} onChange={(e) => onChange(e.target.value)} className="w-full sm:w-[360px]">
        {plans
          .filter((p) => p.review_items > 0)
          .map((p) => (
            <option key={p.plan_id} value={p.plan_id}>
              {p.plan_id} · {p.undecided} to review
            </option>
          ))}
      </NativeSelect>
    </>
  )
}

export function DecisionPanel({ planId, item, onDecided }: { planId: string; item: ReviewItem; onDecided: () => void }) {
  const api = useData()
  const [choice, setChoice] = React.useState<number | null>(item.decision?.option_index ?? item.recommended ?? null)
  const [reason, setReason] = React.useState('')
  const [busy, setBusy] = React.useState(false)
  React.useEffect(() => {
    setChoice(item.decision?.option_index ?? item.recommended ?? null)
    setReason('')
  }, [item.item_id, item.decision, item.recommended])

  async function submit() {
    if (choice === null) return
    if (!reason.trim()) {
      toast.error('Please record the reason for this decision.')
      return
    }
    setBusy(true)
    try {
      const opt = item.options[choice]
      await api.decide(planId, item.item_id, opt.decision, reason.trim(), choice)
      toast.success(
        api.mode === 'demo' ? `Decision saved in this browser only (demo): ${DECISION_META[opt.decision].label}` : `Decision recorded: ${DECISION_META[opt.decision].label}`,
      )
      onDecided()
    } catch (e) {
      toast.error((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-4">
      {item.decision && (
        <div className="rounded-lg border border-success/30 bg-success/5 p-3 text-sm">
          <div className="flex items-center gap-2 font-medium text-success">
            <CheckCircle2 className="h-4 w-4" /> {DECISION_META[item.decision.choice].label}
            {item.decision.remembered && <Badge tone="info">remembered</Badge>}
          </div>
          <p className="mt-1 text-muted-foreground">
            “{item.decision.reason}” — {item.decision.decided_by}, {fmtDate(item.decision.decided_at)}
          </p>
        </div>
      )}
      <fieldset className="space-y-2">
        <legend className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">Decision</legend>
        {item.options.map((opt, i) => (
          <label
            key={i}
            className={cn(
              'flex cursor-pointer items-start gap-3 rounded-lg border p-3 transition-colors hover:bg-muted/50',
              choice === i && 'border-primary bg-accent/50 ring-1 ring-primary',
            )}
          >
            <input type="radio" name={`opt-${item.item_id}`} className="mt-1 accent-[hsl(var(--primary))]" checked={choice === i} onChange={() => setChoice(i)} />
            <span className="min-w-0 flex-1">
              <span className="flex flex-wrap items-center gap-2 text-sm font-medium">
                {opt.label}
                {item.recommended === i && (
                  <Badge tone="info">
                    <Sparkles className="h-3 w-3" /> recommended
                  </Badge>
                )}
              </span>
              {opt.description && <span className="mt-0.5 block text-xs text-muted-foreground">{opt.description}</span>}
              {opt.operations.length > 0 && (
                <span className="mt-0.5 block text-xs text-muted-foreground">
                  {opt.operations.map((o) => (o.kind === 'INSERT_ROW' ? 'inserts a row' : `updates row ${o.target_row ?? '(new)'}`)).join(', ')} ·{' '}
                  {opt.operations.reduce((a, o) => a + o.cells.length, 0)} cells
                </span>
              )}
            </span>
          </label>
        ))}
      </fieldset>
      <div className="space-y-1.5">
        <Label htmlFor={`reason-${item.item_id}`}>Reason (required, recorded in the audit)</Label>
        <Textarea
          id={`reason-${item.item_id}`}
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          placeholder="e.g. Field team confirmed this is a different tubewell at the same project point"
        />
      </div>
      <div className="flex items-center justify-between gap-3">
        <p className="text-xs text-muted-foreground">
          {api.mode === 'demo' ? 'Demo: decisions are stored only in this browser.' : 'Decisions reset plan approval; re-approve before applying.'}
        </p>
        <Button onClick={submit} loading={busy} disabled={choice === null}>
          Record decision
        </Button>
      </div>
    </div>
  )
}

export default function Conflicts() {
  const api = useData()
  const { planId, plans, setPlan, params, setParams } = usePlanChoice()
  const [filter, setFilter] = React.useState({ status: 'undecided' as 'all' | 'undecided' | 'decided', classification: '', q: '', offset: 0 })
  const items = useResource(
    () => (planId ? api.items(planId, { ...filter, limit: 50 }) : Promise.resolve(null)),
    [api, planId, filter.status, filter.classification, filter.q, filter.offset],
  )
  const pending = useResource(() => api.pending(), [api])
  const isDesktop = useMediaQuery('(min-width: 1280px)')
  const selectedId = params.get('item')
  const selected = items.data?.items.find((i) => i.item_id === selectedId) ?? null
  const select = (id: string | null) => {
    const next = new URLSearchParams(params)
    if (id) next.set('item', id)
    else next.delete('item')
    setParams(next, { replace: true })
  }

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Synchronize · review"
        title="Conflicts Center"
        description="Everything that needs a human decision: changed measurements, possible duplicates, identity conflicts and rule-based reviews. Every decision requires a reason and is written to the audit."
        actions={plans.data && <PlanPicker plans={plans.data} planId={planId} onChange={setPlan} />}
      />
      {plans.error && <ErrorState error={plans.error} onRetry={plans.reload} />}
      {!planId && !plans.loading ? (
        <EmptyState icon={Scale} title="Nothing to review" description="No plan has review items. New dry runs that need decisions appear here." />
      ) : (
        <div className="grid grid-cols-1 gap-6 xl:grid-cols-[minmax(0,1fr)_420px]">
          <div className="space-y-4">
            <div className="flex flex-wrap gap-2">
              <Input
                placeholder="Search title or reason…"
                value={filter.q}
                onChange={(e) => setFilter((f) => ({ ...f, q: e.target.value, offset: 0 }))}
                className="w-64"
                aria-label="Search review items"
              />
              <NativeSelect value={filter.status} onChange={(e) => setFilter((f) => ({ ...f, status: e.target.value as typeof f.status, offset: 0 }))} aria-label="Status">
                <option value="undecided">Undecided</option>
                <option value="decided">Decided</option>
                <option value="all">All</option>
              </NativeSelect>
              <NativeSelect value={filter.classification} onChange={(e) => setFilter((f) => ({ ...f, classification: e.target.value, offset: 0 }))} aria-label="Classification">
                <option value="">All types</option>
                {(Object.keys(CLASS_META) as ChangeClass[])
                  .filter((c) => !CLASS_META[c].auto)
                  .map((c) => (
                    <option key={c} value={c}>
                      {CLASS_META[c].label}
                    </option>
                  ))}
              </NativeSelect>
            </div>
            {items.error ? (
              <ErrorState error={items.error} onRetry={items.reload} />
            ) : !items.data ? (
              <LoadingBlock rows={5} />
            ) : items.data.items.length === 0 ? (
              <EmptyState icon={CheckCircle2} title={filter.status === 'undecided' ? 'All items decided' : 'No matching items'} description="Adjust the filters to see other items." />
            ) : (
              <ul className="space-y-2">
                {items.data.items.map((it) => (
                  <li key={it.item_id}>
                    <button
                      onClick={() => select(it.item_id)}
                      className={cn(
                        'w-full rounded-xl border bg-card p-4 text-left shadow-card transition-all hover:shadow-lift focus-visible:ring-2 focus-visible:ring-ring',
                        selectedId === it.item_id && 'ring-2 ring-primary',
                      )}
                    >
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-mono text-xs text-muted-foreground">{it.item_id}</span>
                        <ClassBadge cls={it.classification} />
                        {it.decision ? (
                          <Badge tone="success">
                            <CheckCircle2 className="h-3 w-3" /> {DECISION_META[it.decision.choice].label}
                          </Badge>
                        ) : (
                          <Badge tone="warning">
                            <Clock3 className="h-3 w-3" /> awaiting decision
                          </Badge>
                        )}
                      </div>
                      <div className="mt-2 text-sm font-medium">{it.title}</div>
                      <div className="mt-1 line-clamp-2 text-sm text-muted-foreground">{it.reason}</div>
                    </button>
                  </li>
                ))}
              </ul>
            )}
            {items.data && <Pager offset={filter.offset} limit={50} total={items.data.total} onChange={(o) => setFilter((f) => ({ ...f, offset: o }))} />}
          </div>

          <div className="space-y-4">
            {isDesktop && (
              <Card>
                <CardHeader>
                  <CardTitle>{selected ? selected.item_id : 'Select an item'}</CardTitle>
                  <CardDescription>{selected ? selected.title : 'Choose a review item to see its options and record a decision.'}</CardDescription>
                </CardHeader>
                <CardContent>{selected && planId && <ItemDetail planId={planId} item={selected} onDecided={items.reload} />}</CardContent>
              </Card>
            )}
            {api.mode === 'local' && planId && <BulkDecision planId={planId} onDone={items.reload} />}
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <Layers className="h-4 w-4 text-muted-foreground" /> Carried over from earlier runs
                </CardTitle>
                <CardDescription>Deferred or undecided items recorded in pending_conflicts.json</CardDescription>
              </CardHeader>
              <CardContent>
                {!pending.data ? (
                  <LoadingBlock rows={2} />
                ) : pending.data.length === 0 ? (
                  <p className="text-sm text-muted-foreground">No deferred items.</p>
                ) : (
                  <ul className="space-y-2 text-sm">
                    {pending.data.slice(0, 8).map((p, i) => (
                      <li key={i} className="rounded-lg border p-2.5">
                        <div className="flex items-center gap-2">
                          <ClassBadge cls={p.classification as ChangeClass} />
                          <span className="font-mono text-[11px] text-muted-foreground">{String(p.item_id)}</span>
                        </div>
                        <div className="mt-1 text-muted-foreground">{String(p.title)}</div>
                      </li>
                    ))}
                    {pending.data.length > 8 && <li className="text-xs text-muted-foreground">+{fmtNum(pending.data.length - 8)} more</li>}
                  </ul>
                )}
              </CardContent>
            </Card>
          </div>
        </div>
      )}
      {!isDesktop && (
        <Sheet open={!!selected} onOpenChange={(o) => !o && select(null)} title={selected?.item_id ?? ''} description={selected?.title}>
          {selected && planId && <ItemDetail planId={planId} item={selected} onDecided={items.reload} />}
        </Sheet>
      )}
    </div>
  )
}

function ItemDetail({ planId, item, onDecided }: { planId: string; item: ReviewItem; onDecided: () => void }) {
  const [showOps, setShowOps] = React.useState<number | null>(null)
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-2">
        <ClassBadge cls={item.classification} />
        <Button asChild size="sm" variant="outline">
          <Link to={`/inspect?plan=${planId}&item=${item.item_id}`}>
            <GitCompareArrows /> Match Inspector
          </Link>
        </Button>
      </div>
      <p className="text-sm text-muted-foreground">{item.reason}</p>
      {item.record && <MatchSummary match={item.record.match} />}
      <DecisionPanel planId={planId} item={item} onDecided={onDecided} />
      <Section title="What each option writes">
        <div className="flex flex-wrap gap-1.5">
          {item.options.map((o, i) => (
            <Button key={i} size="sm" variant={showOps === i ? 'default' : 'outline'} onClick={() => setShowOps(showOps === i ? null : i)} disabled={!o.operations.length}>
              {DECISION_META[o.decision].label}
              {o.candidate_row ? ` · row ${o.candidate_row}` : ''}
            </Button>
          ))}
        </div>
        {showOps !== null && <CellChanges operations={item.options[showOps].operations} />}
      </Section>
    </div>
  )
}

function BulkDecision({ planId, onDone }: { planId: string; onDone: () => void }) {
  const api = useData()
  const [cls, setCls] = React.useState('NEW_SOURCE_METADATA')
  const [choice, setChoice] = React.useState('ACCEPT_NEW_VALUES')
  const [reason, setReason] = React.useState('')
  const [busy, setBusy] = React.useState(false)
  async function run() {
    if (!reason.trim()) {
      toast.error('A reason is required')
      return
    }
    setBusy(true)
    try {
      const decided = await api.bulkDecide(planId, cls, choice, reason.trim())
      toast.success(`${decided} item(s) decided`)
      onDone()
    } catch (e) {
      toast.error((e as Error).message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <Card>
      <CardHeader>
        <CardTitle>Bulk decision</CardTitle>
        <CardDescription>Apply one decision to every undecided item of a type (where that option exists).</CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="grid grid-cols-2 gap-2">
          <NativeSelect value={cls} onChange={(e) => setCls(e.target.value)} aria-label="Item type">
            {(Object.keys(CLASS_META) as ChangeClass[])
              .filter((c) => !CLASS_META[c].auto)
              .map((c) => (
                <option key={c} value={c}>
                  {CLASS_META[c].label}
                </option>
              ))}
          </NativeSelect>
          <NativeSelect value={choice} onChange={(e) => setChoice(e.target.value)} aria-label="Decision">
            {Object.entries(DECISION_META).map(([k, v]) => (
              <option key={k} value={k}>
                {v.label}
              </option>
            ))}
          </NativeSelect>
        </div>
        <Textarea value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Reason recorded for each item" aria-label="Bulk reason" />
        <Button onClick={run} loading={busy} className="w-full" variant="outline">
          Apply to all matching undecided items
        </Button>
      </CardContent>
    </Card>
  )
}
