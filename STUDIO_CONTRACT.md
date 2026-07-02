# STUDIO_CONTRACT.md — WingGen Studio interfaces (v1)

Frozen interfaces for the parallel build-out. All agents build against this
document. Changes to shared surfaces (config models, `wingopt.store`, this
file) require coordination with the integrator — do not edit them unilaterally.

## 0. Module ownership (write scopes)

| Branch | Owner scope (only these paths) |
|---|---|
| `feat/gpu-vlm` | `src/wingopt/aero/vlm.py`, `src/wingopt/utils/gpu.py`, `tests/test_vlm.py` |
| `feat/lbm-cfd` | `src/wingopt/cfd/**`, `src/wingopt/organic/cfd_engine.py` (additive only), `tests/test_lbm.py` |
| `feat/sweeps` | `src/wingopt/sweeps/**`, `scripts/sweep.py`, `tests/test_sweeps.py` |
| `feat/studio-server` | `src/wingopt/studio/**`, `tests/test_studio_api.py`, `start.sh`, `stop.sh` |
| `feat/studio-frontend` | `ui/web/**` |

Shared, frozen (already on `dev`): `src/wingopt/config/models.py` (new
`AeroConfig`, `StudioConfig`, `"lbm"` engine), `src/wingopt/store/**`,
`environment.yml`.

## 1. Fidelity tiers

Config section (already parsed by `wingopt.config`):

```toml
[aero]
method = "polar_llt"        # or "vlm"
[aero.vlm]
spanwise_panels = 32
chordwise_panels = 8
backend = "auto"            # auto | mlx | numpy
```

- Tier 1 `polar_llt`: existing polar-based lifting line (`wingopt.aero.model`).
- Tier 2 `vlm`: vortex lattice (`wingopt.aero.vlm`), GPU via MLX when available.
- Tier 3 `lbm`: volumetric lattice-Boltzmann CFD (`wingopt.cfd`), used as an
  `organic_refinement.engine` option and for on-demand design verification.

## 2. VLM solver interface (`feat/gpu-vlm`)

```python
# src/wingopt/aero/vlm.py
@dataclass(frozen=True)
class VlmResult:
    cl: float                       # total lift coefficient
    cdi: float                      # induced drag coefficient
    cm: float                       # pitching moment about the reference point
    neutral_point_x_m: float        # x of neutral point (m, root LE origin)
    span_stations: tuple[float, ...]   # eta in [-1, 1]
    span_loading: tuple[float, ...]    # local cl * c / MAC per station
    backend: str                    # "mlx" or "numpy" actually used

class VlmSolver:
    def __init__(self, settings: VlmSettings) -> None: ...
    def solve(
        self,
        geometry: WingGeometry,            # wingopt.geometry.planform
        alpha_deg: float,
        elevon_deg: float = 0.0,
        dihedral_profile: tuple[tuple[float, float], ...] | None = None,  # (eta, deg)
        airfoil_camber: tuple[tuple[float, float], ...] | None = None,    # (x/c, z/c) mean line
    ) -> VlmResult: ...
```

Requirements: swept/tapered/twisted panels, dihedral profile support, elevon
deflection as camber slope change over the flapped region, Trefftz-plane (or
far-field) induced drag, neutral point via dCm/dCL from two-alpha solve.
Backend selection: `auto` uses MLX if `mx.metal.is_available()` else numpy;
must produce identical results within 1e-4. Validation tests: elliptic-planform
lift slope vs LLT theory (±5%), CDi ≈ CL²/(π·AR·e) with e ≥ 0.9 for AR 8
elliptic-ish planform, symmetric wing at α=0 gives CL≈0.

## 3. LBM engine interface (`feat/lbm-cfd`)

```python
# src/wingopt/cfd/lbm.py
@dataclass(frozen=True)
class LbmResult:
    cl: float
    cd: float
    lift_n: float
    drag_n: float
    resolution: tuple[int, int, int]
    reynolds: float
    steps: int
    converged: bool
    backend: str

class LbmSolver:
    def __init__(self, resolution=(160, 96, 96), backend="auto") -> None: ...
    def solve_wing(
        self,
        geometry: WingGeometry,
        airfoil_coordinates,               # from load_airfoil_coordinates
        alpha_deg: float,
        v_ms: float,
        air_density: float,
        air_viscosity: float,
        dihedral_profile=None,
        max_steps: int = 4000,
    ) -> LbmResult: ...
```

