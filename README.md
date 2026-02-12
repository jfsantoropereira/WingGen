# WingGen

Flying-wing design optimizer with a Python simulation core and an Ink terminal UI.

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
- `--organic-engine proxy|su2|openfoam|dafoam` (override configured pass-2 engine)

Event contract:

- `progress`
- `result`
- `error`

## Organic Pass-2 Refinement

The simulator now runs a second-pass evolutionary organic refinement (enabled in `configs/default_wing.toml`) that optimizes a non-constant spanwise dihedral profile and exports a high-resolution final STL.

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
  - Geometry, aerodynamics, propulsion, structures, stability, performance
  - Separate `wing_optimizer`, `propulsion_optimizer`, and `coordinator`
- Ink UI in `ui/terminal/`
- Lifecycle scripts at root: `start.sh`, `stop.sh`

## Testing

```bash
conda run -n winggen python -m unittest discover -s tests -t . -v
```
