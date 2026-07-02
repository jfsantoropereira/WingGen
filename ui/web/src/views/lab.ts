/** Design Lab: schema-driven parameter editor + Simulate/Sweep/Optimize launch panel. */

import { api } from '../api/client';
import type {
  JobCreateRequest,
  Objective,
  OrganicEngine,
  ParamSchemaEntry,
  ParamValue,
  SweepFidelity,
  SweepMode,
} from '../api/types';
import { el, replaceChildren } from '../core/dom';
import { navigate } from '../core/router';
import { toast } from '../core/toast';
import {
  buildFormGroups,
  clampValue,
  coerceInput,
  computeOverrides,
  fieldLabel,
  isInBounds,
  isNumericKind,
  valuesEqual,
} from '../logic/formModel';
import {
  buildOptimizeSpec,
  buildSweepSpec,
  defaultAxisDraft,
  defaultVariableDraft,
  OBJECTIVES,
  sweepPointCount,
  type AxisDraft,
  type VariableDraft,
} from '../logic/sweep';
import type { View } from './types';

type LaunchTab = 'simulate' | 'sweep' | 'optimize';

const ORGANIC_ENGINES: OrganicEngine[] = ['proxy', 'lbm', 'su2', 'openfoam'];

/** Lab state survives view switches within the session. */
interface LabState {
  values: Map<string, ParamValue>;
  tab: LaunchTab;
  label: string;
  organicEnabled: boolean;
  organicEngine: OrganicEngine;
  sweepAxes: AxisDraft[];
  sweepMode: SweepMode;
  sweepFidelity: SweepFidelity;
  sweepObjective: Objective;
  optimizeVars: VariableDraft[];
  maxEvaluations: number;
  optimizeObjective: Objective;
}

const labState: LabState = {
  values: new Map(),
  tab: 'simulate',
  label: '',
  organicEnabled: true,
  organicEngine: 'proxy',
  sweepAxes: [],
  sweepMode: 'wing_only',
  sweepFidelity: 'polar_llt',
  sweepObjective: 'combined_score',
  optimizeVars: [],
  maxEvaluations: 200,
  optimizeObjective: 'combined_score',
};

function currentValue(entry: ParamSchemaEntry): ParamValue | null {
  const stored = labState.values.get(entry.path);
  if (stored !== undefined) return stored;
  return entry.default === null || typeof entry.default === 'boolean' ? null : entry.default;
}

function objectiveSelect(value: Objective, onChange: (objective: Objective) => void): HTMLSelectElement {
  const select = el(
    'select',
    { onchange: () => onChange(select.value as Objective) },
    ...OBJECTIVES.map((o) => el('option', { value: o, selected: o === value }, o)),
  );
  return select;
}

