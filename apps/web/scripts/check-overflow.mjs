// Reports elements wider than the viewport at phone width (responsive-layout check).
import { chromium } from '@playwright/test'
const base = process.argv[2] ?? 'http://localhost:4173/wash-warehouse-sync'
const routes = ['/', '/sync/new', '/sync/preview', '/conflicts', '/inspect', '/explorer', '/quality', '/history', '/settings', '/docs', '/docs/modes']
const browser = await chromium.launch({ channel: process.env.PW_CHANNEL ?? 'chrome' })
const page = await browser.newPage({ viewport: { width: 390, height: 844 } })
let bad = 0
for (const r of routes) {
  await page.goto(base + r, { waitUntil: 'networkidle' })
  await page.waitForTimeout(500)
  const res = await page.evaluate(() => {
    const vw = document.documentElement.clientWidth
    const offenders = []
    for (const el of document.querySelectorAll('body *')) {
      const rect = el.getBoundingClientRect()
      if (rect.right > vw + 1 && rect.width > 0) {
        const parentOver = el.parentElement && el.parentElement.getBoundingClientRect().right > vw + 1
        if (!parentOver) offenders.push(`${el.tagName.toLowerCase()}.${String(el.className).slice(0, 80)} right=${Math.round(rect.right)}`)
      }
    }
    return { overflow: document.documentElement.scrollWidth - vw, offenders: offenders.slice(0, 6) }
  })
  if (res.overflow > 0) bad++
  console.log(r, 'overflow', res.overflow, res.offenders.join(' | '))
}
await browser.close()
process.exit(bad ? 1 : 0)
