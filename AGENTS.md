# AGENTS.md — Flying Wing Optimizer

## Project Identity

This is a Python-based aerodynamic optimizer for long-range FPV flying wings. It combines parametric geometry, physics-based models, and (optionally) ML surrogate models to explore the design space for maximum endurance and efficiency. The codebase must remain clean, modular, testable, and scientifically grounded.

## Non-Negotiable Delivery Requirements

- Terminal UI must be built with Ink (`https://github.com/vadimdemedes/ink`) and be visually structured (panels, progress, summaries), not plain log spam.
- Runtime lifecycle must use root scripts only:
  - Start: `./start.sh`
  - Stop: `./stop.sh`
- Conda is the only allowed environment/package manager for project setup. Environment definition must live in `environment.yml`.
- No code slop or spaghetti code:
  - Keep strict boundaries between UI, orchestration/runtime control, and simulation core.
  - Prefer cohesive modules, explicit interfaces, and maintainable function/class sizes.

---

## Git Worktree Rules

### Repository Access and Auth

- Canonical remote: `https://github.com/jfsantoropereira/WingGen.git` (must be `origin` for fetch/push).
- Verify remote before any write operation:
  ```bash
  git remote get-url origin
  ```
- Required GitHub identity:
  - GitHub username: `jfsantoropereira`
  - GitHub email: `jfsantoropereira@gmail.com`
- Verify GitHub CLI auth before pull/push:
  ```bash
  gh auth status -h github.com
  ```
  Expected: logged in as `jfsantoropereira` with a valid token.
- If auth is invalid or expired, re-authenticate:
  ```bash
  gh auth login -h github.com -p https -w
  gh auth status -h github.com
  ```
- Verify commit author identity:
  ```bash
  git config --get user.name
  git config --get user.email
  ```
  `user.email` must resolve to `jfsantoropereira@gmail.com` for this repo.

### Branch Strategy

- `main` — stable, tested, working code only. Never commit directly.
- `dev` — integration branch. All feature branches merge here first.
- `feat/<name>` — feature branches for new modules (e.g., `feat/aero-model`, `feat/battery-model`).
- `fix/<name>` — bugfix branches.
- `exp/<name>` — experimental branches for ML experiments, alternative solvers, etc. These may be abandoned.

### Worktree Conventions (Autonomous Mode)

```bash
# Main checkout (this directory)
WingGen/                        # checked out to `dev`

# Optional protected worktree for release/hotfix validation
../WingGen-main/                # checked out to `main`

# One isolated worktree per autonomous task
../WingGen-wt-<task>/           # checked out to feat/fix/exp branch for that task
```

- Each worktree tracks exactly one branch. Never switch branches inside a worktree — create a new worktree instead.
- Shared dependencies live in a single Conda environment defined by `environment.yml`. Worktrees should use the same Conda env.
- Never run `git checkout` inside a worktree that other processes may be using.
- Autonomous rule: every non-trivial task must start in a fresh task worktree and branch.

### Autonomous Worktree Lifecycle

```bash
# 1) Sync base branches in primary checkout
git fetch origin --prune
git checkout dev
git pull --ff-only origin dev

# 2) Create isolated task worktree from dev
git worktree add ../WingGen-wt-<task> -b feat/<task> dev

# 3) Work inside the task worktree
cd ../WingGen-wt-<task>
# edit, test, commit

# 4) Push branch for review/integration
git push -u origin feat/<task>

# 5) After merge, clean up local worktree and branch
cd ../WingGen
git worktree remove ../WingGen-wt-<task>
git branch -d feat/<task>
```

### Commit Discipline

- Atomic commits: one logical change per commit.
- Commit messages follow conventional commits: `feat:`, `fix:`, `refactor:`, `test:`, `docs:`, `exp:`.
- Every commit on `dev` and `main` must pass `pytest` and `ruff check`.
- If Ink/UI code changes, corresponding UI lint/tests must pass before merge.
- Experimental branches (`exp/*`) are exempt from CI but must still be importable.
- Standard commit/push sequence:
  ```bash
  git add -A
  git commit -m "feat: <short description>"
  git push
  ```

---

## Required Work Sequence

For non-trivial tasks, follow this sequence:

1. **Contextualize**: Inspect current code paths, configs, and relevant module interfaces. Read before writing. Understand what exists and what will be affected.
2. **Plan**: List minimal safe steps. Identify dependencies, edge cases, and what could break. Write the plan as comments or in a scratch file before touching source code.
3. **Implement**: Smallest coherent patch set. Prefer many small functions over monoliths. Every function gets a docstring and type hints.
4. **Verify**: Run targeted tests and smoke checks. For numerical code, verify against known analytical solutions or reference data. For ML code, verify shapes and gradients.
5. **Report**: Summarize changes, risks, and untested areas with file references. Flag any assumptions made or parameters chosen without strong justification.

---

## Coding Standards

### Python

