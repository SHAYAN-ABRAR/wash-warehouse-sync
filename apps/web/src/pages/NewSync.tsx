import * as React from 'react'
import { Link, useNavigate } from 'react-router-dom'
import type { ColumnDef } from '@tanstack/react-table'
import { toast } from 'sonner'
import {
  ArrowUp,
  CheckCircle2,
  Download,
  FileSpreadsheet,
  FlaskConical,
  Loader2,
  Lock,
  Play,
  ScanSearch,
  ShieldAlert,
  Sparkles,
  Trash2,
  UploadCloud,
  X,
} from 'lucide-react'
import { ClassBadge, ErrorState, LoadingBlock, PageHeader, SourceBadge } from '@/components/common'
import { CurrentWarehouse } from '@/components/CurrentWarehouse'
import { DataTable } from '@/components/DataTable'
import {
  Badge,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  Input,
  Label,
  NativeSelect,
  Progress,
  Switch,
  Tooltip,
} from '@/components/ui/primitives'
import { Button } from '@/components/ui/button'
import { useData, useJob, useResource } from '@/lib/api'
import type { BrowserPreviewResult, BrowserRecord } from '@/lib/browserPreview'
import { CLASS_ORDER } from '@/lib/meta'
import { assetUrl } from '@/lib/runtime'
import type { Detection, Source, UploadResult } from '@/lib/types'
import { cn, downloadText, fmtBytes, fmtNum, periodLabel } from '@/lib/utils'

type SourceChoice = 'AUTO' | Source

const SOURCE_OPTIONS: { id: SourceChoice; title: string }[] = [
  { id: 'AUTO', title: 'Auto-detect from the header row (recommended)' },
  { id: 'AQUAWATCH', title: 'AquaWatch — annual/seasonal surveys, historical GWC table' },
  { id: 'GPANI', title: 'GPANI — tubewell and community-facility reports' },
  { id: 'WPMS', title: 'WPMS — tubewell lists' },
  { id: 'BORELOG', title: 'Bore Log — five-tab workbook' },
]

interface Staged {
  key: string
  file: File
  status: 'reading' | 'ready' | 'error'
  upload?: UploadResult
  browser?: BrowserPreviewResult
  error?: string
}

function DetectionSummary({ det }: { det: Detection | BrowserPreviewResult['detection'] }) {
  const missing = 'missing_required' in det ? det.missing_required : det.missingRequired
  const pii = 'personal_data_columns' in det ? det.personal_data_columns : det.personalData
  const optional = 'missing_optional' in det ? det.missing_optional : det.missingOptional
  return (
    <div className="space-y-2 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        {det.source ? <SourceBadge source={det.source} /> : <Badge tone="danger">Not recognized</Badge>}
        {det.format && <span className="text-muted-foreground">{det.format}</span>}
        <Badge tone={det.confidence >= 0.8 ? 'success' : det.confidence > 0 ? 'warning' : 'danger'}>confidence {Math.round(det.confidence * 100)}%</Badge>
      </div>
      <p className="text-muted-foreground">{det.reason}</p>
      {missing.length > 0 && <p className="text-destructive">Missing required columns: {missing.join(', ')}</p>}
      {Object.keys(det.matched).length > 0 && (
        <details className="group">
          <summary className="cursor-pointer text-xs font-medium text-primary">Column mapping ({Object.keys(det.matched).length} fields)</summary>
          <div className="mt-2 grid grid-cols-1 gap-1 sm:grid-cols-2">
            {Object.entries(det.matched).map(([field, header]) => (
              <div key={field} className="flex justify-between gap-2 rounded border px-2 py-1 text-xs">
                <span className="font-mono">{field}</span>
                <span className="truncate text-muted-foreground">← {header}</span>
              </div>
            ))}
          </div>
          {optional.length > 0 && <p className="mt-2 text-xs text-muted-foreground">Not in this file (optional): {optional.join(', ')}</p>}
        </details>
      )}
      {pii.length > 0 && (
        <p className="flex items-start gap-1.5 text-xs text-muted-foreground">
          <Lock className="mt-0.5 h-3 w-3 shrink-0" /> Personal-data columns are never read: {pii.join(', ')}
        </p>
      )}
    </div>
  )
}

