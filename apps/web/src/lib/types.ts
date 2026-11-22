// Types mirroring the Python plan / API contract (packages/sync_core/wash_sync/plan.py).

export type ChangeClass =
  | 'NEW_FACILITY'
  | 'NEW_OBSERVATION'
  | 'NEW_SOURCE_METADATA'
  | 'UPDATED_MEASUREMENT'
  | 'FILLED_MISSING_VALUE'
  | 'UNCHANGED'
  | 'EXACT_DUPLICATE'
  | 'POTENTIAL_DUPLICATE'
  | 'CONFLICTING_IDENTITY'
  | 'CONFLICTING_MEASUREMENT'
  | 'INVALID_SOURCE_RECORD'
  | 'REQUIRES_REVIEW'

export type Source = 'GPANI' | 'WPMS' | 'AQUAWATCH' | 'BORELOG'
export type Decision =
  | 'APPROVE_MATCH'
  | 'KEEP_SEPARATE'
  | 'ACCEPT_NEW_VALUES'
  | 'KEEP_EXISTING'
  | 'ACCEPT'
  | 'REJECT'
  | 'DEFER'

export interface CellChange {
  column: string
  old: unknown
  new: unknown
  kind: 'number' | 'string'
  action: 'FILL' | 'SET' | 'APPEND' | 'INSERT'
  raw?: string | null
  policy?: string | null
  category?: string | null
}

export interface Evidence {
  check: string
  value: unknown
  result: 'AGREE' | 'DISAGREE' | 'UNKNOWN' | 'INFO' | 'MATCH' | 'NO_MATCH'
}

export interface CandidateSummary {
  row: number
  candidate_id?: string
  distance_m?: number
  [key: string]: unknown
}

export interface MatchInfo {
  level: string
  confidence: 'EXACT' | 'VERIFIED' | 'POSSIBLE' | 'NONE'
  category: string
  candidate_rows: number[]
  candidate_ids: string[]
  candidates: CandidateSummary[]
  evidence: Evidence[]
  conflicts: string[]
  note?: string | null
}

export interface Operation {
  op_id: string
  kind: 'UPDATE_ROW' | 'INSERT_ROW'
  target_row?: number | null
  target_candidate_id?: string | null
  anchor_row?: number | null
  anchor_explanation?: string | null
  insert_key?: string | null
  identity?: string | null
  cells: CellChange[]
}

export interface Issue {
  code: string
  severity: 'ERROR' | 'WARNING' | 'INFO' | 'REVIEW'
  message: string
  field?: string | null
}

export interface RecordResult {
  rec_id: string
  source: Source
  format: string
  source_file: string
  source_sheet: string
  source_row: number
  source_record_id: string | null
  observation_key: string
  content_hash: string
  period?: string | null
  classification: ChangeClass
  match: MatchInfo
  issues: Issue[]
  flags: string[]
  preview: Record<string, unknown>
  proposed_action: string
  operations: Operation[]
  review_item?: string | null
  notes: string[]
  duplicate_of?: string | null
}

export interface ReviewOption {
  decision: Decision
  label: string
  description?: string
  candidate_row?: number | null
  operations: Operation[]
}

export interface DecisionRecord {
  choice: Decision
  option_index: number
  reason: string
  decided_by: string
  decided_at: string
  remembered?: boolean
}

export interface ReviewItem {
  item_id: string
  rec_id: string
  classification: ChangeClass
  title: string
  reason: string
  options: ReviewOption[]
  recommended?: number | null
  decision?: DecisionRecord | null
  record?: RecordResult | null
}

export interface SourceFileInfo {
  file_name: string
  sha256: string
  size: number
  source: Source | null
  format: string | null
  sheet?: string | null
  confidence: number
  records: number
  periods: string[]
  matched_columns: Record<string, string>
  missing_optional: string[]
  ignored_columns: string[]
  personal_data_columns_ignored: string[]
  completeness: Record<string, number | string>
}

export interface PlanSummaryBlock {
  plan_id: string
  base_version: number
  base_rows: number
  records_total: number
  by_classification: Record<ChangeClass, number>
  by_source: Record<string, Record<string, number>>
  auto_operations: number
  auto_inserts: number
  auto_updates: number
  review_items: number
  undecided_review_items: number
  new_columns: string[]
  files: { file: string; source: string; format: string; records: number; periods: string[] }[]
}

