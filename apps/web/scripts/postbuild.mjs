// GitHub Pages: serve the SPA for deep links (404.html fallback) and disable Jekyll.
import { copyFileSync, writeFileSync, existsSync } from 'node:fs'
import { join } from 'node:path'
const out = process.argv[2] ?? 'dist'
if (!existsSync(join(out, 'index.html'))) throw new Error(`${out}/index.html missing`)
copyFileSync(join(out, 'index.html'), join(out, '404.html'))
writeFileSync(join(out, '.nojekyll'), '')
console.log(`postbuild: wrote ${out}/404.html and ${out}/.nojekyll`)
