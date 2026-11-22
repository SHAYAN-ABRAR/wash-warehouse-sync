import * as React from 'react'
import { BrowserRouter, Route, Routes } from 'react-router-dom'
import { Toaster } from 'sonner'
import { AppShell } from '@/components/layout/AppShell'
import { TooltipProvider } from '@/components/ui/primitives'
import { LoadingBlock } from '@/components/common'
import { DataContext, RevisionContext, createProvider, type DataProvider } from '@/lib/api'
import { routerBasename } from '@/lib/runtime'
import Dashboard from '@/pages/Dashboard'

const NewSync = React.lazy(() => import('@/pages/NewSync'))
const SyncPreview = React.lazy(() => import('@/pages/SyncPreview'))
const Conflicts = React.lazy(() => import('@/pages/Conflicts'))
const MatchInspector = React.lazy(() => import('@/pages/MatchInspector'))
const Explorer = React.lazy(() => import('@/pages/Explorer'))
const Quality = React.lazy(() => import('@/pages/Quality'))
const History = React.lazy(() => import('@/pages/History'))
const SettingsPage = React.lazy(() => import('@/pages/Settings'))
const Docs = React.lazy(() => import('@/pages/Docs'))
const NotFound = React.lazy(() => import('@/pages/NotFound'))

export function AppRoutes() {
  return (
    <React.Suspense fallback={<LoadingBlock rows={6} />}>
      <Routes>
        <Route path="/" element={<Dashboard />} />
        <Route path="/sync/new" element={<NewSync />} />
        <Route path="/sync/preview" element={<SyncPreview />} />
        <Route path="/sync/preview/:planId" element={<SyncPreview />} />
        <Route path="/conflicts" element={<Conflicts />} />
        <Route path="/inspect" element={<MatchInspector />} />
        <Route path="/explorer" element={<Explorer />} />
        <Route path="/quality" element={<Quality />} />
        <Route path="/history" element={<History />} />
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="/docs" element={<Docs />} />
        <Route path="/docs/:section" element={<Docs />} />
        <Route path="*" element={<NotFound />} />
      </Routes>
    </React.Suspense>
  )
}

/** Lets any page ask every page to reload its data (after a warehouse load, apply or rollback). */
export function RevisionProvider({ children }: { children: React.ReactNode }) {
  const [revision, setRevision] = React.useState(0)
  const value = React.useMemo(() => ({ revision, refreshAll: () => setRevision((r) => r + 1) }), [revision])
  return <RevisionContext.Provider value={value}>{children}</RevisionContext.Provider>
}

export default function App({ provider }: { provider?: DataProvider }) {
  const value = React.useMemo(() => provider ?? createProvider(), [provider])
  return (
    <DataContext.Provider value={value}>
      <RevisionProvider>
        <TooltipProvider>
          <BrowserRouter basename={routerBasename()}>
            <AppShell>
              <AppRoutes />
            </AppShell>
          </BrowserRouter>
          <Toaster richColors position="bottom-right" closeButton />
        </TooltipProvider>
      </RevisionProvider>
    </DataContext.Provider>
  )
}
