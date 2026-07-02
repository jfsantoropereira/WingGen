/** Types mirroring STUDIO_CONTRACT.md §5 (events) and §7 (REST API). */

export type ParamKind = 'float' | 'int' | 'str' | 'enum';

export interface ParamSchemaEntry {
  path: string;
  unit?: string | null;
  default: number | string | boolean | null;
  min?: number | null;
  max?: number | null;
  kind: ParamKind;
  choices?: string[] | null;
}

export type ParamValue = number | string | boolean;

export type JobKind = 'simulate' | 'sweep' | 'optimize';
export type JobState = 'pending' | 'running' | 'completed' | 'failed' | 'cancelled';

export interface Job {
  job_id: string;
  run_id: string;
  kind: JobKind;
  state: JobState;
  exit_code?: number | null;
  label?: string | null;
}

export interface JobCreated {
  job_id: string;
  run_id: string;
}

export interface RunRecord {
  run_id: string;
  kind: JobKind;
  label?: string | null;
  status: string;
  created_at?: string;
  summary?: Record<string, unknown> | null;
}

export interface DesignRecord {
  design_id: string;
  run_id: string;
  source: string;
  label?: string | null;
  params: Record<string, ParamValue>;
  metrics: Record<string, number>;
  score: number;
  feasible: boolean;
  artifacts?: Record<string, string> | null;
}

export type Objective = 'combined_score' | 'range_km' | 'endurance_h' | 'cruise_ld';
export type SweepMode = 'wing_only' | 'full';
export type SweepFidelity = 'polar_llt' | 'vlm';

export interface SweepAxisSpec {
  path: string;
  min?: number;
  max?: number;
  steps?: number;
  values?: (number | string)[];
}

export interface SweepSpec {
  kind: 'sweep';
  parameters: SweepAxisSpec[];
  evaluation: { mode: SweepMode; fidelity: SweepFidelity };
  objective: Objective;
}

export interface OptimizeSpec {
  kind: 'optimize';
  variables: Record<string, [number, number]>;
  budget: { max_evaluations: number };
  objective: Objective;
  seed?: number;
}

export type OrganicEngine = 'proxy' | 'lbm' | 'su2' | 'openfoam';

export interface SimulateOptions {
  disable_organic?: boolean;
  organic_engine?: OrganicEngine;
}

export interface JobCreateRequest {
  kind: JobKind;
  label?: string;
  config_overrides?: Record<string, ParamValue>;
  sweep?: SweepSpec;
  optimize?: OptimizeSpec;
  simulate?: SimulateOptions;
}

export interface HealthInfo {
  status: string;
  version: string;
  metal_available: boolean;
}

/** NDJSON contract event (v1.1), one per SSE `data:` line. */
export interface ContractEvent {
  contract_version: string;
  event: string;
  payload: Record<string, unknown>;
}

export interface ProgressPayload {
  stage: string;
  percent: number;
  note?: string;
}

export interface SweepPointPayload {
  index: number;
  total: number;
  params: Record<string, ParamValue>;
  metrics: Record<string, number>;
  score: number;
  feasible: boolean;
  design_id?: string;
}

export interface RunInfoPayload {
  run_id: string;
  kind: JobKind;
  label?: string;
}

export interface ErrorPayload {
  message: string;
  stage: string;
}

export interface DesignQuery {
  run_id?: string;
  feasible?: boolean;
  sort?: string;
  order?: 'asc' | 'desc';
  limit?: number;
}

export interface MeshResolution {
  span_sections: number;
  profile_points: number;
}
