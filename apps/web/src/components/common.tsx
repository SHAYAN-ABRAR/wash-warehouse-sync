import * as React from 'react'
import { AlertCircle, Inbox, type LucideIcon } from 'lucide-react'
import { Badge, Card, InfoTip, Skeleton } from '@/components/ui/primitives'
import { Button } from '@/components/ui/button'
import { CLASS_META, SOURCE_META, TONE_CLASSES, type Tone } from '@/lib/meta'
import type { ChangeClass, Source } from '@/lib/types'
import { cn, fmtNum } from '@/lib/utils'

export function PageHeader({
  title,
  description,
  actions,
  eyebrow,
}: {
  title: string
  description?: React.ReactNode
  actions?: React.ReactNode
  eyebrow?: React.ReactNode
}) {
  return (
    <div className="flex flex-col gap-4 pb-6 md:flex-row md:items-end md:justify-between">
      <div className="min-w-0 space-y-1.5">
        {eyebrow && <div className="text-xs font-medium uppercase tracking-wider text-primary">{eyebrow}</div>}
        <h1 className="text-2xl font-semibold tracking-tight text-balance md:text-[28px]">{title}</h1>
        {description && <p className="max-w-3xl text-sm text-muted-foreground md:text-[15px]">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  )
}

export function StatCard({
  label,
  value,
  hint,
  icon: Icon,
  tone = 'teal',
  info,
  footer,
  className,
}: {
  label: string
  value: React.ReactNode
  hint?: React.ReactNode
  icon?: LucideIcon
  tone?: Tone
  info?: string
  footer?: React.ReactNode
  className?: string
}) {
  const t = TONE_CLASSES[tone]
  return (
    <Card className={cn('relative overflow-hidden p-5 animate-fade-in', className)}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 space-y-1">
          <div className="flex items-center gap-1.5 text-[13px] font-medium text-muted-foreground">
            {label}
            {info && <InfoTip text={info} />}
          </div>
          <div className="text-2xl font-semibold tracking-tight tabular">{value}</div>
          {hint && <div className="text-xs text-muted-foreground">{hint}</div>}
        </div>
        {Icon && (
          <div className={cn('flex h-9 w-9 shrink-0 items-center justify-center rounded-lg', t.soft)}>
            <Icon className={cn('h-[18px] w-[18px]', t.badge.split(' ').find((c) => c.startsWith('text-')))} aria-hidden />
          </div>
        )}
      </div>
      {footer && <div className="mt-3 border-t pt-3 text-xs text-muted-foreground">{footer}</div>}
    </Card>
  )
}

export function ClassBadge({ cls, count, className }: { cls: ChangeClass; count?: number; className?: string }) {
  const meta = CLASS_META[cls]
  const Icon = meta.icon
  return (
    <Badge tone={meta.tone} className={className} title={meta.description}>
      <Icon className="h-3 w-3" aria-hidden />
      {meta.label}
      {count !== undefined && <span className="tabular opacity-80">· {fmtNum(count)}</span>}
    </Badge>
  )
}

export function SourceBadge({ source }: { source: Source | string | null | undefined }) {
  if (!source) return <Badge>Unknown</Badge>
  const meta = SOURCE_META[source as Source]
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs font-medium">
      <span className="h-2 w-2 rounded-full" style={{ background: meta?.color ?? 'gray' }} aria-hidden />
      {meta?.label ?? source}
    </span>
  )
}

export function EmptyState({
  icon: Icon = Inbox,
  title,
  description,
  action,
  className,
}: {
  icon?: LucideIcon
  title: string
  description?: React.ReactNode
  action?: React.ReactNode
  className?: string
}) {
  return (
    <div className={cn('flex flex-col items-center justify-center rounded-xl border border-dashed px-6 py-14 text-center', className)}>
      <div className="mb-3 flex h-11 w-11 items-center justify-center rounded-full bg-muted">
        <Icon className="h-5 w-5 text-muted-foreground" aria-hidden />
      </div>
      <h3 className="font-semibold">{title}</h3>
      {description && <p className="mt-1 max-w-md text-sm text-muted-foreground">{description}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  )
}

export function ErrorState({ error, onRetry, title = 'Something went wrong' }: { error: Error; onRetry?: () => void; title?: string }) {
  return (
    <div role="alert" className="flex items-start gap-3 rounded-xl border border-destructive/30 bg-destructive/5 p-4 text-sm">
      <AlertCircle className="mt-0.5 h-4 w-4 shrink-0 text-destructive" aria-hidden />
      <div className="min-w-0 flex-1">
        <div className="font-medium text-destructive">{title}</div>
        <div className="mt-0.5 break-words text-muted-foreground">{error.message}</div>
      </div>
      {onRetry && (
        <Button size="sm" variant="outline" onClick={onRetry}>
          Retry
        </Button>
      )}
    </div>
  )
}

export function LoadingBlock({ rows = 3, className }: { rows?: number; className?: string }) {
  return (
    <div className={cn('space-y-3', className)} aria-busy="true" aria-label="Loading">
      {Array.from({ length: rows }).map((_, i) => (
        <Skeleton key={i} className="h-10 w-full" />
      ))}
    </div>
  )
}

export function KeyValue({ items, className }: { items: [React.ReactNode, React.ReactNode][]; className?: string }) {
  return (
    <dl className={cn('grid grid-cols-1 gap-x-6 gap-y-2.5 text-sm sm:grid-cols-2', className)}>
      {items.map(([k, v], i) => (
        <div key={i} className="flex min-w-0 items-baseline justify-between gap-3 border-b border-dashed pb-2 sm:block sm:border-0 sm:pb-0">
          <dt className="shrink-0 text-xs font-medium uppercase tracking-wide text-muted-foreground">{k}</dt>
          <dd className="min-w-0 break-words text-right font-medium sm:mt-0.5 sm:text-left">{v}</dd>
        </div>
      ))}
    </dl>
  )
}

export function SectionTitle({ children, info, right }: { children: React.ReactNode; info?: string; right?: React.ReactNode }) {
  return (
    <div className="mb-3 flex items-center justify-between gap-2">
      <h2 className="flex items-center gap-1.5 text-sm font-semibold uppercase tracking-wide text-muted-foreground">
        {children}
        {info && <InfoTip text={info} />}
      </h2>
      {right}
    </div>
  )
}

export function Mono({ children, className }: { children: React.ReactNode; className?: string }) {
  return <code className={cn('rounded bg-muted px-1.5 py-0.5 font-mono text-[12px]', className)}>{children}</code>
}