D3Q19 + Smagorinsky LES, half-domain with symmetry plane, voxelized wing from
the existing STL lofting mesh, bounce-back walls, force via momentum exchange.
MLX backend required, numpy fallback for CI. Register adapter class
`LbmCfdEngine(CfdEngine)` in `src/wingopt/organic/cfd_engine.py` under engine
name `"lbm"` (additive edit only — do not restructure existing classes).
Must run a smoke case (≤64³, ≤300 steps) in <60 s on CPU for tests.
Validation: 2D-extruded Poiseuille profile error <5%; sphere/flat-plate drag
sanity (order of magnitude + monotonic with alpha).

## 4. Run store (frozen, on `dev`)

`wingopt.store.RunStore` — see docstrings. Key facts:

- Root default: `outputs/runs` (config `studio.runs_root`).
- `create_run(kind, label, config) -> RunRecord`; kinds: simulate|sweep|optimize.
- `append_design(run_id, source, params, metrics, score, feasible, label, artifacts)`.
- `update_run(run_id, status=..., summary=...)`; statuses: running|completed|failed|cancelled.
- `list_designs(run_id=None, feasible_only=False, sort_by="score"|metric, limit)` ranks globally.
- Design IDs: `<run_id>-d<seq:04d>`. Artifacts live under `<run_id>/artifacts/`.
- One writer process per run directory. Runner CLIs own their run dir; the
  server only reads (and creates the run before spawning the runner).

`params` keys are dotted config paths (`"geometry.wingspan_m"`). `metrics`
standard keys: `cruise_ld`, `cruise_cd`, `static_margin`, `stall_speed_kmh`,
`total_mass_g`, `gross_mass_g`, `range_km`, `endurance_h`, `combined_score`
(more allowed).

## 5. NDJSON event contract v1.1 (runner stdout)

Existing v1 events keep shape `{"contract_version": "1.1.0", "event": E, "payload": {...}}`:

- `progress`: `{stage, percent, note?}`
- `result`: terminal payload (kind-specific, see below)
- `error`: `{message, stage}`

New events:

- `run_info`: `{run_id, kind, label}` — first event when a store run is attached.
- `sweep_point`: `{index, total, params, metrics, score, feasible, design_id?}`
- `design`: `{design_id, source, score, feasible}` — emitted when a design is persisted.

## 6. Sweep/optimize CLI (`feat/sweeps`)

`scripts/sweep.py` (NDJSON on stdout, same env/entry style as `scripts/simulate.py`):

```
python scripts/sweep.py --config configs/default_wing.toml \
    --spec path/to/spec.json --runs-root outputs/runs [--run-id <id>] [--label L]
```

SweepSpec JSON:

```json
{
  "kind": "sweep",
  "parameters": [
    {"path": "geometry.wingspan_m", "min": 1.2, "max": 1.8, "steps": 7},
    {"path": "geometry.sweep_deg", "values": [22, 26, 30]}
  ],
  "evaluation": {"mode": "wing_only" | "full", "fidelity": "polar_llt" | "vlm"},
  "objective": "combined_score" | "range_km" | "endurance_h" | "cruise_ld"
}
```

- Grid = cartesian product of parameter axes; hard cap 2000 points (error above).
- `wing_only`: geometry+aero+stability+structures metrics per point (fast).
- `full`: adds propulsion matching + range/endurance via existing modules.
- Every point → `RunStore.append_design(source="sweep_point")` + `sweep_point` event.
- `result` payload: `{run_id, total_points, feasible_points, best: DesignRecord-dict, top: [≤10 DesignRecord-dicts]}`.

OptimizeSpec JSON (same CLI, `"kind": "optimize"`):

```json
{
  "kind": "optimize",
  "variables": {"geometry.wingspan_m": [1.3, 1.7], "geometry.sweep_deg": [20, 32]},
  "budget": {"max_evaluations": 400},
  "objective": "combined_score",
  "seed": 42
}
```

Maps `variables` onto `design_space` bound overrides, runs the existing
coordinator (respecting the budget), persists top candidates + best design to
the store, `source="optimize"`. Unknown variable paths → `error` event, exit 2.

