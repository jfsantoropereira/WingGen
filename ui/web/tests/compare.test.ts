import { describe, expect, it } from 'vitest';
import type { DesignRecord } from '../src/api/types';
import { buildCompareRows } from '../src/logic/compare';

function design(id: string, score: number, metrics: Record<string, number>, params: Record<string, number | string>): DesignRecord {
  return {
    design_id: id,
    run_id: 'run-x',
    source: 'sweep_point',
    params,
    metrics,
    score,
    feasible: true,
  };
}

const a = design('d1', 0.7, { range_km: 50, total_mass_g: 1200 }, { 'geometry.wingspan_m': 1.5 });
const b = design('d2', 0.8, { range_km: 58, total_mass_g: 1100 }, { 'geometry.wingspan_m': 1.7, 'geometry.airfoil': 'mh45' });

describe('buildCompareRows', () => {
  it('returns empty for no designs', () => {
    expect(buildCompareRows([])).toEqual([]);
  });

  it('orders rows: score, metrics, params (union of keys)', () => {
    const rows = buildCompareRows([a, b]);
    expect(rows.map((row) => `${row.section}:${row.key}`)).toEqual([
      'score:score',
      'metric:range_km',
      'metric:total_mass_g',
      'param:geometry.airfoil',
      'param:geometry.wingspan_m',
    ]);
  });

  it('computes deltas vs the first selection', () => {
    const rows = buildCompareRows([a, b]);
    const range = rows.find((row) => row.key === 'range_km');
    expect(range?.deltas).toEqual([null, 8]);
    const score = rows.find((row) => row.key === 'score');
    expect(score?.deltas?.[1]).toBeCloseTo(0.1);
  });

  it('marks best per row with direction awareness', () => {
    const rows = buildCompareRows([a, b]);
    expect(rows.find((row) => row.key === 'range_km')?.bestIndex).toBe(1); // higher better
    expect(rows.find((row) => row.key === 'total_mass_g')?.bestIndex).toBe(1); // lower better (1100 < 1200)
    expect(rows.find((row) => row.key === 'score')?.bestIndex).toBe(1);
    expect(rows.find((row) => row.key === 'geometry.wingspan_m')?.bestIndex).toBeNull(); // params have no "best"
  });

  it('fills missing keys with null and skips them for best/deltas', () => {
    const rows = buildCompareRows([a, b]);
    const airfoil = rows.find((row) => row.key === 'geometry.airfoil');
    expect(airfoil?.values).toEqual([null, 'mh45']);
    expect(airfoil?.deltas).toEqual([null, null]);
  });
});
