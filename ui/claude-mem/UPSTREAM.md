# Upstream UI provenance

Source: https://github.com/thedotmack/claude-mem/tree/b80b29bc49d08a71312f44b8feacb2e7585edff4

The pinned upstream revision uses Apache-2.0. Its LICENSE and NOTICE accompany the locally modified source. The static assets include bundled dependency notices and the OFL texts for Geist and Hanken Grotesk. The classic viewer uses local fallback fonts.

## Kept upstream

- `my-work`: upstream `src/ui/my-work`, including React, Zustand store, task/card/detail UI, status normalization, work-product rendering, local-source mapping, exact-version review client and connection freshness logic. Upstream sample/test fixtures remain as source context; the deployed source is forced to local and never falls back to samples. Upstream test sources were omitted from this bounded integration, not executed.
- `viewer`: upstream `src/ui/viewer`, retaining feed pagination, sessions, observation cards, theme, dialog focus and SSE transport. Original standalone viewer HTML/CSS and local icons are at `contextrail/ui_static/viewer.html` and its sibling assets.
- `services/work-view/nav-words.*`: original shared pure module; the three imports are shortened for this vendored layout.

## Local adaptations

- User-selected Maple Ridge House presentation (2026-11-04 screenshot supersedes the earlier MedPanel interpretation): `contextrail/ui_static/context-theme.css` supplies shared visual tokens and raised/pressed navigation controls; `rail.html/css/js` organize original status into six panels. The generated ContextRail PNG is shared by headers/favicons. My Work's CmemMark now displays the local product mark; original native AgentMark paths remain. See `CONTEXT_RAIL_BRAND.md` for reference, palette, fonts and asset ownership.

- `my-work/src/App.tsx`: ContextRail identity, project settings label and local workbench link; remove Team/feedback/GitHub promotion controls.
- `my-work/src/pages/my-rules.tsx`: existing registry-backed Codex/Claude project toggles, exact configuration revision, abortable read and visible failures.
- `my-work/src/components/request-detail.tsx` and `live/review-response.ts`: label human review as having viewed a version. No completion, verification or authority claim.
- `my-work/src/store/index.ts`: original local source only. `live/usage.ts`: no telemetry.
- `my-work/vite.config.ts`: output static package assets, no development server in deployment.
- `viewer/components/Header.tsx`, `ContextSettingsModal.tsx`, `DeleteButton.tsx`, `SessionCardMenu.tsx`, `WelcomeCard.tsx`: local navigation/settings and read-only original memory; remove account, cloud and delete actions. `ObservationCard.tsx`: bounded original Markdown link.
- `viewer/hooks/useSSE.ts`, `viewer/App.tsx`: show actual source failures and link the existing fault workbench; browser timeout type; no unsupported upstream worker-log drawer.
- Build-specific `package.json`, `build-viewer.mjs`, standalone viewer tsconfig and lockfile. Existing project root package/lock are untouched.

## Build

Node 22.6+ (existing upstream adapter requirement). From `ui/claude-mem`:

```powershell
npm.cmd ci --ignore-scripts --no-audit --no-fund
npm.cmd run typecheck
npm.cmd run build
Set-Location my-work
npm.cmd ci --ignore-scripts --no-audit --no-fund
$env:NODE_OPTIONS='--max-old-space-size=512'
npm.cmd run build
```

Dependencies are locked separately from the host repository. Generated static assets are checked in and packaged in the wheel. Daily use needs Python and the existing on-demand Node formatter, without npm, Vite, the upstream worker, Chroma or a new observer.

Backend adapter: `contextrail/ui_server.py`; original R/M remain authoritative. Supplementary `rail.html/css/js` are the small local workbench for existing fault explanations, by-file navigation and registered products; they do not replace the upstream task/memory pages.
