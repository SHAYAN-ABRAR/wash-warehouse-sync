import * as React from 'react'
import { AlertOctagon, AlertTriangle, ArrowRight, CheckCircle2, CircleHelp, Info, MinusCircle, XCircle } from 'lucide-react'
import { Badge } from '@/components/ui/primitives'
import { Mono } from '@/components/common'
import { MATCH_LEVEL_META } from '@/lib/meta'
import type { CellChange, Evidence, Issue, MatchInfo, Operation } from '@/lib/types'
import { cn, fmtValue } from '@/lib/utils'

const SEVERITY = {
  ERROR: { icon: AlertOctagon, cls: 'text-destructive', tone: 'danger' as const },
  REVIEW: { icon: CircleHelp, cls: 'text-warning', tone: 'warning' as const },
  WARNING: { icon: AlertTriangle, cls: 'text-warning', tone: 'warning' as const },
  INFO: { icon: Info, cls: 'text-info', tone: 'info' as const },
}

export function IssueList({ issues }: { issues: Issue[] }) {
  if (!issues.length) return <p className="text-sm text-muted-foreground">No validation issues.</p>
  return (
    <ul className="space-y-1.5">
      {issues.map((i, k) => {
        const s = SEVERITY[i.severity] ?? SEVERITY.INFO
        return (
          <li key={k} className="flex items-start gap-2 text-sm">
            <s.icon className={cn('mt-0.5 h-4 w-4 shrink-0', s.cls)} aria-hidden />
            <div className="min-w-0">
              <span className="font-mono text-[11px] font-semibold">{i.code}</span>
              <span className="ml-1.5 text-muted-foreground">{i.message}</span>
            </div>
            <Badge tone={s.tone} className="ml-auto shrink-0">
              {i.severity}
            </Badge>
          </li>
        )
      })}
    </ul>
  )
}

const RESULT_STYLE: Record<Evidence['result'], { icon: typeof CheckCircle2; cls: string; label: string }> = {
  AGREE: { icon: CheckCircle2, cls: 'text-success', label: 'agrees' },
  MATCH: { icon: CheckCircle2, cls: 'text-success', label: 'match' },
  DISAGREE: { icon: XCircle, cls: 'text-destructive', label: 'disagrees' },
  NO_MATCH: { icon: MinusCircle, cls: 'text-muted-foreground', label: 'no match' },
  UNKNOWN: { icon: CircleHelp, cls: 'text-muted-foreground', label: 'unknown' },
  INFO: { icon: Info, cls: 'text-info', label: 'info' },
}

export function EvidenceList({ evidence, filterRow }: { evidence: Evidence[]; filterRow?: number }) {
  const rows = filterRow === undefined ? evidence : evidence.filter((e) => !e.check.startsWith('row') || e.check.startsWith(`row${filterRow}:`))
  if (!rows.length) return <p className="text-sm text-muted-foreground">No evidence recorded.</p>
  return (
    <ul className="divide-y rounded-lg border">
      {rows.map((e, k) => {
        const s = RESULT_STYLE[e.result] ?? RESULT_STYLE.INFO
        const check = e.check.replace(/^row\d+:/, '').replaceAll('_', ' ')
        return (
          <li key={k} className="flex items-center gap-3 px-3 py-2 text-sm">
            <s.icon className={cn('h-4 w-4 shrink-0', s.cls)} aria-label={s.label} />
            <span className="w-36 shrink-0 capitalize text-muted-foreground">{check}</span>
            <span className="min-w-0 flex-1 truncate font-mono text-xs" title={fmtValue(e.value)}>
              {fmtValue(e.value)}
            </span>
          </li>
        )
      })}
    </ul>
  )
}

export function MatchSummary({ match }: { match: MatchInfo }) {
  const meta = MATCH_LEVEL_META[match.level] ?? MATCH_LEVEL_META.NONE
  return (
    <div className="rounded-lg border bg-muted/30 p-3 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={match.confidence === 'EXACT' || match.confidence === 'VERIFIED' ? 'success' : match.confidence === 'POSSIBLE' ? 'warning' : 'neutral'}>
          {meta.label}
        </Badge>
        <span className="text-muted-foreground">confidence: {match.confidence.toLowerCase()}</span>
        {match.conflicts.map((c) => (
          <Badge key={c} tone="danger">
            {c.replaceAll('_', ' ').toLowerCase()}
          </Badge>
        ))}
      </div>
      <p className="mt-1.5 text-muted-foreground">{match.note ?? meta.description}</p>
    </div>
  )
}

