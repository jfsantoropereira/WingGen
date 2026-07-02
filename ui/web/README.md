# WingGen Studio — web frontend

Vite + vanilla TypeScript (strict) + three.js single-page app for the WingGen
Studio server (`STUDIO_CONTRACT.md` §7/§8). No UI framework, no router/state
libraries, no CDN or runtime network dependencies.

## Views (hash-routed)

| Route | View |
|---|---|
| `#/lab` | Design Lab — schema-driven parameter editor + Simulate / Sweep / Optimize launch |
| `#/jobs`, `#/jobs/:id` | Jobs — 2 s polled list, SSE live progress, sweep-point stream, cancel |
| `#/designs` | Designs — ranked table, filters, multi-select (2–4) side-by-side compare |
| `#/viewer/:designId` | Viewer — three.js STL viewer (orbit controls, grid/axes, wireframe, resolution, export) |

## Scripts

All commands run from the repo root with the `winggen` conda env (node lives there):

```bash
conda run -n winggen npm --prefix ui/web install
conda run -n winggen npm --prefix ui/web run dev        # dev server, /api → http://127.0.0.1:8151
conda run -n winggen npm --prefix ui/web run dev:mock   # standalone QA, no server needed
conda run -n winggen npm --prefix ui/web run build      # typecheck + bundle → ui/web/dist
conda run -n winggen npm --prefix ui/web run typecheck  # tsc --noEmit (strict)
conda run -n winggen npm --prefix ui/web run test       # vitest (pure logic: form model, sweep spec, compare)
```

The studio server serves `ui/web/dist` at `/` when the directory exists.

## Mock mode

`npm run dev:mock` starts Vite with `--mode mock`; `src/main.ts` then installs
an in-memory transport (`src/mock/install.ts`) behind the API client's
transport seam — every `/api` request and job event stream is served locally:

- `src/mock/fixtures.ts` — parameter schema (21 params across 8 sections,
  incl. enums and ints), 2 completed runs, 8 seed designs.
- `src/mock/install.ts` — fetch router + job simulator. Launching a job from
  the Design Lab produces scripted `run_info`/`progress`/`sweep_point`/
  `design`/`result` events on a timer, persists mock designs, and honors
  cancel. Give a job a label containing `fail` to exercise the error path.
- `src/mock/stl.ts` — generates a tiny valid binary STL box (span × chord ×
  30 mm, Z-up like the real exporter) for `mesh.stl` requests, so the Viewer
  works offline.

## Layout

```
ui/web/
├── index.html
├── vite.config.ts          # dev proxy /api → 127.0.0.1:8151, dist build
├── src/
│   ├── main.ts             # app shell, nav, health poll, router mount
│   ├── style.css           # dark technical theme
│   ├── api/                # typed fetch wrapper + contract types (transport seam)
│   ├── core/               # dom helpers, hash router, pub/sub store, toasts
│   ├── logic/              # pure logic (unit-tested): form model, sweep/optimize specs, compare
│   ├── views/              # lab, jobs, designs, viewer
│   └── mock/               # dev:mock fixtures + in-memory transport
└── tests/                  # vitest suites for src/logic
```
