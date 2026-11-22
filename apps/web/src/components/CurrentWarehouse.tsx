import * as React from 'react'
import { Link } from 'react-router-dom'
import { toast } from 'sonner'
import { Database, FileSpreadsheet, Laptop, Loader2, RefreshCw, UploadCloud, X } from 'lucide-react'
import { Badge, Input, Label, Progress } from '@/components/ui/primitives'
import { Button } from '@/components/ui/button'
import { useData, useJob, useRefreshAll } from '@/lib/api'
import { VERSION_KIND_META, versionLabel } from '@/lib/meta'
import type { StatusResponse, VersionRecord } from '@/lib/types'
import { cn, fmtBytes, fmtDate, fmtNum } from '@/lib/utils'

const PHASES: Record<string, string> = {
  verify: 'Step 1 of 3 · Checking the workbook',
  copy: 'Step 2 of 3 · Copying it into the private state folder',
  index: 'Step 3 of 3 · Preparing it for search and matching',
  done: 'Done',
}

/**
 * Step 1 of a sync: the warehouse that new files are compared with, and the way to load a newer
 * one. In the public demo it shows the synthetic sample and explains where real loading happens.
 */
export function CurrentWarehouse({ status, onStatusStale }: { status: StatusResponse | undefined; onStatusStale?: () => void }) {
  const api = useData()
  const refreshAll = useRefreshAll()
  const current = status?.current_version ?? null
  const [open, setOpen] = React.useState(false)
  const [file, setFile] = React.useState<File | null>(null)
  const [reason, setReason] = React.useState('')
  const [sending, setSending] = React.useState(false)
  const [jobId, setJobId] = React.useState<string | null>(null)
  const [error, setError] = React.useState<string | null>(null)

  // Resume following a load that is still running (for example after visiting another page).
  React.useEffect(() => {
    if (!jobId && status?.active_job?.kind === 'load') setJobId(status.active_job.job_id)
  }, [status, jobId])

  // While the server prepares the current version for search, refresh its status every few seconds.
  React.useEffect(() => {
    if (!onStatusStale || !status?.index || status.index.ready) return
    const t = setTimeout(onStatusStale, 3000)
    return () => clearTimeout(t)
  }, [status, onStatusStale])

  const job = useJob(api, jobId, (j) => {
    if (j.status === 'succeeded') {
      const r = j.result as VersionRecord & { unchanged?: boolean }
      if (r.unchanged) toast.info(`${r.imported_from ?? 'This file'} already is the current warehouse (${versionLabel(r.version)}). Nothing changed.`)
      else toast.success(`${r.imported_from ?? 'The warehouse'} is now the current warehouse (${versionLabel(r.version)}). Every page now shows it.`)
      setFile(null)
      setReason('')
      setOpen(false)
      refreshAll()
    } else setError(j.error ?? `Loading the warehouse ${j.status}`)
  })
  const running = sending || job?.status === 'queued' || job?.status === 'running'

  function pick(list: FileList | null) {
    const f = list?.[0]
    if (!f) return
    setError(null)
    if (!/\.(xlsx|xlsm)$/i.test(f.name)) {
      setError('Choose the warehouse workbook (.xlsx). Source exports belong in step 2, “Files to sync”.')
      return
    }
    setFile(f)
  }

  async function start() {
    if (!file) return
    setError(null)
    setSending(true)
    try {
      setJobId((await api.loadWarehouse(file, reason.trim() || undefined)).job_id)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setSending(false)
    }
  }

  return (
    <div className="space-y-4">
      {current && <WarehouseSummary current={current} index={status?.index} demo={api.mode === 'demo'} />}
      {api.mode === 'demo' ? (
        <DemoNote />
      ) : (
        <>
          {current && !open && !running && (
            <div className="flex flex-wrap items-center gap-3">
              <Button variant="outline" onClick={() => setOpen(true)}>
                <RefreshCw /> Load a newer warehouse file
              </Button>
              <span className="text-xs text-muted-foreground">Only needed when the warehouse was edited outside this app.</span>
            </div>
          )}
          {(!current || open || running) && (
            <div className="space-y-3 rounded-xl border p-4">
              <p className="text-sm text-muted-foreground">
                {current
                  ? 'Use this when the warehouse was changed outside this app, for example QA corrections made in Excel. After it is checked the file becomes the next version; earlier versions stay in Sync History.'
                  : 'Start with your latest warehouse workbook. It is checked, copied into the private state folder on this computer and prepared for search. The original file is never changed.'}
              </p>
              {!running && <DropZone hasCurrent={!!current} onPick={pick} />}
              {file && (
                <div className="space-y-3">
                  <div className="flex items-center gap-3 rounded-lg border p-3">
                    <FileSpreadsheet className="h-5 w-5 shrink-0 text-muted-foreground" />
                    <div className="min-w-0 flex-1">
                      <div className="truncate font-medium">{file.name}</div>
                      <div className="text-xs text-muted-foreground">{fmtBytes(file.size)}</div>
                    </div>
                    {!running && (
                      <Button variant="ghost" size="icon" aria-label={`Remove ${file.name}`} onClick={() => setFile(null)}>
                        <X />
                      </Button>
                    )}
                  </div>
                  {current && !running && (
                    <div className="space-y-1.5">
                      <Label htmlFor="wh-reason">What changed? (optional, kept in Sync History)</Label>
                      <Input id="wh-reason" value={reason} maxLength={2000} placeholder="e.g. QA corrections made in Excel" onChange={(e) => setReason(e.target.value)} />
                    </div>
                  )}
                  {!running && <Button onClick={start}>{current ? 'Load as the current warehouse' : 'Load warehouse'}</Button>}
                </div>
              )}
              {running && (
                <div className="space-y-1.5" aria-live="polite">
                  <div className="flex justify-between gap-3 text-sm">
                    <span className="font-medium">{sending ? 'Sending the file to the engine on this computer…' : PHASES[job?.phase ?? ''] ?? job?.message ?? 'Starting…'}</span>
                    {!sending && <span className="tabular text-muted-foreground">{Math.round((job?.fraction ?? 0) * 100)}%</span>}
                  </div>
                  <Progress value={sending ? 2 : (job?.fraction ?? 0) * 100} label="Warehouse loading progress" />
                  <p className="text-xs text-muted-foreground">A warehouse of about 600,000 rows takes roughly 5 minutes. You can open other pages meanwhile.</p>
                </div>
              )}
              {error && (
                <p role="alert" className="text-sm text-destructive">
                  {error}
                </p>
              )}
              {current && open && !running && (
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => {
                    setOpen(false)
                    setFile(null)
                    setError(null)
                  }}
                >
                  Cancel
                </Button>
              )}
            </div>
          )}
        </>
      )}
    </div>
  )
}

