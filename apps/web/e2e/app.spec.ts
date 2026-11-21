import { expect, test, type Page } from '@playwright/test'

function trackErrors(page: Page): string[] {
  const errors: string[] = []
  page.on('pageerror', (e) => errors.push(e.message))
  page.on('console', (m) => m.type() === 'error' && errors.push(m.text()))
  return errors
}

test('dashboard loads under the Pages base path with assets and no console errors', async ({ page }) => {
  const errors = trackErrors(page)
  const response = await page.goto('./')
  expect(response?.status()).toBe(200)
  await expect(page.getByRole('heading', { name: 'Warehouse dashboard' })).toBeVisible()
  await expect(page.getByText('Public demonstration.')).toBeVisible()
  await expect(page.getByText('Current warehouse version')).toBeVisible()
  const css = await page.evaluate(() => getComputedStyle(document.body).backgroundColor)
  expect(css).not.toBe('rgba(0, 0, 0, 0)') // Tailwind styles applied
  expect(errors).toEqual([])
})

test('direct navigation and refresh work on deep links', async ({ page }) => {
  for (const [path, heading] of [
    ['conflicts', 'Conflicts Center'],
    ['sync/preview', 'Sync preview'],
    ['explorer', 'Warehouse Explorer'],
    ['docs/architecture', 'Documentation'],
  ]) {
    await page.goto(path)
    await expect(page.getByRole('heading', { name: heading, level: 1 })).toBeVisible()
    await page.reload()
    await expect(page.getByRole('heading', { name: heading, level: 1 })).toBeVisible()
  }
})

test('sidebar navigation and browser history', async ({ page }) => {
  await page.goto('./')
  const nav = page.getByRole('navigation', { name: 'Main' })
  await nav.getByRole('link', { name: 'Data Quality' }).click()
  await expect(page.getByRole('heading', { name: 'Data quality', level: 1 })).toBeVisible()
  await nav.getByRole('link', { name: 'Sync History' }).click()
  await expect(page.getByRole('heading', { name: 'Sync history', level: 1 })).toBeVisible()
  await expect(page.getByText('v001').first()).toBeVisible()
  await page.goBack()
  await expect(page.getByRole('heading', { name: 'Data quality', level: 1 })).toBeVisible()
})

test('theme toggle persists across reloads', async ({ page }) => {
  await page.goto('docs')
  await page.evaluate(() => localStorage.setItem('wash-theme', 'light'))
  await page.reload()
  await page.getByRole('button', { name: 'Switch to dark theme' }).click()
  await expect(page.locator('html')).toHaveClass(/dark/)
  await page.reload()
  await expect(page.locator('html')).toHaveClass(/dark/)
  await page.getByRole('button', { name: 'Switch to light theme' }).click()
  await expect(page.locator('html')).not.toHaveClass(/dark/)
})

test('sample export is previewed entirely in the browser', async ({ page }) => {
  const uploads: string[] = []
  page.on('request', (r) => r.method() !== 'GET' && uploads.push(r.url()))
  await page.goto('sync/new')
  await page.getByRole('button', { name: 'Try it' }).first().click()
  await expect(page.getByText('Browser preview — simplified rules')).toBeVisible({ timeout: 20_000 })
  await expect(page.getByText(/New observation/).first()).toBeVisible()
  await expect(page.getByText(/Possible duplicate/).first()).toBeVisible()
  await expect(page.getByText(/new columns/)).toBeVisible()
  expect(uploads).toEqual([]) // nothing is transmitted anywhere
})

test('conflict decision is recorded with a reason (browser-only in demo)', async ({ page }) => {
  await page.goto('conflicts')
  await page.getByRole('button', { name: /nearby compatible/ }).first().click()
  const panel = page.locator('fieldset').first().locator('..')
  await panel.getByRole('radio').nth(1).check()
  await panel.getByRole('button', { name: 'Record decision' }).click()
  await expect(page.getByText('Please record the reason for this decision.')).toBeVisible()
  await panel.getByLabel(/Reason/).fill('Different tubewell at a shared project point')
  await panel.getByRole('button', { name: 'Record decision' }).click()
  await expect(page.getByText(/saved in this browser only/)).toBeVisible()
})

test('apply is honestly unavailable in the public demo', async ({ page }) => {
  await page.goto('sync/preview')
  await page.getByRole('button', { name: 'Apply plan' }).click()
  await expect(page.getByRole('dialog').getByText(/runs only in Local Production Mode/)).toBeVisible()
  await expect(page.getByRole('button', { name: 'Apply and publish' })).toHaveCount(0)
})

test('audit files are downloadable', async ({ page, request }) => {
  await page.goto('sync/preview')
  const href = await page.getByRole('link', { name: /Every record and action/ }).first().getAttribute('href')
  expect(href).toBeTruthy()
  const res = await request.get(href!)
  expect(res.status()).toBe(200)
  expect(await res.text()).toContain('classification')
})

test('explorer searches and opens a record', async ({ page }) => {
  await page.goto('explorer')
  await page.getByLabel('Search the warehouse').fill('borelog')
  const row = page.locator('tbody tr[data-index]', { hasText: 'BORELOG:' }).first()
  await expect(row).toBeVisible() // results update after the debounced search
  await row.click()
  await expect(page.getByRole('dialog')).toBeVisible()
  await expect(page.getByRole('dialog').getByText('borelog_id')).toBeVisible()
})

test('unknown routes show a not-found page', async ({ page }) => {
  await page.goto('this/does/not/exist')
  await expect(page.getByRole('heading', { name: 'Page not found' })).toBeVisible()
})

test('mobile layout has no horizontal overflow and a working menu @mobile', async ({ page }) => {
  for (const path of ['./', 'sync/new', 'sync/preview', 'conflicts', 'explorer', 'quality', 'history', 'settings', 'docs']) {
    await page.goto(path)
    await page.waitForLoadState('networkidle')
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)
    expect(overflow, `horizontal overflow on ${path}`).toBeLessThanOrEqual(0)
  }
  await page.getByRole('button', { name: 'Open navigation' }).click()
  await page.getByRole('dialog', { name: 'Navigation' }).getByRole('link', { name: 'Documentation' }).click()
  await expect(page.getByRole('heading', { name: 'Documentation', level: 1 })).toBeVisible()
})
