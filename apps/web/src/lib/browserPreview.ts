/**
 * In-browser preview for the public demo.
 *
 * Parses a small CSV/XLSX file *locally* (nothing is uploaded anywhere), detects the
 * source system with the same header profiles as the Python engine and classifies
 * records against the synthetic demo warehouse using a simplified subset of the
 * engine's rules (exact source identity, period columns, 30 m geographic check).
 * The authoritative classification always comes from the engine in local mode.
 */
import { strFromU8, unzipSync } from 'fflate'
import Papa from 'papaparse'
import profilesJson from '@shared/source_profiles.json'
import type { ChangeClass } from './types'

type Fields = Record<string, string[]>
interface FormatProfile {
  label: string
  required: string[]
  fields: Fields | Record<string, Fields>
  sheets?: Record<string, string[]>
  year_columns_regex?: string
  min_year_columns?: number
  signature?: string[]
}
interface Profiles {
  personal_data_columns: string[]
  sources: Record<string, { label: string; formats: Record<string, FormatProfile> }>
}
const profiles = profilesJson as unknown as Profiles

export interface Table {
  sheet: string
  headers: string[]
  rows: (string | null)[][]
  headerRow: number
}

export interface BrowserDetection {
  source: string | null
  format: string | null
  sheet: string | null
  confidence: number
  matched: Record<string, string>
  missingRequired: string[]
  missingOptional: string[]
  personalData: string[]
  ignored: string[]
  reason: string
}

export interface BrowserRecord {
  row: number
  id: string | null
  period: string | null
  classification: ChangeClass
  reason: string
  candidateRow?: number
}

export interface BrowserPreviewResult {
  fileName: string
  detection: BrowserDetection
  totalRows: number
  counts: Partial<Record<ChangeClass, number>>
  periods: Record<string, number>
  newPeriods: string[]
  completeness: Record<string, number>
  records: BrowserRecord[]
}

export interface WarehouseBundle {
  headers: string[]
  rows: { row: number; values: Record<string, unknown> }[]
}

// ----------------------------------------------------------------------------- parsing

