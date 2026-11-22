import * as React from 'react'
import { NavLink, useParams } from 'react-router-dom'
import {
  ArrowDown,
  ArrowRight,
  BookOpen,
  Boxes,
  CheckCircle2,
  Cloud,
  Cpu,
  FileSpreadsheet,
  GitBranch,
  Globe,
  HardDrive,
  Laptop,
  LifeBuoy,
  Lock,
  Scale,
  ShieldCheck,
  Terminal,
  XCircle,
} from 'lucide-react'
import { PageHeader } from '@/components/common'
import { Badge, Card, CardContent } from '@/components/ui/primitives'
import { CLASS_META, CLASS_ORDER, GLOSSARY } from '@/lib/meta'
import { REPO_URL } from '@/lib/runtime'
import { cn } from '@/lib/utils'

const SECTIONS = [
  { id: 'getting-started', label: 'Getting started', icon: BookOpen },
  { id: 'modes', label: 'Demo vs. production', icon: Globe },
  { id: 'architecture', label: 'Architecture', icon: Boxes },
  { id: 'matching', label: 'Matching & safety rules', icon: Scale },
  { id: 'quality', label: 'Data-quality rules', icon: ShieldCheck },
  { id: 'cli', label: 'Command line', icon: Terminal },
  { id: 'troubleshooting', label: 'Troubleshooting', icon: LifeBuoy },
]

function Code({ children }: { children: string }) {
  return (
    <pre className="overflow-x-auto rounded-lg border bg-slate-950 px-4 py-3 text-[13px] leading-relaxed text-slate-100 scrollbar-thin">
      <code>{children}</code>
    </pre>
  )
}
function H({ children }: { children: React.ReactNode }) {
  return <h2 className="mt-8 text-lg font-semibold tracking-tight first:mt-0">{children}</h2>
}
function P({ children }: { children: React.ReactNode }) {
  return <p className="mt-3 text-[15px] leading-relaxed text-muted-foreground">{children}</p>
}

function Step({ n, title, children }: { n: number; title: string; children: React.ReactNode }) {
  return (
    <li className="relative pl-12">
      <span className="absolute left-0 top-0 flex h-8 w-8 items-center justify-center rounded-full bg-primary text-sm font-semibold text-primary-foreground">{n}</span>
      <h3 className="font-semibold">{title}</h3>
      <div className="mt-1.5 space-y-2 text-[15px] leading-relaxed text-muted-foreground">{children}</div>
    </li>
  )
}

function GettingStarted() {
  return (
    <div>
      <H>Run it on your computer</H>
      <P>
        The sync engine runs on the computer that holds your warehouse, so the private file never goes to a website. You need Python 3.12+ and Node.js 20+
        (used once to build the interface). One command installs everything on first use and opens the app in your browser:
      </P>
      <Code>{`git clone ${REPO_URL}.git
cd wash-warehouse-sync
./scripts/start-local.sh          # Windows PowerShell: scripts\\start-local.ps1`}</Code>
      <P>
        The app opens at <code>http://127.0.0.1:8765</code>, which only this computer can reach. It looks like this website but works with your real files,
        kept in a private state folder (choose it with <code>--state-dir</code>).
      </P>
      <H>Your first synchronization</H>
      <ol className="mt-6 space-y-7">
        <Step n={1} title="Load the current warehouse">
          <p>
            Open <em>New Sync</em> and, in step 1, choose your latest warehouse workbook. It is checked (single worksheet, structure, ranges), copied into the
            private state folder and prepared for search — about 5 minutes for 600,000 rows. The original file is never changed. From then on every page —
            Dashboard, Warehouse Explorer, Data Quality, Sync History — shows this warehouse.
          </p>
        </Step>
        <Step n={2} title="Add the files to sync and run the dry run">
          <p>
            In step 2, drop the new GPANI, WPMS, AquaWatch or bore-log export(s). The source system, columns and survey period are detected from the header
            row. The dry run compares every record with the warehouse: records already in it are recognized as unchanged, so nothing is added twice. The
            warehouse itself is not modified.
          </p>
        </Step>
        <Step n={3} title="Review what needs a decision">
          <p>
            Changed measurements, possible duplicates and identity conflicts wait in the <em>Conflicts Center</em> (or <em>Match Inspector</em>). Every decision
            needs a reason.
          </p>
        </Step>
        <Step n={4} title="Approve and apply">
          <p>
            The engine writes a new workbook, verifies it, proves that every untouched cell was preserved and publishes it as the next version (for example{' '}
            <code>WASH_Warehouse_v001.xlsx</code>). All pages refresh to show it; download it from <em>Sync History</em>.
          </p>
        </Step>
        <Step n={5} title="Next time">
          <p>
            The latest version is remembered, so go straight to step 2 with the new exports. If the warehouse was edited outside the app (for example QA
            corrections in Excel), load that file in step 1 first: it becomes the next version and earlier versions stay available.
          </p>
        </Step>
      </ol>
      <H>Glossary</H>
      <dl className="mt-4 grid grid-cols-1 gap-3 md:grid-cols-2">
        {Object.entries(GLOSSARY).map(([k, v]) => (
          <div key={k} className="rounded-lg border p-3">
            <dt className="font-medium">{k}</dt>
            <dd className="mt-1 text-sm text-muted-foreground">{v}</dd>
          </div>
        ))}
      </dl>
    </div>
  )
}

