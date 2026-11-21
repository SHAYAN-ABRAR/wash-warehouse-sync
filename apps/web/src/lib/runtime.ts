declare const __DEPLOY_TARGET__: string
declare const __APP_VERSION__: string

export type Mode = 'demo' | 'local'

export const APP_VERSION: string = typeof __APP_VERSION__ === 'string' ? __APP_VERSION__ : '0.0.0'
export const DEPLOY_TARGET: string = typeof __DEPLOY_TARGET__ === 'string' ? __DEPLOY_TARGET__ : 'pages'
export const REPO_URL = 'https://github.com/SHAYAN-ABRAR/wash-warehouse-sync'

function meta(name: string): string | null {
  if (typeof document === 'undefined') return null
  return document.querySelector(`meta[name="${name}"]`)?.getAttribute('content') ?? null
}

/**
 * GitHub Pages builds are *always* demo mode. Local Production Mode is only enabled
 * when the page is served by the local engine (`wash-sync serve`), which injects a
 * per-session token. The public site never contacts a local engine.
 */
export function detectMode(): Mode {
  if (DEPLOY_TARGET === 'pages') return 'demo'
  return meta('wash-sync-mode') === 'local' && !!meta('wash-sync-token') ? 'local' : 'demo'
}

export function sessionToken(): string {
  return meta('wash-sync-token') ?? ''
}

export const BASE_URL: string = import.meta.env.BASE_URL ?? '/'

export function assetUrl(path: string): string {
  return `${BASE_URL.replace(/\/$/, '')}/${path.replace(/^\//, '')}`
}

export function routerBasename(): string {
  const base = BASE_URL.replace(/\/$/, '')
  return base === '' || base === '.' ? '/' : base
}
