import { defineConfig, devices } from '@playwright/test'

// End-to-end tests run against the production GitHub Pages build (`npm run build:pages`)
// served under the same base path as the live site. Set E2E_BASE_URL to run the same tests
// against a deployed site instead, e.g. E2E_BASE_URL=https://shayan-abrar.github.io/wash-warehouse-sync/
const BASE = '/wash-warehouse-sync/'
const REMOTE = process.env.E2E_BASE_URL
const PORT = Number(process.env.E2E_PORT ?? 4173)
const channel = process.env.PW_CHANNEL // e.g. "chrome" to use an installed Google Chrome

export default defineConfig({
  testDir: './e2e',
  timeout: 45_000,
  expect: { timeout: 10_000 },
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [['github'], ['list']] : 'list',
  use: {
    baseURL: REMOTE ?? `http://localhost:${PORT}${BASE}`,
    trace: 'retain-on-failure',
    ...(channel ? { channel } : {}),
  },
  projects: [
    { name: 'desktop', use: { ...devices['Desktop Chrome'], viewport: { width: 1440, height: 900 }, ...(channel ? { channel } : {}) }, grepInvert: /@mobile/ },
    { name: 'mobile', use: { ...devices['Pixel 7'], ...(channel ? { channel } : {}) }, grep: /@mobile/ },
  ],
  webServer: REMOTE
    ? undefined
    : {
        command: `npx vite preview --base ${BASE} --port ${PORT} --strictPort`,
        url: `http://localhost:${PORT}${BASE}`,
        reuseExistingServer: !process.env.CI,
        timeout: 60_000,
      },
})
