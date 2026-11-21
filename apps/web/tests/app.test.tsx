import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { AppRoutes } from '@/App'
import { AppShell } from '@/components/layout/AppShell'
import { TooltipProvider } from '@/components/ui/primitives'
import { DataContext, DemoUnavailableError, type DataProvider } from '@/lib/api'
import type { Plan, PlanListItem, ReviewItem, StatusResponse, VersionRecord } from '@/lib/types'

const version: VersionRecord = {
  version: 1, file: 'WASH_Warehouse_v001.xlsx', sha256: 'a'.repeat(64), size: 1000, created_at: '2026-11-19T10:00:00+0600',
  previous_version: 0, kind: 'SYNC', plan_id: 'plan-1', rows: 2358, columns: 201, sources: [],
  counts: { added_facilities: 18, new_observations: 120 }, validation: { ok: true, checks: [] }, schema_version: '2026.11', engine_version: '1.0.0',
}
const planList: PlanListItem = {
  plan_id: 'plan-2', created_at: '2026-11-19T11:00:00+0600', status: 'DRAFT', base_version: 1, files: ['wet.xlsx'],
  summary: { NEW_FACILITY: 3, POTENTIAL_DUPLICATE: 1 }, review_items: 1, undecided: 1, new_columns: [],
}
const item: ReviewItem = {
  item_id: 'R00001', rec_id: '0:5', classification: 'POTENTIAL_DUPLICATE', title: 'AQUAWATCH SYN-C1: 1 nearby compatible facility',
  reason: 'GPS alone is not proof of identity', recommended: 1,
  options: [
    { decision: 'APPROVE_MATCH', label: 'Same facility as row 12', candidate_row: 12, operations: [] },
    { decision: 'KEEP_SEPARATE', label: 'Keep as a separate facility (new row)', operations: [] },
    { decision: 'REJECT', label: 'Reject this source record', operations: [] },
  ],
}

function fakeProvider(mode: 'demo' | 'local' = 'demo'): DataProvider {
  const status: StatusResponse = {
    mode, engine_version: '1.0.0', schema_version: '2026.11', initialized: true, current_version: version, versions: 2,
    last_sync: version, recent_plans: [planList], recent_events: [{ at: '2026-11-19T10:00:00+0600', event: 'APPLY', version: 1, previous_version: 0, by: 'tester' }],
    pending_conflicts: 2,
  }
  const plan = { plan_id: 'plan-2', created_at: planList.created_at, engine_version: '1.0.0', schema_version: '2026.11', status: 'DRAFT', base_version: 1,
    base_sha256: 'b'.repeat(64), base_rows: 2358, base_columns: 201, sources: [], new_columns: [],
    summary: { plan_id: 'plan-2', base_version: 1, base_rows: 2358, records_total: 4, by_classification: { NEW_FACILITY: 3, POTENTIAL_DUPLICATE: 1 }, by_source: {}, auto_operations: 3, auto_inserts: 3, auto_updates: 0, review_items: 1, undecided_review_items: 1, new_columns: [], files: [] },
    review_counts: { total: 1, undecided: 1 } } as unknown as Plan
  const none = () => Promise.reject(new DemoUnavailableError('This action'))
  return {
    mode,
    status: async () => status,
    init: none,
    history: async () => ({ current_version: 1, versions: [version], events: status.recent_events }),
    pending: async () => [],
    plans: async () => [planList],
    plan: async () => plan,
    records: async () => ({ total: 0, offset: 0, limit: 50, items: [] }),
    items: async () => ({ total: 1, offset: 0, limit: 50, items: [item] }),
    decide: vi.fn(async () => undefined),
    bulkDecide: vi.fn(async () => 0),
    approve: none,
    apply: none,
    upload: none,
    preview: none,
    job: none,
    cancelJob: none,
    schema: async () => ({ headers: ['candidate_id'], columns: [], rows: 2358, periods: [], sheet: 'FLAT_WASH_WAREHOUSE' }),
    rows: async () => ({ total: 0, offset: 0, limit: 50, columns: [], items: [] }),
    row: async (row: number) => ({ row, values: {} }),
    facets: async () => ({ division: [], match_status: [] }),
    quality: async () => ({ rows: 2358, columns: [], quality: { rows: 2358, gps: { valid: 2358 }, missing_admin: {}, missing_facility_type: 0, top_flags: [], sources: { GPANI: 1800 }, match_status: [], outliers: {}, water_table_outliers: 0, unit_issue_flags: 0, conflict_flags: 0, duplicate_identifiers: {}, shared_exact_coordinates: 0 } }),
    settings: none,
    saveSettings: none,
    rollback: none,
    verify: none,
    auditUrl: (p, s, f) => `/demo/audit/${p}/${s}/${f}`,
    downloadVersionUrl: () => null,
  }
}

