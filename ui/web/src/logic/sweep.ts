/** Pure builders/validators for SweepSpec and OptimizeSpec (contract §6). */

import type {
  Objective,
  OptimizeSpec,
  ParamSchemaEntry,
  SweepAxisSpec,
  SweepFidelity,
  SweepMode,
  SweepSpec,
} from '../api/types';
import { isNumericKind } from './formModel';

export const MAX_SWEEP_POINTS = 2000;
export const OBJECTIVES: Objective[] = ['combined_score', 'range_km', 'endurance_h', 'cruise_ld'];

export interface AxisDraft {
  path: string;
  min: number | null;
  max: number | null;
  steps: number | null;
  /** Explicit values for enum/string axes. */
  values: string[];
}

export interface VariableDraft {
  path: string;
  min: number | null;
  max: number | null;
}

function defaultNumber(entry: ParamSchemaEntry): number | null {
  return typeof entry.default === 'number' ? entry.default : null;
}

/** Axis pre-filled from schema bounds (numeric) or choices (enum). */
export function defaultAxisDraft(entry: ParamSchemaEntry): AxisDraft {
  if (isNumericKind(entry)) {
    const fallback = defaultNumber(entry);
    return {
      path: entry.path,
      min: entry.min ?? fallback,
      max: entry.max ?? fallback,
      steps: 5,
      values: [],
    };
  }
  const choices = entry.choices ?? [];
  const values = choices.length > 0 ? [...choices] : typeof entry.default === 'string' ? [entry.default] : [];
  return { path: entry.path, min: null, max: null, steps: null, values };
}

/** Variable pre-filled from schema bounds. */
export function defaultVariableDraft(entry: ParamSchemaEntry): VariableDraft {
  const fallback = defaultNumber(entry);
  return { path: entry.path, min: entry.min ?? fallback, max: entry.max ?? fallback };
}

export function axisPointCount(axis: AxisDraft, entry: ParamSchemaEntry | undefined): number {
  if (entry && !isNumericKind(entry)) return Math.max(axis.values.length, 0);
  return axis.steps !== null && axis.steps > 0 ? Math.floor(axis.steps) : 0;
}

export function sweepPointCount(axes: AxisDraft[], schema: ParamSchemaEntry[]): number {
  const byPath = new Map(schema.map((entry) => [entry.path, entry]));
  let total = axes.length > 0 ? 1 : 0;
  for (const axis of axes) total *= axisPointCount(axis, byPath.get(axis.path));
  return total;
}

export interface SweepBuildResult {
  spec: SweepSpec | null;
  errors: string[];
  points: number;
}