const ACTION_TONE: Record<CellChange['action'], 'success' | 'info' | 'warning' | 'neutral'> = {
  INSERT: 'success',
  FILL: 'info',
  SET: 'warning',
  APPEND: 'neutral',
}

export function CellChanges({ operations }: { operations: Operation[] }) {
  const cells = operations.flatMap((o) => o.cells.map((c) => ({ ...c, op: o })))
  if (!cells.length) return <p className="text-sm text-muted-foreground">No cell changes.</p>
  return (
    <div className="overflow-hidden rounded-lg border">
      <table className="w-full text-sm">
        <thead className="bg-muted/60 text-xs uppercase tracking-wide text-muted-foreground">
          <tr>
            <th className="px-3 py-2 text-left font-semibold">Column</th>
            <th className="px-3 py-2 text-left font-semibold">Existing</th>
            <th className="w-6" />
            <th className="px-3 py-2 text-left font-semibold">Proposed</th>
            <th className="px-3 py-2 text-left font-semibold">Action</th>
          </tr>
        </thead>
        <tbody className="divide-y">
          {cells.map((c, k) => (
            <tr key={k} className={c.action === 'SET' ? 'bg-warning/[0.06]' : undefined}>
              <td className="max-w-[220px] truncate px-3 py-1.5 font-mono text-xs" title={c.column}>
                {c.column}
              </td>
              <td className="max-w-[200px] truncate px-3 py-1.5 text-muted-foreground" title={fmtValue(c.old)}>
                {c.old === null || c.old === undefined ? <span className="italic opacity-60">blank</span> : fmtValue(c.old)}
              </td>
              <td className="text-muted-foreground">
                <ArrowRight className="h-3.5 w-3.5" aria-hidden />
              </td>
              <td className="max-w-[240px] truncate px-3 py-1.5 font-medium" title={fmtValue(c.new)}>
                {fmtValue(c.new)}
                {c.raw && String(c.raw) !== String(c.new) && (
                  <span className="ml-1.5 text-[11px] font-normal text-muted-foreground">(source: {c.raw})</span>
                )}
              </td>
              <td className="px-3 py-1.5">
                <Badge tone={ACTION_TONE[c.action]}>{c.action.toLowerCase()}</Badge>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export function OperationSummary({ operations }: { operations: Operation[] }) {
  return (
    <ul className="space-y-1 text-sm">
      {operations.map((o) => (
        <li key={o.op_id} className="flex flex-wrap items-center gap-2">
          <Badge tone={o.kind === 'INSERT_ROW' ? 'success' : 'info'}>{o.kind === 'INSERT_ROW' ? 'Insert row' : 'Update row'}</Badge>
          {o.kind === 'INSERT_ROW' ? (
            <span className="text-muted-foreground">
              after row <Mono>{o.anchor_row}</Mono> — {o.anchor_explanation}
            </span>
          ) : o.target_row ? (
            <span className="text-muted-foreground">
              row <Mono>{o.target_row}</Mono> · {o.target_candidate_id}
            </span>
          ) : (
            <span className="text-muted-foreground">facility created in this run ({o.identity})</span>
          )}
          <span className="text-xs text-muted-foreground">· {o.cells.length} cells</span>
        </li>
      ))}
    </ul>
  )
}

export function SourceFields({ preview }: { preview: Record<string, unknown> }) {
  const entries = Object.entries(preview).filter(([, v]) => v !== null && v !== undefined && v !== '')
  return (
    <dl className="grid grid-cols-1 gap-x-4 gap-y-1.5 text-sm sm:grid-cols-2">
      {entries.map(([k, v]) => (
        <div key={k} className="flex min-w-0 justify-between gap-2 border-b border-dashed py-1">
          <dt className="shrink-0 text-muted-foreground">{k}</dt>
          <dd className="truncate text-right font-medium" title={fmtValue(v)}>
            {fmtValue(v)}
          </dd>
        </div>
      ))}
    </dl>
  )
}

export function Section({ title, children, className }: { title: string; children: React.ReactNode; className?: string }) {
  return (
    <section className={cn('space-y-2.5', className)}>
      <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">{title}</h3>
      {children}
    </section>
  )
}
