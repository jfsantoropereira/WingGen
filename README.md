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

Event contract:

- `progress`
- `result`
- `error`

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
