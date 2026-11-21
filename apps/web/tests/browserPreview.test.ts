import { describe, expect, it } from 'vitest'
import { classify, detect, normHeader, normId, parseCsv, parsePeriod, type WarehouseBundle } from '@/lib/browserPreview'

const HEADERS = 'SL,Survey Year,Ground Water Resource,Latitude,Longitude,Type of Tubewell,Depth of Tubewell,Division,District,Upazila,Union,Arsenic (AS),Water Table (m),Iron (Fe),Chloride (Cl)'

const warehouse: WarehouseBundle = {
  headers: ['candidate_id', 'aqua_gwc_id', 'latitude', 'longitude', 'aqua_2026_wet_arsenic_raw', 'aqua_2026_wet_water_table_m', 'aqua_2026_wet_iron_raw', 'aqua_2026_wet_chloride_raw'],
  rows: [
    { row: 2, values: { candidate_id: 'AQUA2026WET:SYN-1', aqua_gwc_id: 'SYN-1', latitude: 23.7, longitude: 90.4, aqua_2026_wet_arsenic_raw: 0.01, aqua_2026_wet_water_table_m: 5.5, aqua_2026_wet_iron_raw: 0.3, aqua_2026_wet_chloride_raw: 40 } },
    { row: 3, values: { candidate_id: 'GP:99000000000000000000001@24.000000,90.000000', latitude: 24.0, longitude: 90.0 } },
  ],
}

describe('parsePeriod', () => {
  it.each([
    ['2026 (Wet Season)', '2026_wet'],
    ['2026 (Dry Season)', '2026_dry'],
    ['2025', '2025_annual'],
    ['2024.0', '2024_annual'],
    ['Dry 2027', '2027_dry'],
  ])('%s → %s', (label, key) => {
    expect(parsePeriod(label)?.key).toBe(key)
  })
  it('rejects unknown season words instead of guessing', () => {
    expect(parsePeriod('2026 (Monsoon)')).toBeNull()
    expect(parsePeriod('')).toBeNull()
  })
})

describe('normalization', () => {
  it('normalizes identifiers and headers', () => {
    expect(normId(' syn-1 ')).toBe('SYN-1')
    expect(normId('১২৩.0')).toBe('123')
    expect(normId('N/A')).toBeNull()
    expect(normHeader('Arsenic (AS)')).toBe(normHeader('arsenic (As)'))
  })
})

describe('detect + classify', () => {
  const csv = [
    HEADERS,
    '1,2026 (Wet Season),SYN-1,23.7,90.4,Deep,120,Dhaka,D,U,X,0.01,5.5,0.3,40', // unchanged
    '2,2027 (Wet Season),SYN-1,23.7,90.4,Deep,120,Dhaka,D,U,X,0.02,5.1,0.3,41', // new observation
    '3,2027 (Wet Season),SYN-NEW,24.00001,90.00001,Deep,120,Dhaka,D,U,X,0.02,5.1,0.3,41', // near row 3 → possible duplicate
    '4,2027 (Wet Season),SYN-FAR,25.5,89.5,Deep,120,Dhaka,D,U,X,0.02,5.1,0.3,41', // new facility
    '5,2027 (Wet Season),,25.5,89.5,Deep,120,Dhaka,D,U,X,0.02,5.1,0.3,41', // invalid
    '6,2027 (Wet Season),SYN-FAR,25.5,89.5,Deep,120,Dhaka,D,U,X,0.02,5.1,0.3,41', // exact duplicate of row 4
    '7,2026 (Wet Season),SYN-1,23.7,90.4,Deep,120,Dhaka,D,U,X,0.09,5.5,0.3,40', // duplicate key, different content
  ].join('\n')

  it('detects AquaWatch by header names regardless of order and case', () => {
    const det = detect(parseCsv(csv))
    expect(det.source).toBe('AQUAWATCH')
    expect(det.format).toBe('aquawatch_survey')
    expect(det.missingRequired).toEqual([])
  })

  it('classifies records against the demo warehouse', () => {
    const tables = parseCsv(csv)
    const result = classify('s.csv', tables, detect(tables), warehouse)
    const byRow = Object.fromEntries(result.records.map((r) => [r.row, r.classification]))
    expect(byRow[2]).toBe('UNCHANGED')
    expect(byRow[3]).toBe('NEW_OBSERVATION')
    expect(byRow[4]).toBe('POTENTIAL_DUPLICATE')
    expect(byRow[5]).toBe('NEW_FACILITY')
    expect(byRow[6]).toBe('INVALID_SOURCE_RECORD')
    expect(byRow[7]).toBe('EXACT_DUPLICATE')
    expect(byRow[8]).toBe('CONFLICTING_MEASUREMENT')
    expect(result.newPeriods).toEqual(['2027_wet'])
  })

  it('flags personal-data columns and never needs them', () => {
    const tables = parseCsv('GO Code,Division,Caretaker Name,Mobile Number,Type\n980001,ঢাকা,Someone,0170,Deep')
    const det = detect(tables)
    expect(det.source).toBe('WPMS')
    expect(det.personalData).toEqual(['Caretaker Name', 'Mobile Number'])
  })

  it('reports unrecognized files', () => {
    expect(detect(parseCsv('a,b,c\n1,2,3')).source).toBeNull()
  })
})