- **Python 3.11+**. Use modern syntax (match statements, type unions with `|`, etc.).
- **Type hints everywhere**. Use `numpy.typing` for array annotations.
- **Docstrings**: Google style. Every public function, class, and module.
- **No magic numbers**: All physical constants and design parameters live in config files or dataclasses, never inline.
- **Units**: SI internally, always. Conversions happen only at I/O boundaries. Document units in docstrings.
- **Linting**: `ruff` for linting and formatting. Config in `pyproject.toml`.
- **Testing**: `pytest`. Aim for >80% coverage on core physics modules. Numerical tests should use `np.testing.assert_allclose` with explicit tolerances.

### TypeScript / Ink UI

- Use TypeScript for Ink UI code with strict typing enabled.
- Keep UI components focused on rendering/state; keep simulation logic in Python modules.
- Avoid ad-hoc process management in components; isolate process orchestration in a dedicated runtime layer.
- UI interactions with the simulator must use explicit, testable contracts (structured CLI output or IPC).

### Project Structure (enforced)

```
WingGen/
├── AGENTS.md
├── SYSTEM_OUTLINE.md
├── environment.yml
├── pyproject.toml
├── README.md
├── start.sh
├── stop.sh
├── src/
│   └── wingopt/
│       ├── __init__.py
│       ├── config/           # Dataclasses, YAML/TOML loaders
│       ├── geometry/         # Wing planform, airfoil, CG
│       ├── aero/             # Aerodynamic models (panel, VLM, surrogate)
│       ├── propulsion/       # Motor, prop, battery models
│       ├── structures/       # Weight estimation, spar sizing
│       ├── stability/        # CG, static margin, trim
│       ├── optimizer/        # Objective functions, constraints, search
│       ├── ml/               # Surrogate models (optional)
│       ├── viz/              # Plotting, 3D preview
│       └── utils/            # Unit conversions, interpolation, I/O
├── ui/
│   └── terminal/             # Ink application (terminal UI)
├── tests/
│   ├── test_geometry.py
│   ├── test_aero.py
│   ├── test_propulsion.py
│   └── ...
├── data/
│   ├── airfoils/             # .dat files (Selig format)
│   ├── motors/               # Motor spec sheets / CSV
│   ├── props/                # Prop performance data
│   └── materials/            # Carbon, foam, adhesive properties
├── configs/
│   └── default_wing.toml     # Default design parameters
├── notebooks/                # Jupyter exploration (not production)
└── scripts/                  # CLI entry points
```

### Numerical Code Rules

- Never silently clamp or clip values. Raise or warn if inputs are out of valid range.
- All optimization constraints must be explicitly documented with physical justification.
- Random seeds must be settable and logged for reproducibility.
- Prefer `scipy.optimize` over hand-rolled solvers unless there's a documented reason.

---

## Modus Operandi for Coding Agents

### Before Writing Any Code

1. Read `SYSTEM_OUTLINE.md` to understand the full system architecture.
2. Read the module you're about to modify and its tests.
3. Check `configs/default_wing.toml` for current parameter values.
4. Check `environment.yml` and verify dependency impact (Conda-first workflow).
5. If your change affects interfaces between modules, check all importers.

### When Adding a New Module

1. Create the module file in the correct `src/wingopt/` subdirectory.
2. Create a corresponding test file in `tests/`.
3. Add the module to `SYSTEM_OUTLINE.md` if it represents a new capability.
4. Write at least one analytical validation test (not just "does it run").
5. Update `configs/default_wing.toml` if new parameters are introduced.

### When Modifying Physics Models

- Always cite the source (paper, textbook, XFOIL validation, etc.) in a docstring or comment.
- If using empirical correlations, state the valid range of applicability.
- Never change a physics model without updating or adding a test that validates the change.

### When Working with ML / Surrogate Models

- ML is optional and supplements, never replaces, the physics-based models.
- All training data must be generated from the physics pipeline and be reproducible.
- Surrogate models must report uncertainty / confidence bounds.
- Log all hyperparameters and training metrics.
- Never deploy a surrogate model without comparing its predictions to the physics model on a held-out test set.

### What NOT to Do

- Do not install packages without adding them to `pyproject.toml`.
- Do not install project dependencies with `pip`/virtualenv workflows when Conda is required.
- Do not hardcode file paths. Use `pathlib` and config-relative paths.
- Do not print to stdout in library code. Use `logging`.
- Do not use `from module import *`.
- Do not commit notebooks with executed cells to `main` or `dev`.
- Do not optimize prematurely. Profile first, then optimize the bottleneck.
- Do not bypass lifecycle scripts (`start.sh`, `stop.sh`) for standard simulator operation.
- Do not couple Ink rendering code directly to physics calculations.

---

## Agent Communication Protocol

When handing off work or reporting results, always include:

```
## Handoff Report
- **Branch**: feat/xxx
- **Files changed**: list with one-line summary each
- **Tests added/modified**: list
- **Known issues**: any edge cases, untested paths, or assumptions
- **Next steps**: what the next agent or human should do
- **Dependencies**: any new packages or data files required
```
