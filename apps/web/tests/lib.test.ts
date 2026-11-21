import { afterEach, describe, expect, it, vi } from 'vitest'
import { createProvider, DemoUnavailableError } from '@/lib/api'
import { detectMode } from '@/lib/runtime'
import { fmtNum, parseStamp, pct, periodLabel } from '@/lib/utils'

describe('utils', () => {
  it('formats numbers, shares and periods', () => {
    expect(fmtNum(626686)).toBe('626,686')
    expect(fmtNum(undefined)).toBe('—')
    expect(pct(1, 3)).toBe('33%')
    expect(pct(1, 10000)).toBe('<0.1%')
    expect(periodLabel('2027_dry')).toBe('2027 Dry season')
    expect(periodLabel('2025_annual')).toBe('2025 (annual)')
  })
  it('parses Python %z timestamps', () => {
    expect(parseStamp('2026-11-19T15:13:18+0600')?.toISOString()).toBe('2026-11-19T09:13:18.000Z')
  })
})

describe('runtime mode', () => {
  afterEach(() => {
    document.head.innerHTML = ''
  })
  it('a GitHub Pages build stays in demo mode even if a token is injected', () => {
    document.head.innerHTML = '<meta name="wash-sync-mode" content="local"><meta name="wash-sync-token" content="x">'
    expect(detectMode()).toBe('demo')
  })
})

describe('demo provider', () => {
  const records = [
    { rec_id: '0:2', source: 'AQUAWATCH', source_file: 'a.xlsx', source_record_id: 'SYN-1', classification: 'NEW_OBSERVATION', match: { candidate_ids: [] } },
    { rec_id: '0:3', source: 'GPANI', source_file: 'b.xlsx', source_record_id: 'GP:1', classification: 'NEW_FACILITY', match: { candidate_ids: [] } },
  ]
  const items = [{ item_id: 'R00001', rec_id: '0:2', classification: 'UPDATED_MEASUREMENT', title: 'A', reason: 'B', options: [{ decision: 'ACCEPT_NEW_VALUES', label: 'x', operations: [] }, { decision: 'REJECT', label: 'y', operations: [] }] }]

  function mockFetch() {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => {
        const body = url.endsWith('.records.json') ? records : url.endsWith('.items.json') ? items : {}
        return { ok: true, status: 200, json: async () => body } as Response
      }),
    )
  }
  afterEach(() => {
    vi.unstubAllGlobals()
    localStorage.clear()
  })

  it('filters records client-side', async () => {
    mockFetch()
    const api = createProvider('demo')
    expect((await api.records('p1', { source: 'GPANI' })).items.map((r) => r.rec_id)).toEqual(['0:3'])
    expect((await api.records('p1', { q: 'syn-1' })).total).toBe(1)
  })

  it('stores demo decisions only in the browser and requires a reason', async () => {
    mockFetch()
    const api = createProvider('demo')
    await expect(api.decide('p1', 'R00001', 'REJECT', '  ')).rejects.toThrow(/reason/)
    await api.decide('p1', 'R00001', 'REJECT', 'bad sample')
    const page = await api.items('p1', { status: 'decided' })
    expect(page.items[0].decision?.choice).toBe('REJECT')
    expect(page.items[0].decision?.decided_by).toMatch(/browser only/)
  })

  it('never pretends to run the engine', async () => {
    const api = createProvider('demo')
    await expect(api.apply('p1', true)).rejects.toBeInstanceOf(DemoUnavailableError)
    await expect(api.approve('p1')).rejects.toBeInstanceOf(DemoUnavailableError)
    expect(api.downloadVersionUrl(1)).toBeNull()
  })
})
