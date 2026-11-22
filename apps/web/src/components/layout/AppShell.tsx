import * as React from 'react'
import { NavLink, useLocation } from 'react-router-dom'
import {
  BookOpen,
  Database,
  FileSearch,
  GitCompareArrows,
  Github,
  History,
  LayoutDashboard,
  Menu,
  Moon,
  Scale,
  Settings as SettingsIcon,
  ShieldCheck,
  Sparkles,
  Sun,
  TableProperties,
  UploadCloud,
  UserRound,
  X,
} from 'lucide-react'
import { Badge, Input, Tooltip } from '@/components/ui/primitives'
import { Button } from '@/components/ui/button'
import { useData, reviewerName, setReviewerName } from '@/lib/api'
import { APP_VERSION, REPO_URL } from '@/lib/runtime'
import { cn } from '@/lib/utils'

const NAV = [
  { group: 'Overview', items: [{ to: '/', label: 'Dashboard', icon: LayoutDashboard, end: true }] },
  {
    group: 'Synchronize',
    items: [
      { to: '/sync/new', label: 'New Sync', icon: UploadCloud },
      { to: '/sync/preview', label: 'Sync Preview', icon: FileSearch },
      { to: '/conflicts', label: 'Conflicts Center', icon: Scale },
      { to: '/inspect', label: 'Match Inspector', icon: GitCompareArrows },
    ],
  },
  {
    group: 'Warehouse',
    items: [
      { to: '/explorer', label: 'Warehouse Explorer', icon: TableProperties },
      { to: '/quality', label: 'Data Quality', icon: ShieldCheck },
      { to: '/history', label: 'Sync History', icon: History },
    ],
  },
  {
    group: 'System',
    items: [
      { to: '/settings', label: 'Settings', icon: SettingsIcon },
      { to: '/docs', label: 'Documentation', icon: BookOpen },
    ],
  },
]

function useTheme(): [boolean, () => void] {
  const [dark, setDark] = React.useState(() => document.documentElement.classList.contains('dark'))
  const toggle = React.useCallback(() => {
    setDark((d) => {
      const next = !d
      document.documentElement.classList.toggle('dark', next)
      try {
        localStorage.setItem('wash-theme', next ? 'dark' : 'light')
      } catch {
        /* ignore */
      }
      return next
    })
  }, [])
  return [dark, toggle]
}

function Brand() {
  return (
    <div className="flex items-center gap-2.5 px-2">
      <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-gradient-to-br from-sky-400 to-teal-600 shadow-lg shadow-teal-900/30">
        <Database className="h-[18px] w-[18px] text-white" aria-hidden />
      </div>
      <div className="leading-tight">
        <div className="text-[15px] font-semibold tracking-tight text-sidebar-foreground">WASH Warehouse</div>
        <div className="text-[11px] font-medium uppercase tracking-[0.14em] text-sidebar-muted">Incremental Sync</div>
      </div>
    </div>
  )
}

