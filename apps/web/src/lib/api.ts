import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'
import { assetUrl, detectMode, sessionToken, type Mode } from './runtime'
import type {
  ColumnProfile,
  DecisionRecord,
  ExplorerPage,
  HistoryEvent,
  Job,
  Page,
  Plan,
  PlanListItem,
  QualityResponse,
  RecordResult,
  ReviewItem,
  Settings,
  StatusResponse,
  UploadResult,
  VersionRecord,
} from './types'

export class DemoUnavailableError extends Error {
  constructor(action: string) {
    super(
      `${action} runs only in Local Production Mode. The public demo never executes the engine or contacts a computer — ` +
        'run `wash-sync serve` on the machine that holds the private warehouse.',
    )
    this.name = 'DemoUnavailableError'
  }
}

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

export interface RecordFilter {
  classification?: string
  source?: string
  q?: string
  offset?: number
  limit?: number
}
export interface ItemFilter {
  status?: 'all' | 'undecided' | 'decided'
  classification?: string
  q?: string
  offset?: number
  limit?: number
}
export interface RowsQuery {
  q?: string
  division?: string
  source?: string
  match_status?: string
  flag?: string
  gps?: string
  columns?: string[]
  offset?: number
  limit?: number
}
export interface HistoryResponse {
  current_version: number | null
  versions: VersionRecord[]
  events: HistoryEvent[]
}
export interface SchemaResponse {
  headers: string[]
  columns: ColumnProfile[]
  rows: number
  periods: string[]
  sheet: string
}

export interface DataProvider {
  mode: Mode
  status(): Promise<StatusResponse>
  history(): Promise<HistoryResponse>
  pending(): Promise<Record<string, unknown>[]>
  plans(): Promise<PlanListItem[]>
  plan(id: string): Promise<Plan>
  records(id: string, f: RecordFilter): Promise<Page<RecordResult>>
  items(id: string, f: ItemFilter): Promise<Page<ReviewItem>>
  decide(id: string, item: string, choice: string, reason: string, optionIndex?: number): Promise<void>
  bulkDecide(id: string, classification: string, choice: string, reason: string): Promise<number>
  approve(id: string, note?: string): Promise<void>
  apply(id: string, authorize: boolean): Promise<Job>
  upload(file: File): Promise<UploadResult>
  preview(uploadIds: string[], opts: { source?: string; period?: string; strict?: boolean }): Promise<Job>
  job(id: string): Promise<Job>
  cancelJob(id: string): Promise<Job>
  schema(): Promise<SchemaResponse>
  rows(q: RowsQuery): Promise<ExplorerPage>
  row(row: number): Promise<{ row: number; values: Record<string, unknown> }>
  facets(): Promise<{ division: [string, number][]; match_status: [string, number][] }>
  quality(): Promise<QualityResponse>
  settings(): Promise<Settings>
  saveSettings(s: Partial<Settings>): Promise<Settings>
  rollback(version: number, reason: string): Promise<void>
  verify(version: number): Promise<Job>
  auditUrl(planId: string, stage: 'preview' | 'apply', file: string): string
  downloadVersionUrl(version: number): string | null
}

// ---------------------------------------------------------------------------- local

const reviewerKey = 'wash-reviewer'
export function reviewerName(): string {
  try {
    return localStorage.getItem(reviewerKey) ?? ''
  } catch {
    return ''
  }
}
export function setReviewerName(name: string) {
  try {
    localStorage.setItem(reviewerKey, name)
  } catch {
    /* storage unavailable */
  }
}

class LocalProvider implements DataProvider {
  mode: Mode = 'local'
  private token = sessionToken()

  private async req<T>(path: string, init: RequestInit = {}): Promise<T> {
    const headers: Record<string, string> = { 'X-Wash-Sync-Token': this.token, ...(init.headers as Record<string, string>) }
    const reviewer = reviewerName()
    if (reviewer) headers['X-Wash-Sync-User'] = reviewer
    if (init.body && !(init.body instanceof FormData)) headers['Content-Type'] = 'application/json'
    const res = await fetch(`/api/v1${path}`, { ...init, headers, credentials: 'same-origin' })
    if (!res.ok) {
      let detail = res.statusText
      try {
        const body = await res.json()
        detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
      } catch {
        /* not json */
      }
      throw new ApiError(res.status, detail)
    }
    return (await res.json()) as T
  }

