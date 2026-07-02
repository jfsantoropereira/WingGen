/** Pure comparison logic for the Designs view side-by-side panel. */

import type { DesignRecord } from '../api/types';

/** Metrics where a smaller value wins; everything else is higher-is-better. */
const LOWER_IS_BETTER = new Set([
  'total_mass_g',
  'gross_mass_g',
  'cruise_cd',
  'stall_speed_kmh',
]);

export type CompareValue = number | string | boolean | null;

export interface CompareRow {
  key: string;
  section: 'score' | 'metric' | 'param';
  values: CompareValue[];
  /** Index of the best numeric value (metrics/score only), null when n/a. */
  bestIndex: number | null;
  /** Numeric delta vs the first selection; null for index 0 / non-numeric. */
  deltas: (number | null)[];
}

function unionKeys(records: Record<string, unknown>[]): string[] {
  const keys = new Set<string>();
  for (const record of records) for (const key of Object.keys(record)) keys.add(key);
  return [...keys].sort();
}

function makeRow(
  key: string,
  section: CompareRow['section'],
  values: CompareValue[],
): CompareRow {
  const deltas: (number | null)[] = values.map((value, index) => {
    if (index === 0) return null;
    const base = values[0];
    if (typeof value !== 'number' || typeof base !== 'number') return null;
    return value - base;
  });

  let bestIndex: number | null = null;
  if (section !== 'param') {
    const lowerBetter = LOWER_IS_BETTER.has(key);
    for (let i = 0; i < values.length; i += 1) {
      const value = values[i];
      if (typeof value !== 'number' || !Number.isFinite(value)) continue;
      const best = bestIndex === null ? null : (values[bestIndex] as number);
      if (best === null || (lowerBetter ? value < best : value > best)) bestIndex = i;
    }
  }
  return { key, section, values, bestIndex, deltas };
}

/**
 * Aligned rows for 2–4 designs: score first, then all metrics, then all
 * params (union of keys, missing values rendered as null).
 */
export function buildCompareRows(designs: DesignRecord[]): CompareRow[] {
  if (designs.length === 0) return [];
  const rows: CompareRow[] = [];

  rows.push(makeRow('score', 'score', designs.map((d) => d.score ?? null)));

  for (const key of unionKeys(designs.map((d) => d.metrics))) {
    rows.push(makeRow(key, 'metric', designs.map((d) => d.metrics[key] ?? null)));
  }
  for (const key of unionKeys(designs.map((d) => d.params))) {
    rows.push(makeRow(key, 'param', designs.map((d) => d.params[key] ?? null)));
  }
  return rows;
}