function SidebarNav({ onNavigate }: { onNavigate?: () => void }) {
  return (
    <nav className="space-y-6" aria-label="Main">
      {NAV.map((g) => (
        <div key={g.group}>
          <div className="mb-1.5 px-3 text-[11px] font-semibold uppercase tracking-[0.12em] text-sidebar-muted/80">{g.group}</div>
          <ul className="space-y-0.5">
            {g.items.map((item) => (
              <li key={item.to}>
                <NavLink
                  to={item.to}
                  end={'end' in item ? item.end : false}
                  onClick={onNavigate}
                  className={({ isActive }) =>
                    cn(
                      'group flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors',
                      isActive
                        ? 'bg-white/10 text-white shadow-inner'
                        : 'text-sidebar-muted hover:bg-white/5 hover:text-sidebar-foreground',
                    )
                  }
                >
                  {({ isActive }) => (
                    <>
                      <item.icon className={cn('h-4 w-4', isActive ? 'text-sky-300' : 'text-sidebar-muted group-hover:text-sidebar-foreground')} aria-hidden />
                      {item.label}
                    </>
                  )}
                </NavLink>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </nav>
  )
}

function ModeCard() {
  const { mode } = useData()
  return (
    <div className="rounded-xl border border-white/10 bg-white/[0.04] p-3.5 text-xs">
      <div className="flex items-center gap-2 font-semibold text-sidebar-foreground">
        <span className={cn('h-2 w-2 rounded-full', mode === 'local' ? 'bg-emerald-400 shadow-[0_0_8px] shadow-emerald-400' : 'bg-amber-400')} />
        {mode === 'local' ? 'Local Production Mode' : 'Sample-data website'}
      </div>
      <p className="mt-1.5 leading-relaxed text-sidebar-muted">
        {mode === 'local'
          ? 'Connected to the engine on this computer. Changes are written to new private versions only after approval.'
          : 'Synthetic data only. Your real warehouse is loaded and synchronized in the app running on your computer.'}
      </p>
      <div className="mt-2 text-[11px] text-sidebar-muted/80">v{APP_VERSION}</div>
    </div>
  )
}

export function ModeBadge() {
  const { mode } = useData()
  return mode === 'local' ? (
    <Tooltip content="Served by `wash-sync serve` on this computer. API calls stay on 127.0.0.1 and require the session token.">
      <span>
        <Badge tone="success" dot>
          Local production
        </Badge>
      </span>
    </Tooltip>
  ) : (
    <Tooltip content="Static public demo with clearly synthetic data. It never connects to a warehouse or a local engine.">
      <span>
        <Badge tone="warning" dot>
          Demo · synthetic data
        </Badge>
      </span>
    </Tooltip>
  )
}

function ReviewerField() {
  const [name, setName] = React.useState(reviewerName)
  return (
    <div className="hidden items-center gap-2 lg:flex">
      <UserRound className="h-4 w-4 text-muted-foreground" aria-hidden />
      <label htmlFor="reviewer" className="sr-only">
        Reviewer name
      </label>
      <Input
        id="reviewer"
        value={name}
        placeholder="Reviewer name"
        className="h-8 w-40"
        onChange={(e) => {
          setName(e.target.value)
          setReviewerName(e.target.value)
        }}
      />
    </div>
  )
}

function DemoBanner() {
  const { mode } = useData()
  if (mode !== 'demo') return null
  return (
    <div className="border-b border-amber-500/20 bg-amber-500/[0.08] px-4 py-2 text-center text-[13px] text-amber-800 dark:text-amber-200">
      <Sparkles className="mr-1.5 inline h-3.5 w-3.5 -translate-y-px" aria-hidden />
      <strong className="font-semibold">Sample-data website.</strong> Everything here is synthetic (processed by the real engine) so you can try every
      step. To load your real warehouse and sync new files into it, run the same app on your computer —{' '}
      <NavLink to="/docs/getting-started" className="font-medium underline underline-offset-2">
        how to run it
      </NavLink>
      .
    </div>
  )
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const [dark, toggleTheme] = useTheme()
  const [mobileOpen, setMobileOpen] = React.useState(false)
  const location = useLocation()
  const { mode } = useData()
  React.useEffect(() => setMobileOpen(false), [location.pathname])

  return (
    <div className="min-h-screen bg-background">
      <a href="#main" className="sr-only focus:not-sr-only focus:fixed focus:left-3 focus:top-3 focus:z-[100] focus:rounded-md focus:bg-card focus:px-3 focus:py-2">
        Skip to content
      </a>
      <aside className="fixed inset-y-0 left-0 z-40 hidden w-64 flex-col bg-sidebar px-3 py-5 lg:flex">
        <Brand />
        <div className="mt-8 flex-1 overflow-y-auto scrollbar-thin">
          <SidebarNav />
        </div>
        <ModeCard />
      </aside>

      {mobileOpen && (
        <div className="fixed inset-0 z-50 lg:hidden" role="dialog" aria-modal="true" aria-label="Navigation">
          <div className="absolute inset-0 bg-slate-950/60" onClick={() => setMobileOpen(false)} />
          <div className="absolute inset-y-0 left-0 flex w-72 flex-col bg-sidebar px-3 py-5 shadow-2xl animate-in slide-in-from-left">
            <div className="flex items-center justify-between">
              <Brand />
              <button className="rounded-md p-2 text-sidebar-muted hover:text-white" onClick={() => setMobileOpen(false)} aria-label="Close navigation">
                <X className="h-5 w-5" />
              </button>
            </div>
            <div className="mt-8 flex-1 overflow-y-auto">
              <SidebarNav onNavigate={() => setMobileOpen(false)} />
            </div>
            <ModeCard />
          </div>
        </div>
      )}

      <div className="lg:pl-64">
        <DemoBanner />
        <header className="sticky top-0 z-30 flex h-14 items-center gap-3 border-b bg-background/85 px-4 backdrop-blur md:px-6">
          <Button variant="ghost" size="icon" className="lg:hidden" onClick={() => setMobileOpen(true)} aria-label="Open navigation">
            <Menu className="h-5 w-5" />
          </Button>
          <div className="flex min-w-0 flex-1 items-center gap-3">
            <ModeBadge />
          </div>
          {mode === 'local' && <ReviewerField />}
          <Tooltip content={dark ? 'Switch to light theme' : 'Switch to dark theme'}>
            <Button variant="ghost" size="icon" onClick={toggleTheme} aria-label={dark ? 'Switch to light theme' : 'Switch to dark theme'}>
              {dark ? <Sun /> : <Moon />}
            </Button>
          </Tooltip>
          <Tooltip content="Source code on GitHub">
            <Button variant="ghost" size="icon" asChild>
              <a href={REPO_URL} target="_blank" rel="noreferrer noopener" aria-label="Source code on GitHub">
                <Github />
              </a>
            </Button>
          </Tooltip>
        </header>
        <main id="main" className="mx-auto w-full max-w-[1440px] px-4 py-6 md:px-8 md:py-8">
          {children}
        </main>
        <footer className="mx-auto max-w-[1440px] px-4 pb-8 text-xs text-muted-foreground md:px-8">
          WASH Warehouse Sync v{APP_VERSION} · single-sheet Excel warehouse · every change is versioned, verified and audited.
        </footer>
      </div>
    </div>
  )
}