function WarehouseSummary({ current, index, demo }: { current: VersionRecord; index: StatusResponse['index']; demo: boolean }) {
  const kind = VERSION_KIND_META[current.kind] ?? { label: current.kind, tone: 'neutral' as const }
  return (
    <div className="flex flex-col gap-3 rounded-xl border bg-muted/20 p-4 sm:flex-row sm:items-center">
      <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-primary/10">
        <Database className="h-5 w-5 text-primary" />
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-semibold">{versionLabel(current.version)}</span>
          <span className="min-w-0 truncate text-sm text-muted-foreground">{current.file}</span>
          <Badge tone={kind.tone}>{kind.label}</Badge>
          {demo && <Badge tone="warning">Synthetic sample</Badge>}
        </div>
        <div className="mt-1 text-sm text-muted-foreground">
          {fmtNum(current.rows)} facility rows × {fmtNum(current.columns)} columns · {fmtDate(current.created_at)}
          {current.kind !== 'SYNC' && current.imported_from && (
            <>
              {' '}
              · from <span className="font-medium text-foreground">{current.imported_from}</span>
            </>
          )}
        </div>
      </div>
      {index &&
        (index.ready ? (
          <Badge tone="success" dot>
            Ready for search
          </Badge>
        ) : (
          <Badge tone="info">
            <Loader2 className="h-3 w-3 animate-spin" /> Preparing for search{index.progress !== null ? ` ${Math.round(index.progress * 100)}%` : '…'}
          </Badge>
        ))}
    </div>
  )
}

function DropZone({ hasCurrent, onPick }: { hasCurrent: boolean; onPick: (files: FileList | null) => void }) {
  const [dragging, setDragging] = React.useState(false)
  const inputRef = React.useRef<HTMLInputElement>(null)
  return (
    <div
      onDragOver={(e) => {
        e.preventDefault()
        setDragging(true)
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(e) => {
        e.preventDefault()
        setDragging(false)
        onPick(e.dataTransfer.files)
      }}
      className={cn(
        'flex flex-col items-center justify-center rounded-lg border-2 border-dashed px-4 py-6 text-center transition-colors',
        dragging ? 'border-primary bg-accent/50' : 'border-border bg-muted/20',
      )}
    >
      <UploadCloud className="mb-2 h-6 w-6 text-primary" />
      <p className="font-medium">{hasCurrent ? 'Drop the newer warehouse workbook here' : 'Drop your latest warehouse workbook here'}</p>
      <p className="mt-1 text-xs text-muted-foreground">The single-sheet .xlsx warehouse — not a source export</p>
      <Button variant="outline" size="sm" className="mt-3" onClick={() => inputRef.current?.click()}>
        Choose file
      </Button>
      <input
        ref={inputRef}
        type="file"
        accept=".xlsx,.xlsm"
        className="sr-only"
        aria-label="Choose the warehouse workbook"
        onChange={(e) => {
          onPick(e.target.files)
          e.target.value = ''
        }}
      />
    </div>
  )
}

function DemoNote() {
  return (
    <div className="flex items-start gap-3 rounded-xl border border-amber-500/30 bg-amber-500/[0.06] p-4 text-sm">
      <Laptop className="mt-0.5 h-4 w-4 shrink-0 text-amber-600 dark:text-amber-400" aria-hidden />
      <div className="space-y-2">
        <p className="font-medium">Your own warehouse is loaded in the app running on your computer.</p>
        <p className="text-muted-foreground">
          This public website uses the synthetic sample above so every step can be tried safely. A public website should not receive your private warehouse,
          and it cannot run the sync engine. Start the same app on your computer with one command; there, this step loads your latest warehouse and the
          files you add in step 2 are synchronized into it.
        </p>
        <Button asChild size="sm" variant="outline">
          <Link to="/docs/getting-started">How to run it on your computer</Link>
        </Button>
      </div>
    </div>
  )
}
