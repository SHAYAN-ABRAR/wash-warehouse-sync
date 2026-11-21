// Captures README screenshots from the production build (synthetic demo data only).
// Usage: npx vite preview --base /wash-warehouse-sync/ --port 4173 & node scripts/screenshots.mjs ../../docs/screenshots
import { chromium } from '@playwright/test'
import { mkdirSync } from 'node:fs'

const out = process.argv[2] ?? '../../docs/screenshots'
const base = process.argv[3] ?? 'http://localhost:4173/wash-warehouse-sync'
mkdirSync(out, { recursive: true })
const browser = await chromium.launch({ channel: process.env.PW_CHANNEL ?? 'chrome' })
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 })
const page = await ctx.newPage()
await page.addInitScript(() => localStorage.setItem('wash-theme', 'light'))

async function shot(path, name, prepare) {
  await page.goto(base + path, { waitUntil: 'networkidle' })
  if (prepare) await prepare()
  await page.waitForTimeout(700)
  await page.screenshot({ path: `${out}/${name}.png` })
  console.log('saved', name)
}

await shot('/', 'dashboard')
await shot('/sync/preview', 'sync-preview', async () => page.mouse.wheel(0, 640))
await shot('/conflicts', 'conflicts-center', async () => {
  await page.getByRole('button', { name: /nearby compatible/ }).first().click()
})
await shot('/conflicts', 'match-inspector', async () => {
  await page.getByRole('button', { name: /nearby compatible/ }).first().click()
  await page.getByRole('link', { name: 'Match Inspector' }).first().click()
  await page.waitForLoadState('networkidle')
  await page.mouse.wheel(0, 300)
})
await shot('/sync/new', 'new-sync-browser-preview', async () => {
  await page.getByRole('button', { name: 'Try it' }).first().click()
  await page.getByText('Browser preview — simplified rules').waitFor()
  await page.getByText('aquawatch_survey_2027_wet.xlsx').last().scrollIntoViewIfNeeded()
  await page.mouse.wheel(0, 260)
})
await shot('/explorer', 'warehouse-explorer')
await shot('/quality', 'data-quality')
await shot('/history', 'sync-history')
await shot('/docs/architecture', 'architecture')
await page.evaluate(() => localStorage.setItem('wash-theme', 'dark'))
await page.addInitScript(() => localStorage.setItem('wash-theme', 'dark'))
await shot('/', 'dashboard-dark')
const mobile = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2 })
const m = await mobile.newPage()
await m.addInitScript(() => localStorage.setItem('wash-theme', 'light'))
await m.goto(base + '/conflicts', { waitUntil: 'networkidle' })
await m.waitForTimeout(600)
await m.screenshot({ path: `${out}/mobile-conflicts.png` })
console.log('saved mobile-conflicts')
await browser.close()
