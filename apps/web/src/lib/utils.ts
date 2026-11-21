import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

const nf = new Intl.NumberFormat('en-US')

export function fmtNum(value: number | null | undefined, fallback = '—'): string {
  if (value === null || value === undefined || Number.isNaN(value)) return fallback
  return nf.format(value)
}

export function fmtCompact(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  return new Intl.NumberFormat('en-US', { notation: 'compact', maximumFractionDigits: 1 }).format(value)
}

export function fmtBytes(bytes: number | null | undefined): string {
  if (!bytes && bytes !== 0) return '—'
  const units = ['B', 'KB', 'MB', 'GB']
  let i = 0
  let v = bytes
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024
    i++
  }
  return `${v.toFixed(v >= 10 || i === 0 ? 0 : 1)} ${units[i]}`
}

export function parseStamp(stamp: string | null | undefined): Date | null {
  if (!stamp) return null
  // Python strftime('%z') produces +0600; normalise to +06:00 for Date parsing.
  const fixed = stamp.replace(/([+-]\d{2})(\d{2})$/, '$1:$2')
  const d = new Date(fixed)
  return Number.isNaN(d.getTime()) ? null : d
}

export function fmtDate(stamp: string | null | undefined, withTime = true): string {
  const d = parseStamp(stamp)
  if (!d) return stamp ?? '—'
  return d.toLocaleString('en-GB', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
    ...(withTime ? { hour: '2-digit', minute: '2-digit' } : {}),
  })
}

export function fmtRelative(stamp: string | null | undefined): string {
  const d = parseStamp(stamp)
  if (!d) return '—'
  const diff = (Date.now() - d.getTime()) / 1000
  const rtf = new Intl.RelativeTimeFormat('en', { numeric: 'auto' })
  const steps: [number, Intl.RelativeTimeFormatUnit][] = [
    [60, 'second'],
    [3600, 'minute'],
    [86400, 'hour'],
    [86400 * 30, 'day'],
    [86400 * 365, 'month'],
  ]
  let prev = 1
  for (const [limit, unit] of steps) {
    if (Math.abs(diff) < limit) return rtf.format(-Math.round(diff / prev), unit)
    prev = limit
  }
  return rtf.format(-Math.round(diff / (86400 * 365)), 'year')
}

export function fmtValue(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'number') return Number.isInteger(value) ? nf.format(value) : String(value)
  if (typeof value === 'object') return JSON.stringify(value)
  return String(value)
}

export function pct(part: number, total: number): string {
  if (!total) return '0%'
  const p = (part / total) * 100
  return `${p < 0.1 && p > 0 ? '<0.1' : p.toFixed(p < 10 ? 1 : 0)}%`
}

export function periodLabel(key: string | null | undefined): string {
  if (!key) return '—'
  const [year, season] = key.split('_')
  if (season === 'annual') return `${year} (annual)`
  if (season === 'wet' || season === 'dry') return `${year} ${season === 'wet' ? 'Wet' : 'Dry'} season`
  return key
}

export function downloadText(filename: string, text: string, type = 'application/json') {
  const blob = new Blob([text], { type })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
