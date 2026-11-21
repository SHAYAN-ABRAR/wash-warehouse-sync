import * as React from 'react'
import { Bar, BarChart, Cell, Pie, PieChart, ResponsiveContainer, Tooltip as RTooltip, XAxis, YAxis } from 'recharts'
import { AlertTriangle, Copy, FlaskConical, MapPinOff, Ruler, Shuffle } from 'lucide-react'
import { ErrorState, LoadingBlock, PageHeader, StatCard } from '@/components/common'
import { Badge, Card, CardContent, CardDescription, CardHeader, CardTitle, Input } from '@/components/ui/primitives'
import { useData, useResource } from '@/lib/api'
import { cn, fmtNum, pct } from '@/lib/utils'

const GPS_COLORS: Record<string, string> = {
  valid: 'hsl(160 65% 38%)',
  missing: 'hsl(215 16% 62%)',
  reversed: 'hsl(36 92% 48%)',
  invalid: 'hsl(0 72% 52%)',
}

export default function Quality() {
  const api = useData()
  const quality = useResource(() => api.quality(), [api])
  const [colFilter, setColFilter] = React.useState('')
  if (quality.error) return <ErrorState error={quality.error} onRetry={quality.reload} />
  if (!quality.data) return <LoadingBlock rows={8} />
  const { quality: q, columns, rows } = quality.data
  const gps = Object.entries(q.gps).map(([name, value]) => ({ name, value }))
  const missingAdmin = Object.entries(q.missing_admin).map(([k, v]) => ({ name: k.replace('_name', ''), value: v }))
  const flags = q.top_flags.filter(([f]) => f).slice(0, 12).map(([name, value]) => ({ name, value }))
  const outlierTotal = Object.values(q.outliers).reduce((a, o) => a + o.count, 0)
  const dupTotal = Object.values(q.duplicate_identifiers).reduce((a, b) => a + b, 0)
  const cols = columns
    .filter((c) => !colFilter || c.name.includes(colFilter.toLowerCase()))
    .map((c) => ({ ...c, fill: rows ? c.nonempty / rows : 0 }))

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Warehouse"
        title="Data quality"
        description="Source-aware quality profile of the current version: GPS validity, administrative completeness, duplicate identifiers, implausible values and chemistry unit issues. Values are flagged, never deleted."
      />
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
        <StatCard label="Invalid or reversed GPS" value={fmtNum((q.gps.invalid ?? 0) + (q.gps.reversed ?? 0))} hint={`${pct((q.gps.invalid ?? 0) + (q.gps.reversed ?? 0), rows)} of rows · raw values preserved`} icon={MapPinOff} tone="danger" />
        <StatCard label="Missing GPS" value={fmtNum(q.gps.missing ?? 0)} hint={`${pct(q.gps.missing ?? 0, rows)} of rows — geographic checks not possible`} icon={MapPinOff} tone="neutral" />
        <StatCard label="Duplicate identifiers" value={fmtNum(dupTotal)} hint="IDs that appear on more than one row (by identifier type)" icon={Copy} tone="warning" />
        <StatCard label="Implausible values" value={fmtNum(outlierTotal + q.water_table_outliers)} hint="Depth, chemistry and water-table values outside plausible ranges" icon={Ruler} tone="warning" />
        <StatCard label="Chemistry unit issues" value={fmtNum(q.unit_issue_flags)} hint="Rows flagged for unspecified or unverified units" icon={FlaskConical} tone="violet" />
        <StatCard label="Shared exact coordinates" value={fmtNum(q.shared_exact_coordinates)} hint="Coordinate pairs used by several rows — never merged automatically" icon={Shuffle} tone="info" />
      </div>
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        <Card>
          <CardHeader>
            <CardTitle>GPS status</CardTitle>
            <CardDescription>Validated against the Bangladesh bounding box</CardDescription>
          </CardHeader>
          <CardContent className="h-[240px]">
            <ResponsiveContainer width="100%" height="100%">
              <PieChart>
                <Pie data={gps} dataKey="value" nameKey="name" innerRadius={55} outerRadius={85} paddingAngle={2}>
                  {gps.map((g) => (
                    <Cell key={g.name} fill={GPS_COLORS[g.name] ?? 'gray'} />
                  ))}
                </Pie>
                <RTooltip formatter={(v: number) => fmtNum(v)} contentStyle={{ background: 'hsl(var(--popover))', border: '1px solid hsl(var(--border))', borderRadius: 8, fontSize: 12 }} />
              </PieChart>
            </ResponsiveContainer>
          </CardContent>
          <CardContent className="flex flex-wrap gap-2 pt-0">
            {gps.map((g) => (
              <span key={g.name} className="inline-flex items-center gap-1.5 text-xs">
                <span className="h-2 w-2 rounded-full" style={{ background: GPS_COLORS[g.name] }} />
                {g.name} · {fmtNum(g.value)}
              </span>
            ))}
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Missing administrative names</CardTitle>
            <CardDescription>Rows with a blank level</CardDescription>
          </CardHeader>
          <CardContent className="h-[280px]">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={missingAdmin} margin={{ left: -8 }}>
                <XAxis dataKey="name" tick={{ fontSize: 11, fill: 'hsl(var(--muted-foreground))' }} axisLine={false} tickLine={false} />
                <YAxis tick={{ fontSize: 11, fill: 'hsl(var(--muted-foreground))' }} axisLine={false} tickLine={false} />
                <RTooltip formatter={(v: number) => fmtNum(v)} contentStyle={{ background: 'hsl(var(--popover))', border: '1px solid hsl(var(--border))', borderRadius: 8, fontSize: 12 }} />
                <Bar dataKey="value" fill="hsl(var(--primary))" radius={[6, 6, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Implausible values by column</CardTitle>
            <CardDescription>Flagged for review; originals are preserved</CardDescription>
          </CardHeader>
          <CardContent className="space-y-2">
            {Object.entries(q.outliers).length === 0 && <p className="text-sm text-muted-foreground">No implausible values detected.</p>}
            {Object.entries(q.outliers).map(([col, o]) => (
              <div key={col} className="flex items-center justify-between gap-3 rounded-lg border px-3 py-2 text-sm">
                <div className="min-w-0">
                  <div className="truncate font-mono text-xs">{col}</div>
                  <div className="text-xs text-muted-foreground">
                    expected {o.rule[0]}–{o.rule[1]} {o.rule[2]}
                  </div>
                </div>
                <Badge tone="warning">
                  <AlertTriangle className="h-3 w-3" /> {fmtNum(o.count)}
                </Badge>
              </div>
            ))}
            {q.water_table_outliers > 0 && (
              <div className="flex items-center justify-between rounded-lg border px-3 py-2 text-sm">
                <span className="font-mono text-xs">*_water_table_m</span>
                <Badge tone="warning">{fmtNum(q.water_table_outliers)}</Badge>
              </div>
            )}
          </CardContent>
        </Card>
      </div>
      <Card>
        <CardHeader>
          <CardTitle>Most frequent quality flags</CardTitle>
          <CardDescription>Tokens from the quality_flags column</CardDescription>
        </CardHeader>
        <CardContent className="h-[360px]">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={flags} layout="vertical" margin={{ left: 12, right: 24 }}>
              <XAxis type="number" tick={{ fontSize: 11, fill: 'hsl(var(--muted-foreground))' }} axisLine={false} tickLine={false} />
              <YAxis type="category" dataKey="name" width={260} tick={{ fontSize: 10.5, fill: 'hsl(var(--muted-foreground))' }} axisLine={false} tickLine={false} />
              <RTooltip formatter={(v: number) => fmtNum(v)} contentStyle={{ background: 'hsl(var(--popover))', border: '1px solid hsl(var(--border))', borderRadius: 8, fontSize: 12 }} />
              <Bar dataKey="value" fill="hsl(36 92% 48%)" radius={[0, 6, 6, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </CardContent>
      </Card>
      <Card>
        <CardHeader className="gap-3 md:flex-row md:items-end md:justify-between">
          <div>
            <CardTitle>Column completeness</CardTitle>
            <CardDescription>Share of rows with a value. Source-specific columns are naturally sparse.</CardDescription>
          </div>
          <Input placeholder="Filter columns…" value={colFilter} onChange={(e) => setColFilter(e.target.value)} className="w-56" aria-label="Filter columns" />
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-1 max-h-[480px] gap-x-6 gap-y-2 overflow-y-auto pr-1 md:grid-cols-2 xl:grid-cols-3">
            {cols.map((c) => (
              <div key={c.name} className="text-xs">
                <div className="flex justify-between gap-2">
                  <span className="truncate font-mono" title={c.name}>
                    {c.name}
                  </span>
                  <span className="tabular text-muted-foreground">{pct(c.nonempty, rows)}</span>
                </div>
                <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-muted">
                  <div className={cn('h-full rounded-full', c.fill > 0.9 ? 'bg-success' : c.fill > 0.2 ? 'bg-primary' : 'bg-muted-foreground/50')} style={{ width: `${Math.max(1, c.fill * 100)}%` }} />
                </div>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