`fidelity: "vlm"` may be a stub falling back to `polar_llt` until integration
(emit a `progress` note if so) — do not import `wingopt.aero.vlm` at module top level.

## 7. Studio server API (`feat/studio-server`)

FastAPI app factory `wingopt.studio.server:create_app(config_path: Path) -> FastAPI`,
launched via `python -m wingopt.studio --config configs/default_wing.toml`
(add `__main__.py`). Uses `studio.host/port/runs_root` from config.

REST (all JSON unless noted):

| Method + path | Behavior |
|---|---|
| `GET /api/health` | `{status:"ok", version, metal_available}` |
| `GET /api/schema/params` | Parameter schema: `[{path, unit, default, min?, max?, kind:"float\|int\|str\|enum", choices?}]` derived from config + design_space |
| `GET /api/config/default` | Default config TOML parsed to JSON |
| `POST /api/jobs` | Body: `{kind, label?, config_overrides?: {dotted: value}, sweep?: SweepSpec, optimize?: OptimizeSpec, simulate?: {disable_organic?, organic_engine?}}` → `{job_id, run_id}` (202) |
| `GET /api/jobs` / `GET /api/jobs/{id}` | Job status: `{job_id, run_id, kind, state: pending\|running\|completed\|failed\|cancelled, exit_code?}` |
| `POST /api/jobs/{id}/cancel` | SIGTERM the runner, mark run cancelled |
| `GET /api/jobs/{id}/events` | SSE (`text/event-stream`), replays `events.ndjson` then tails live; each SSE `data:` line is one contract event |
| `GET /api/runs` / `GET /api/runs/{run_id}` | RunRecords |
| `GET /api/designs?run_id=&feasible=1&sort=score&order=desc&limit=50` | Ranked DesignRecords |
| `GET /api/designs/{design_id}` | Full DesignRecord |
| `GET /api/designs/{design_id}/mesh.stl?span_sections=121&profile_points=241` | Binary STL rebuilt from `params` via `wingopt.viz.export_wing_stl` (cache in run artifacts) |
| `GET /api/designs/{design_id}/export.json` | Download design JSON (params+metrics+artifacts) |

Job execution: server creates the store run (so `run_id` is known), then spawns
`sys.executable scripts/simulate.py|sweep.py` with `--run-id`, cwd = repo root,
captures stdout NDJSON → appends to `<run>/events.ndjson` → notifies SSE
subscribers. For `simulate` jobs (which don't natively use the store), the
server parses the `result` event and persists `best_design` + top candidates
via `RunStore` itself. Max 2 concurrent jobs; queue extras (state `pending`).

Static frontend: serve `ui/web/dist` at `/` if the directory exists.
`start.sh --studio` starts the studio server (daemonizable like the TUI path);
`stop.sh` must kill it.

## 8. Frontend (`feat/studio-frontend`)

`ui/web/`: Vite + TypeScript + `three` (npm deps; no CDN/network at runtime).
`npm run build` → `ui/web/dist`. Dev proxy `/api` → `http://127.0.0.1:8151`.

Views (single-page, left nav):
1. **Design Lab** — parameter editor generated from `/api/schema/params`
   (grouped by section, units shown, bounds enforced), launch buttons:
   Simulate / Sweep (axis picker: 1–2 params, range+steps) / Optimize
   (variable picker with bounds).
2. **Jobs** — job list + live progress (SSE): stage, percent, streaming
   sweep points table, cancel button.
3. **Designs** — ranked table (sortable by score/range/L-D/mass; feasibility
   filter; multi-select compare showing param/metric deltas side by side).
4. **Viewer** — Three.js canvas loading `/api/designs/{id}/mesh.stl` via
   `STLLoader`, `OrbitControls` (trackpad: two-finger orbit implicit, pinch
   zoom, shift-drag pan), grid + axes helpers, wireframe toggle, and
   Export STL / Export JSON buttons (download the API URLs).

Dark, technical aesthetic; no UI framework required (vanilla TS + small
helpers fine); keep bundle lean.

## 9. Verification gates (all branches)

- `conda run -n winggen python -m unittest discover -s tests -t .` green.
- `conda run -n winggen ruff check .` clean.
- New physics: at least one analytical validation test each.
- Frontend: `npm run build` succeeds; server serves the built app.
