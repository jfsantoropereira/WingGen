import { describe, expect, it } from 'vitest';
import type { ParamSchemaEntry, ParamValue } from '../src/api/types';
import {
  buildFormGroups,
  clampValue,
  coerceInput,
  computeOverrides,
  fieldLabel,
  groupTitle,
  isInBounds,
  valuesEqual,
} from '../src/logic/formModel';

const wingspan: ParamSchemaEntry = {
  path: 'geometry.wingspan_m',
  unit: 'm',
  default: 1.5,
  min: 0.8,
  max: 2.5,
  kind: 'float',
};
const cells: ParamSchemaEntry = {
  path: 'propulsion.battery_cells',
  unit: 'S',
  default: 4,
  min: 2,
  max: 12,
  kind: 'int',
};
const airfoil: ParamSchemaEntry = {
  path: 'geometry.airfoil',
  unit: '',
  default: 'mh60',
  kind: 'enum',
  choices: ['mh60', 'mh45'],
};

describe('buildFormGroups', () => {
  it('groups by first path segment preserving schema order', () => {
    const groups = buildFormGroups([wingspan, cells, airfoil]);
    expect(groups.map((group) => group.key)).toEqual(['geometry', 'propulsion']);
    expect(groups[0]?.entries.map((entry) => entry.path)).toEqual([
      'geometry.wingspan_m',
      'geometry.airfoil',
    ]);
  });

  it('titles known and unknown groups', () => {
    expect(groupTitle('geometry')).toBe('Geometry');
    expect(groupTitle('flight_controller')).toBe('Flight Controller');
  });

  it('labels fields without the group prefix', () => {
    expect(fieldLabel('geometry.wingspan_m')).toBe('wingspan_m');
    expect(fieldLabel('bare')).toBe('bare');
  });
});

describe('bounds and coercion', () => {
  it('clamps to schema bounds and rounds ints', () => {
    expect(clampValue(wingspan, 5)).toBe(2.5);
    expect(clampValue(wingspan, 0.1)).toBe(0.8);
    expect(clampValue(cells, 6.7)).toBe(7);
    expect(clampValue(cells, 99)).toBe(12);
  });

  it('validates bounds', () => {
    expect(isInBounds(wingspan, 1.5)).toBe(true);
    expect(isInBounds(wingspan, 2.6)).toBe(false);
    expect(isInBounds(wingspan, Number.NaN)).toBe(false);
  });

  it('coerces numeric input text', () => {
    expect(coerceInput(wingspan, ' 1.75 ')).toBe(1.75);
    expect(coerceInput(wingspan, 'abc')).toBeNull();
    expect(coerceInput(wingspan, '')).toBeNull();
    expect(coerceInput(airfoil, 'mh45')).toBe('mh45');
  });
});

describe('computeOverrides', () => {
  it('emits only dirty values keyed by dotted path', () => {
    const values = new Map<string, ParamValue>([
      ['geometry.wingspan_m', 1.8],
      ['propulsion.battery_cells', 4],
      ['geometry.airfoil', 'mh45'],
    ]);
    const overrides = computeOverrides(values, [wingspan, cells, airfoil]);
    expect(overrides).toEqual({ 'geometry.wingspan_m': 1.8, 'geometry.airfoil': 'mh45' });
  });

  it('treats numerically-equal floats as clean', () => {
    expect(valuesEqual(1.5, 1.5 + 1e-12)).toBe(true);
    const values = new Map<string, ParamValue>([['geometry.wingspan_m', 1.5 + 1e-12]]);
    expect(computeOverrides(values, [wingspan])).toEqual({});
  });

  it('ignores unknown paths', () => {
    const values = new Map<string, ParamValue>([['nope.missing', 3]]);
    expect(computeOverrides(values, [wingspan])).toEqual({});
  });
});
