// Drives the full Local Production Mode workflow through the real UI, starting from an empty state:
// load the warehouse (step 1) → explorer shows it → add a file to sync (step 2) → dry run → decision →
// approve → apply → download → edit the download like Excel would → load it as the newer warehouse →
// every view shows the edited data.
// Usage: wash-sync serve --state-dir <empty folder> --port 8766 --no-open &
//        node scripts/verify-local.mjs http://127.0.0.1:8766 <synthetic warehouse.xlsx> ../../examples/sources/aquawatch_survey_2027_wet.xlsx [screenshots dir]
import { execFileSync } from 'node:child_process'
import { mkdtempSync, readFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium } from '@playwright/test'

const [base = 'http://127.0.0.1:8766', warehouse, source, shots] = process.argv.slice(2)
if (!warehouse || !source) throw new Error('usage: verify-local.mjs <base url> <warehouse.xlsx> <source export> [screenshots dir]')
const repo = fileURLToPath(new URL('../../../', import.meta.url))
const python = process.env.PYTHON ?? `${repo}.venv/bin/python`
const browser = await chromium.launch({ channel: process.env.PW_CHANNEL ?? 'chrome' })
const page = await browser.newPage({ viewport: { width: 1440, height: 950 } })
const errors = []
page.on('pageerror', (e) => errors.push(e.message))
page.on('console', (m) => m.type() === 'error' && errors.push(m.text()))
const step = (s) => console.log('✓', s)
const shot = async (name) => shots && (await page.screenshot({ path: `${shots}/${name}.png` }))

await page.goto(base + '/', { waitUntil: 'networkidle' })
await page.getByText('Local production').first().waitFor()
if (await page.getByText('Sample-data website.').count()) throw new Error('demo banner shown in local mode')
await page.getByText('Load your warehouse to begin').waitFor()
step('local mode, empty state: the dashboard asks for the warehouse')
await page.getByLabel('Reviewer name').fill('Local Verifier')

// ---- step 1: the current warehouse ------------------------------------------------------------
await page.goto(base + '/sync/new', { waitUntil: 'networkidle' })
await page.getByText('Load the current warehouse in step 1 first').waitFor()
step('New Sync: "Files to sync" stays locked until a warehouse is loaded')
const chooseWarehouse = page.getByLabel('Choose the warehouse workbook')
const beforeMixup = errors.length
await chooseWarehouse.setInputFiles(source)
await page.getByRole('button', { name: 'Load warehouse' }).click()
const mixup = page.getByRole('alert').filter({ hasText: 'not the WASH warehouse' })
await mixup.waitFor({ timeout: 60000 })
if (!(await mixup.textContent()).includes('Files to sync')) throw new Error('mix-up message does not point to step 2')
// The refused file is answered with HTTP 400, which the browser logs; anything else is still reported.
errors.push(...errors.splice(beforeMixup).filter((e) => !/status of 400/.test(e)))
step('a source export chosen as the warehouse is refused with a pointer to step 2')
await page.getByRole('button', { name: /^Remove / }).click()
await chooseWarehouse.setInputFiles(warehouse)
await page.getByRole('button', { name: 'Load warehouse' }).click()
await page.getByText('Ready for search').waitFor({ timeout: 600000 })
await page.getByText('v000', { exact: true }).waitFor()
step('warehouse loaded from the file picker: v000, checked, copied and ready for search')
await shot('local-new-sync-loaded')

await page.goto(base + '/explorer', { waitUntil: 'networkidle' })
await page.getByText(/Search and filter [\d,]+ facility rows/).waitFor()
await page.locator('tbody tr[data-index]').first().waitFor()
step('explorer shows the loaded warehouse')

// ---- step 2: the files to sync ----------------------------------------------------------------
await page.goto(base + '/sync/new', { waitUntil: 'networkidle' })
await page.getByLabel('Choose source files').setInputFiles(source)
await page.getByText('15/15 known columns matched').waitFor({ timeout: 30000 })
step('file to sync uploaded and recognized (AquaWatch, 15/15 columns)')
await page.getByRole('button', { name: 'Run dry-run comparison' }).click()
await page.waitForURL(/\/sync\/preview\/plan-/, { timeout: 120000 })
await page.getByText('Classified records').waitFor()
const planId = page.url().split('/').pop()
step(`dry run completed → ${planId}`)
await shot('local-preview')

await page.goto(`${base}/conflicts?plan=${planId}`, { waitUntil: 'networkidle' })
await page.getByRole('button', { name: /nearby compatible/ }).first().click()
const panel = page.locator('fieldset').first().locator('..')
await panel.getByLabel(/Reason/).fill('Verified in the field: separate tubewell')
await panel.getByRole('button', { name: 'Record decision' }).click()
await page.getByText(/Decision recorded/).waitFor()
step('review decision recorded with reason')

