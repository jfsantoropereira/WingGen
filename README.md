# WingGen

Local flying-wing design and optimization studio: a Python simulation core with
GPU-accelerated aerodynamics (Apple Metal via MLX), parameter sweeps, bounded
optimization, a web studio with an interactive 3D viewer, and an Ink terminal UI.

## Fidelity Tiers

| Tier | Engine | Use | Speed (M2 Pro) |
|---|---|---|---|
| 1 | `polar_llt` — polar-based lifting line | optimizer inner loops, sweeps | ~ms/point |
| 2 | `vlm` — vortex-lattice (`wingopt.aero.vlm`, wake + Trefftz drag; MLX/Metal or numpy) | sweep enrichment (`evaluation.fidelity: "vlm"`), trim/NP/CDi verification | ~0.5 s/point |
| 3 | `lbm` — D3Q19 lattice-Boltzmann volumetric CFD (`wingopt.cfd`, Smagorinsky LES on Metal) | organic-refinement engine option, qualitative flow verification | ~3 min/eval @ 96×64×48×1500 steps |

Caveats: the LBM tier is resolution-limited (simulated Reynolds ~10², not
flight Re ~2×10⁵), so its force coefficients are qualitative — use it for
relative comparisons and flow sanity checks, not calibrated drag numbers. The
VLM tier is validated against lifting-line theory (lift slope, e≈0.99 Trefftz
efficiency, NP at ~0.24 MAC for rectangular wings) and is grid-converged.
External SU2/OpenFOAM/DAFoam adapters remain available for production CFD.

## Web Studio

```bash
./start.sh --studio            # interactive (Ctrl-C to stop)
./start.sh --studio --daemon   # background; ./stop.sh to stop
```

Open http://127.0.0.1:8151 — Design Lab (parameter editor + simulate/sweep/
optimize launchers), Jobs (live NDJSON progress via SSE), Designs (ranked
comparisons across all runs), Viewer (Three.js 3D wing with trackpad
orbit/pan/zoom, wireframe, resolution presets, STL/JSON export).

Frontend build (served automatically from `ui/web/dist` when present):

```bash
conda run -n winggen npm --prefix ui/web install
conda run -n winggen npm --prefix ui/web run build
```

## Sweeps and Bounded Optimization (CLI)

```bash
conda run -n winggen python scripts/sweep.py \
  --config configs/default_wing.toml --spec my_sweep.json \
  --runs-root outputs/runs --label "span x sweep study"
```

Sweep spec (1–2+ axes, cartesian grid ≤ 2000 points):

```json
{
  "kind": "sweep",
  "parameters": [
    {"path": "geometry.wingspan_m", "min": 1.2, "max": 1.8, "steps": 7},
    {"path": "geometry.sweep_deg", "values": [22, 26, 30]}
  ],
  "evaluation": {"mode": "full", "fidelity": "vlm"},
  "objective": "range_km"
}
```

Optimize spec (bounded design-space override, existing coordinator):

```json
{
  "kind": "optimize",
  "variables": {"geometry.wingspan_m": [1.3, 1.7], "geometry.sweep_deg": [20, 32]},
  "budget": {"max_evaluations": 400},
  "objective": "combined_score"
}
```

Every evaluated design is persisted to `outputs/runs/<run_id>/` (see
`wingopt.store.RunStore`) and ranked in the studio's Designs view. Full
interface contract: `STUDIO_CONTRACT.md`.

## Requirements

- Conda
- Node.js and npm (installed via Conda environment)
- Python 3.11+

## Quick Start

1. Create the environment:

```bash
conda env create -f environment.yml
```

2. Start the simulator UI:

```bash
./start.sh
```

3. Stop the simulator:

```bash
./stop.sh
```

Daemon/background mode (optional):

```bash
./start.sh --daemon
./stop.sh
```

## Conda-First Commands

Install Ink UI dependencies inside Conda env:

```bash
conda run --no-capture-output -n winggen npm --prefix ui/terminal install
```

Run UI lint/typecheck inside Conda env:

```bash
conda run --no-capture-output -n winggen npm --prefix ui/terminal run lint
```

Run interactive TUI directly from Conda env:

```bash
conda run --no-capture-output -n winggen npm --prefix ui/terminal run start
```

## Backend CLI (machine-readable)

Run the optimizer directly (NDJSON events):

```bash
conda run -n winggen python scripts/simulate.py --config configs/default_wing.toml
```

Useful switches:

- `--disable-organic` (skip pass-2 refinement)
- `--organic-engine proxy|lbm|su2|openfoam|dafoam` (override configured pass-2 engine)

Event contract:

- `progress`
- `result`
- `error`

## Organic Pass-2 Refinement

The simulator now runs a second-pass evolutionary organic refinement (enabled in `configs/default_wing.toml`) that optimizes a non-constant spanwise dihedral profile and exports a high-resolution final STL.

Engines: `proxy` (fast, default), `lbm` (in-process GPU volumetric CFD;
~3 min/candidate — reduce population/generations or use for final-candidate
verification), `su2`/`openfoam`/`dafoam` (external runner contract below).

Default final STL:

- `outputs/best_wing_organic_highres.stl`

### External CFD Runner Contract

For `organic_refinement.engine = "su2" | "openfoam" | "dafoam"`, set:

- `organic_refinement.cfd.external_runner`

Template placeholders supported:

- `{engine}`
- `{case_dir}`
- `{input_json}`
- `{output_json}`

Example contract runner (included):

```bash
python3 scripts/cfd/mock_external_cfd.py \
  --engine {engine} \
  --input-json {input_json} \
  --output-json {output_json}
```

## Architecture

- Python core modules in `src/wingopt/`
  - Geometry, aerodynamics (`aero` polar tier + `aero/vlm.py`), volumetric CFD
    (`cfd/`), propulsion, structures, stability, performance
  - Separate `wing_optimizer`, `propulsion_optimizer`, and `coordinator`
  - `sweeps/` (grid sweeps + bounded optimize), `store/` (run/design records),
    `studio/` (FastAPI server), `utils/gpu.py` (MLX/Metal backend selection)
- Web studio UI in `ui/web/` (Vite + TypeScript + Three.js), Ink UI in `ui/terminal/`
- Lifecycle scripts at root: `start.sh` (`--studio`), `stop.sh`
- Interface contract for all of the above: `STUDIO_CONTRACT.md`

## Testing

```bash
conda run -n winggen python -m unittest discover -s tests -t . -v
```
