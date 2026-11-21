# Deployment

## GitHub Pages (public demo)

`.github/workflows/deploy-pages.yml` runs on every push to `main` and on manual
dispatch:

1. checkout, Node 22, `npm ci` (lockfile);
2. frontend tests (`npm test`);
3. `npm run build:pages` — type-check and build with `base=/wash-warehouse-sync/`;
   `404.html` is a copy of `index.html` so deep links and refreshes work;
4. a guard that refuses to publish anything except the static build and the synthetic
   demo bundle (`demo/manifest.json` must declare `"synthetic": true`);
5. `actions/configure-pages`, `actions/upload-pages-artifact`, `actions/deploy-pages`
   with `contents: read`, `pages: write`, `id-token: write` and the `github-pages`
   environment.

Repository setting: **Settings → Pages → Source: GitHub Actions** (the workflow can
also be enabled with `gh api -X POST repos/<owner>/<repo>/pages -f build_type=workflow`).

The Pages build is compiled in demo mode and has no code path to a local engine.
Regenerate the synthetic bundle with `wash-sync demo-data --out apps/web/public/demo`.

## Local production mode

```bash
./scripts/start-local.sh            # Linux / macOS
scripts\start-local.ps1             # Windows PowerShell
```

or manually: `pip install -e ".[server]"`, `npm --prefix apps/web ci`,
`npm --prefix apps/web run build:local`, `python -m wash_sync serve`.

The server binds to `127.0.0.1:8765`. Do not expose it to a network; if you must
(`--host 0.0.0.0`), do so only on a trusted private network.

## CI

`.github/workflows/ci.yml` runs ruff and the Python test suite on Linux (3.12, 3.13),
Windows and macOS, and on the web side: unit/component tests, both production
builds and the Playwright end-to-end suite against the Pages build.
