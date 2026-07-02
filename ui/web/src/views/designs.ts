/** Designs: ranked/filterable table with multi-select side-by-side compare. */

import { api, downloadFile } from '../api/client';
import type { DesignRecord, RunRecord } from '../api/types';
import { el, fmtNum, replaceChildren } from '../core/dom';
import { navigate } from '../core/router';
import { toast } from '../core/toast';
import { buildCompareRows } from '../logic/compare';
import type { View } from './types';

const SORT_FIELDS = ['score', 'range_km', 'endurance_h', 'cruise_ld', 'total_mass_g'] as const;
const METRIC_COLUMNS = ['range_km', 'endurance_h', 'cruise_ld', 'total_mass_g'] as const;
const LIMITS = [25, 50, 100, 200] as const;
const DEFAULT_MESH = { span_sections: 121, profile_points: 241 };
const MAX_COMPARE = 4;

interface DesignsFilters {
  sort: string;
  order: 'asc' | 'desc';
  feasibleOnly: boolean;
  runId: string;
  limit: number;
}

export function createDesignsView(initialRunId?: string): View {
  const filters: DesignsFilters = {
    sort: 'score',
    order: 'desc',
    feasibleOnly: false,
    runId: initialRunId ?? '',
    limit: 50,
  };

  let designs: DesignRecord[] = [];
  const selected = new Set<string>();
  let disposed = false;

  const tableBody = el('tbody');
  const comparePanel = el('section', { class: 'panel compare-panel', style: 'display:none' });
  const compareButton = el('button', { class: 'btn btn-accent', disabled: true }, 'Compare') as HTMLButtonElement;
  const countLabel = el('span', { class: 'mono muted' }, '');

  function updateCompareButton(): void {
    compareButton.disabled = selected.size < 2;
    compareButton.textContent = selected.size >= 2 ? `Compare (${selected.size})` : 'Compare';
  }

  function renderCompare(): void {
    const chosen = designs.filter((design) => selected.has(design.design_id));
    if (chosen.length < 2) {
      comparePanel.style.display = 'none';
      return;
    }
    const rows = buildCompareRows(chosen);
    comparePanel.style.display = '';
    replaceChildren(
      comparePanel,
      el('header', { class: 'panel-header' },
        el('h2', {}, 'Compare'),
        el('div', { class: 'header-actions' },
          el('span', { class: 'muted mono' }, 'deltas vs first selection'),
          el('button', {
            class: 'btn btn-ghost',
            onclick: () => {
              comparePanel.style.display = 'none';
            },
          }, 'Close'),
        ),
      ),
      el('div', { class: 'table-scroll' },
        el('table', { class: 'data-table compare-table' },
          el('thead', {},
            el('tr', {},
              el('th', {}, ''),
              ...chosen.map((design) =>
                el('th', { class: 'mono' },
                  el('a', { href: `#/viewer/${design.design_id}`, title: 'open in viewer' }, design.design_id),
                ),
              ),
            ),
          ),
          el('tbody', {},
            ...rows.map((row) =>
              el('tr', { class: `compare-${row.section}` },
                el('td', { class: 'mono muted', title: row.key }, row.key),
                ...row.values.map((value, index) => {
                  const delta = row.deltas[index];
                  return el('td', { class: `mono${row.bestIndex === index ? ' best-cell' : ''}` },
                    el('span', {}, fmtNum(value)),
                    typeof delta === 'number' && delta !== 0
                      ? el('span', { class: `delta ${delta > 0 ? 'delta-up' : 'delta-down'}` },
                          ` ${delta > 0 ? '+' : ''}${fmtNum(delta)}`)
                      : null,
                  );
                }),
              ),
            ),
          ),
        ),
      ),
    );
    comparePanel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }

  function renderTable(): void {
    countLabel.textContent = `${designs.length} designs`;
    replaceChildren(
      tableBody,
      ...designs.map((design) => {
        const checkbox = el('input', {
          type: 'checkbox',
          checked: selected.has(design.design_id),
          onchange: () => {
            if (checkbox.checked) {
              if (selected.size >= MAX_COMPARE) {
                checkbox.checked = false;
                toast(`Select at most ${MAX_COMPARE} designs to compare`, 'info');
                return;
              }
              selected.add(design.design_id);
            } else {
              selected.delete(design.design_id);
            }
            updateCompareButton();
          },
        }) as HTMLInputElement;

        return el('tr', {},
          el('td', {}, checkbox),
          el('td', { class: 'mono' }, design.design_id),
          el('td', { class: 'mono strong' }, fmtNum(design.score)),
          ...METRIC_COLUMNS.map((key) => el('td', { class: 'mono' }, fmtNum(design.metrics[key]))),
          el('td', {}, el('span', { class: design.feasible ? 'badge badge-ok' : 'badge badge-bad' }, design.feasible ? 'feasible' : 'infeasible')),
          el('td', {}, el('span', { class: 'badge badge-source' }, design.source)),
          el('td', { class: 'mono muted' }, design.run_id),
          el('td', { class: 'row-actions' },
            el('button', {
              class: 'btn btn-ghost btn-small',
              onclick: () => navigate({ name: 'viewer', designId: design.design_id }),
            }, 'View 3D'),
            el('button', {
              class: 'btn btn-ghost btn-small',
              title: 'download binary STL',
              onclick: () => void downloadFile(api.meshUrl(design.design_id, DEFAULT_MESH), `${design.design_id}.stl`).catch(() => undefined),
            }, 'STL'),
            el('button', {
              class: 'btn btn-ghost btn-small',
              title: 'download design JSON',
              onclick: () => void downloadFile(api.exportUrl(design.design_id), `${design.design_id}.json`).catch(() => undefined),
            }, 'JSON'),
          ),
        );
      }),
    );
    if (designs.length === 0) {
      tableBody.appendChild(el('tr', {}, el('td', { colspan: '11', class: 'muted' }, 'no designs match the current filters')));
    }
  }

  async function refresh(): Promise<void> {
    try {
      designs = await api.designs({
        sort: filters.sort,
        order: filters.order,
        feasible: filters.feasibleOnly,
        run_id: filters.runId || undefined,
        limit: filters.limit,
      });
    } catch {
      designs = [];
    }
    if (disposed) return;
    // Drop selections that fell out of the result set.
    const visible = new Set(designs.map((design) => design.design_id));
    for (const id of [...selected]) if (!visible.has(id)) selected.delete(id);
    updateCompareButton();
    renderTable();
  }

  // ------------------------------------------------------------- controls

  const sortSelect = el('select', {
    onchange: () => { filters.sort = sortSelect.value; void refresh(); },
  }, ...SORT_FIELDS.map((field) => el('option', { value: field, selected: field === filters.sort }, field))) as HTMLSelectElement;

  const orderSelect = el('select', {
    onchange: () => { filters.order = orderSelect.value as 'asc' | 'desc'; void refresh(); },
  },
    el('option', { value: 'desc', selected: true }, 'desc'),
    el('option', { value: 'asc' }, 'asc'),
  ) as HTMLSelectElement;

  const feasibleCheckbox = el('input', {
    type: 'checkbox',
    checked: filters.feasibleOnly,
    onchange: () => { filters.feasibleOnly = feasibleCheckbox.checked; void refresh(); },
  }) as HTMLInputElement;

  const runSelect = el('select', {
    onchange: () => { filters.runId = runSelect.value; void refresh(); },
  }, el('option', { value: '' }, 'all runs')) as HTMLSelectElement;

  const limitSelect = el('select', {
    onchange: () => { filters.limit = Number(limitSelect.value); void refresh(); },
  }, ...LIMITS.map((limit) => el('option', { value: String(limit), selected: limit === filters.limit }, String(limit)))) as HTMLSelectElement;

  compareButton.addEventListener('click', renderCompare);

  void api.runs({ silent: true }).then((runs: RunRecord[]) => {
    if (disposed) return;
    for (const run of runs) {
      runSelect.appendChild(el('option', {
        value: run.run_id,
        selected: run.run_id === filters.runId,
      }, `${run.run_id} (${run.kind})`));
    }
  }).catch(() => undefined);

  const element = el(
    'div',
    { class: 'view view-designs' },
    el('section', { class: 'panel' },
      el('header', { class: 'panel-header' },
        el('h2', {}, 'Designs'),
        el('div', { class: 'header-actions' }, countLabel, compareButton),
      ),
      el('div', { class: 'controls-bar' },
        el('label', { class: 'inline-field' }, el('span', { class: 'mono muted' }, 'sort'), sortSelect),
        el('label', { class: 'inline-field' }, el('span', { class: 'mono muted' }, 'order'), orderSelect),
        el('label', { class: 'inline-field' }, feasibleCheckbox, el('span', {}, 'feasible only')),
        el('label', { class: 'inline-field' }, el('span', { class: 'mono muted' }, 'run'), runSelect),
        el('label', { class: 'inline-field' }, el('span', { class: 'mono muted' }, 'limit'), limitSelect),
        el('button', { class: 'btn btn-ghost', onclick: () => void refresh() }, 'Refresh'),
      ),
      el('div', { class: 'table-scroll' },
        el('table', { class: 'data-table' },
          el('thead', {},
            el('tr', {},
              el('th', {}, ''),
              el('th', {}, 'design'),
              el('th', {}, 'score'),
              ...METRIC_COLUMNS.map((key) => el('th', { class: 'mono' }, key)),
              el('th', {}, 'feasible'),
              el('th', {}, 'source'),
              el('th', {}, 'run'),
              el('th', {}, ''),
            ),
          ),
          tableBody,
        ),
      ),
    ),
    comparePanel,
  );

  void refresh();

  return {
    element,
    destroy(): void {
      disposed = true;
    },
  };
}
