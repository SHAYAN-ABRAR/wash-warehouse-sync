import * as React from 'react'
import { toast } from 'sonner'
import { Download, FolderLock, Plus, Save, Trash2 } from 'lucide-react'
import { ErrorState, LoadingBlock, PageHeader } from '@/components/common'
import { Badge, Card, CardContent, CardDescription, CardHeader, CardTitle, InfoTip, Input, Label, NativeSelect, Switch } from '@/components/ui/primitives'
import { Button } from '@/components/ui/button'
import { useData, useResource } from '@/lib/api'
import type { Settings } from '@/lib/types'
import { downloadText } from '@/lib/utils'

function Field({ label, info, children, htmlFor }: { label: string; info?: string; children: React.ReactNode; htmlFor?: string }) {
  return (
    <div className="space-y-1.5">
      <div className="flex items-center gap-1.5">
        <Label htmlFor={htmlFor}>{label}</Label>
        {info && <InfoTip text={info} />}
      </div>
      {children}
    </div>
  )
}

export default function SettingsPage() {
  const api = useData()
  const res = useResource(() => api.settings(), [api])
  const [draft, setDraft] = React.useState<Settings | null>(null)
  const [saving, setSaving] = React.useState(false)
  React.useEffect(() => {
    if (res.data) setDraft(structuredClone(res.data))
  }, [res.data])
  const readOnly = api.mode === 'demo'
  if (res.error) return <ErrorState error={res.error} onRetry={res.reload} />
  if (!draft) return <LoadingBlock rows={8} />
  const set = <K extends keyof Settings>(k: K, v: Settings[K]) => setDraft({ ...draft, [k]: v })
  const setM = (k: keyof Settings['matching'], v: number | boolean) => setDraft({ ...draft, matching: { ...draft.matching, [k]: v } })

  async function save() {
    setSaving(true)
    try {
      const saved = await api.saveSettings(draft!)
      setDraft(structuredClone(saved))
      toast.success('Settings saved')
    } catch (e) {
      toast.error((e as Error).message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="System"
        title="Settings"
        description={readOnly ? 'Demo mode shows the default configuration read-only. Download it as a starting point for your local settings.json.' : 'Stored in settings.json inside the private state directory.'}
        actions={
          <>
            <Button variant="outline" onClick={() => downloadText('wash_sync_settings.example.json', JSON.stringify({ ...draft, state_dir: undefined }, null, 2))}>
              <Download /> Download settings.json
            </Button>
            {!readOnly && (
              <Button onClick={save} loading={saving}>
                <Save /> Save settings
              </Button>
            )}
          </>
        }
      />
      <fieldset disabled={readOnly} className="grid min-w-0 grid-cols-1 gap-6 xl:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Warehouse & storage</CardTitle>
            <CardDescription>Where the private engine keeps versions, plans, audits and indexes</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <Field label="Private state directory" info="Fixed for a running engine. Set WASH_SYNC_HOME or --state-dir before starting it.">
              <Input value={draft.state_dir ?? '(set on the local engine)'} readOnly />
            </Field>
            <Field label="Warehouse file name prefix" htmlFor="wn">
              <Input id="wn" value={draft.warehouse_name} onChange={(e) => set('warehouse_name', e.target.value)} />
            </Field>
            <Field label="Export directory (optional)" htmlFor="ed">
              <Input id="ed" value={draft.export_dir ?? ''} onChange={(e) => set('export_dir', e.target.value || null)} />
            </Field>
            {(['AQUAWATCH', 'GPANI', 'WPMS', 'BORELOG'] as const).map((src) => (
              <Field key={src} label={`${src} source directory`} htmlFor={`sd-${src}`}>
                <Input
                  id={`sd-${src}`}
                  value={draft.source_dirs[src] ?? ''}
                  placeholder="Folder where new exports are saved"
                  onChange={(e) => set('source_dirs', { ...draft.source_dirs, [src]: e.target.value })}
                />
              </Field>
            ))}
            <div className="grid grid-cols-2 gap-4">
              <Field label="Backup retention" info="Keep the newest N versions (0 = keep all). The baseline and current version are always kept." htmlFor="br">
                <Input id="br" type="number" min={0} value={draft.backup_retention} onChange={(e) => set('backup_retention', Number(e.target.value))} />
              </Field>
              <Field label="ZIP compression level" htmlFor="cl">
                <Input id="cl" type="number" min={1} max={9} value={draft.compression_level} onChange={(e) => set('compression_level', Number(e.target.value))} />
              </Field>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Validation</CardTitle>
            <CardDescription>Source-aware rules; strict mode rejects records with configured warnings</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <Field label="Mode" htmlFor="vm">
              <NativeSelect id="vm" value={draft.validation.mode} onChange={(e) => set('validation', { ...draft.validation, mode: e.target.value as 'standard' | 'strict' })}>
                <option value="standard">Standard — errors reject, warnings are reported</option>
                <option value="strict">Strict — configured warnings also reject</option>
              </NativeSelect>
            </Field>
            <Field label="Minimum automatic survey year" info="Earlier AquaWatch survey years require review (they overlap the historical table)." htmlFor="my">
              <Input id="my" type="number" value={draft.validation.min_survey_year} onChange={(e) => set('validation', { ...draft.validation, min_survey_year: Number(e.target.value) })} />
            </Field>
            <Field label="Completeness rules per source" info="Logical fields whose absence is reported for that source only.">
              <div className="space-y-2">
                {Object.entries(draft.validation.completeness).map(([src, fields]) => (
                  <div key={src} className="grid grid-cols-[110px_1fr] items-center gap-2">
                    <Badge>{src}</Badge>
                    <Input
                      value={fields.join(', ')}
                      onChange={(e) =>
                        set('validation', {
                          ...draft.validation,
                          completeness: { ...draft.validation.completeness, [src]: e.target.value.split(',').map((s) => s.trim()).filter(Boolean) },
                        })
                      }
                      aria-label={`${src} completeness fields`}
                    />
                  </div>
                ))}
              </div>
            </Field>
            <Field label="Escalated in strict mode">
              <div className="flex flex-wrap gap-1.5">
                {draft.validation.strict_escalations.map((c) => (
                  <Badge key={c} tone="warning">
                    {c}
                  </Badge>
                ))}
              </div>
            </Field>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Matching thresholds</CardTitle>
            <CardDescription>Geographic proximity only proposes candidates for review — it never merges on its own</CardDescription>
          </CardHeader>
          <CardContent className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            {(
              [
                ['possible_match_radius_m', 'Possible-match radius (m)', 'Nearby compatible facilities within this distance are proposed for review.'],
                ['exact_coincidence_m', 'Exact coincidence (m)', 'Treated as identical coordinates.'],
                ['verify_gps_max_m', 'Verification GPS distance (m)', 'Maximum distance when verifying a shared code between sources.'],
                ['depth_tolerance_abs_m', 'Depth tolerance (m)', 'Absolute tolerance when comparing depths.'],
                ['depth_tolerance_ratio', 'Depth tolerance (ratio)', 'Relative tolerance when comparing depths.'],
              ] as const
            ).map(([k, label, info]) => (
              <Field key={k} label={label} info={info} htmlFor={k}>
                <Input id={k} type="number" step="any" value={draft.matching[k] as number} onChange={(e) => setM(k, Number(e.target.value))} />
              </Field>
            ))}
            <div className="flex items-start justify-between gap-3 rounded-lg border p-3 sm:col-span-2">
              <div>
                <Label htmlFor="auto-x">Apply fully verified cross-source matches automatically</Label>
                <p className="mt-1 text-xs text-muted-foreground">Shared code + administrative location + GPS + type + depth all agree. Turn off to review each one.</p>
              </div>
              <Switch id="auto-x" checked={draft.matching.auto_apply_verified_cross_source} onCheckedChange={(v) => setM('auto_apply_verified_cross_source', v)} />
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Approved location aliases</CardTitle>
            <CardDescription>Validated spellings compared as the same place. No fuzzy matching is ever applied.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-2">
            {draft.admin_aliases.map((a, i) => (
              <div key={i} className="grid grid-cols-[120px_1fr_auto] items-center gap-2">
                <NativeSelect
                  value={a.level}
                  onChange={(e) => set('admin_aliases', draft.admin_aliases.map((x, j) => (j === i ? { ...x, level: e.target.value } : x)))}
                  aria-label="Level"
                >
                  {['any', 'division', 'district', 'upazila', 'union_name', 'village_name'].map((l) => (
                    <option key={l} value={l}>
                      {l}
                    </option>
                  ))}
                </NativeSelect>
                <Input
                  value={a.names.join(' / ')}
                  onChange={(e) =>
                    set('admin_aliases', draft.admin_aliases.map((x, j) => (j === i ? { ...x, names: e.target.value.split('/').map((s) => s.trim()).filter(Boolean) } : x)))
                  }
                  aria-label="Equivalent names"
                />
                <Button variant="ghost" size="icon" onClick={() => set('admin_aliases', draft.admin_aliases.filter((_, j) => j !== i))} aria-label="Remove alias group">
                  <Trash2 />
                </Button>
              </div>
            ))}
            <Button variant="outline" size="sm" onClick={() => set('admin_aliases', [...draft.admin_aliases, { level: 'district', names: ['', ''] }])}>
              <Plus /> Add alias group
            </Button>
          </CardContent>
        </Card>

        <Card className="xl:col-span-2">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <FolderLock className="h-4 w-4 text-muted-foreground" /> Private storage destination
            </CardTitle>
            <CardDescription>
              Optional copy of verified versions to an authorized private folder (for example a synced organisational drive). Publishing always requires explicit
              confirmation and never happens automatically. No tokens are entered in this interface.
            </CardDescription>
          </CardHeader>
          <CardContent className="grid grid-cols-1 gap-4 md:grid-cols-[auto_1fr_1fr] md:items-end">
            <div className="flex items-center gap-2">
              <Switch
                id="ps-enabled"
                checked={draft.private_storage.enabled}
                onCheckedChange={(v) => set('private_storage', { ...draft.private_storage, enabled: v })}
              />
              <Label htmlFor="ps-enabled">Enabled</Label>
            </div>
            <Field label="Destination folder" htmlFor="ps-dest">
              <Input
                id="ps-dest"
                value={draft.private_storage.destination ?? ''}
                onChange={(e) => set('private_storage', { ...draft.private_storage, destination: e.target.value || null })}
              />
            </Field>
            <Field label="Label shown in the audit" htmlFor="ps-label">
              <Input id="ps-label" value={draft.private_storage.label ?? ''} onChange={(e) => set('private_storage', { ...draft.private_storage, label: e.target.value || null })} />
            </Field>
          </CardContent>
        </Card>
      </fieldset>
    </div>
  )
}