  private qs(obj: object): string {
    const p = new URLSearchParams()
    for (const [k, v] of Object.entries(obj)) {
      if (v === undefined || v === null || v === '') continue
      p.set(k, Array.isArray(v) ? v.join(',') : String(v))
    }
    const s = p.toString()
    return s ? `?${s}` : ''
  }

  status = () => this.req<StatusResponse>('/status')
  history = () => this.req<HistoryResponse>('/versions')
  pending = () => this.req<Record<string, unknown>[]>('/pending')
  plans = () => this.req<PlanListItem[]>('/plans')
  plan = (id: string) => this.req<Plan>(`/plans/${id}`)
  records = (id: string, f: RecordFilter) => this.req<Page<RecordResult>>(`/plans/${id}/records${this.qs(f)}`)
  items = (id: string, f: ItemFilter) => this.req<Page<ReviewItem>>(`/plans/${id}/review-items${this.qs(f)}`)
  async decide(id: string, item: string, choice: string, reason: string, optionIndex?: number) {
    await this.req(`/plans/${id}/review-items/${item}/decision`, {
      method: 'POST',
      body: JSON.stringify({ choice, reason, option_index: optionIndex }),
    })
  }
  async bulkDecide(id: string, classification: string, choice: string, reason: string) {
    const r = await this.req<{ decided: number }>(`/plans/${id}/bulk-decision`, {
      method: 'POST',
      body: JSON.stringify({ classification, choice, reason }),
    })
    return r.decided
  }
  async approve(id: string, note?: string) {
    await this.req(`/plans/${id}/approve`, { method: 'POST', body: JSON.stringify({ note }) })
  }
  apply = (id: string, authorize: boolean) =>
    this.req<Job>(`/plans/${id}/apply`, { method: 'POST', body: JSON.stringify({ authorize }) })
  upload(file: File) {
    const fd = new FormData()
    fd.append('file', file)
    return this.req<UploadResult>('/uploads', { method: 'POST', body: fd })
  }
  preview = (uploadIds: string[], opts: { source?: string; period?: string; strict?: boolean }) =>
    this.req<Job>('/previews', { method: 'POST', body: JSON.stringify({ upload_ids: uploadIds, ...opts }) })
  job = (id: string) => this.req<Job>(`/jobs/${id}`)
  cancelJob = (id: string) => this.req<Job>(`/jobs/${id}/cancel`, { method: 'POST' })
  schema = () => this.req<SchemaResponse>('/warehouse/schema')
  rows = (q: RowsQuery) => this.req<ExplorerPage>(`/warehouse/rows${this.qs(q)}`)
  row = (row: number) => this.req<{ row: number; values: Record<string, unknown> }>(`/warehouse/rows/${row}`)
  facets = () => this.req<{ division: [string, number][]; match_status: [string, number][] }>('/warehouse/facets')
  quality = () => this.req<QualityResponse>('/quality')
  settings = () => this.req<Settings>('/settings')
  saveSettings = (s: Partial<Settings>) => this.req<Settings>('/settings', { method: 'PUT', body: JSON.stringify(s) })
  async rollback(version: number, reason: string) {
    await this.req(`/versions/${version}/rollback`, { method: 'POST', body: JSON.stringify({ reason }) })
  }
  verify = (version: number) => this.req<Job>(`/versions/${version}/verify`, { method: 'POST' })
  auditUrl = (planId: string, stage: 'preview' | 'apply', file: string) => `/api/v1/plans/${planId}/audit/${stage}/${file}`
  downloadVersionUrl = (version: number) => `/api/v1/versions/${version}/download`
}

// ---------------------------------------------------------------------------- demo

interface WarehouseBundle {
  headers: string[]
  rows: { row: number; values: Record<string, unknown> }[]
}

const decisionsKey = (planId: string) => `wash-demo-decisions:${planId}`

class DemoProvider implements DataProvider {
  mode: Mode = 'demo'
  private cache = new Map<string, Promise<unknown>>()

  private get<T>(file: string): Promise<T> {
    if (!this.cache.has(file)) {
      this.cache.set(
        file,
        fetch(assetUrl(`demo/${file}`)).then((r) => {
          if (!r.ok) throw new ApiError(r.status, `Demo data ${file} could not be loaded (${r.status})`)
          return r.json()
        }),
      )
    }
    return this.cache.get(file) as Promise<T>
  }

  private localDecisions(planId: string): Record<string, DecisionRecord> {
    try {
      return JSON.parse(localStorage.getItem(decisionsKey(planId)) ?? '{}')
    } catch {
      return {}
    }
  }

