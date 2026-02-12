# SYSTEM_OUTLINE.md — Flying Wing Optimizer

## 1. Project Purpose

A Python optimizer for long-range FPV flying wings that finds the best combination of geometric, aerodynamic, propulsion, and structural parameters to maximize endurance and range, subject to practical constraints.

The tool should answer: **Given a target mission (range, speed, payload), what wing geometry, motor/prop/battery combination, and structural layout gives the best performance?**

## 1.1 Non-Negotiable Implementation Requirements

1. **Ink terminal UI is mandatory**:
   - The simulator must run through an Ink-based terminal interface (`https://github.com/vadimdemedes/ink`).
   - The terminal UI must be visually rich (clear layout, progress feedback, result tables/charts), not plain text logs.
2. **Root lifecycle scripts are mandatory**:
   - Startup must be done with `./start.sh`.
   - Shutdown must be done with `./stop.sh`.
3. **Conda is mandatory for environments**:
   - All dependency installation and environment setup must be done with Conda.
   - Project environment definition lives in `environment.yml`.
4. **No spaghetti code**:
   - Enforce strict separation between UI, orchestration, and simulation core.
   - Keep modules cohesive, typed, testable, and small enough to be maintainable.
5. **Full parameterization is mandatory**:
   - No hardcoded mission, atmosphere, or sizing constants in optimization paths.
   - Wingspan, AUW/component masses, ambient temperature, and airfoil choice must be configurable and optionally optimizable.
   - Every optimizable parameter must have explicit units and bounds in config.

## 1.2 Parameterization Scope

- All major domains are parameterized: mission, environment, geometry, structure, propulsion, and mass properties.
- Any parameter can be marked as fixed, swept, or optimized.
- Environmental conditions (temperature, altitude/pressure, humidity) must support single-case and multi-scenario runs.
- Airfoil must support both single selection and candidate set evaluation during optimization.
- Optimization must be decomposed into separate wing and propulsion optimizers coordinated by a higher-level routine.

---

## 2. Reference Design (Baseline Constraints)

These values are baseline defaults (not hardcoded limits). The optimizer explores around and beyond them to stay anchored to a real, buildable aircraft.

| Parameter | Value | Source / Rationale |
|---|---|---|
| Wingspan | 1.5 m | User constraint |
| Airfoil | MH60 | High L/D at low Re, gentle stall |
| Planform | Flying wing (no tail) | Simplicity, low drag |
| Sweep | 25–28° | Pitch stability without excessive reflex |
| Cruise speed | ~60 km/h (16.7 m/s) | Max efficiency target |
| Max speed | 100 km/h (27.8 m/s) | Structural / flutter limit |
| Motor | 900 Kv (brushless outrunner) | User-selected, may be re-evaluated |
| Prop | 8–9" (downsized from 12" due to Kv) | Matched to motor Kv on 4S |
| Battery | 4S Li-ion (18650 cells) | Energy density for endurance |
| Battery config | 4S1P or 4S2P | Optimizer should evaluate |
| Servos | 9g metal gear digital × 4 | Split elevon configuration |
| Control surfaces | 4× split elevons (~195mm × 45mm, 0.5mm carbon) | Aerodynamic yaw authority |
| Flight controller | Matek H743-WLITE | ArduPilot Plane |
| Construction | Foam core, carbon spar, carbon center plate | Practical constraint |
| Washout | 2–4° root to tip | Tip stall prevention |

### Component Weight Budget (Initial Estimates)

