import { chromium } from '@playwright/test'
const out = process.argv[2]
const base = 'http://localhost:4173/wash-warehouse-sync'
const routes = ['/', '/sync/new', '/sync/preview', '/conflicts', '/inspect', '/explorer', '/quality', '/history', '/settings', '/docs', '/docs/architecture', '/nope']
const browser = await chromium.launch({ channel: 'chrome' })
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })
const errors = []
page.on('console', (m) => { if (m.type() === 'error' || m.type() === 'warning') errors.push(`[${m.type()}] ${page.url()} :: ${m.text().slice(0, 300)}`) })
page.on('pageerror', (e) => errors.push(`[pageerror] ${page.url()} :: ${e.message}`))
for (const r of routes) {
  await page.goto(base + r, { waitUntil: 'networkidle' })
  await page.waitForTimeout(700)
  const name = r === '/' ? 'dashboard' : r.replaceAll('/', '_').replace(/^_/, '')
  await page.screenshot({ path: `${out}/${name}.png`, fullPage: false })
  const h1 = await page.locator('h1').first().textContent().catch(() => null)
  console.log(r, '->', h1)
}
await page.goto(base + '/sync/preview', { waitUntil: 'networkidle' })
await page.waitForTimeout(500)
await page.screenshot({ path: `${out}/preview_full.png`, fullPage: true })
const mobile = await browser.newPage({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2 })
mobile.on('pageerror', (e) => errors.push(`[pageerror mobile] ${e.message}`))
await mobile.goto(base + '/', { waitUntil: 'networkidle' })
await mobile.waitForTimeout(600)
await mobile.screenshot({ path: `${out}/mobile_dashboard.png` })
const overflow = await mobile.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
console.log('mobile horizontal overflow px:', overflow)
await browser.close()
console.log('ERRORS:', errors.length)
for (const e of errors) console.log(e)