function renderAt(path: string, provider = fakeProvider()) {
  return render(
    <DataContext.Provider value={provider}>
      <TooltipProvider>
        <MemoryRouter initialEntries={[path]}>
          <AppShell>
            <AppRoutes />
          </AppShell>
        </MemoryRouter>
      </TooltipProvider>
    </DataContext.Provider>,
  )
}

describe('application shell', () => {
  it('renders the dashboard with real status values and a demo banner', async () => {
    renderAt('/')
    expect(await screen.findByRole('heading', { name: 'Warehouse dashboard' })).toBeInTheDocument()
    expect(await screen.findByText('2,358')).toBeInTheDocument()
    expect(screen.getByText('v001')).toBeInTheDocument()
    expect(screen.getByText(/Public demonstration/)).toBeInTheDocument()
    expect(screen.getAllByText(/Demo · synthetic data/).length).toBeGreaterThan(0)
  })

  it('shows a local-mode indicator without the demo banner', async () => {
    renderAt('/', fakeProvider('local'))
    expect(await screen.findByRole('heading', { name: 'Warehouse dashboard' })).toBeInTheDocument()
    expect(screen.queryByText(/Public demonstration/)).not.toBeInTheDocument()
    expect(screen.getAllByText('Local production').length).toBeGreaterThan(0)
  })

  it('navigates between pages with the sidebar', async () => {
    const user = userEvent.setup()
    renderAt('/')
    await user.click(screen.getAllByRole('link', { name: 'Documentation' })[0])
    expect(await screen.findByRole('heading', { name: 'Documentation' })).toBeInTheDocument()
    await user.click(screen.getAllByRole('link', { name: 'Conflicts Center' })[0])
    expect(await screen.findByRole('heading', { name: 'Conflicts Center' })).toBeInTheDocument()
  })

  it('toggles the dark theme and remembers it', async () => {
    const user = userEvent.setup()
    renderAt('/docs')
    await user.click(screen.getByRole('button', { name: /Switch to dark theme/ }))
    expect(document.documentElement.classList.contains('dark')).toBe(true)
    expect(localStorage.getItem('wash-theme')).toBe('dark')
    await user.click(screen.getByRole('button', { name: /Switch to light theme/ }))
    expect(document.documentElement.classList.contains('dark')).toBe(false)
  })

  it('renders a not-found page for unknown routes', async () => {
    renderAt('/does-not-exist')
    expect(await screen.findByRole('heading', { name: 'Page not found' })).toBeInTheDocument()
  })
})

describe('conflict resolution', () => {
  it('requires a reason and records the chosen option', async () => {
    const user = userEvent.setup()
    const provider = fakeProvider()
    renderAt('/conflicts?plan=plan-2&item=R00001', provider)
    // jsdom is narrower than the desktop breakpoint, so the decision panel opens as a modal sheet.
    const panel = await screen.findByRole('dialog')
    expect(within(panel).getByText('Keep as a separate facility (new row)')).toBeInTheDocument()
    const submit = within(panel).getByRole('button', { name: 'Record decision' })
    await user.click(submit)
    expect(provider.decide).not.toHaveBeenCalled()
    await user.type(within(panel).getByLabelText(/Reason/), 'Different tubewell at a shared project point')
    await user.click(submit)
    await waitFor(() =>
      expect(provider.decide).toHaveBeenCalledWith('plan-2', 'R00001', 'KEEP_SEPARATE', 'Different tubewell at a shared project point', 1),
    )
  })
})

describe('missing backend messaging', () => {
  it('explains that applying needs Local Production Mode', async () => {
    const user = userEvent.setup()
    renderAt('/sync/preview/plan-2')
    await user.click(await screen.findByRole('button', { name: /Apply plan/ }))
    expect(await screen.findByText(/runs only in Local Production Mode/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Apply and publish' })).not.toBeInTheDocument()
  })
})