| Component | Estimated Weight (g) | Notes |
|---|---|---|
| Motor (900Kv ~2216 class) | 60–75 | Depending on exact model |
| ESC (30-40A) | 25–35 | |
| Propeller (8-9") | 12–18 | |
| Battery 4S1P (18650) | 180–200 | 4× cells + holder/wiring |
| Battery 4S2P (18650) | 360–400 | 8× cells + holder/wiring |
| Flight controller (H743-WLITE) | 12 | Per Matek specs |
| GPS module | 10–15 | |
| ELRS receiver | 2–5 | |
| DJI Air Unit (O3 or similar) | 30–40 | Depends on unit |
| Servos × 4 (9g each) | 36 | |
| Wiring, connectors, hardware | 30–50 | Estimate |
| Airframe (foam + carbon) | TBD | **Structures module calculates this** |

**Target AUW**: 800–1200g depending on battery config. Optimizer must evaluate trade-off.

---

## 3. System Architecture

```
┌─────────────────────────────────────────────────────┐
│                   OPTIMIZER LOOP                     │
│         (scipy / evolutionary / Bayesian)            │
│                                                      │
│   Design Vector x = [b, cr, ct, Λ, ε, ...]         │
│                        │                             │
│              ┌─────────▼──────────┐                  │
│              │   GEOMETRY MODULE  │                  │
│              │  Planform, airfoil │                  │
│              │  CG placement      │                  │
│              └────────┬───────────┘                  │
│                       │                              │
│         ┌─────────────┼─────────────┐                │
│         ▼             ▼             ▼                │
│  ┌────────────┐ ┌───────────┐ ┌──────────────┐      │
│  │    AERO    │ │ STRUCTURE │ │  PROPULSION  │      │
│  │ CL, CD, Cm│ │  Weights  │ │ Motor+Prop   │      │
│  │ L/D, trim │ │  Spar size│ │ Battery, η   │      │
│  └─────┬──────┘ └─────┬─────┘ └──────┬───────┘      │
│        │              │              │               │
│        └──────────────┼──────────────┘               │
│                       ▼                              │
│              ┌─────────────────┐                     │
│              │   PERFORMANCE   │                     │
│              │  Range, endure  │                     │
│              │  Rate of climb  │                     │
│              └────────┬────────┘                     │
│                       │                              │
│              ┌────────▼────────┐                     │
│              │   STABILITY    │                      │
│              │  Static margin │                      │
│              │  Trim drag     │                      │
│              └────────┬────────┘                     │
│                       │                              │
│                       ▼                              │
│              Objective f(x) + Constraints g(x)       │
└─────────────────────────────────────────────────────┘
```

### 3.1 Optimization Decomposition

- The optimization stack is split into three explicit modules:
  - `wing_optimizer`: geometry, airfoil, stability, and wing-mass-driven decisions.
  - `propulsion_optimizer`: motor/prop/battery matching under performance and thermal constraints.
  - `coordinator`: couples both optimizers and converges integrated designs.
- A single monolithic optimizer implementation is not allowed.

---

## 4. Module Specifications

### 4.1 Geometry Module (`src/wingopt/geometry/`)

**Purpose**: Define the wing planform from a design vector and compute all derived geometric properties.

**Design vector parameters**:
- `b` — wingspan [m] (fixed or variable, default 1.5)
- `c_r` — root chord [m]
- `c_t` — tip chord [m] (or taper ratio λ = c_t / c_r)
- `sweep` — quarter-chord sweep angle Λ [deg]
- `twist` — washout angle ε [deg] (linear root-to-tip)
- `elevon_span_frac` — fraction of semi-span occupied by elevons
- `elevon_chord_frac` — fraction of local chord occupied by elevon
- `split_ratio` — inner/outer elevon split (0.5 = equal)

**Outputs**:
- Wing area S [m²]
- Aspect ratio AR
- Mean aerodynamic chord (MAC)
- Taper ratio λ
- Chord distribution c(y) along span
- Quarter-chord line coordinates
- Elevon geometry (4 surfaces: positions, areas, moment arms)
- Planform plot

**Key equations**:
- `S = b/2 × (c_r + c_t)` (trapezoidal planform)
- `AR = b² / S`
- `MAC = (2/3) × c_r × (1 + λ + λ²) / (1 + λ)`

**Airfoil handling**:
- Load airfoil coordinates from a library of `.dat` files (Selig format)
- Allow discrete airfoil candidate sets during optimization (not only a single fixed airfoil)
- Interpolate / resample for analysis
- Support reflex airfoils (positive Cm0 required for tailless stability)
- Store per-airfoil polars (CL, CD, Cm vs α) from XFOIL or precomputed tables

### 4.2 Aerodynamics Module (`src/wingopt/aero/`)

**Purpose**: Predict lift, drag, and pitching moment for the full wing at given flight conditions.

**Approach (tiered)**:

1. **Tier 1 — Lifting Line Theory (LLT)**: Fast, good for initial sweeps. Handles taper, twist, sweep (approximately). Prandtl or Weissinger extended lifting line.
2. **Tier 2 — Vortex Lattice Method (VLM)**: More accurate for swept wings. Use AVL (Athena Vortex Lattice) as subprocess or implement a basic panel method. Handles camber, twist, sweep, control surface deflections.
3. **Tier 3 — ML Surrogate** (optional): Train on Tier 2 outputs for fast evaluation during optimization. Gaussian Process or small neural net.

**Required outputs at each flight condition (V, α, δ_elevon)**:
- CL, CD, CDi (induced), CDp (parasitic), CD0 (zero-lift)
- Cm (pitching moment about CG)
- L/D ratio
- Span loading distribution (for structural loads)
- Trim elevator deflection for given CG

**Parasitic drag model**:
- Fuselage/pod: flat plate equivalent area based on component dimensions
- Form factor method for each component (wing, pod, servos, antennas)
- Interference factors

**Reynolds number considerations**:
- At 60 km/h and MAC ~0.2m: Re ≈ 200,000–300,000
- Selected airfoil polar data must cover this Re range
- Laminar separation bubbles are significant at these Re — use XFOIL polars that capture transition

**Atmospheric parameterization**:
- Evaluate aerodynamic states using configurable atmosphere inputs: temperature, altitude or pressure, and optional humidity.
- Compute `ρ` and `μ` per scenario before solving aero/propulsion coupling.
- Support multi-scenario objective aggregation (weighted average or worst-case).

**Control surface aerodynamics**:
- Elevon deflection modeled as local camber change
- Hinge moment estimation for servo sizing validation
- Split elevon differential for yaw: model as asymmetric drag from differential deflection

### 4.3 Propulsion Module (`src/wingopt/propulsion/`)

**Purpose**: Model the motor-prop-battery system to predict thrust, power draw, and endurance at any operating point.

**Motor model**:
- Input: Kv [RPM/V], Rm [Ω], I0 [A] (no-load current), max current
- RPM = Kv × (V_batt - I × Rm)
- Torque = (I - I0) × Kt, where Kt = 1 / Kv (in SI)
- Electrical power: P_elec = V_batt × I
- Mechanical power: P_mech = Torque × ω
- Motor efficiency: η_motor = P_mech / P_elec

**Propeller model**:
- Use APC or UIUC prop database for performance data (CT, CP vs J)
- Advance ratio: J = V / (n × D)
- Thrust: T = CT × ρ × n² × D⁴
- Power: P = CP × ρ × n³ × D⁵
- Prop efficiency: η_prop = J × CT / CP
- If no data available: use Blade Element Momentum Theory (BEMT) as fallback

**Motor-prop matching**:
- Find operating point where motor torque = prop torque at given RPM and airspeed
- Iterate to convergence (typically 5-10 iterations)
- Output: thrust, current draw, efficiency at each airspeed

**Battery model**:
- Cell chemistry: Li-ion 18650 (e.g., Samsung 30Q: 3000mAh, 15A continuous, ~46g)
- Pack configurations: 4S1P, 4S2P, etc.
- Voltage sag model: V_cell(I, SOC) using internal resistance
- Usable capacity: assume 80% of rated (cutoff at 3.0V/cell)
- Energy: E = Σ V(t) × I(t) × dt over discharge
- Weight: n_cells × cell_mass + wiring overhead (~10%)

**Key outputs**:
- Thrust available vs airspeed curve
- Current draw vs airspeed curve
- Endurance at cruise speed [minutes]
- Range at cruise speed [km]
- Max rate of climb

### 4.4 Structures Module (`src/wingopt/structures/`)

**Purpose**: Estimate airframe weight and validate structural integrity.

**Weight estimation**:
- Foam wing panels: volume × density (EPS ~20 kg/m³, EPP ~30 kg/m³, XPS ~35 kg/m³)
- Carbon spar: tube/rod dimensions × carbon density (~1600 kg/m³)
- Carbon center plate: area × thickness × density
- Skin (if used): area × areal weight (fiberglass ~50-200 g/m², carbon ~100-300 g/m²)
- Control surfaces: 4× (area × 0.5mm × carbon density)
- Adhesive/epoxy: empirical allowance (~5-10% of structural weight)

**Structural validation** (simplified):
- Root bending moment from span loading at max load factor (n = 3–4g for gusts)
- Spar sizing: required EI for bending, GJ for torsion
- Flutter margin: check that torsional frequency > 2× max expected gust frequency
- Wing deflection at max load: should be < 5% of semi-span

**Material properties database**:
- Carbon fiber: E = 70–230 GPa (depending on fiber type), σ_ult = 600–3000 MPa
- Foam core: E = 5–30 MPa, shear strength = 0.1–0.5 MPa
- Epoxy: E = 2.5–4.5 GPa

### 4.5 Stability Module (`src/wingopt/stability/`)

**Purpose**: Ensure the flying wing is statically and dynamically stable (longitudinally).

**Static longitudinal stability**:
- Neutral point (NP) location: from VLM or analytical approximation
  - NP ≈ aerodynamic center + corrections for sweep, taper
- CG location: from weight buildup and component placement
- Static margin: SM = (x_NP - x_CG) / MAC
- **Constraint: SM > 5% (minimum), target 10-15%**

**Trim analysis**:
- Find elevon deflection δ_trim that gives Cm = 0 at desired CL
- Trim drag penalty: additional drag from deflected elevons
- **This directly affects cruise efficiency — a poorly balanced wing wastes energy trimming**

**CG envelope**:
- Calculate CG for each battery config (4S1P vs 4S2P)
- CG travel with fuel burn (battery SOC doesn't change CG, but consumables do)
- Determine required battery position range for CG adjustment

**Control authority check**:
- At minimum airspeed (stall + margin): can elevons generate enough Cm to trim?
- At max speed: are surfaces not overloaded? (validated by hinge moments from aero module)
- Yaw authority from split differential: sufficient for coordinated turns?

### 4.6 Performance Module (integrated)

**Purpose**: Combine aero + propulsion + weight to compute mission performance.

**Endurance calculation**:
```
For given cruise speed V_cruise:
1. W = AUW × g
2. CL_required = W / (0.5 × ρ × V² × S)
3. CD_total = CD(CL_required) including trim drag
4. D = 0.5 × ρ × V² × S × CD_total
5. T_required = D (level flight, steady state)
6. P_required = T_required × V / η_prop  (shaft power)
7. I_draw = P_required / (η_motor × V_batt)
8. Endurance = battery_capacity / I_draw [hours]
9. Range = V_cruise × Endurance [km]
```

**Climb performance**:
- Excess thrust: T_available - D
- Rate of climb: RC = (T_excess × V) / W
- Service ceiling: altitude where RC = 0.5 m/s

**Stall speed**:
- V_stall = sqrt(2W / (ρ × S × CL_max))
- Must be well below cruise speed

**Scenario-aware performance**:
- Run performance for one or more environment scenarios (temperature/altitude/pressure combinations).
- Report both nominal and robustness metrics (e.g., nominal range plus worst-case range).

### 4.7 Wing Optimizer Module (`src/wingopt/optimizer/wing_optimizer.py`)

**Purpose**: Optimize wing geometry, airfoil choice, and stability-driven parameters independent of propulsion component selection.

**Design variables**:
- Wingspan
- Root chord, tip chord (or taper ratio)
- Sweep angle
- Twist/washout
- Elevon sizing (span fraction, chord fraction, split ratio)
- Airfoil selection (from candidate set)
- CG position
- Structural sizing knobs that affect wing mass and stiffness

**Objectives**:
- Maximize aerodynamic efficiency and trim quality across mission points.
- Minimize drag and trim penalties while satisfying static stability and control authority.

**Constraints**:
| Constraint | Limit | Justification |
|---|---|---|
| Static margin | > 5% MAC | Longitudinal stability |
| Stall speed | < 30 km/h | Safe landing speed |
| Max wing loading | < 50 g/dm² | Reasonable for foam wing |
| Spar bending stress | < allowable / 1.5 | Safety factor |
| Elevon hinge moment | < servo max torque | Servo not overloaded |
| Min cruise L/D | > 8 | Sanity check |

**Outputs**:
- Pareto-ranked wing candidates with aero/stability metrics
- Required thrust/power envelopes passed to propulsion optimizer

### 4.8 Propulsion Optimizer Module (`src/wingopt/optimizer/propulsion_optimizer.py`)

**Purpose**: Optimize motor-prop-battery configuration against required thrust/power profiles from the wing optimizer.

**Design variables**:
- Motor option / Kv / resistance ranges (or discrete catalog entries)
- Propeller diameter/pitch from allowed dataset
- Battery chemistry, series, parallel, usable fraction, mass
- ESC limits and efficiency assumptions
- Optional payload/AUW distribution parameters

**Objectives**:
- Maximize endurance/range and cruise efficiency for each wing candidate.
- Minimize thermal/current margin violations and unnecessary mass.

**Constraints**:
| Constraint | Limit | Justification |
|---|---|---|
| AUW | < configured practical limit | Launch/handling practicality |
| Cruise throttle | < 70% | Thermal margin on motor |
| Current draw | < motor/ESC/battery continuous limits | Reliability |
| Voltage sag | within safe operating window | Stable control electronics |
| Prop tip speed | below noise/compressibility threshold | Efficiency and safety |

**Outputs**:
- Best propulsion configuration per wing candidate
- Endurance/range predictions across all defined environment scenarios

### 4.9 Optimization Coordinator (`src/wingopt/optimizer/coordinator.py`)

**Purpose**: Coordinate the two optimizers and converge to coupled wing + propulsion solutions.

**Workflow**:
1. Run wing optimizer over geometry/airfoil space under environment and mass bounds.
2. For top wing candidates, run propulsion optimizer using required thrust/power envelopes.
3. Feed resulting propulsion mass/performance back into wing evaluation when needed.
4. Iterate until coupled metrics converge (or stop criteria reached).
5. Report ranked integrated designs and robustness across scenarios.

### 4.10 Terminal UI + Runtime Orchestration (`ui/terminal/`, `start.sh`, `stop.sh`)

**Purpose**: Provide the primary user interface and controlled runtime lifecycle for the simulator.

**Ink UI requirements**:
- Built with Ink (React for CLIs), with TypeScript preferred.
- Presents mission/config inputs, run controls, live optimization progress, and final results summaries.
- Uses structured terminal components (panels, tables, progress indicators) instead of unstructured print logs.

**Runtime lifecycle requirements**:
- `start.sh`:
  - Activates (or creates, if needed) the Conda environment.
  - Launches the Ink application as the default user entrypoint.
- `stop.sh`:
  - Performs graceful shutdown of running simulator/UI processes started by `start.sh`.
  - Cleans PID/lock artifacts if used.

**Integration boundary**:
- Ink UI orchestrates runs and displays results.
- Physics/optimization modules remain Python and do not contain terminal rendering code.
- Interface between UI and simulation core must be explicit (CLI contract, JSON I/O, or IPC), versioned, and testable.

---

## 5. ML Strategy (Optional, Phase 2)

**When to use ML**: Only after the physics pipeline is working and validated. ML accelerates the optimizer by replacing expensive aero evaluations with fast surrogate predictions.

**Approach**:
1. Generate training data: run VLM for 5,000–10,000 design points (Latin Hypercube sampling of design space)
2. Train Gaussian Process (GP) surrogate for CL, CD, Cm as functions of geometry + flight condition
3. GP provides uncertainty estimates — use these to guide active learning (sample where uncertainty is highest)
4. Validate surrogate: compare predictions vs VLM on 500 held-out points. Require R² > 0.95 and max error < 5%
5. Use surrogate in optimizer loop. Fall back to VLM for final validation of top candidates.

**Libraries**: `scikit-learn` (GP), `botorch` (Bayesian optimization), or `pytorch` if neural surrogate is needed.

**When NOT to use ML**:
- The physics models are fast enough already (LLT runs in milliseconds)
- You don't have enough training data
- The design space is small enough for exhaustive search

---

## 6. Data Pipeline

### Input Data Required

1. **Airfoil polar library** — CL, CD, Cm vs α at Re = 100k, 200k, 300k, 500k for each supported airfoil
   - Source: XFOIL runs or precomputed from airfoiltools.com / UIUC-based datasets
   - Format: CSV with columns `alpha, CL, CD, Cm, Re`
   - Store in `data/airfoils/polars/<airfoil_name>.csv`

2. **Airfoil coordinate library** — x, y points defining each airfoil shape
   - Source: UIUC airfoil database
   - Format: Selig `.dat`
   - Store in `data/airfoils/coordinates/<airfoil_name>.dat`

3. **Propeller performance data** — CT, CP vs J at various RPM
   - Source: APC or UIUC prop database
   - Format: CSV with columns `J, CT, CP, eta`
   - Store in `data/props/<prop_name>.csv`

4. **Motor specifications**
   - Format: TOML or CSV with Kv, Rm, I0, max_I, weight
   - Store in `data/motors/`

5. **Material properties**
   - Format: TOML
   - Store in `data/materials/`

6. **Atmospheric scenario definitions**
   - Parameters: temperature, altitude or pressure, humidity, and scenario weight
   - Format: TOML/CSV
   - Store in `configs/atmosphere/` or embedded in run config

### Output Data

- Optimization results: CSV + JSON with all design variables and performance metrics
- Convergence history plots
- Best design summary with geometry visualization
- Weight breakdown pie chart
- Performance envelope plots (thrust vs drag vs speed)

---

## 7. Implementation Phases

### Mandatory Delivery Gates
- [ ] Simulator can be fully started via `./start.sh` and fully stopped via `./stop.sh`.
- [ ] Ink terminal UI is the primary interactive interface.
- [ ] Conda environment setup is fully reproducible from `environment.yml`.
- [ ] Architecture remains modular (UI, orchestration, and simulation core separated).
- [ ] Full parameterization is enforced (wingspan, mass/AUW, temperature, atmosphere, and airfoil candidates with bounds).
- [ ] Wing and propulsion optimizers are implemented as separate modules with a coordinator.

### Phase 1 — Foundation (MVP)
- [ ] Conda-first bootstrap (`environment.yml`, documented activation flow)
- [ ] Project scaffolding (pyproject.toml, directory structure, configs)
- [ ] Define global parameter schema with units and optimization bounds
- [ ] Geometry module with planform generation
- [ ] Load multi-airfoil coordinate + polar library
- [ ] Simple aero model (lifting line + empirical drag buildup)
- [ ] Motor-prop matching (from database lookup)
- [ ] Battery model (simple discharge)
- [ ] Weight estimation (component-based)
- [ ] Atmosphere model with temperature/altitude/pressure inputs
- [ ] Basic performance calculator (endurance, range) over one or more scenarios
- [ ] CG and static margin check
- [ ] Wing optimizer module (geometry/airfoil/stability)
- [ ] Propulsion optimizer module (motor/prop/battery)
- [ ] Optimization coordinator for coupled convergence
- [ ] Backend simulation entrypoint consumable by Ink UI (structured machine-readable output)
- [ ] Root lifecycle scripts: `start.sh` and `stop.sh`
- [ ] Ink UI MVP: configuration panel, run status/progress, and final result summary view

### Phase 2 — Refinement
- [ ] VLM integration (AVL subprocess or native implementation)
- [ ] Trim drag accounting
- [ ] Structural sizing (spar, skin)
- [ ] Multi-objective optimization (range vs weight)
- [ ] Sensitivity analysis (tornado plots)
- [ ] 3D planform visualization
- [ ] Ink UI refinement: richer visualization and run history navigation

### Phase 3 — ML Augmentation (Optional)
- [ ] Training data generation pipeline
- [ ] GP surrogate model
- [ ] Bayesian optimization loop
- [ ] Active learning for data-efficient training
- [ ] Surrogate validation and comparison

### Phase 4 — Polish
- [ ] Ink UI polish pass (layout, UX consistency, accessibility in terminal)
- [ ] Export optimized design to CAD-ready coordinates
- [ ] DXF/SVG export for foam cutting templates
- [ ] Flight sim integration (optional: export to ArduPilot SITL)

---

## 8. Configuration Schema

The default config file (`configs/default_wing.toml`) defines all parameters:

```toml
[mission]
cruise_speed_kmh = 60.0
max_speed_kmh = 100.0
target_range_km = 30.0          # aspiration, not hard constraint

[environment]
temperature_c = 20.0
altitude_m = 100.0
pressure_pa = 101325            # optional if altitude model is used
relative_humidity = 0.50

[[environment.scenarios]]
name = "nominal"
temperature_c = 20.0
altitude_m = 100.0
relative_humidity = 0.50
weight = 0.6

[[environment.scenarios]]
name = "hot_high"
temperature_c = 35.0
altitude_m = 1500.0
relative_humidity = 0.30
weight = 0.4

[geometry]
wingspan_m = 1.5
sweep_deg = 26.0                # quarter-chord
twist_deg = 3.0                 # washout, root to tip
taper_ratio = 0.5               # tip_chord / root_chord
airfoil = "mh60"
airfoil_candidates = ["mh60", "mh61", "pw51"]

[geometry.elevons]
span_fraction = 0.55            # of semi-span
chord_fraction = 0.22           # of local chord
split_ratio = 0.5               # equal split
num_surfaces = 4

[propulsion.motor]
kv = 900
rm_ohm = 0.060
i0_amp = 0.8
max_current_amp = 30
weight_g = 68

[propulsion.prop]
diameter_in = 9.0
pitch_in = 6.0
data_file = "apc_9x6.csv"

[propulsion.battery]
chemistry = "li-ion-18650"
cell_capacity_mah = 3000
cell_weight_g = 48
cell_max_continuous_a = 15
series = 4
parallel = 1
usable_fraction = 0.80

[structure]
foam_type = "xps"
foam_density_kgm3 = 35
spar_type = "carbon_tube"
spar_od_mm = 8.0
spar_id_mm = 6.0
center_plate_thickness_mm = 2.0
skin = "none"                   # "none", "fiberglass", "carbon"
elevon_material = "carbon_sheet"
elevon_thickness_mm = 0.5

[components]
fc_weight_g = 12
gps_weight_g = 12
rx_weight_g = 3
vtx_weight_g = 35
servo_weight_g = 9
servo_count = 4
esc_weight_g = 30
wiring_weight_g = 40

[mass]
payload_weight_g = 0
auw_limit_g = 1500

[stability]
min_static_margin = 0.05
target_static_margin = 0.12
max_cg_travel_fraction = 0.03

[design_space.wing]
wingspan_m = [1.2, 1.8]
sweep_deg = [18.0, 35.0]
twist_deg = [0.0, 6.0]
taper_ratio = [0.3, 0.8]

[design_space.propulsion]
prop_diameter_in = [7.0, 10.0]
prop_pitch_in = [4.0, 8.0]
battery_parallel = [1, 3]

[design_space.environment]
temperature_c = [-10.0, 45.0]
altitude_m = [0.0, 3000.0]
payload_weight_g = [0.0, 400.0]

[optimizer.wing]
method = "differential_evolution"
max_evaluations = 1200
population_size = 60
objective = "aero_efficiency_and_stability"
seed = 42

[optimizer.propulsion]
method = "differential_evolution"
max_evaluations = 800
population_size = 40
objective = "range_endurance_efficiency"
seed = 43

[optimizer.coordinator]
max_coupling_iterations = 5
convergence_tolerance = 0.01
aggregation = "weighted_scenario_mean"   # or "worst_case"
```

---

## 9. Key Equations Reference

### Aerodynamics
- Lift: `L = 0.5 × ρ × V² × S × CL`
- Drag: `D = 0.5 × ρ × V² × S × CD`
- L/D: `CL / CD`
- Oswald efficiency: `e ≈ 1.78 × (1 - 0.045 × AR^0.68) - 0.64` (Raymer)
- Induced drag: `CDi = CL² / (π × AR × e)`
- Re = `ρ × V × c / μ` (use scenario-specific `ρ` and `μ`)

### Atmosphere
- Ideal gas density: `ρ = p / (R × T)` (dry-air approximation)
- Sutherland viscosity model (recommended): `μ(T) = μ0 × (T/T0)^(3/2) × (T0 + C)/(T + C)`
- Altitude model: use ISA or configured pressure directly, then apply temperature offset

### Propulsion
- Motor RPM: `n = Kv × (V_batt - I × Rm)`
- Advance ratio: `J = V / (n/60 × D)`
- System efficiency: `η = η_motor × η_prop × η_esc`

### Performance
- Level flight power: `P = D × V / η_system`
- Endurance: `t = E_battery / P`
- Range: `R = V × t`
- Breguet (for constant speed): `R = (η / g) × (L/D) × (E_specific / m) × ln(m_initial / m_final)`
  - (Simplified for electric: mass doesn't change, so `R = V × E_battery / P`)

### Stability
- Static margin: `SM = (x_NP - x_CG) / MAC`
- NP approximation (swept wing): `x_NP ≈ x_AC + ΔNP(sweep, taper)`