export default function NewSync() {
  const api = useData()
  const navigate = useNavigate()
  const status = useResource(() => api.status(), [api])
  const hasWarehouse = !!status.data?.current_version
  const [source, setSource] = React.useState<SourceChoice>('AUTO')
  const [period, setPeriod] = React.useState('')
  const [strict, setStrict] = React.useState(false)
  const [files, setFiles] = React.useState<Staged[]>([])
  const [dragging, setDragging] = React.useState(false)
  const [jobId, setJobId] = React.useState<string | null>(null)
  const inputRef = React.useRef<HTMLInputElement>(null)
  const workerRef = React.useRef<Worker | null>(null)
  const samples = useResource(
    () => (api.mode === 'demo' ? fetch(assetUrl('demo/samples.json')).then((r) => r.json() as Promise<{ file: string; title: string; description: string }[]>) : Promise.resolve([])),
    [api],
  )
  const job = useJob(api, jobId, (j) => {
    if (j.status === 'succeeded') {
      toast.success('Dry run complete')
      navigate(`/sync/preview/${j.result.plan_id}`)
    } else toast.error(j.error ?? `Dry run ${j.status}`)
  })

  React.useEffect(() => () => workerRef.current?.terminate(), [])

  const runBrowser = React.useCallback(
    (file: File): Promise<BrowserPreviewResult> =>
      new Promise((resolve, reject) => {
        workerRef.current ??= new Worker(new URL('../workers/preview.worker.ts', import.meta.url), { type: 'module' })
        const worker = workerRef.current
        file.arrayBuffer().then((data) => {
          worker.onmessage = (e: MessageEvent<{ ok: boolean; result?: BrowserPreviewResult; error?: string }>) =>
            e.data.ok ? resolve(e.data.result!) : reject(new Error(e.data.error))
          worker.postMessage({ name: file.name, data, hint: source === 'AUTO' ? undefined : source, warehouseUrl: assetUrl('demo/warehouse.json') }, [data])
        })
      }),
    [source],
  )

  async function stage(list: FileList | File[]) {
    const incoming = Array.from(list).filter((f) => /\.(xlsx|xlsm|xls|csv)$/i.test(f.name))
    if (!incoming.length) {
      toast.error('Choose .xlsx, .xls or .csv source exports')
      return
    }
    for (const file of incoming) {
      const key = `${file.name}-${file.size}-${file.lastModified}-${Math.random()}`
      setFiles((fs) => [...fs, { key, file, status: 'reading' }])
      try {
        if (api.mode === 'local') {
          const upload = await api.upload(file)
          setFiles((fs) => fs.map((f) => (f.key === key ? { ...f, status: 'ready', upload } : f)))
        } else {
          const browser = await runBrowser(file)
          setFiles((fs) => fs.map((f) => (f.key === key ? { ...f, status: 'ready', browser } : f)))
        }
      } catch (e) {
        setFiles((fs) => fs.map((f) => (f.key === key ? { ...f, status: 'error', error: (e as Error).message } : f)))
      }
    }
  }

  async function trySample(file: string) {
    const res = await fetch(assetUrl(`demo/samples/${file}`))
    if (!res.ok) {
      toast.error('Sample could not be loaded')
      return
    }
    const blob = await res.blob()
    await stage([new File([blob], file, { type: blob.type })])
  }

  async function runDryRun() {
    const ready = files.filter((f) => f.status === 'ready' && f.upload)
    try {
      const j = await api.preview(
        ready.map((f) => f.upload!.upload_id),
        { source: source === 'AUTO' ? undefined : source, period: period || undefined, strict },
      )
      setJobId(j.job_id)
    } catch (e) {
      toast.error((e as Error).message)
    }
  }

  const ready = files.filter((f) => f.status === 'ready')
  const recognized = ready.filter((f) => (f.upload?.detection.source ?? f.browser?.detection.source) && !(f.upload?.detection.missing_required.length ?? f.browser?.detection.missingRequired.length))
  const step = !hasWarehouse ? 1 : jobId ? 4 : ready.length ? 3 : 2

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Synchronize"
        title="New synchronization"
        description="Two inputs: the current warehouse (step 1) and the new files to sync (step 2). Every incoming record is compared with the warehouse — records already in it are recognized and skipped, so nothing is added twice. Nothing is written until you approve."
      />
      <ol className="grid grid-cols-1 gap-2 sm:grid-cols-4" aria-label="Steps">
        {['Current warehouse', 'Files to sync', 'Check detection', 'Dry run'].map((label, i) => (
          <li
            key={label}
            className={cn(
              'flex items-center gap-2 rounded-lg border px-3 py-2 text-sm',
              i + 1 < step ? 'border-success/30 bg-success/5' : i + 1 === step ? 'border-primary bg-accent/60 font-medium' : 'text-muted-foreground',
            )}
          >
            <span className={cn('flex h-6 w-6 items-center justify-center rounded-full text-xs font-semibold', i + 1 < step ? 'bg-success text-success-foreground' : i + 1 === step ? 'bg-primary text-primary-foreground' : 'bg-muted')}>
              {i + 1 < step ? <CheckCircle2 className="h-3.5 w-3.5" /> : i + 1}
            </span>
            {label}
          </li>
        ))}
      </ol>

      <Card>
        <CardHeader>
          <CardTitle>1 · Current warehouse</CardTitle>
          <CardDescription>The warehouse that the new files are compared with. After it is loaded, every page — explorer, data quality, history — shows it.</CardDescription>
        </CardHeader>
        <CardContent>{status.loading && !status.data ? <LoadingBlock rows={2} /> : <CurrentWarehouse status={status.data} onStatusStale={status.reload} />}</CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>2 · Files to sync</CardTitle>
          <CardDescription>
            The new GPANI, WPMS, AquaWatch or bore-log exports to bring into the warehouse.{' '}
            {api.mode === 'local'
              ? 'Files are copied into the private state folder on this computer — never to an external service.'
              : 'On this demo website files are read entirely inside your browser and never uploaded.'}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {api.mode === 'local' && !hasWarehouse ? (
            <div className="flex items-center gap-3 rounded-xl border border-dashed p-6 text-sm text-muted-foreground">
              <ArrowUp className="h-4 w-4 shrink-0" aria-hidden /> Load the current warehouse in step 1 first — new files are compared with it.
            </div>
          ) : (
            <>
              <div className="flex flex-col gap-1.5 sm:flex-row sm:items-center sm:gap-3">
                <Label htmlFor="source-system" className="shrink-0">
                  Source system
                </Label>
                <NativeSelect id="source-system" value={source} onChange={(e) => setSource(e.target.value as SourceChoice)} className="max-w-full sm:max-w-md">
                  {SOURCE_OPTIONS.map((o) => (
                    <option key={o.id} value={o.id}>
                      {o.title}
                    </option>
                  ))}
                </NativeSelect>
              </div>
              <div
                onDragOver={(e) => {
                  e.preventDefault()
                  setDragging(true)
                }}
                onDragLeave={() => setDragging(false)}
                onDrop={(e) => {
                  e.preventDefault()
                  setDragging(false)
                  stage(e.dataTransfer.files)
                }}
                className={cn(
                  'relative flex flex-col items-center justify-center rounded-xl border-2 border-dashed px-6 py-12 text-center transition-colors',
                  dragging ? 'border-primary bg-accent/50' : 'border-border bg-muted/20 hover:bg-muted/40',
                )}
              >
                <div className="mb-3 flex h-12 w-12 items-center justify-center rounded-full bg-primary/10">
                  <UploadCloud className="h-6 w-6 text-primary" />
                </div>
                <p className="font-medium">Drag & drop source exports here</p>
                <p className="mt-1 text-sm text-muted-foreground">.xlsx, .xls or .csv {api.mode === 'demo' && '(browser preview: .xlsx and .csv)'}</p>
                <Button className="mt-4" variant="outline" onClick={() => inputRef.current?.click()}>
                  Browse files
                </Button>
                <input
                  ref={inputRef}
                  type="file"
                  multiple
                  accept=".xlsx,.xlsm,.xls,.csv"
                  className="sr-only"
                  aria-label="Choose source files"
                  onChange={(e) => e.target.files && stage(e.target.files)}
                />
              </div>

              {api.mode === 'demo' && samples.data && samples.data.length > 0 && (
                <div className="rounded-xl border bg-muted/20 p-4">
                  <div className="mb-3 flex items-center gap-2 text-sm font-medium">
                    <FlaskConical className="h-4 w-4 text-primary" /> Synthetic sample exports
                  </div>
                  <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
                    {samples.data.map((s) => (
                      <div key={s.file} className="flex items-center justify-between gap-3 rounded-lg border bg-card p-3">
                        <div className="min-w-0">
                          <div className="truncate text-sm font-medium">{s.title}</div>
                          <div className="truncate text-xs text-muted-foreground">{s.description}</div>
                        </div>
                        <div className="flex shrink-0 gap-1">
                          <Tooltip content="Download sample">
                            <Button variant="ghost" size="icon" asChild>
                              <a href={assetUrl(`demo/samples/${s.file}`)} download aria-label={`Download ${s.file}`}>
                                <Download />
                              </a>
                            </Button>
                          </Tooltip>
                          <Button size="sm" variant="outline" onClick={() => trySample(s.file)} disabled={!/\.(csv|xlsx)$/i.test(s.file)}>
                            Try it
                          </Button>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {files.length > 0 && (
                <ul className="space-y-3">
                  {files.map((f) => (
                    <li key={f.key} className="rounded-xl border p-4">
                      <div className="flex items-start justify-between gap-3">
                        <div className="flex min-w-0 items-center gap-3">
                          <FileSpreadsheet className="h-5 w-5 shrink-0 text-muted-foreground" />
                          <div className="min-w-0">
                            <div className="truncate font-medium">{f.file.name}</div>
                            <div className="text-xs text-muted-foreground">
                              {fmtBytes(f.file.size)}
                              {f.upload && ` · SHA-256 ${f.upload.sha256.slice(0, 12)}…`}
                              {f.browser && ` · ${fmtNum(f.browser.totalRows)} data rows`}
                            </div>
                          </div>
                        </div>
                        <div className="flex items-center gap-2">
                          {f.status === 'reading' && (
                            <Badge tone="info">
                              <Loader2 className="h-3 w-3 animate-spin" /> reading
                            </Badge>
                          )}
                          <Button variant="ghost" size="icon" onClick={() => setFiles((fs) => fs.filter((x) => x.key !== f.key))} aria-label={`Remove ${f.file.name}`}>
                            <Trash2 />
                          </Button>
                        </div>
                      </div>
                      {f.status === 'error' && <p className="mt-3 text-sm text-destructive">{f.error}</p>}
                      {f.upload && (
                        <div className="mt-3">
                          <DetectionSummary det={f.upload.detection} />
                        </div>
                      )}
                      {f.browser && <BrowserResult result={f.browser} />}
                    </li>
                  ))}
                </ul>
              )}
            </>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>3 · Options and dry run</CardTitle>
          <CardDescription>The dry run compares every record with the current warehouse version and writes a plan plus audit. The warehouse is not modified.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-5">
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="period">Expected survey period (optional)</Label>
              <Input id="period" placeholder="e.g. 2027 dry, 2027 wet or 2027" value={period} onChange={(e) => setPeriod(e.target.value)} />
              <p className="text-xs text-muted-foreground">Rows reporting a different period are routed to review instead of being written.</p>
            </div>
            <div className="flex items-start justify-between gap-4 rounded-lg border p-3">
              <div>
                <Label htmlFor="strict">Strict validation</Label>
                <p className="mt-1 text-xs text-muted-foreground">Escalates configured warnings (invalid GPS, missing type, implausible values…) to rejections.</p>
              </div>
              <Switch id="strict" checked={strict} onCheckedChange={setStrict} />
            </div>
          </div>
          {job && (
            <div className="rounded-lg border p-4">
              <div className="mb-2 flex items-center justify-between text-sm">
                <span className="font-medium">{job.message || 'Starting…'}</span>
                <span className="tabular text-muted-foreground">{Math.round(job.fraction * 100)}%</span>
              </div>
              <Progress value={job.fraction * 100} label="Dry run progress" />
              <div className="mt-2 flex items-center justify-between text-xs text-muted-foreground">
                <span>
                  Phase: {job.phase || 'queued'} · {job.elapsed}s
                </span>
                {job.status === 'running' && (
                  <Button size="sm" variant="ghost" onClick={() => api.cancelJob(job.job_id)}>
                    <X /> Cancel
                  </Button>
                )}
              </div>
              {job.error && <ErrorState error={new Error(job.error)} title={`Dry run ${job.status}`} />}
            </div>
          )}
          <div className="flex flex-wrap items-center justify-between gap-3">
            <p className="flex items-center gap-1.5 text-sm text-muted-foreground">
              <ShieldAlert className="h-4 w-4" /> {recognized.length} of {files.length} file(s) recognized
            </p>
            {api.mode === 'local' ? (
              <Button size="lg" onClick={runDryRun} disabled={!recognized.length || (job?.status === 'running')}>
                <Play /> Run dry-run comparison
              </Button>
            ) : (
              <div className="flex flex-wrap gap-2">
                <Button variant="outline" asChild>
                  <Link to="/sync/preview">
                    <ScanSearch /> Open the engine-generated demo plan
                  </Link>
                </Button>
                <Tooltip content="The full dry run (and apply) runs in Local Production Mode: wash-sync serve">
                  <span>
                    <Button size="lg" disabled>
                      <Play /> Run engine dry run
                    </Button>
                  </span>
                </Tooltip>
              </div>
            )}
          </div>
        </CardContent>
      </Card>
    </div>
  )
}

function BrowserResult({ result }: { result: BrowserPreviewResult }) {
  const columns = React.useMemo<ColumnDef<BrowserRecord>[]>(
    () => [
      { header: 'Row', size: 60, cell: ({ row }) => <span className="tabular text-xs text-muted-foreground">{row.original.row}</span> },
      { header: 'Classification', size: 190, cell: ({ row }) => <ClassBadge cls={row.original.classification} /> },
      { header: 'Source ID', size: 180, cell: ({ row }) => <span className="font-mono text-xs">{row.original.id ?? '—'}</span> },
      { header: 'Period', size: 120, cell: ({ row }) => <span className="text-xs">{row.original.period ? periodLabel(row.original.period) : '—'}</span> },
      { header: 'Reason', cell: ({ row }) => <span className="text-sm text-muted-foreground">{row.original.reason}</span> },
    ],
    [],
  )
  return (
    <div className="mt-4 space-y-4">
      <DetectionSummary det={result.detection} />
      {result.detection.source && (
        <>
          <div className="rounded-lg border border-primary/20 bg-accent/40 p-3 text-xs text-accent-foreground">
            <Sparkles className="mr-1 inline h-3.5 w-3.5" /> Browser preview — simplified rules against the synthetic demo warehouse. The engine applies the full
            matching, validation and change-detection rules.
          </div>
          {Object.keys(result.periods).length > 0 && (
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <span className="text-muted-foreground">Detected periods:</span>
              {Object.entries(result.periods).map(([p, n]) => (
                <Badge key={p} tone={result.newPeriods.includes(p) ? 'success' : 'info'}>
                  {periodLabel(p)} · {fmtNum(n)}
                  {result.newPeriods.includes(p) && ' · new columns'}
                </Badge>
              ))}
            </div>
          )}
          <div className="flex flex-wrap gap-2">
            {CLASS_ORDER.filter((c) => result.counts[c]).map((c) => (
              <ClassBadge key={c} cls={c} count={result.counts[c]} />
            ))}
          </div>
          <details>
            <summary className="cursor-pointer text-xs font-medium text-primary">Completeness by field</summary>
            <div className="mt-2 grid grid-cols-2 gap-1 sm:grid-cols-4">
              {Object.entries(result.completeness)
                .filter(([f]) => f in result.detection.matched)
                .map(([f, missing]) => (
                  <div key={f} className="rounded border px-2 py-1 text-xs">
                    <span className="font-mono">{f}</span>
                    <span className={cn('float-right tabular', missing ? 'text-warning' : 'text-success')}>{missing ? `${missing} blank` : 'complete'}</span>
                  </div>
                ))}
            </div>
          </details>
          <DataTable ariaLabel={`Preview of ${result.fileName}`} data={result.records} columns={columns} height={360} getRowId={(r) => String(r.row)} />
          <Button variant="ghost" size="sm" onClick={() => downloadText(`${result.fileName}.browser-preview.json`, JSON.stringify(result, null, 2))}>
            <Download /> Download preview (JSON)
          </Button>
        </>
      )}
    </div>
  )
}