  status = () => this.get<StatusResponse>('status.json')
  history = () => this.get<HistoryResponse>('versions.json')
  pending = () => this.get<Record<string, unknown>[]>('pending.json')
  plans = () => this.get<PlanListItem[]>('plans.json')
  plan = (id: string) => this.get<Plan>(`plans/${id}.json`)

  async records(id: string, f: RecordFilter): Promise<Page<RecordResult>> {
    let rows = await this.get<RecordResult[]>(`plans/${id}.records.json`)
    if (f.classification) rows = rows.filter((r) => r.classification === f.classification)
    if (f.source) rows = rows.filter((r) => r.source === f.source)
    if (f.q) {
      const q = f.q.toLowerCase()
      rows = rows.filter(
        (r) =>
          (r.source_record_id ?? '').toLowerCase().includes(q) ||
          r.source_file.toLowerCase().includes(q) ||
          r.match.candidate_ids.some((c) => c.toLowerCase().includes(q)),
      )
    }
    const offset = f.offset ?? 0
    const limit = f.limit ?? 50
    return { total: rows.length, offset, limit, items: rows.slice(offset, offset + limit) }
  }

  async items(id: string, f: ItemFilter): Promise<Page<ReviewItem>> {
    const local = this.localDecisions(id)
    let items = (await this.get<ReviewItem[]>(`plans/${id}.items.json`)).map((i) =>
      local[i.item_id] ? { ...i, decision: local[i.item_id] } : i,
    )
    if (f.status === 'undecided') items = items.filter((i) => !i.decision)
    if (f.status === 'decided') items = items.filter((i) => !!i.decision)
    if (f.classification) items = items.filter((i) => i.classification === f.classification)
    if (f.q) {
      const q = f.q.toLowerCase()
      items = items.filter((i) => i.title.toLowerCase().includes(q) || i.reason.toLowerCase().includes(q))
    }
    const offset = f.offset ?? 0
    const limit = f.limit ?? 50
    return { total: items.length, offset, limit, items: items.slice(offset, offset + limit) }
  }

  async decide(id: string, item: string, choice: string, reason: string, optionIndex?: number) {
    if (!reason.trim()) throw new Error('A reason is required for every decision')
    const items = await this.get<ReviewItem[]>(`plans/${id}.items.json`)
    const target = items.find((i) => i.item_id === item)
    if (!target) throw new Error(`Unknown review item ${item}`)
    const idx = optionIndex ?? target.options.findIndex((o) => o.decision === choice)
    const all = this.localDecisions(id)
    all[item] = {
      choice: choice as DecisionRecord['choice'],
      option_index: idx,
      reason,
      decided_by: `${reviewerName() || 'demo visitor'} (browser only)`,
      decided_at: new Date().toISOString(),
    }
    try {
      localStorage.setItem(decisionsKey(id), JSON.stringify(all))
    } catch {
      throw new Error('Browser storage is unavailable; demo decisions cannot be kept')
    }
  }

  async bulkDecide(): Promise<number> {
    throw new DemoUnavailableError('Bulk decisions')
  }
  async approve(): Promise<void> {
    throw new DemoUnavailableError('Approving a plan')
  }
  async apply(): Promise<Job> {
    throw new DemoUnavailableError('Applying a plan')
  }
  async upload(): Promise<UploadResult> {
    throw new DemoUnavailableError('Uploading to the engine')
  }
  async preview(): Promise<Job> {
    throw new DemoUnavailableError('Server-side dry run')
  }
  async job(): Promise<Job> {
    throw new DemoUnavailableError('Background jobs')
  }
  async cancelJob(): Promise<Job> {
    throw new DemoUnavailableError('Background jobs')
  }
  schema = () => this.get<SchemaResponse>('schema.json')

