import { describe, expect, it } from 'vitest';
import type { ParamSchemaEntry } from '../src/api/types';
import {
  buildOptimizeSpec,
  buildSweepSpec,
  defaultAxisDraft,
  defaultVariableDraft,
  MAX_SWEEP_POINTS,
  sweepPointCount,
  type AxisDraft,
} from '../src/logic/sweep';

const schema: ParamSchemaEntry[] = [
  { path: 'geometry.wingspan_m', unit: 'm', default: 1.5, min: 0.8, max: 2.5, kind: 'float' },
  { path: 'geometry.sweep_deg', unit: 'deg', default: 26, min: 10, max: 40, kind: 'float' },
  { path: 'geometry.airfoil', unit: '', default: 'mh60', kind: 'enum', choices: ['mh60', 'mh45'] },
];

const numericAxis = (path: string, min: number, max: number, steps: number): AxisDraft => ({
  path,
  min,
  max,
  steps,
  values: [],
});

describe('buildSweepSpec', () => {
  it('builds a numeric 2-axis grid spec', () => {
    const result = buildSweepSpec(
      [numericAxis('geometry.wingspan_m', 1.2, 1.8, 7), numericAxis('geometry.sweep_deg', 22, 30, 3)],
      schema,
      'wing_only',
      'polar_llt',
      'combined_score',
    );
    expect(result.errors).toEqual([]);
    expect(result.points).toBe(21);
    expect(result.spec).toEqual({
      kind: 'sweep',
      parameters: [
        { path: 'geometry.wingspan_m', min: 1.2, max: 1.8, steps: 7 },
        { path: 'geometry.sweep_deg', min: 22, max: 30, steps: 3 },
      ],
      evaluation: { mode: 'wing_only', fidelity: 'polar_llt' },
      objective: 'combined_score',
    });
  });

  it('emits explicit values for enum axes', () => {
    const axis: AxisDraft = { path: 'geometry.airfoil', min: null, max: null, steps: null, values: ['mh60', 'mh45'] };
    const result = buildSweepSpec([axis], schema, 'full', 'vlm', 'range_km');
    expect(result.spec?.parameters).toEqual([{ path: 'geometry.airfoil', values: ['mh60', 'mh45'] }]);
    expect(result.points).toBe(2);
  });

  it('rejects invalid enum values', () => {
    const axis: AxisDraft = { path: 'geometry.airfoil', min: null, max: null, steps: null, values: ['nope'] };
    const result = buildSweepSpec([axis], schema, 'wing_only', 'polar_llt', 'combined_score');
    expect(result.spec).toBeNull();
    expect(result.errors.join(' ')).toContain('invalid values');
  });

  it('rejects 0 or 3+ axes, out-of-bound ranges, and inverted ranges', () => {
    expect(buildSweepSpec([], schema, 'wing_only', 'polar_llt', 'combined_score').spec).toBeNull();
    const three = [
      numericAxis('geometry.wingspan_m', 1.2, 1.8, 3),
      numericAxis('geometry.sweep_deg', 22, 30, 3),
      numericAxis('geometry.airfoil', 0, 1, 2),
    ];
    expect(buildSweepSpec(three, schema, 'wing_only', 'polar_llt', 'combined_score').spec).toBeNull();
    const below = buildSweepSpec([numericAxis('geometry.wingspan_m', 0.1, 1.8, 3)], schema, 'wing_only', 'polar_llt', 'combined_score');
    expect(below.errors.join(' ')).toContain('below schema bound');
    const inverted = buildSweepSpec([numericAxis('geometry.wingspan_m', 1.8, 1.2, 3)], schema, 'wing_only', 'polar_llt', 'combined_score');
    expect(inverted.errors.join(' ')).toContain('min must be < max');
  });

  it('enforces the 2000-point cap', () => {
    const result = buildSweepSpec(
      [numericAxis('geometry.wingspan_m', 1.0, 2.0, 50), numericAxis('geometry.sweep_deg', 12, 38, 50)],
      schema,
      'wing_only',
      'polar_llt',
      'combined_score',
    );
    expect(result.points).toBe(2500);
    expect(result.spec).toBeNull();
    expect(result.errors.join(' ')).toContain(String(MAX_SWEEP_POINTS));
  });

  it('counts grid points from drafts', () => {
    const axes = [numericAxis('geometry.wingspan_m', 1.2, 1.8, 4), defaultAxisDraft(schema[2]!)];
    expect(sweepPointCount(axes, schema)).toBe(8);
  });
});

describe('buildOptimizeSpec', () => {
  it('builds bounds from variable drafts', () => {
    const result = buildOptimizeSpec(
      [
        { path: 'geometry.wingspan_m', min: 1.3, max: 1.7 },
        { path: 'geometry.sweep_deg', min: 20, max: 32 },
      ],
      400,
      'combined_score',
      schema,
      42,
    );
    expect(result.errors).toEqual([]);
    expect(result.spec).toEqual({
      kind: 'optimize',
      variables: { 'geometry.wingspan_m': [1.3, 1.7], 'geometry.sweep_deg': [20, 32] },
      budget: { max_evaluations: 400 },
      objective: 'combined_score',
      seed: 42,
    });
  });

  it('pre-fills variable drafts from schema bounds', () => {
    expect(defaultVariableDraft(schema[0]!)).toEqual({ path: 'geometry.wingspan_m', min: 0.8, max: 2.5 });
  });

  it('rejects non-numeric variables, bad bounds, and bad budgets', () => {
    const enumVar = buildOptimizeSpec([{ path: 'geometry.airfoil', min: 0, max: 1 }], 100, 'combined_score', schema);
    expect(enumVar.errors.join(' ')).toContain('only numeric');
    const badBounds = buildOptimizeSpec([{ path: 'geometry.wingspan_m', min: 2.0, max: 1.0 }], 100, 'combined_score', schema);
    expect(badBounds.spec).toBeNull();
    const badBudget = buildOptimizeSpec([{ path: 'geometry.wingspan_m', min: 1.3, max: 1.7 }], 0, 'combined_score', schema);
    expect(badBudget.errors.join(' ')).toContain('max_evaluations');
    const empty = buildOptimizeSpec([], 100, 'combined_score', schema);
    expect(empty.spec).toBeNull();
  });
});