export interface Plan {
  plan_id: string
  created_at: string
  engine_version: string
  schema_version: string
  status: 'DRAFT' | 'APPROVED' | 'APPLIED' | 'SUPERSEDED' | 'NO_CHANGES'
  base_version: number | null
  base_file?: string | null
  base_sha256: string
  base_rows: number
  base_columns: number
  sources: SourceFileInfo[]
  summary: PlanSummaryBlock
  new_columns: { name: string; insert_after: string; reason: string }[]
  approval?: { approved_by: string; approved_at: string; plan_hash: string; note?: string | null } | null
  applied_version?: number | null
  elapsed_seconds?: number | null
  review_counts?: { total: number; undecided: number }
  record_count?: number
}

export interface PlanListItem {
  plan_id: string
  created_at: string
  status: Plan['status']
  base_version: number | null
  applied_version?: number | null
  files: string[]
  summary: Record<string, number>
  review_items: number
  undecided: number
  new_columns: string[]
  approval?: Plan['approval']
}

export interface VersionRecord {
  version: number
  file: string
  sha256: string
  size: number
  created_at: string
  previous_version: number | null
  kind: 'BASELINE' | 'SYNC' | 'LOADED'
  reason?: string | null
  plan_id?: string
  rows: number
  columns: number
  sources: { file_name: string; sha256: string; source: string; format: string; records: number; periods?: string[] }[]
  counts: Record<string, number>
  new_columns?: string[]
  validation: { ok: boolean; checks: { name: string; status: string; detail: string }[]; preservation?: Record<string, number> }
  schema_version: string
  engine_version: string
  approved_by?: string | null
  applied_by?: string | null
  imported_from?: string
}

export interface HistoryEvent {
  at: string
  event: string
  [key: string]: unknown
}

export interface StatusResponse {
  mode: 'local' | 'demo'
  engine_version: string
  schema_version: string
  initialized: boolean
  current_version: VersionRecord | null
  versions: number
  last_sync: VersionRecord | null
  recent_plans: PlanListItem[]
  recent_events: HistoryEvent[]
  pending_conflicts: number
  active_job?: Job | null
  state_dir?: string
  /** Whether the current version is ready for search (local mode). */
  index?: { ready: boolean; progress: number | null } | null
}

export interface Job {
  job_id: string
  kind: string
  status: 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled'
  phase: string
  fraction: number
  message: string
  result: any
  error: string | null
  elapsed: number
}

export interface Page<T> {
  total: number
  offset: number
  limit: number
  items: T[]
}

export interface ExplorerPage {
  total: number
  offset: number
  limit: number
  columns: string[]
  items: Array<{ row: number } & Record<string, unknown>>
}

export interface ColumnProfile {
  idx: number
  name: string
  header_style: string | null
  nonempty: number
  n_num: number
  n_str: number
  dom_style: string | null
  max_len: number
  num_min: number | null
  num_max: number | null
}

export interface QualityResponse {
  rows: number
  columns: ColumnProfile[]
  quality: {
    rows: number
    gps: Record<string, number>
    missing_admin: Record<string, number>
    missing_facility_type: number
    top_flags: [string, number][]
    sources: Record<string, number>
    match_status: [string, number][]
    outliers: Record<string, { count: number; rule: [number | null, number | null, string]; examples: string[] }>
    water_table_outliers: number
    unit_issue_flags: number
    conflict_flags: number
    duplicate_identifiers: Record<string, number>
    shared_exact_coordinates: number
  }
}

export interface Detection {
  source: Source | null
  format: string | null
  sheet: string | null
  confidence: number
  matched: Record<string, string>
  missing_required: string[]
  missing_optional: string[]
  ignored_columns: string[]
  personal_data_columns: string[]
  sheets: string[]
  reason: string
}

export interface UploadResult {
  upload_id: string
  file_name: string
  size: number
  sha256: string
  detection: Detection
}

export interface Settings {
  warehouse_name: string
  source_dirs: Record<string, string>
  export_dir: string | null
  validation: { mode: 'standard' | 'strict'; strict_escalations: string[]; completeness: Record<string, string[]>; min_survey_year: number }
  matching: {
    possible_match_radius_m: number
    exact_coincidence_m: number
    verify_gps_max_m: number
    depth_tolerance_ratio: number
    depth_tolerance_abs_m: number
    auto_apply_verified_cross_source: boolean
  }
  admin_aliases: { level: string; names: string[] }[]
  backup_retention: number
  compression_level: number
  private_storage: { enabled: boolean; destination: string | null; label: string | null }
  state_dir?: string
}