  async rows(q: RowsQuery): Promise<ExplorerPage> {
    const bundle = await this.get<WarehouseBundle>('warehouse.json')
    const cols = (q.columns?.length ? q.columns : DEFAULT_COLUMNS).filter((c) => bundle.headers.includes(c))
    let rows = bundle.rows
    if (q.q) {
      const needle = q.q.toLowerCase()
      rows = rows.filter((r) =>
        ['candidate_id', 'division', 'district', 'upazila', 'union_name', 'village_name', 'aqua_gwc_id', 'wpms_go_code', 'gpani_source_geocode', 'borelog_id'].some(
          (k) => String(r.values[k] ?? '').toLowerCase().includes(needle),
        ),
      )
    }
    if (q.division) rows = rows.filter((r) => String(r.values.division ?? '').toUpperCase() === q.division!.toUpperCase())
    if (q.match_status) rows = rows.filter((r) => r.values.match_status === q.match_status)
    if (q.flag) rows = rows.filter((r) => String(r.values.quality_flags ?? '').includes(q.flag!))
    if (q.source) {
      const col = { GPANI: 'gpani_source_geocode', WPMS: 'wpms_go_code', AQUAWATCH: 'aqua_gwc_id', BORELOG: 'borelog_id' }[q.source]
      if (col) rows = rows.filter((r) => r.values[col] !== undefined)
    }
    if (q.gps) {
      rows = rows.filter((r) => {
        const lat = Number(r.values.latitude)
        const lon = Number(r.values.longitude)
        const ok = lat >= 20.3 && lat <= 26.9 && lon >= 87.9 && lon <= 92.9
        return q.gps === 'valid' ? ok : !ok
      })
    }
    const offset = q.offset ?? 0
    const limit = q.limit ?? 50
    return {
      total: rows.length,
      offset,
      limit,
      columns: cols,
      items: rows.slice(offset, offset + limit).map((r) => ({ row: r.row, ...Object.fromEntries(cols.map((c) => [c, r.values[c]])) })),
    }
  }

  async row(row: number) {
    const bundle = await this.get<WarehouseBundle>('warehouse.json')
    const found = bundle.rows.find((r) => r.row === row)
    if (!found) throw new ApiError(404, 'Row not found')
    return found
  }
  facets = () => this.get<{ division: [string, number][]; match_status: [string, number][] }>('facets.json')
  quality = () => this.get<QualityResponse>('quality.json')
  settings = () => this.get<Settings>('settings.json')
  async saveSettings(): Promise<Settings> {
    throw new DemoUnavailableError('Saving settings')
  }
  async rollback(): Promise<void> {
    throw new DemoUnavailableError('Rollback')
  }
  async verify(): Promise<Job> {
    throw new DemoUnavailableError('Verification')
  }
  auditUrl = (planId: string, stage: 'preview' | 'apply', file: string) => assetUrl(`demo/audit/${planId}/${stage}/${file}`)
  downloadVersionUrl = () => null
}

export const DEFAULT_COLUMNS = [
  'candidate_id',
  'division',
  'district',
  'upazila',
  'union_name',
  'village_name',
  'facility_type',
  'latitude',
  'longitude',
  'record_sources',
  'match_status',
]

export function createProvider(mode: Mode = detectMode()): DataProvider {
  return mode === 'local' ? new LocalProvider() : new DemoProvider()
}

export const DataContext = createContext<DataProvider>(createProvider('demo'))
export const useData = () => useContext(DataContext)

// ---------------------------------------------------------------------------- tiny async-state hook

export interface Resource<T> {
  data: T | undefined
  error: Error | undefined
  loading: boolean
  reload: () => void
}

export function useResource<T>(fn: () => Promise<T>, deps: unknown[]): Resource<T> {
  const [state, setState] = useState<{ data?: T; error?: Error; loading: boolean }>({ loading: true })
  const [tick, setTick] = useState(0)
  const fnRef = useRef(fn)
  fnRef.current = fn
  useEffect(() => {
    let alive = true
    setState((s) => ({ ...s, loading: true, error: undefined }))
    fnRef
      .current()
      .then((data) => alive && setState({ data, loading: false }))
      .catch((error: Error) => alive && setState({ error, loading: false }))
    return () => {
      alive = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick])
  const reload = useCallback(() => setTick((t) => t + 1), [])
  return { data: state.data, error: state.error, loading: state.loading, reload }
}

/** Poll a background job until it finishes. Progress comes from real work counters. */
export function useJob(provider: DataProvider, jobId: string | null, onDone?: (job: Job) => void) {
  const [job, setJob] = useState<Job | null>(null)
  const doneRef = useRef(onDone)
  doneRef.current = onDone
  useEffect(() => {
    if (!jobId) return
    let alive = true
    let timer: ReturnType<typeof setTimeout>
    const tick = async () => {
      try {
        const j = await provider.job(jobId)
        if (!alive) return
        setJob(j)
        if (['succeeded', 'failed', 'cancelled'].includes(j.status)) {
          doneRef.current?.(j)
          return
        }
      } catch {
        /* transient error: retry */
      }
      timer = setTimeout(tick, 700)
    }
    tick()
    return () => {
      alive = false
      clearTimeout(timer)
    }
  }, [provider, jobId])
  return job
}