function Modes() {
  const rows: [string, boolean, boolean][] = [
    ['Browse documentation and architecture', true, true],
    ['Synthetic dashboards, plans, conflicts and audits (produced by the real engine)', true, false],
    ['Preview small CSV/XLSX files in the browser (simplified rules, nothing uploaded)', true, true],
    ['Load your own current warehouse', false, true],
    ['Full dry run against the real warehouse', false, true],
    ['Record review decisions that affect the warehouse', false, true],
    ['Approve, apply, verify and publish new versions', false, true],
    ['Download real workbooks and private audits', false, true],
    ['Contacts a local engine or any external service', false, false],
  ]
  return (
    <div>
      <div className="rounded-xl border border-amber-500/30 bg-amber-500/[0.06] p-4 text-[15px] leading-relaxed">
        <strong>This public website is the sample-data version of the app.</strong> It shows every screen with synthetic data and can preview small files in
        your browser. Loading your real warehouse and synchronizing new files into it happens in the same app running on your computer (Local Production
        Mode) — see <em>Getting started</em>. A public website cannot run the sync engine, and it should never receive a private warehouse.
      </div>
      <div className="mt-6 overflow-hidden rounded-xl border">
        <table className="w-full text-sm">
          <thead className="bg-muted text-xs uppercase tracking-wide text-muted-foreground">
            <tr>
              <th className="px-4 py-2.5 text-left">Capability</th>
              <th className="px-4 py-2.5 text-center">This website (sample data)</th>
              <th className="px-4 py-2.5 text-center">On your computer</th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {rows.map(([label, demo, local]) => (
              <tr key={label}>
                <td className="px-4 py-2.5">{label}</td>
                {[demo, local].map((ok, i) => (
                  <td key={i} className="px-4 py-2.5 text-center">
                    {ok ? <CheckCircle2 className="mx-auto h-4 w-4 text-success" aria-label="yes" /> : <XCircle className="mx-auto h-4 w-4 text-muted-foreground" aria-label="no" />}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <H>Why the public site never connects to your computer</H>
      <P>
        A public web page that could reach a local engine would expose it to every other website as well. The local engine therefore binds to 127.0.0.1,
        rejects unexpected Host headers, sends no CORS headers and requires a per-session token that only the page it serves can read. The GitHub Pages
        build is compiled in demo mode and contains no code path to a local API, and no one is ever asked to paste tokens into it.
      </P>
    </div>
  )
}

function Node({ icon: Icon, title, text, tone = 'default' }: { icon: typeof Cpu; title: string; text: string; tone?: 'default' | 'private' | 'public' }) {
  return (
    <div
      className={cn(
        'rounded-xl border bg-card p-4 shadow-card',
        tone === 'private' && 'border-emerald-500/40',
        tone === 'public' && 'border-sky-500/40',
      )}
    >
      <div className="flex items-center gap-2 font-semibold">
        <Icon className={cn('h-4 w-4', tone === 'private' ? 'text-emerald-500' : tone === 'public' ? 'text-sky-500' : 'text-primary')} /> {title}
      </div>
      <p className="mt-1.5 text-sm text-muted-foreground">{text}</p>
    </div>
  )
}

function Architecture() {
  return (
    <div>
      <H>Two deployments, one interface</H>
      <div className="mt-5 grid grid-cols-1 gap-6 lg:grid-cols-2">
        <div className="space-y-3 rounded-2xl border border-dashed border-sky-500/40 p-4">
          <Badge tone="info">Public · GitHub Pages</Badge>
          <Node icon={Cloud} title="Static React app" text="Demo mode only. Ships synthetic data generated by the real engine and an in-browser preview worker." tone="public" />
          <div className="flex justify-center text-muted-foreground">
            <ArrowDown className="h-5 w-5" />
          </div>
          <Node icon={Globe} title="Visitor's browser" text="Parses sample files locally (Web Worker). Nothing is uploaded; no engine is contacted." tone="public" />
        </div>
        <div className="space-y-3 rounded-2xl border border-dashed border-emerald-500/40 p-4">
          <Badge tone="success">Private · your computer</Badge>
          <Node icon={Laptop} title="Same React app (local build)" text="Served by the engine with a session token; all actions are enabled." tone="private" />
          <div className="flex justify-center text-muted-foreground">
            <ArrowDown className="h-5 w-5" />
          </div>
          <Node icon={Cpu} title="FastAPI on 127.0.0.1" text="Background jobs with real progress, no CORS, trusted hosts only." tone="private" />
          <div className="flex justify-center text-muted-foreground">
            <ArrowDown className="h-5 w-5" />
          </div>
          <Node icon={Boxes} title="wash_sync engine (Python)" text="Adapters → validation → matching → change detection → plan → streaming XLSX writer → verification." tone="private" />
          <div className="flex justify-center text-muted-foreground">
            <ArrowDown className="h-5 w-5" />
          </div>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <Node icon={FileSpreadsheet} title="Versioned workbooks" text="WASH_Warehouse_vNNN.xlsx — one sheet each, read-only, verified." tone="private" />
            <Node icon={HardDrive} title="Private state" text="Manifest, fingerprints, crosswalk, history, plans, audits, rebuildable index." tone="private" />
          </div>
        </div>
      </div>
      <H>Synchronization pipeline</H>
      <div className="mt-4 flex flex-wrap items-center gap-2 text-sm">
        {['Source export', 'Header-based adapter', 'Source-aware validation', 'Fingerprint', 'Match (L1 → L2 → L3)', 'Field-level diff', 'Plan + audit', 'Review & approve', 'Stream-write temp file', 'Verify + preservation proof', 'Atomic publish vNNN'].map(
          (s, i, arr) => (
            <React.Fragment key={s}>
              <span className="rounded-lg border bg-card px-3 py-1.5 shadow-card">{s}</span>
              {i < arr.length - 1 && <ArrowRight className="h-4 w-4 text-muted-foreground" />}
            </React.Fragment>
          ),
        )}
      </div>
      <H>Why streaming</H>
      <P>
        The verified warehouse holds about 627,000 rows × 196 columns (≈800 MB of worksheet XML inside a 70 MB file). The engine never materialises
        cells as spreadsheet objects: it streams the worksheet in bounded chunks, copies unchanged rows byte-for-byte, rebuilds only touched rows and keeps
        a private SQLite index for lookups. On the real warehouse a full apply (write, verify and preservation proof) took about four minutes using under
        300 MB of memory.
      </P>
    </div>
  )
}

function Matching() {
  return (
    <div>
      <H>Matching levels</H>
      <div className="mt-4 grid grid-cols-1 gap-3 md:grid-cols-3">
        {[
          ['Level 1 · exact source identity', 'AquaWatch resource ID (+ type and station for the historical table), bore-log ID, WPMS GO code, GPANI geocode + source GPS.'],
          ['Level 2 · verified cross-source', 'Reviewed crosswalk links, or a shared government code (TW ID / GO code = GPANI geocode) verified by admin location, GPS, tubewell type and depth.'],
          ['Level 3 · geographic candidate', 'Nearby compatible facilities. Always routed to review — location alone (same village or same coordinates) is never proof of identity.'],
        ].map(([t, d]) => (
          <Card key={t}>
            <CardContent className="p-4">
              <div className="font-semibold">{t}</div>
              <p className="mt-1.5 text-sm text-muted-foreground">{d}</p>
            </CardContent>
          </Card>
        ))}
      </div>
      <H>Change classifications</H>
      <div className="mt-4 grid grid-cols-1 gap-2 md:grid-cols-2">
        {CLASS_ORDER.map((c) => {
          const m = CLASS_META[c]
          return (
            <div key={c} className="flex gap-3 rounded-lg border p-3 text-sm">
              <m.icon className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
              <div>
                <div className="flex items-center gap-2 font-medium">
                  {m.label} <code className="text-[11px] text-muted-foreground">{c}</code>
                </div>
                <p className="mt-0.5 text-muted-foreground">{m.description}</p>
              </div>
            </div>
          )
        })}
      </div>
      <H>Safety rules that are always enforced</H>
      <ul className="mt-3 list-disc space-y-1.5 pl-6 text-[15px] text-muted-foreground">
        <li>A blank-fill never overwrites: it is applied only if the target cell is still blank.</li>
        <li>Changed existing values, possible duplicates and identity conflicts are never applied without a recorded decision and reason.</li>
        <li>Plans are pinned to the SHA-256 of their base version; a plan for an older version is refused.</li>
        <li>A new observation never creates a second facility row; new years/seasons create new columns (dry and wet never mix).</li>
        <li>Re-running the same files produces no new rows, columns, values or counts and no new version.</li>
        <li>The published warehouse is always exactly one worksheet; Excel's row/column/cell limits fail visibly instead of truncating or adding sheets.</li>
      </ul>
    </div>
  )
}

function Quality() {
  return (
    <div>
      <H>Source-aware validation</H>
      <P>Not every source measures everything. Bore logs have no confirmed water-table field, so completeness rules are configured per source rather than applied globally.</P>
      <div className="mt-4 grid grid-cols-1 gap-3 md:grid-cols-2">
        {[
          ['Identifiers', 'Missing or placeholder IDs reject a record; duplicate IDs within a run become exact duplicates or conflicts.'],
          ['Coordinates', 'Bangla digits and decimal commas are parsed; reversed lat/lon is corrected with a reason; out-of-range values are kept raw and flagged.'],
          ['Depth & units', '“750 ft”, “46.02 m”, “৬৬০ ft” are converted; unitless depths are never assumed; implausible depths (e.g. 12,345 m) are flagged as possible test records.'],
          ['Chemistry', 'Raw readings are kept verbatim. mg/L, ppm (≡ mg/L) and µg/L/ppb (÷1000) are converted only when the unit is stated; censored values (<LOQ, BDL) are never turned into 0.'],
          ['Periods', '“2026 (Wet Season)”, “2025”, “Dry 2027” are recognized; unknown season words are rejected instead of guessed.'],
          ['Strict mode', 'Configured warnings (invalid GPS, missing type, implausible values…) escalate to rejections.'],
        ].map(([t, d]) => (
          <div key={t} className="rounded-lg border p-3">
            <div className="font-medium">{t}</div>
            <p className="mt-1 text-sm text-muted-foreground">{d}</p>
          </div>
        ))}
      </div>
      <H>Historical AquaWatch values</H>
      <P>
        The 1986–2017 historical columns have no confirmed measurement definition. They are preserved exactly — including non-numeric text — and are never
        reinterpreted as water-table depths.
      </P>
    </div>
  )
}

function Cli() {
  return (
    <div>
      <H>Commands</H>
      <Code>{`wash-sync load-warehouse WAREHOUSE.xlsx [-r "why"]  # make it current (v000 first, later the next version)
wash-sync inspect FILE...                           # detect source, format and column mapping
wash-sync preview FILE... [--period "2027 dry"]     # dry run → plan + audit
wash-sync review list PLAN_ID                       # review items and options
wash-sync review decide PLAN_ID R00001 -d KEEP_SEPARATE -r "reason"
wash-sync approve PLAN_ID                           # lock decisions (undecided → deferred)
wash-sync apply PLAN_ID                             # write → verify → publish vNNN
wash-sync verify [--version N]                      # integrity report
wash-sync history                                   # versions + events
wash-sync rollback --to N -r "reason"               # make an earlier version current
wash-sync recover                                   # clean up after an interrupted run
python -m wash_sync serve                           # local production app`}</Code>
      <P>
        Every command accepts <code>--state-dir</code> (or the <code>WASH_SYNC_HOME</code> environment variable). <code>apply</code> refuses unapproved plans
        unless <code>--yes</code> is given as explicit authorization.
      </P>
    </div>
  )
}

function Troubleshooting() {
  const items: [string, string][] = [
    ['“Plan was prepared for version N but the current version is M”', 'Another plan was applied (or a rollback happened) after this preview. Run the preview again.'],
    ['“Another operation is running”', 'Only one apply/init runs at a time. If a previous process crashed, run wash-sync recover — the verified warehouse is never modified by a failed run.'],
    ['A file is “not recognized”', 'Make sure the header row is present. Columns are matched by name; see the column mapping in New Sync and the aliases in source_profiles.json.'],
    ['Bangla names are flagged as unmapped', 'Bangla administrative names are translated only via mappings learned from verified warehouse rows. Add a validated alias in Settings if needed.'],
    ['Verification failed during apply', 'Nothing was published and the temporary file was removed. The error lists the failed checks; keep the audit and report the issue.'],
    ['The browser preview differs from the engine', 'The browser preview uses simplified rules on synthetic data. The engine dry run is authoritative.'],
  ]
  return (
    <div className="space-y-3">
      {items.map(([q, a]) => (
        <details key={q} className="group rounded-lg border p-4">
          <summary className="cursor-pointer font-medium">{q}</summary>
          <p className="mt-2 text-sm text-muted-foreground">{a}</p>
        </details>
      ))}
      <P>
        Source code, issues and the full README: <a className="text-primary underline" href={REPO_URL}>{REPO_URL}</a>
      </P>
    </div>
  )
}

const CONTENT: Record<string, () => React.ReactElement> = {
  'getting-started': GettingStarted,
  modes: Modes,
  architecture: Architecture,
  matching: Matching,
  quality: Quality,
  cli: Cli,
  troubleshooting: Troubleshooting,
}

export default function Docs() {
  const { section = 'getting-started' } = useParams()
  const Body = CONTENT[section] ?? GettingStarted
  return (
    <div>
      <PageHeader eyebrow="System" title="Documentation" description="How the synchronization works, how to run it on your own computer, and the rules that keep the warehouse safe." />
      <div className="grid grid-cols-1 gap-8 lg:grid-cols-[220px_minmax(0,1fr)]">
        <nav aria-label="Documentation sections" className="lg:sticky lg:top-20 lg:self-start">
          <ul className="flex gap-1 overflow-x-auto pb-2 lg:flex-col lg:pb-0">
            {SECTIONS.map((s) => (
              <li key={s.id}>
                <NavLink
                  to={`/docs/${s.id}`}
                  className={({ isActive }) =>
                    cn(
                      'flex items-center gap-2 whitespace-nowrap rounded-lg px-3 py-2 text-sm transition-colors',
                      isActive || (s.id === 'getting-started' && section === 'getting-started') ? 'bg-accent font-medium text-accent-foreground' : 'text-muted-foreground hover:bg-muted',
                    )
                  }
                >
                  <s.icon className="h-4 w-4" /> {s.label}
                </NavLink>
              </li>
            ))}
          </ul>
          <div className="mt-4 hidden rounded-lg border p-3 text-xs text-muted-foreground lg:block">
            <Lock className="mb-1 h-3.5 w-3.5" /> Real warehouses, source exports and audits stay private. Only synthetic data is published.
            <GitBranch className="mb-1 mt-3 h-3.5 w-3.5" /> Every change is versioned and reversible.
          </div>
        </nav>
        <article className="min-w-0 max-w-4xl">
          <Body />
        </article>
      </div>
    </div>
  )
}
