import * as React from 'react'
import { Link } from 'react-router-dom'
import { ArrowLeftRight, Database, FileInput, GitCompareArrows, MapPin } from 'lucide-react'
import { ClassBadge, EmptyState, ErrorState, LoadingBlock, Mono, PageHeader, SourceBadge } from '@/components/common'
import { CellChanges, EvidenceList, IssueList, MatchSummary, Section, SourceFields } from '@/components/RecordParts'
import { Badge, Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/primitives'
import { Button } from '@/components/ui/button'
import { DecisionPanel, PlanPicker, usePlanChoice } from '@/pages/Conflicts'
import { useData, useResource } from '@/lib/api'
import { DECISION_META } from '@/lib/meta'
import type { CandidateSummary, ReviewItem } from '@/lib/types'
import { cn, fmtValue } from '@/lib/utils'

const COMPARE_FIELDS: [string, string][] = [
  ['candidate_id', 'Warehouse ID'],
  ['division', 'Division'],
  ['district', 'District'],
  ['upazila', 'Upazila'],
  ['union_name', 'Union'],
  ['village_name', 'Village'],
  ['facility_type', 'Facility type'],
  ['latitude', 'Latitude'],
  ['longitude', 'Longitude'],
  ['record_sources', 'Record sources'],
  ['aqua_gwc_id', 'AquaWatch ID'],
  ['aqua_well_type', 'AquaWatch type'],
  ['aqua_well_depth_m', 'AquaWatch depth (m)'],
  ['wpms_go_code', 'WPMS GO code'],
  ['wpms_total_depth_m', 'WPMS depth (m)'],
  ['gpani_source_geocode', 'GPANI geocode'],
  ['gpani_tubewell_depth_raw', 'GPANI depth'],
  ['borelog_id', 'Bore-log ID'],
  ['borelog_well_depth_m', 'Bore-log depth (m)'],
]

async function findItem(api: ReturnType<typeof useData>, planId: string, itemId: string): Promise<ReviewItem | null> {
  for (let offset = 0; offset < 20000; offset += 500) {
    const page = await api.items(planId, { status: 'all', offset, limit: 500 })
    const hit = page.items.find((i) => i.item_id === itemId)
    if (hit) return hit
    if (offset + 500 >= page.total) break
  }
  return null
}

export default function MatchInspector() {
  const api = useData()
  const { planId, plans, setPlan, params, setParams } = usePlanChoice()
  const itemId = params.get('item')
  const list = useResource(() => (planId ? api.items(planId, { status: 'undecided', limit: 12 }) : Promise.resolve(null)), [api, planId])
  const item = useResource(() => (planId && itemId ? findItem(api, planId, itemId) : Promise.resolve(null)), [api, planId, itemId])
  const [candIdx, setCandIdx] = React.useState(0)
  React.useEffect(() => setCandIdx(0), [itemId])
  const it = item.data
  const rec = it?.record
  const candidates: CandidateSummary[] = rec?.match.candidates ?? []
  const cand = candidates[candIdx]
  const row = useResource(() => (cand ? api.row(cand.row) : Promise.resolve(null)), [api, cand?.row])
  const optionForCandidate = it?.options.findIndex((o) => o.candidate_row === cand?.row) ?? -1
  const [optView, setOptView] = React.useState<number | null>(null)
  React.useEffect(() => setOptView(null), [itemId, candIdx])
  const shownOption = optView ?? (optionForCandidate >= 0 ? optionForCandidate : it?.recommended ?? 0)

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Synchronize · review"
        title="Match Inspector"
        description="Compare an incoming source record with each existing warehouse candidate, side by side, with the evidence behind every proposed match."
        actions={plans.data && <PlanPicker plans={plans.data} planId={planId} onChange={setPlan} />}
      />
      {!itemId ? (
        <Card>
          <CardHeader>
            <CardTitle>Choose a record to inspect</CardTitle>
            <CardDescription>Undecided review items of the selected plan</CardDescription>
          </CardHeader>
          <CardContent>
            {list.error ? (
              <ErrorState error={list.error} />
            ) : !list.data ? (
              <LoadingBlock rows={4} />
            ) : list.data.items.length === 0 ? (
              <EmptyState icon={GitCompareArrows} title="Nothing to inspect" description="There are no undecided review items in this plan." />
            ) : (
              <ul className="grid grid-cols-1 gap-2 md:grid-cols-2">
                {list.data.items.map((i) => (
                  <li key={i.item_id}>
                    <button
                      className="w-full rounded-lg border p-3 text-left transition-colors hover:bg-muted/50"
                      onClick={() => setParams(new URLSearchParams({ plan: planId ?? '', item: i.item_id }))}
                    >
                      <div className="flex items-center gap-2">
                        <span className="font-mono text-xs text-muted-foreground">{i.item_id}</span>
                        <ClassBadge cls={i.classification} />
                      </div>
                      <div className="mt-1.5 line-clamp-2 text-sm">{i.title}</div>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
      ) : item.error ? (
        <ErrorState error={item.error} onRetry={item.reload} />
      ) : !it || !rec ? (
        item.loading ? <LoadingBlock rows={6} /> : <EmptyState title="Review item not found" description={`No item ${itemId} in plan ${planId}.`} />
      ) : (
        <>
          <Card className="p-5">
            <div className="flex flex-wrap items-center gap-2">
              <Mono>{it.item_id}</Mono>
              <ClassBadge cls={it.classification} />
              <SourceBadge source={rec.source} />
              {it.decision && <Badge tone="success">Decided: {DECISION_META[it.decision.choice].label}</Badge>}
              <Button variant="ghost" size="sm" asChild className="ml-auto">
                <Link to={`/conflicts?plan=${planId}&item=${it.item_id}`}>Back to Conflicts Center</Link>
              </Button>
            </div>
            <h2 className="mt-3 text-lg font-semibold">{it.title}</h2>
            <p className="mt-1 text-sm text-muted-foreground">{it.reason}</p>
            <div className="mt-4">
              <MatchSummary match={rec.match} />
            </div>
          </Card>

          {candidates.length > 1 && (
            <div className="flex flex-wrap items-center gap-2" role="tablist" aria-label="Candidates">
              <span className="text-sm text-muted-foreground">Candidates:</span>
              {candidates.map((c, i) => (
                <button
                  key={c.row}
                  role="tab"
                  aria-selected={i === candIdx}
                  onClick={() => setCandIdx(i)}
                  className={cn('rounded-full border px-3 py-1 text-sm', i === candIdx ? 'border-primary bg-accent font-medium' : 'hover:bg-muted')}
                >
                  Row {c.row}
                  {c.distance_m !== undefined && <span className="text-muted-foreground"> · {c.distance_m} m</span>}
                </button>
              ))}
            </div>
          )}

          <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)]">
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <FileInput className="h-4 w-4 text-primary" /> Incoming source record
                </CardTitle>
                <CardDescription>
                  {rec.source_file} · row {rec.source_row}
                  {rec.period ? ` · ${rec.period}` : ''}
                </CardDescription>
              </CardHeader>
              <CardContent>
                <SourceFields preview={rec.preview} />
              </CardContent>
            </Card>
            <div className="hidden items-center justify-center lg:flex" aria-hidden>
              <div className="flex h-10 w-10 items-center justify-center rounded-full border bg-card shadow-card">
                <ArrowLeftRight className="h-4 w-4 text-muted-foreground" />
              </div>
            </div>
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <Database className="h-4 w-4 text-primary" /> Existing warehouse candidate
                </CardTitle>
                <CardDescription>
                  {cand ? (
                    <>
                      Row {cand.row}
                      {cand.distance_m !== undefined && (
                        <>
                          {' '}
                          · <MapPin className="inline h-3 w-3" /> {cand.distance_m} m from the source coordinates
                        </>
                      )}
                    </>
                  ) : (
                    'No existing candidate — a new facility row is proposed.'
                  )}
                </CardDescription>
              </CardHeader>
              <CardContent>
                {!cand ? (
                  <EmptyState title="No candidate" description="The record has no warehouse candidate." className="py-8" />
                ) : row.error ? (
                  <ErrorState error={row.error} />
                ) : !row.data ? (
                  <LoadingBlock rows={6} />
                ) : (
                  <dl className="grid grid-cols-1 gap-x-4 gap-y-1.5 text-sm sm:grid-cols-2">
                    {COMPARE_FIELDS.filter(([k]) => row.data!.values[k] !== undefined).map(([k, label]) => (
                      <div key={k} className="flex min-w-0 justify-between gap-2 border-b border-dashed py-1">
                        <dt className="shrink-0 text-muted-foreground">{label}</dt>
                        <dd className="truncate text-right font-medium" title={fmtValue(row.data!.values[k])}>
                          {fmtValue(row.data!.values[k])}
                        </dd>
                      </div>
                    ))}
                  </dl>
                )}
              </CardContent>
            </Card>
          </div>

          <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1fr)_420px]">
            <div className="space-y-4">
              <Card>
                <CardHeader>
                  <CardTitle>Match evidence{cand ? ` · row ${cand.row}` : ''}</CardTitle>
                  <CardDescription>Deterministic checks: identifiers, administrative location, GPS distance, tubewell type and depth</CardDescription>
                </CardHeader>
                <CardContent>
                  <EvidenceList evidence={rec.match.evidence} filterRow={candidates.length > 1 ? cand?.row : undefined} />
                </CardContent>
              </Card>
              <Card>
                <CardHeader>
                  <CardTitle>Proposed cell changes</CardTitle>
                  <CardDescription>Exactly what each decision would write</CardDescription>
                </CardHeader>
                <CardContent className="space-y-3">
                  <div className="flex flex-wrap gap-1.5">
                    {it.options.map((o, i) => (
                      <Button key={i} size="sm" variant={shownOption === i ? 'default' : 'outline'} onClick={() => setOptView(i)}>
                        {DECISION_META[o.decision].label}
                        {o.candidate_row ? ` · row ${o.candidate_row}` : ''}
                      </Button>
                    ))}
                  </div>
                  {it.options[shownOption]?.operations.length ? (
                    <CellChanges operations={it.options[shownOption].operations} />
                  ) : (
                    <p className="text-sm text-muted-foreground">This decision does not write anything to the warehouse.</p>
                  )}
                </CardContent>
              </Card>
              <Card>
                <CardHeader>
                  <CardTitle>Validation</CardTitle>
                </CardHeader>
                <CardContent>
                  <Section title="Issues">
                    <IssueList issues={rec.issues} />
                  </Section>
                </CardContent>
              </Card>
            </div>
            <Card className="h-fit">
              <CardHeader>
                <CardTitle>Decision</CardTitle>
                <CardDescription>{api.mode === 'demo' ? 'Demo: stored in this browser only' : 'Recorded with your reviewer name and reason'}</CardDescription>
              </CardHeader>
              <CardContent>{planId && <DecisionPanel planId={planId} item={it} onDecided={item.reload} />}</CardContent>
            </Card>
          </div>
        </>
      )}
    </div>
  )
}