export function buildSweepSpec(
  axes: AxisDraft[],
  schema: ParamSchemaEntry[],
  mode: SweepMode,
  fidelity: SweepFidelity,
  objective: Objective,
): SweepBuildResult {
  const errors: string[] = [];
  const byPath = new Map(schema.map((entry) => [entry.path, entry]));

  if (axes.length < 1 || axes.length > 2) {
    errors.push('Pick 1–2 sweep parameters.');
  }
  const seen = new Set<string>();
  const parameters: SweepAxisSpec[] = [];
  let points = axes.length > 0 ? 1 : 0;

  for (const axis of axes) {
    if (axis.path.trim().length === 0) {
      errors.push('Every axis needs a parameter path.');
      continue;
    }
    if (seen.has(axis.path)) {
      errors.push(`Duplicate axis: ${axis.path}`);
      continue;
    }
    seen.add(axis.path);
    const entry = byPath.get(axis.path);
    if (!entry) {
      errors.push(`Unknown parameter: ${axis.path}`);
      continue;
    }
    if (isNumericKind(entry)) {
      const { min, max, steps } = axis;
      if (min === null || max === null || !Number.isFinite(min) || !Number.isFinite(max)) {
        errors.push(`${axis.path}: min/max required.`);
        continue;
      }
      if (min >= max) {
        errors.push(`${axis.path}: min must be < max.`);
        continue;
      }
      if (steps === null || !Number.isInteger(steps) || steps < 2) {
        errors.push(`${axis.path}: steps must be an integer ≥ 2.`);
        continue;
      }
      if (typeof entry.min === 'number' && min < entry.min) {
        errors.push(`${axis.path}: min below schema bound ${entry.min}.`);
        continue;
      }
      if (typeof entry.max === 'number' && max > entry.max) {
        errors.push(`${axis.path}: max above schema bound ${entry.max}.`);
        continue;
      }
      points *= steps;
      parameters.push({ path: axis.path, min, max, steps });
    } else {
      if (axis.values.length === 0) {
        errors.push(`${axis.path}: pick at least one value.`);
        continue;
      }
      if (entry.choices && entry.choices.length > 0) {
        const invalid = axis.values.filter((value) => !entry.choices?.includes(value));
        if (invalid.length > 0) {
          errors.push(`${axis.path}: invalid values: ${invalid.join(', ')}`);
          continue;
        }
      }
      points *= axis.values.length;
      parameters.push({ path: axis.path, values: [...axis.values] });
    }
  }

  if (points > MAX_SWEEP_POINTS) {
    errors.push(`Grid has ${points} points — the hard cap is ${MAX_SWEEP_POINTS}.`);
  }

  if (errors.length > 0) return { spec: null, errors, points };
  return {
    spec: { kind: 'sweep', parameters, evaluation: { mode, fidelity }, objective },
    errors: [],
    points,
  };
}

export interface OptimizeBuildResult {
  spec: OptimizeSpec | null;
  errors: string[];
}

export function buildOptimizeSpec(
  variables: VariableDraft[],
  maxEvaluations: number | null,
  objective: Objective,
  schema: ParamSchemaEntry[],
  seed?: number,
): OptimizeBuildResult {
  const errors: string[] = [];
  const byPath = new Map(schema.map((entry) => [entry.path, entry]));
  const bounds: Record<string, [number, number]> = {};
  const seen = new Set<string>();

  if (variables.length === 0) errors.push('Pick at least one optimization variable.');

  for (const variable of variables) {
    if (variable.path.trim().length === 0) {
      errors.push('Every variable needs a parameter path.');
      continue;
    }
    if (seen.has(variable.path)) {
      errors.push(`Duplicate variable: ${variable.path}`);
      continue;
    }
    seen.add(variable.path);
    const entry = byPath.get(variable.path);
    if (!entry) {
      errors.push(`Unknown parameter: ${variable.path}`);
      continue;
    }
    if (!isNumericKind(entry)) {
      errors.push(`${variable.path}: only numeric parameters can be optimized.`);
      continue;
    }
    const { min, max } = variable;
    if (min === null || max === null || !Number.isFinite(min) || !Number.isFinite(max)) {
      errors.push(`${variable.path}: bounds required.`);
      continue;
    }
    if (min >= max) {
      errors.push(`${variable.path}: lower bound must be < upper bound.`);
      continue;
    }
    if (typeof entry.min === 'number' && min < entry.min) {
      errors.push(`${variable.path}: lower bound below schema bound ${entry.min}.`);
      continue;
    }
    if (typeof entry.max === 'number' && max > entry.max) {
      errors.push(`${variable.path}: upper bound above schema bound ${entry.max}.`);
      continue;
    }
    bounds[variable.path] = [min, max];
  }

  if (maxEvaluations === null || !Number.isInteger(maxEvaluations) || maxEvaluations < 1) {
    errors.push('max_evaluations must be a positive integer.');
  }

  if (errors.length > 0) return { spec: null, errors };
  const spec: OptimizeSpec = {
    kind: 'optimize',
    variables: bounds,
    budget: { max_evaluations: maxEvaluations as number },
    objective,
  };
  if (seed !== undefined) spec.seed = seed;
  return { spec, errors: [] };
}
