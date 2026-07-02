/** Static fixtures for mock mode: parameter schema, runs, and seed designs. */

import type { DesignRecord, ParamSchemaEntry, RunRecord } from '../api/types';

export const MOCK_SCHEMA: ParamSchemaEntry[] = [
  { path: 'mission.range_target_km', unit: 'km', default: 40, min: 5, max: 200, kind: 'float' },
  { path: 'mission.cruise_speed_ms', unit: 'm/s', default: 16, min: 8, max: 40, kind: 'float' },
  { path: 'mission.payload_g', unit: 'g', default: 250, min: 0, max: 2000, kind: 'float' },
  { path: 'environment.altitude_m', unit: 'm', default: 300, min: 0, max: 4000, kind: 'float' },
  { path: 'environment.air_density', unit: 'kg/m³', default: 1.19, min: 0.7, max: 1.35, kind: 'float' },
  { path: 'geometry.wingspan_m', unit: 'm', default: 1.5, min: 0.8, max: 2.5, kind: 'float' },
  { path: 'geometry.root_chord_m', unit: 'm', default: 0.28, min: 0.12, max: 0.5, kind: 'float' },
  { path: 'geometry.taper_ratio', unit: '', default: 0.45, min: 0.2, max: 1.0, kind: 'float' },
  { path: 'geometry.sweep_deg', unit: 'deg', default: 26, min: 10, max: 40, kind: 'float' },
  { path: 'geometry.twist_deg', unit: 'deg', default: -3, min: -8, max: 0, kind: 'float' },
  { path: 'geometry.root_incidence_deg', unit: 'deg', default: 2.0, min: -2, max: 6, kind: 'float' },
  { path: 'geometry.tip_incidence_deg', unit: 'deg', default: -1.0, min: -6, max: 3, kind: 'float' },
  { path: 'geometry.airfoil', unit: '', default: 'mh60', kind: 'enum', choices: ['mh60', 'mh45', 's5010', 'phoenix'] },
  { path: 'propulsion.battery_capacity_mah', unit: 'mAh', default: 5000, min: 1000, max: 20000, kind: 'int' },
  { path: 'propulsion.battery_cells', unit: 'S', default: 4, min: 2, max: 12, kind: 'int' },
  { path: 'structure.spar_material', unit: '', default: 'carbon', kind: 'enum', choices: ['carbon', 'glass', 'wood'] },
  { path: 'stability.static_margin_target', unit: 'MAC', default: 0.08, min: 0.02, max: 0.2, kind: 'float' },
  { path: 'stability.cg_fraction_mac', unit: 'MAC', default: 0.25, min: 0.1, max: 0.4, kind: 'float' },
  { path: 'aero.method', unit: '', default: 'polar_llt', kind: 'enum', choices: ['polar_llt', 'vlm'] },
  { path: 'aero.vlm.spanwise_panels', unit: '', default: 32, min: 8, max: 128, kind: 'int' },
  { path: 'organic_refinement.engine', unit: '', default: 'proxy', kind: 'enum', choices: ['proxy', 'lbm', 'su2', 'openfoam'] },
];

export const MOCK_RUNS: RunRecord[] = [
  {
    run_id: 'run-20260701-120000-sweep',
    kind: 'sweep',
    label: 'wingspan × sweep grid',
    status: 'completed',
    created_at: '2026-07-01T12:00:00Z',
    summary: { total_points: 12, feasible_points: 9 },
  },
  {
    run_id: 'run-20260701-140000-optimize',
    kind: 'optimize',
    label: 'range optimization',
    status: 'completed',
    created_at: '2026-07-01T14:00:00Z',
    summary: { evaluations: 180 },
  },
];

interface SeedDesign {
  seq: number;
  run: 0 | 1;
  source: string;
  wingspan: number;
  sweep: number;
  score: number;
  feasible: boolean;
}

const SEEDS: SeedDesign[] = [
  { seq: 1, run: 0, source: 'sweep_point', wingspan: 1.2, sweep: 22, score: 0.612, feasible: true },
  { seq: 2, run: 0, source: 'sweep_point', wingspan: 1.4, sweep: 22, score: 0.671, feasible: true },
  { seq: 3, run: 0, source: 'sweep_point', wingspan: 1.6, sweep: 26, score: 0.734, feasible: true },
  { seq: 4, run: 0, source: 'sweep_point', wingspan: 1.8, sweep: 26, score: 0.702, feasible: false },
  { seq: 5, run: 0, source: 'sweep_point', wingspan: 1.6, sweep: 30, score: 0.688, feasible: true },
  { seq: 1, run: 1, source: 'optimize', wingspan: 1.62, sweep: 27.4, score: 0.781, feasible: true },
  { seq: 2, run: 1, source: 'optimize', wingspan: 1.58, sweep: 25.9, score: 0.769, feasible: true },
  { seq: 3, run: 1, source: 'optimize', wingspan: 1.71, sweep: 28.8, score: 0.744, feasible: false },
];

function seedToRecord(seed: SeedDesign): DesignRecord {
  const run = MOCK_RUNS[seed.run];
  const runId = run ? run.run_id : 'run-unknown';
  const cruiseLd = 14 + seed.score * 10;
  return {
    design_id: `${runId}-d${String(seed.seq).padStart(4, '0')}`,
    run_id: runId,
    source: seed.source,
    label: null,
    params: {
      'geometry.wingspan_m': seed.wingspan,
      'geometry.root_chord_m': 0.28,
      'geometry.taper_ratio': 0.45,
      'geometry.sweep_deg': seed.sweep,
      'geometry.twist_deg': -3,
      'geometry.airfoil': 'mh60',
      'propulsion.battery_capacity_mah': 5000,
    },
    metrics: {
      cruise_ld: Number(cruiseLd.toFixed(2)),
      cruise_cd: Number((0.031 - seed.score * 0.01).toFixed(4)),
      static_margin: 0.08,
      stall_speed_kmh: Number((34 - seed.wingspan * 4).toFixed(1)),
      total_mass_g: Number((820 + seed.wingspan * 220).toFixed(0)),
      gross_mass_g: Number((1070 + seed.wingspan * 220).toFixed(0)),
      range_km: Number((seed.score * 75).toFixed(1)),
      endurance_h: Number((seed.score * 2.1).toFixed(2)),
      combined_score: seed.score,
    },
    score: seed.score,
    feasible: seed.feasible,
    artifacts: null,
  };
}

export const MOCK_DESIGNS: DesignRecord[] = SEEDS.map(seedToRecord);
