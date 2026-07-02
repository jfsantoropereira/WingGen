/** Pure schema -> form-model logic for the Design Lab parameter editor. */

import type { ParamSchemaEntry, ParamValue } from '../api/types';

export interface FormGroup {
  key: string;
  title: string;
  entries: ParamSchemaEntry[];
}

const GROUP_TITLES: Record<string, string> = {
  mission: 'Mission',
  environment: 'Environment',
  geometry: 'Geometry',
  propulsion: 'Propulsion',
  structure: 'Structure',
  components: 'Components',
  mass: 'Mass Budget',
  stability: 'Stability',
  aero: 'Aerodynamics',
  design_space: 'Design Space',
  organic_refinement: 'Organic Refinement',
  simulate: 'Simulation',
  studio: 'Studio',
};

export function groupKey(path: string): string {
  const dot = path.indexOf('.');
  return dot >= 0 ? path.slice(0, dot) : path;
}

export function groupTitle(key: string): string {
  const known = GROUP_TITLES[key];
  if (known) return known;
  return key
    .split('_')
    .map((word) => (word.length > 0 ? word.charAt(0).toUpperCase() + word.slice(1) : word))
    .join(' ');
}

/** Label shown next to an input: the path with its group prefix stripped. */
export function fieldLabel(path: string): string {
  const dot = path.indexOf('.');
  return dot >= 0 ? path.slice(dot + 1) : path;
}

/** Group schema entries by first path segment, preserving schema order. */
export function buildFormGroups(schema: ParamSchemaEntry[]): FormGroup[] {
  const groups = new Map<string, ParamSchemaEntry[]>();
  for (const entry of schema) {
    const key = groupKey(entry.path);
    const list = groups.get(key);
    if (list) list.push(entry);
    else groups.set(key, [entry]);
  }
  return [...groups.entries()].map(([key, entries]) => ({ key, title: groupTitle(key), entries }));
}

export function isNumericKind(entry: ParamSchemaEntry): boolean {
  return entry.kind === 'float' || entry.kind === 'int';
}

/** Clamp a numeric value to the schema bounds (and round for ints). */
export function clampValue(entry: ParamSchemaEntry, value: number): number {
  let result = entry.kind === 'int' ? Math.round(value) : value;
  if (typeof entry.min === 'number' && result < entry.min) result = entry.min;
  if (typeof entry.max === 'number' && result > entry.max) result = entry.max;
  return result;
}

export function isInBounds(entry: ParamSchemaEntry, value: number): boolean {
  if (!Number.isFinite(value)) return false;
  if (typeof entry.min === 'number' && value < entry.min) return false;
  if (typeof entry.max === 'number' && value > entry.max) return false;
  return true;
}

/** Parse raw input text into a typed value; null when unparseable. */
export function coerceInput(entry: ParamSchemaEntry, raw: string): ParamValue | null {
  if (isNumericKind(entry)) {
    const trimmed = raw.trim();
    if (trimmed.length === 0) return null;
    const value = Number(trimmed);
    return Number.isFinite(value) ? value : null;
  }
  return raw;
}

export function valuesEqual(a: ParamValue | null | undefined, b: ParamValue | null | undefined): boolean {
  if (typeof a === 'number' && typeof b === 'number') {
    const scale = Math.max(Math.abs(a), Math.abs(b), 1);
    return Math.abs(a - b) <= 1e-9 * scale;
  }
  return a === b;
}

/**
 * Dirty values only: entries whose current value differs from the schema
 * default, keyed by dotted path — the `config_overrides` job payload.
 */
export function computeOverrides(
  values: ReadonlyMap<string, ParamValue>,
  schema: ParamSchemaEntry[],
): Record<string, ParamValue> {
  const byPath = new Map(schema.map((entry) => [entry.path, entry]));
  const overrides: Record<string, ParamValue> = {};
  for (const [path, value] of values) {
    const entry = byPath.get(path);
    if (!entry) continue;
    const fallback = entry.default === null || typeof entry.default === 'boolean' ? null : entry.default;
    if (!valuesEqual(value, fallback)) overrides[path] = value;
  }
  return overrides;
}