const BANGLA = '০১২৩৪৫৬৭৮৯'
export function asciiDigits(s: string): string {
  return s.replace(/[০-৯]/g, (d) => String(BANGLA.indexOf(d)))
}
export function normHeader(s: unknown): string {
  return String(s ?? '')
    .normalize('NFKC')
    .toLowerCase()
    .replace(/[^0-9a-zঀ-৿]+/g, '')
}
export function normId(s: unknown): string | null {
  if (s === null || s === undefined) return null
  let t = asciiDigits(String(s)).normalize('NFKC').replace(/\s+/g, ' ').trim().toUpperCase()
  t = t.replace(/^(-?\d+)\.0+$/, '$1')
  if (!t || ['N/A', 'NA', 'NONE', 'NULL', '-', 'NO DATA'].includes(t)) return null
  return t
}
function xmlText(s: string): string {
  return s
    .replace(/_x([0-9A-Fa-f]{4})_/g, (_, h) => String.fromCharCode(parseInt(h, 16)))
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&apos;/g, "'")
    .replace(/&#(\d+);/g, (_, d) => String.fromCharCode(Number(d)))
    .replace(/&#x([0-9a-fA-F]+);/g, (_, h) => String.fromCharCode(parseInt(h, 16)))
    .replace(/&amp;/g, '&')
}
function colIndex(letters: string): number {
  let n = 0
  for (const ch of letters) n = n * 26 + (ch.charCodeAt(0) - 64)
  return n
}

function locateHeader(rows: (string | null)[][], sheet: string): Table {
  const known = new Set<string>()
  for (const src of Object.values(profiles.sources))
    for (const fmt of Object.values(src.formats)) {
      const groups: string[][] = Object.values(fmt.fields as Record<string, string[] | Fields>).flatMap((v) =>
        Array.isArray(v) ? [v] : Object.values(v),
      )
      for (const aliases of groups) for (const a of aliases) known.add(normHeader(a))
    }
  let best = -1
  let bestScore = -1
  rows.slice(0, 15).forEach((r, i) => {
    const nonEmpty = r.filter((v) => v)
    if (nonEmpty.length < 2) return
    const score = nonEmpty.filter((v) => known.has(normHeader(v))).length
    if (score > bestScore) {
      best = i
      bestScore = score
    }
  })
  if (best < 0) return { sheet, headers: [], rows: [], headerRow: 0 }
  const headers = rows[best].map((h, i) => (h ? String(h).trim() : `column_${i + 1}`))
  return { sheet, headers, rows: rows.slice(best + 1).filter((r) => r.some((v) => v)), headerRow: best + 1 }
}

export function parseCsv(text: string, name = 'CSV'): Table[] {
  const parsed = Papa.parse<string[]>(text.replace(/^﻿/, ''), { skipEmptyLines: true })
  const rows = parsed.data.map((r) => r.map((v) => (v === '' ? null : v)))
  return [locateHeader(rows, name)]
}

export function parseXlsx(data: Uint8Array): Table[] {
  const files = unzipSync(data, {
    filter: (f) => f.name.startsWith('xl/worksheets/sheet') || ['xl/workbook.xml', 'xl/sharedStrings.xml', 'xl/_rels/workbook.xml.rels'].includes(f.name),
  })
  const shared: string[] = []
  if (files['xl/sharedStrings.xml']) {
    const xml = strFromU8(files['xl/sharedStrings.xml'])
    for (const m of xml.matchAll(/<si>([\s\S]*?)<\/si>/g)) {
      shared.push(xmlText([...m[1].replace(/<rPh[\s\S]*?<\/rPh>/g, '').matchAll(/<t[^>]*>([\s\S]*?)<\/t>/g)].map((t) => t[1]).join('')))
    }
  }
  const wb = strFromU8(files['xl/workbook.xml'] ?? new Uint8Array())
  const rels = strFromU8(files['xl/_rels/workbook.xml.rels'] ?? new Uint8Array())
  const target: Record<string, string> = {}
  for (const m of rels.matchAll(/<Relationship[^>]*Id="([^"]+)"[^>]*Target="([^"]+)"/g)) target[m[1]] = m[2]
  for (const m of rels.matchAll(/<Relationship[^>]*Target="([^"]+)"[^>]*Id="([^"]+)"/g)) target[m[2]] = m[1]
  const tables: Table[] = []
  for (const m of wb.matchAll(/<sheet [^>]*name="([^"]+)"[^>]*r:id="([^"]+)"/g)) {
    const path = 'xl/' + (target[m[2]] ?? '').replace(/^\/?xl\//, '').replace(/^\//, '')
    const sheetXml = files[path] ? strFromU8(files[path]) : ''
    const rows: (string | null)[][] = []
    for (const r of sheetXml.matchAll(/<row\b[^>]*?(?:\/>|>([\s\S]*?)<\/row>)/g)) {
      const cells: (string | null)[] = []
      for (const c of (r[1] ?? '').matchAll(/<c\b([^>]*?)(?:\/>|>([\s\S]*?)<\/c>)/g)) {
        const ref = /r="([A-Z]+)\d+"/.exec(c[1])
        const type = /t="(\w+)"/.exec(c[1])?.[1] ?? 'n'
        const idx = ref ? colIndex(ref[1]) - 1 : cells.length
        const body = c[2] ?? ''
        let value: string | null = null
        if (type === 'inlineStr') value = xmlText([...body.matchAll(/<t[^>]*>([\s\S]*?)<\/t>/g)].map((t) => t[1]).join(''))
        else {
          const v = /<v>([\s\S]*?)<\/v>/.exec(body)?.[1]
          if (v !== undefined) value = type === 's' ? shared[Number(v)] ?? null : xmlText(v)
        }
        while (cells.length < idx) cells.push(null)
        cells[idx] = value === '' ? null : value
      }
      rows.push(cells)
    }
    tables.push(locateHeader(rows, xmlText(m[1])))
  }
  return tables
}

// ----------------------------------------------------------------------------- detection

function headerMap(headers: string[], fields: Fields) {
  const norm = new Map<string, number>()
  headers.forEach((h, i) => !norm.has(normHeader(h)) && norm.set(normHeader(h), i))
  const index: Record<string, number> = {}
  const matched: Record<string, string> = {}
  for (const [name, aliases] of Object.entries(fields)) {
    for (const a of aliases) {
      const i = norm.get(normHeader(a))
      if (i !== undefined && !Object.values(index).includes(i)) {
        index[name] = i
        matched[name] = headers[i]
        break
      }
    }
  }
  return { index, matched }
}

export function detect(tables: Table[], hint?: string): BrowserDetection {
  const pii = new Set(profiles.personal_data_columns.map(normHeader))
  const sheetNames = new Set(tables.map((t) => normHeader(t.sheet)))
  const bore = profiles.sources.BORELOG.formats.borelog_workbook
  const tabs = Object.entries(bore.sheets ?? ({} as Record<string, string[]>)).filter(([, aliases]) => aliases.some((a) => sheetNames.has(normHeader(a))))
  if (tabs.length >= 3 && (!hint || hint === 'BORELOG')) {
    const general = tables.find((t) => (bore.sheets?.general ?? []).some((a) => normHeader(a) === normHeader(t.sheet)))
    const fields = (bore.fields as Record<string, Fields>).general
    const { matched, index } = headerMap(general?.headers ?? [], fields)
    return {
      source: 'BORELOG', format: 'borelog_workbook', sheet: general?.sheet ?? null, confidence: 1, matched,
      missingRequired: index.id === undefined ? ['id'] : [], missingOptional: [], ignored: [],
      personalData: (general?.headers ?? []).filter((h) => pii.has(normHeader(h))),
      reason: `bore-log workbook with tabs ${tabs.map(([k]) => k).join(', ')}`,
    }
  }
  let best: BrowserDetection | null = null
  for (const table of tables) {
    for (const [source, spec] of Object.entries(profiles.sources)) {
      if (source === 'BORELOG' || (hint && hint !== source)) continue
      for (const [fmtName, fmt] of Object.entries(spec.formats)) {
        const fields = fmt.fields as Fields
        const { index, matched } = headerMap(table.headers, fields)
        const missingRequired = fmt.required.filter((r) => index[r] === undefined)
        if (fmt.year_columns_regex) {
          const re = new RegExp(fmt.year_columns_regex)
          if (table.headers.filter((h) => re.test(h.trim())).length < (fmt.min_year_columns ?? 1)) missingRequired.push('year_columns')
        }
        const total = Object.keys(fields).length
        let score = missingRequired.length ? 0 : Object.keys(index).length / total
        if (fmt.signature) score += 0.1 * fmt.signature.filter((s) => table.headers.some((h) => normHeader(h) === normHeader(s))).length
        const used = new Set(Object.values(index))
        const det: BrowserDetection = {
          source, format: fmtName, sheet: table.sheet, confidence: Math.min(1, Math.round(score * 1000) / 1000), matched, missingRequired,
          missingOptional: Object.keys(fields).filter((f) => index[f] === undefined && !fmt.required.includes(f)),
          personalData: table.headers.filter((h) => pii.has(normHeader(h))),
          ignored: table.headers.filter((h, i) => !used.has(i) && !pii.has(normHeader(h))),
          reason: `${Object.keys(index).length}/${total} known columns matched on sheet “${table.sheet}”`,
        }
        if (!best || det.confidence > best.confidence) best = det
      }
    }
  }
  if (!best || best.confidence <= 0)
    return { source: null, format: null, sheet: null, confidence: 0, matched: {}, missingRequired: [], missingOptional: [], personalData: [], ignored: [], reason: 'No supported source schema recognized' }
  return best
}

export function parsePeriod(label: unknown): { year: number; season: 'wet' | 'dry' | null; key: string } | null {
  const t = String(label ?? '').trim().replace(/\.0+$/, '')
  const m =
    /^((?:19|20)\d{2})\s*(?:\(\s*(wet|dry)\s*(?:season)?\s*\)|[-_ /]*(wet|dry)(?:\s*season)?)?\s*$/i.exec(t) ??
    /^(wet|dry)\s*(?:season)?\s*[-_ /,]*\s*((?:19|20)\d{2})$/i.exec(t)
  if (!m) return null
  const yearFirst = /^\d/.test(m[1])
  const year = Number(yearFirst ? m[1] : m[2])
  const season = ((yearFirst ? m[2] ?? m[3] : m[1]) ?? '').toLowerCase() as 'wet' | 'dry' | ''
  return { year, season: season || null, key: `${year}_${season || 'annual'}` }
}

const num = (v: unknown): number | null => {
  const t = asciiDigits(String(v ?? '').trim())
  return /^[-+]?(\d+(\.\d*)?|\.\d+)$/.test(t) ? Number(t) : null
}
const same = (a: unknown, b: unknown): boolean => {
  const na = num(a)
  const nb = num(b)
  if (na !== null && nb !== null) return Math.abs(na - nb) <= 1e-9 * Math.max(1, Math.abs(nb))
  return String(a ?? '').trim().toLowerCase() === String(b ?? '').trim().toLowerCase()
}
function haversine(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const r = (d: number) => (d * Math.PI) / 180
  const a = Math.sin(r(lat2 - lat1) / 2) ** 2 + Math.cos(r(lat1)) * Math.cos(r(lat2)) * Math.sin(r(lon2 - lon1) / 2) ** 2
  return 2 * 6371008.8 * Math.asin(Math.min(1, Math.sqrt(a)))
}
const inBd = (lat: number | null, lon: number | null) => lat !== null && lon !== null && lat >= 20.3 && lat <= 26.9 && lon >= 87.9 && lon <= 92.9

// ----------------------------------------------------------------------------- classification

export function classify(fileName: string, tables: Table[], det: BrowserDetection, wh: WarehouseBundle): BrowserPreviewResult {
  const result: BrowserPreviewResult = { fileName, detection: det, totalRows: 0, counts: {}, periods: {}, newPeriods: [], completeness: {}, records: [] }
  if (!det.source || det.missingRequired.length) return result
  const table = tables.find((t) => t.sheet === det.sheet) ?? tables[0]
  const fmt = profiles.sources[det.source].formats[det.format!]
  const fields = (det.format === 'borelog_workbook' ? (fmt.fields as Record<string, Fields>).general : fmt.fields) as Fields
  const { index } = headerMap(table.headers, fields)
  const g = (row: (string | null)[], f: string) => (index[f] === undefined ? null : row[index[f]] ?? null)
  const by = (col: string) => {
    const m = new Map<string, typeof wh.rows>()
    for (const r of wh.rows) {
      const v = normId(r.values[col])
      if (v) m.set(v, [...(m.get(v) ?? []), r])
    }
    return m
  }
  const idCol = { AQUAWATCH: 'aqua_gwc_id', WPMS: 'wpms_go_code', GPANI: 'candidate_id', BORELOG: 'borelog_id' }[det.source]!
  const byId = by(idCol)
  const byGeocode = det.source === 'GPANI' ? by('gpani_source_geocode') : null
  const existingPeriods = new Set(wh.headers.map((h) => /^aqua_(\d{4})(?:_(wet|dry))?_arsenic_raw$/.exec(h)).filter(Boolean).map((m) => `${m![1]}_${m![2] ?? 'annual'}`))
  const seen = new Map<string, string>()
  const add = (rec: BrowserRecord) => {
    result.counts[rec.classification] = (result.counts[rec.classification] ?? 0) + 1
    if (result.records.length < 1000) result.records.push(rec)
  }
  for (const [optional] of Object.entries(fields)) result.completeness[optional] = 0

  table.rows.forEach((row, i) => {
    result.totalRows++
    const excelRow = table.headerRow + i + 1
    for (const f of Object.keys(index)) if (!g(row, f)) result.completeness[f] = (result.completeness[f] ?? 0) + 1
    let id: string | null
    let period: ReturnType<typeof parsePeriod> = null
    if (det.source === 'AQUAWATCH' && det.format === 'aquawatch_survey') {
      id = normId(g(row, 'resource_id'))
      period = parsePeriod(g(row, 'survey_year'))
      if (!id || !period) {
        add({ row: excelRow, id, period: null, classification: 'INVALID_SOURCE_RECORD', reason: !id ? 'Ground Water Resource ID is blank' : `Unrecognized survey period “${g(row, 'survey_year') ?? ''}”` })
        return
      }
      result.periods[period.key] = (result.periods[period.key] ?? 0) + 1
    } else if (det.source === 'GPANI') {
      const geo = asciiDigits(String(g(row, 'geocode') ?? '')).replace(/\D/g, '')
      const lat = num(g(row, 'latitude'))
      const lon = num(g(row, 'longitude'))
      id = geo ? `GP:${geo}@${lat !== null ? lat.toFixed(6) : 'NA'},${lon !== null ? lon.toFixed(6) : 'NA'}` : null
    } else id = normId(g(row, det.source === 'WPMS' ? 'go_code' : det.source === 'BORELOG' ? 'id' : 'gwc_id'))
    if (!id) {
      add({ row: excelRow, id, period: null, classification: 'INVALID_SOURCE_RECORD', reason: 'Source identifier is blank' })
      return
    }
    // Content excludes provenance (serial numbers), like the engine's fingerprints.
    const content = JSON.stringify(Object.keys(index).filter((f) => f !== 'serial').sort().map((f) => g(row, f)))
    const key = `${id}|${period?.key ?? ''}`
    if (seen.has(key)) {
      add({ row: excelRow, id, period: period?.key ?? null, classification: seen.get(key) === content ? 'EXACT_DUPLICATE' : 'CONFLICTING_MEASUREMENT', reason: 'Same observation appears earlier in this file' })
      return
    }
    seen.set(key, content)
    const rows = byId.get(normId(id) ?? '') ?? []
    if (rows.length > 1) {
      add({ row: excelRow, id, period: period?.key ?? null, classification: 'CONFLICTING_IDENTITY', reason: `ID appears on ${rows.length} warehouse rows` })
      return
    }
    if (rows.length === 1) {
      const wrow = rows[0]
      if (period) {
        const prefix = `aqua_${period.year}${period.season ? `_${period.season}` : ''}`
        const pairs: [string, string][] = [['arsenic', 'arsenic_raw'], ['iron', 'iron_raw'], ['chloride', 'chloride_raw'], ['water_table', 'water_table_m']]
        const present = pairs.filter(([, m]) => wrow.values[`${prefix}_${m}`] !== undefined)
        if (!present.length) add({ row: excelRow, id, period: period.key, classification: 'NEW_OBSERVATION', reason: `Adds ${period.key.replace('_', ' ')} values to row ${wrow.row}`, candidateRow: wrow.row })
        else {
          const diff = pairs.filter(([f, m]) => g(row, f) && wrow.values[`${prefix}_${m}`] !== undefined && !same(g(row, f), wrow.values[`${prefix}_${m}`]))
          const fill = pairs.filter(([f, m]) => g(row, f) && wrow.values[`${prefix}_${m}`] === undefined)
          if (diff.length) add({ row: excelRow, id, period: period.key, classification: 'UPDATED_MEASUREMENT', reason: `Differs from row ${wrow.row}: ${diff.map((d) => d[0]).join(', ')} (review required)`, candidateRow: wrow.row })
          else if (fill.length) add({ row: excelRow, id, period: period.key, classification: 'FILLED_MISSING_VALUE', reason: `Fills blank ${fill.map((d) => d[0]).join(', ')} on row ${wrow.row}`, candidateRow: wrow.row })
          else add({ row: excelRow, id, period: period.key, classification: 'UNCHANGED', reason: `Already present on row ${wrow.row}`, candidateRow: wrow.row })
        }
      } else if (det.source === 'WPMS') {
        const status = g(row, 'status')
        const changed = status && wrow.values.wpms_project_status_raw !== undefined && !same(status, wrow.values.wpms_project_status_raw)
        add({ row: excelRow, id, period: null, classification: changed ? 'NEW_SOURCE_METADATA' : 'UNCHANGED', reason: changed ? `Status differs from row ${wrow.row}` : `Already present on row ${wrow.row}`, candidateRow: wrow.row })
      } else add({ row: excelRow, id, period: null, classification: 'UNCHANGED', reason: `Source identity already on row ${wrow.row} (field-level comparison runs in the engine)`, candidateRow: wrow.row })
      return
    }
    if (byGeocode) {
      const geo = id.split('@')[0].slice(3)
      if (byGeocode.has(geo)) {
        add({ row: excelRow, id, period: null, classification: 'CONFLICTING_IDENTITY', reason: 'Geocode exists with different coordinates (geocodes are not unique well IDs)' })
        return
      }
    }
    const lat = num(g(row, 'latitude'))
    const lon = num(g(row, 'longitude'))
    if (inBd(lat, lon)) {
      const near = wh.rows.find((r) => {
        const la = Number(r.values.latitude)
        const lo = Number(r.values.longitude)
        return inBd(la, lo) && haversine(lat!, lon!, la, lo) <= 30
      })
      if (near) {
        add({ row: excelRow, id, period: period?.key ?? null, classification: 'POTENTIAL_DUPLICATE', reason: `Within 30 m of row ${near.row} — GPS alone is never proof of identity (review)`, candidateRow: near.row })
        return
      }
    }
    add({ row: excelRow, id, period: period?.key ?? null, classification: 'NEW_FACILITY', reason: 'No warehouse row carries this source ID; no compatible facility within 30 m' })
  })
  result.newPeriods = Object.keys(result.periods).filter((p) => !existingPeriods.has(p))
  return result
}

export async function previewFile(file: { name: string; data: ArrayBuffer }, wh: WarehouseBundle, hint?: string): Promise<BrowserPreviewResult> {
  const lower = file.name.toLowerCase()
  let tables: Table[]
  if (lower.endsWith('.csv') || lower.endsWith('.txt')) tables = parseCsv(new TextDecoder('utf-8').decode(file.data), file.name)
  else if (lower.endsWith('.xlsx') || lower.endsWith('.xlsm')) tables = parseXlsx(new Uint8Array(file.data))
  else throw new Error('The browser preview supports .csv and .xlsx. Legacy .xls files are processed by the local engine.')
  const det = detect(tables, hint || undefined)
  return classify(file.name, tables, det, wh)
}
