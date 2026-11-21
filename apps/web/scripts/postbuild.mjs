// GitHub Pages post-build step.
//
// Pages only serves files, so a direct visit or refresh on /wash-warehouse-sync/conflicts would
// otherwise be answered by 404.html with HTTP status 404. For every route the app defines, a copy
// of index.html is written where Pages looks for it (`conflicts.html` for /conflicts and
// `conflicts/index.html` for /conflicts/), so those requests return 200. Any other path still gets
// 404.html, which is the same app and renders "Page not found" with a genuine 404. Jekyll is off.
import { copyFileSync, existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const out = process.argv[2] ?? 'dist'
const src = join(dirname(fileURLToPath(import.meta.url)), '..', 'src')
const index = join(out, 'index.html')
if (!existsSync(index)) throw new Error(`${index} missing`)

// Route patterns are read from the router itself so this list cannot drift from the app.
const patterns = [...readFileSync(join(src, 'App.tsx'), 'utf8').matchAll(/<Route path="([^"]+)"/g)].map((m) => m[1])
if (!patterns.length) throw new Error('postbuild: no <Route path="…"> found in src/App.tsx')

// Values for parameterised routes: the plans shipped in the demo bundle and the documentation sections.
const params = {
  ':planId': () =>
    existsSync(join(out, 'demo', 'plans'))
      ? readdirSync(join(out, 'demo', 'plans'))
          .filter((f) => /^plan-[\w-]+\.json$/.test(f))
          .map((f) => f.slice(0, -'.json'.length))
      : [],
  ':section': () => {
    const docs = readFileSync(join(src, 'pages', 'Docs.tsx'), 'utf8')
    const start = docs.indexOf('const SECTIONS')
    return [...docs.slice(start, docs.indexOf(']', start)).matchAll(/id: '([\w-]+)'/g)].map((m) => m[1])
  },
}

const routes = []
for (const pattern of patterns) {
  if (pattern === '/' || pattern === '*') continue
  let expanded = [[]]
  for (const part of pattern.split('/').filter(Boolean)) {
    if (!part.startsWith(':')) {
      expanded = expanded.map((prefix) => [...prefix, part])
      continue
    }
    if (!params[part]) throw new Error(`postbuild: add a value list for ${part} (route ${pattern})`)
    const values = params[part]()
    if (!values.length) console.warn(`postbuild: no values for ${pattern}; it is served by 404.html`)
    expanded = expanded.flatMap((prefix) => values.map((value) => [...prefix, value]))
  }
  routes.push(...expanded.map((segments) => segments.join('/')))
}

for (const route of routes) {
  mkdirSync(join(out, route), { recursive: true })
  copyFileSync(index, join(out, route, 'index.html'))
  copyFileSync(index, join(out, `${route}.html`))
}
copyFileSync(index, join(out, '404.html'))
writeFileSync(join(out, '.nojekyll'), '')
console.log(`postbuild: ${routes.length} routes written to ${out} (${routes.join(', ')}), plus 404.html and .nojekyll`)