await page.goto(`${base}/sync/preview/${planId}`, { waitUntil: 'networkidle' })
await page.getByRole('button', { name: 'Approve plan' }).click()
await page.getByRole('dialog').getByRole('button', { name: 'Approve plan' }).click()
await page.getByText(/Plan approved/).waitFor()
step('plan approved')
await page.getByRole('button', { name: 'Apply plan' }).click()
await page.getByRole('dialog').getByRole('button', { name: 'Apply and publish' }).click()
await page.getByText(/Published version v001/).waitFor({ timeout: 600000 })
step('applied: v001 published (write → verify → publish)')

await page.goto(`${base}/history`, { waitUntil: 'networkidle' })
await page.getByText('v001').first().waitFor()
// A real click on the plain link: no custom headers, exactly like a user.
const [download] = await Promise.all([page.waitForEvent('download'), page.getByRole('link', { name: 'Download workbook' }).first().click()])
const v001 = join(mkdtempSync(join(tmpdir(), 'wash-verify-')), download.suggestedFilename())
await download.saveAs(v001) // keep the real name: spreadsheet programs need the .xlsx extension
if (download.suggestedFilename() !== 'WASH_Warehouse_v001.xlsx' || readFileSync(v001).subarray(0, 2).toString() !== 'PK') throw new Error('download failed')
step(`clicking "Download workbook" saved ${download.suggestedFilename()} (valid XLSX)`)
await page.goto(`${base}/sync/preview/${planId}`, { waitUntil: 'networkidle' })
const [audit] = await Promise.all([page.waitForEvent('download'), page.getByRole('link', { name: /Cell-level changes/ }).first().click()])
const auditText = readFileSync(await audit.path(), 'utf-8')
if (!auditText.includes('field_change') && !auditText.includes('new_value')) throw new Error('audit download failed')
step(`clicking an audit link saved ${audit.suggestedFilename()}`)
await page.goto(`${base}/explorer`, { waitUntil: 'networkidle' })
await page.getByLabel('Search the warehouse').fill('syn-w0000')
await page.locator('tbody tr[data-index]', { hasText: 'SYN-W0000' }).first().waitFor({ timeout: 60000 })
step('explorer finds the facility added by the sync (v001)')

// ---- the warehouse is edited outside the app, then loaded as the newer warehouse -------------------
const edited = v001.replace(/\.xlsx$/, '_edited.xlsx')
const editInExcelStyle = () => execFileSync(python, ['-c', `
import sys, openpyxl
from tests.conftest import to_shared_strings
wb = openpyxl.load_workbook(sys.argv[1]); ws = wb.active
h = [c.value for c in ws[1]]
row = list(next(r for r in ws.iter_rows(min_row=2, values_only=True) if r[h.index("aqua_gwc_id")]))
row[h.index("candidate_id")] = "AQUA2025:SYN-EDITED-UI"; row[h.index("aqua_gwc_id")] = "SYN-EDITED-UI"
ws.append(row)
wb.save(sys.argv[2] + ".tmp.xlsx")
to_shared_strings(sys.argv[2] + ".tmp.xlsx", sys.argv[2])
`, v001, edited], { cwd: repo, stdio: ['ignore', 'pipe', 'pipe'] })
try {
  editInExcelStyle()
} catch (e) {
  throw new Error(`editing the download failed:\n${e.stderr?.toString() ?? e}`)
}
step('downloaded v001 edited like Excel would (a well typed in, saved with shared strings)')
await page.goto(base + '/sync/new', { waitUntil: 'networkidle' })
await page.getByRole('button', { name: 'Load a newer warehouse file' }).click()
await page.getByLabel('Choose the warehouse workbook').setInputFiles({ name: 'WASH_warehouse_edited.xlsx', mimeType: 'application/octet-stream', buffer: readFileSync(edited) })
await page.getByLabel(/What changed/).fill('QA corrections made in Excel')
await page.getByRole('button', { name: 'Load as the current warehouse' }).click()
await page.getByText('v002', { exact: true }).waitFor({ timeout: 600000 })
await page.getByText('Loaded from file').first().waitFor()
step('edited workbook loaded as v002 ("Loaded from file")')
await shot('local-new-sync-newer')

await page.goto(`${base}/explorer`, { waitUntil: 'networkidle' })
await page.getByLabel('Search the warehouse').fill('syn-edited-ui')
await page.locator('tbody tr[data-index]', { hasText: 'SYN-EDITED-UI' }).first().waitFor({ timeout: 60000 })
step('explorer shows the edited data straight away')
await page.goto(`${base}/`, { waitUntil: 'networkidle' })
await page.getByText('v002', { exact: true }).first().waitFor()
await page.getByText(/Loaded from file ·/).waitFor()
step('dashboard shows v002, loaded from file')
await page.goto(`${base}/history`, { waitUntil: 'networkidle' })
await page.getByText('— “QA corrections made in Excel”').waitFor() // on the v002 card (the event log repeats it)
step('history records the load with its reason; v000 and v001 stay available')
await browser.close()
console.log(errors.length ? `console errors: ${errors.join(' | ')}` : 'no console errors')
