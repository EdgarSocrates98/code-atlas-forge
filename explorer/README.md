# Code Atlas Explorer (frontend)

Angular 18 workbench, local-first, offline-first. Consumes the local Code
Atlas HTTP API at `http://127.0.0.1:8741/api/v1`. No telemetry, no cloud
dependency.

## Commands

```bash
npm install
npm start              # dev server
npm run build           # production build with budgets
npm test                 # Karma + Jasmine, ChromeHeadless
```

See `docs/explorer-frontend-architecture.md` and
`docs/explorer-design-system.md` at the repository root for architecture and
design-token documentation.

> This workspace's dependency versions were pinned by hand against known-good
> Angular 18.2.x releases. Run `npm install` and commit the resulting
> `package-lock.json` as the first step before relying on CI.