export function createLabView(): View {
  const paramsSection = el('section', { class: 'panel lab-params' }, el('div', { class: 'panel-loading' }, 'loading schema…'));
  const launchBody = el('div', { class: 'launch-body' });
  const errorsBox = el('div', { class: 'launch-errors' });
  const overridesBadge = el('span', { class: 'mono muted' }, '0 overrides');
  const launchButton = el('button', { class: 'btn btn-accent' }, 'Launch') as HTMLButtonElement;

  let schema: ParamSchemaEntry[] = [];
  let disposed = false;

  const dirtyCallbacks: (() => void)[] = [];
  const refreshDirtyMarks = (): void => {
    for (const callback of dirtyCallbacks) callback();
    const overrides = computeOverrides(labState.values, schema);
    overridesBadge.textContent = `${Object.keys(overrides).length} overrides`;
  };

  // ---------------------------------------------------------------- editor

  function fieldRow(entry: ParamSchemaEntry): HTMLElement {
    const value = currentValue(entry);
    let input: HTMLInputElement | HTMLSelectElement;

    if (entry.kind === 'enum' && entry.choices && entry.choices.length > 0) {
      input = el(
        'select',
        {
          onchange: () => {
            labState.values.set(entry.path, input.value);
            refreshDirtyMarks();
          },
        },
        ...entry.choices.map((choice) =>
          el('option', { value: choice, selected: choice === value }, choice),
        ),
      );
    } else if (isNumericKind(entry)) {
      input = el('input', {
        type: 'number',
        step: entry.kind === 'int' ? '1' : 'any',
        ...(typeof entry.min === 'number' ? { min: String(entry.min) } : {}),
        ...(typeof entry.max === 'number' ? { max: String(entry.max) } : {}),
        value: value === null ? '' : String(value),
        oninput: () => {
          const parsed = coerceInput(entry, input.value);
          const valid = typeof parsed === 'number' && isInBounds(entry, parsed);
          input.classList.toggle('invalid', !valid);
          if (valid) {
            labState.values.set(entry.path, parsed);
            refreshDirtyMarks();
          }
        },
        onchange: () => {
          const parsed = coerceInput(entry, input.value);
          if (typeof parsed !== 'number') {
            // Revert unparseable text to the last committed value.
            const committed = currentValue(entry);
            input.value = committed === null ? '' : String(committed);
          } else {
            const clamped = clampValue(entry, parsed);
            input.value = String(clamped);
            labState.values.set(entry.path, clamped);
          }
          input.classList.remove('invalid');
          refreshDirtyMarks();
        },
      });
    } else {
      input = el('input', {
        type: 'text',
        value: value === null ? '' : String(value),
        oninput: () => {
          labState.values.set(entry.path, input.value);
          refreshDirtyMarks();
        },
      });
    }

    const bounds =
      isNumericKind(entry) && (entry.min !== null || entry.max !== null)
        ? `${entry.min ?? '−∞'} … ${entry.max ?? '∞'}`
        : '';

    const row = el(
      'label',
      { class: 'field-row' },
      el('span', { class: 'field-label mono', title: entry.path }, fieldLabel(entry.path)),
      input,
      el('span', { class: 'field-unit mono' }, entry.unit ?? ''),
      el('span', { class: 'field-bounds mono muted', title: 'schema bounds' }, bounds),
    );

    dirtyCallbacks.push(() => {
      const stored = labState.values.get(entry.path);
      const fallback = entry.default === null || typeof entry.default === 'boolean' ? null : entry.default;
      row.classList.toggle('dirty', stored !== undefined && !valuesEqual(stored, fallback));
    });
    return row;
  }

  function renderEditor(): void {
    const groups = buildFormGroups(schema);
    replaceChildren(
      paramsSection,
      el(
        'header',
        { class: 'panel-header' },
        el('h2', {}, 'Parameters'),
        el('div', { class: 'header-actions' },
          overridesBadge,
          el('button', {
            class: 'btn btn-ghost',
            onclick: () => {
              labState.values.clear();
              renderEditor();
              refreshDirtyMarks();
            },
          }, 'Reset all'),
        ),
      ),
      ...groups.map((group) =>
        el(
          'fieldset',
          { class: 'param-group' },
          el('legend', {}, group.title),
          ...group.entries.map((entry) => fieldRow(entry)),
        ),
      ),
    );
    refreshDirtyMarks();
  }

  // ------------------------------------------------------------- launch UI

  function pathDatalist(id: string, numericOnly: boolean): HTMLDataListElement {
    return el(
      'datalist',
      { id },
      ...schema
        .filter((entry) => (numericOnly ? isNumericKind(entry) : true))
        .map((entry) => el('option', { value: entry.path })),
    );
  }

  function showErrors(errors: string[]): void {
    replaceChildren(errorsBox, ...errors.map((message) => el('div', { class: 'error-line' }, message)));
  }

  function renderSimulateTab(): HTMLElement {
    const organicCheckbox = el('input', {
      type: 'checkbox',
      checked: labState.organicEnabled,
      onchange: () => {
        labState.organicEnabled = organicCheckbox.checked;
      },
    }) as HTMLInputElement;
    const engineSelect = el(
      'select',
      {
        onchange: () => {
          labState.organicEngine = engineSelect.value as OrganicEngine;
        },
      },
      ...ORGANIC_ENGINES.map((engine) =>
        el('option', { value: engine, selected: engine === labState.organicEngine }, engine),
      ),
    ) as HTMLSelectElement;

    return el(
      'div',
      { class: 'launch-tab' },
      el('p', { class: 'muted' }, 'Single evaluation of the current configuration.'),
      el('label', { class: 'inline-field' }, organicCheckbox, el('span', {}, 'organic refinement')),
      el('label', { class: 'inline-field' }, el('span', { class: 'mono muted' }, 'engine'), engineSelect),
    );
  }

  function renderSweepTab(): HTMLElement {
    if (labState.sweepAxes.length === 0) {
      const first = schema.find((entry) => isNumericKind(entry));
      if (first) labState.sweepAxes.push(defaultAxisDraft(first));
    }
    const container = el('div', { class: 'launch-tab' });
    const axesBox = el('div', { class: 'axes-box' });
    const pointsInfo = el('div', { class: 'mono muted' });

    const updatePoints = (): void => {
      const points = sweepPointCount(labState.sweepAxes, schema);
      pointsInfo.textContent = `grid: ${points} points (cap 2000)`;
      pointsInfo.classList.toggle('error-text', points > 2000);
    };

    const axisEditor = (axis: AxisDraft, index: number): HTMLElement => {
      const detail = el('div', { class: 'axis-detail' });

      const renderDetail = (): void => {
        const entry = schema.find((candidate) => candidate.path === axis.path);
        if (!entry) {
          replaceChildren(detail, el('span', { class: 'muted' }, 'pick a parameter'));
          return;
        }
        if (isNumericKind(entry)) {
          const numberInput = (
            key: 'min' | 'max' | 'steps',
            label: string,
          ): HTMLElement => {
            const input = el('input', {
              type: 'number',
              class: 'axis-num',
              step: key === 'steps' ? '1' : 'any',
              value: axis[key] === null ? '' : String(axis[key]),
              oninput: () => {
                const value = Number(input.value);
                axis[key] = input.value.trim() === '' || !Number.isFinite(value) ? null : value;
                updatePoints();
              },
            }) as HTMLInputElement;
            return el('label', { class: 'axis-cell' }, el('span', { class: 'mono muted' }, label), input);
          };
          replaceChildren(
            detail,
            numberInput('min', 'min'),
            numberInput('max', 'max'),
            numberInput('steps', 'steps'),
            el('span', { class: 'field-unit mono muted' }, entry.unit ?? ''),
          );
        } else {
          const choices = entry.choices ?? [];
          replaceChildren(
            detail,
            el(
              'div',
              { class: 'choice-list' },
              ...choices.map((choice) => {
                const checkbox = el('input', {
                  type: 'checkbox',
                  checked: axis.values.includes(choice),
                  onchange: () => {
                    axis.values = checkbox.checked
                      ? [...axis.values, choice]
                      : axis.values.filter((existing) => existing !== choice);
                    updatePoints();
                  },
                }) as HTMLInputElement;
                return el('label', { class: 'inline-field' }, checkbox, el('span', { class: 'mono' }, choice));
              }),
            ),
          );
        }
      };

      const pathInput = el('input', {
        type: 'text',
        class: 'path-input mono',
        list: 'sweep-paths',
        placeholder: 'parameter path…',
        value: axis.path,
        onchange: () => {
          const entry = schema.find((candidate) => candidate.path === pathInput.value);
          const fresh = entry ? defaultAxisDraft(entry) : { path: pathInput.value, min: null, max: null, steps: null, values: [] };
          Object.assign(axis, fresh);
          renderDetail();
          updatePoints();
        },
      }) as HTMLInputElement;

      renderDetail();
      return el(
        'div',
        { class: 'axis-row' },
        el('span', { class: 'mono muted' }, `axis ${index + 1}`),
        pathInput,
        detail,
        el('button', {
          class: 'btn btn-ghost btn-small',
          title: 'remove axis',
          onclick: () => {
            labState.sweepAxes.splice(index, 1);
            renderAxes();
          },
        }, '×'),
      );
    };

    const addButton = el('button', {
      class: 'btn btn-ghost',
      onclick: () => {
        const used = new Set(labState.sweepAxes.map((axis) => axis.path));
        const next = schema.find((entry) => isNumericKind(entry) && !used.has(entry.path));
        if (next) labState.sweepAxes.push(defaultAxisDraft(next));
        renderAxes();
      },
    }, '+ add axis') as HTMLButtonElement;

    function renderAxes(): void {
      replaceChildren(axesBox, ...labState.sweepAxes.map((axis, index) => axisEditor(axis, index)));
      addButton.disabled = labState.sweepAxes.length >= 2;
      updatePoints();
    }

    const modeSelect = el(
      'select',
      { onchange: () => { labState.sweepMode = modeSelect.value as SweepMode; } },
      ...(['wing_only', 'full'] as const).map((mode) =>
        el('option', { value: mode, selected: mode === labState.sweepMode }, mode),
      ),
    ) as HTMLSelectElement;
    const fidelitySelect = el(
      'select',
      { onchange: () => { labState.sweepFidelity = fidelitySelect.value as SweepFidelity; } },
      ...(['polar_llt', 'vlm'] as const).map((fidelity) =>
        el('option', { value: fidelity, selected: fidelity === labState.sweepFidelity }, fidelity),
      ),
    ) as HTMLSelectElement;

    renderAxes();
    container.append(
      pathDatalist('sweep-paths', false),
      axesBox,
      el('div', { class: 'launch-row' }, addButton, pointsInfo),
      el('div', { class: 'launch-row' },
        el('label', { class: 'inline-field' }, el('span', { class: 'mono muted' }, 'mode'), modeSelect),
        el('label', { class: 'inline-field' }, el('span', { class: 'mono muted' }, 'fidelity'), fidelitySelect),
        el('label', { class: 'inline-field' },
          el('span', { class: 'mono muted' }, 'objective'),
          objectiveSelect(labState.sweepObjective, (objective) => { labState.sweepObjective = objective; }),
        ),
      ),
    );
    return container;
  }

  function renderOptimizeTab(): HTMLElement {
    if (labState.optimizeVars.length === 0) {
      const first = schema.find((entry) => isNumericKind(entry));
      if (first) labState.optimizeVars.push(defaultVariableDraft(first));
    }
    const container = el('div', { class: 'launch-tab' });
    const varsBox = el('div', { class: 'axes-box' });

    const variableEditor = (variable: VariableDraft, index: number): HTMLElement => {
      const boundInput = (key: 'min' | 'max'): HTMLElement => {
        const input = el('input', {
          type: 'number',
          class: 'axis-num',
          step: 'any',
          value: variable[key] === null ? '' : String(variable[key]),
          oninput: () => {
            const value = Number(input.value);
            variable[key] = input.value.trim() === '' || !Number.isFinite(value) ? null : value;
          },
        }) as HTMLInputElement;
        return el('label', { class: 'axis-cell' }, el('span', { class: 'mono muted' }, key), input);
      };

      const pathInput = el('input', {
        type: 'text',
        class: 'path-input mono',
        list: 'optimize-paths',
        placeholder: 'parameter path…',
        value: variable.path,
        onchange: () => {
          const entry = schema.find((candidate) => candidate.path === pathInput.value);
          const fresh = entry
            ? defaultVariableDraft(entry)
            : { path: pathInput.value, min: null, max: null };
          Object.assign(variable, fresh);
          renderVars();
        },
      }) as HTMLInputElement;

      return el(
        'div',
        { class: 'axis-row' },
        el('span', { class: 'mono muted' }, `var ${index + 1}`),
        pathInput,
        el('div', { class: 'axis-detail' }, boundInput('min'), boundInput('max')),
        el('button', {
          class: 'btn btn-ghost btn-small',
          title: 'remove variable',
          onclick: () => {
            labState.optimizeVars.splice(index, 1);
            renderVars();
          },
        }, '×'),
      );
    };

    function renderVars(): void {
      replaceChildren(varsBox, ...labState.optimizeVars.map((variable, index) => variableEditor(variable, index)));
    }

    const evalsInput = el('input', {
      type: 'number',
      class: 'axis-num',
      min: '1',
      step: '1',
      value: String(labState.maxEvaluations),
      oninput: () => {
        const value = Number(evalsInput.value);
        if (Number.isFinite(value)) labState.maxEvaluations = value;
      },
    }) as HTMLInputElement;

    renderVars();
    container.append(
      pathDatalist('optimize-paths', true),
      varsBox,
      el('div', { class: 'launch-row' },
        el('button', {
          class: 'btn btn-ghost',
          onclick: () => {
            const used = new Set(labState.optimizeVars.map((variable) => variable.path));
            const next = schema.find((entry) => isNumericKind(entry) && !used.has(entry.path));
            if (next) labState.optimizeVars.push(defaultVariableDraft(next));
            renderVars();
          },
        }, '+ add variable'),
      ),
      el('div', { class: 'launch-row' },
        el('label', { class: 'inline-field' }, el('span', { class: 'mono muted' }, 'max_evaluations'), evalsInput),
        el('label', { class: 'inline-field' },
          el('span', { class: 'mono muted' }, 'objective'),
          objectiveSelect(labState.optimizeObjective, (objective) => { labState.optimizeObjective = objective; }),
        ),
      ),
    );
    return container;
  }

  const tabButtons = new Map<LaunchTab, HTMLButtonElement>();

  function renderTab(): void {
    for (const [tab, button] of tabButtons) button.classList.toggle('active', tab === labState.tab);
    showErrors([]);
    switch (labState.tab) {
      case 'simulate':
        replaceChildren(launchBody, renderSimulateTab());
        break;
      case 'sweep':
        replaceChildren(launchBody, renderSweepTab());
        break;
      case 'optimize':
        replaceChildren(launchBody, renderOptimizeTab());
        break;
    }
  }

  // ---------------------------------------------------------------- launch

  async function launch(): Promise<void> {
    const overrides = computeOverrides(labState.values, schema);
    const body: JobCreateRequest = { kind: labState.tab, config_overrides: overrides };
    if (labState.label.trim().length > 0) body.label = labState.label.trim();

    if (labState.tab === 'simulate') {
      body.simulate = {
        disable_organic: !labState.organicEnabled,
        organic_engine: labState.organicEngine,
      };
    } else if (labState.tab === 'sweep') {
      const result = buildSweepSpec(
        labState.sweepAxes, schema, labState.sweepMode, labState.sweepFidelity, labState.sweepObjective,
      );
      if (!result.spec) {
        showErrors(result.errors);
        return;
      }
      body.sweep = result.spec;
    } else {
      const result = buildOptimizeSpec(
        labState.optimizeVars, labState.maxEvaluations, labState.optimizeObjective, schema,
      );
      if (!result.spec) {
        showErrors(result.errors);
        return;
      }
      body.optimize = result.spec;
    }

    showErrors([]);
    launchButton.disabled = true;
    try {
      const created = await api.createJob(body);
      toast(`Job ${created.job_id} started (run ${created.run_id})`, 'success');
      navigate({ name: 'jobs', jobId: created.job_id });
    } catch {
      // Error toast already shown by the client.
    } finally {
      launchButton.disabled = false;
    }
  }

  launchButton.addEventListener('click', () => void launch());

  const labelInput = el('input', {
    type: 'text',
    placeholder: 'label (optional)',
    value: labState.label,
    oninput: () => {
      labState.label = labelInput.value;
    },
  }) as HTMLInputElement;

  const launchSection = el(
    'aside',
    { class: 'panel lab-launch' },
    el('header', { class: 'panel-header' }, el('h2', {}, 'Launch')),
    el(
      'div',
      { class: 'tab-bar' },
      ...(['simulate', 'sweep', 'optimize'] as const).map((tab) => {
        const button = el('button', {
          class: 'tab-btn',
          onclick: () => {
            labState.tab = tab;
            renderTab();
          },
        }, tab) as HTMLButtonElement;
        tabButtons.set(tab, button);
        return button;
      }),
    ),
    launchBody,
    errorsBox,
    el('div', { class: 'launch-footer' }, labelInput, launchButton),
  );

  const element = el('div', { class: 'view view-lab' }, paramsSection, launchSection);

  void api
    .schema()
    .then((entries) => {
      if (disposed) return;
      schema = entries;
      renderEditor();
      renderTab();
    })
    .catch(() => {
      if (disposed) return;
      replaceChildren(
        paramsSection,
        el('div', { class: 'panel-loading error-text' }, 'Failed to load /api/schema/params — is the studio server running?'),
      );
    });

  return {
    element,
    destroy(): void {
      disposed = true;
    },
  };
}
