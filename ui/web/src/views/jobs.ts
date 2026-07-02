/** Jobs: 2 s polled list + SSE live detail (progress, sweep points, result). */

import { api, type EventStreamHandle } from '../api/client';
import type {
  ContractEvent,
  ErrorPayload,
  Job,
  ProgressPayload,
  RunInfoPayload,
  SweepPointPayload,
} from '../api/types';
import { el, fmtNum, replaceChildren } from '../core/dom';
import { navigate } from '../core/router';
import { toast } from '../core/toast';
import type { View } from './types';

const POLL_INTERVAL_MS = 2000;
const MAX_POINT_ROWS = 1000;

function stateBadge(state: string): HTMLElement {
  return el('span', { class: `badge badge-${state}` }, state);
}

export function createJobsView(selectedJobId?: string): View {
  const listBody = el('tbody');
  const detailSection = el('section', { class: 'panel job-detail' });
  let jobs: Job[] = [];
  let disposed = false;
  let stream: EventStreamHandle | null = null;
  let firstLoad = true;

  // --- detail state (reset per selection) ---
  let pointColumns: string[] = [];
  const pointsTableBody = el('tbody');
  const pointsTableHead = el('thead');
  let pointRowCount = 0;

  const progressStage = el('span', { class: 'mono' }, '—');
  const progressNote = el('span', { class: 'muted mono' }, '');
  const progressPct = el('span', { class: 'mono' }, '');
  const progressFill = el('div', { class: 'progress-fill' });
  const errorBox = el('div', { class: 'job-error', style: 'display:none' });
  const resultBox = el('div', { class: 'result-box', style: 'display:none' });
  const detailHeader = el('div', { class: 'job-detail-header' });
  const pointsWrap = el('div', { class: 'table-scroll points-wrap', style: 'display:none' },
    el('table', { class: 'data-table' }, pointsTableHead, pointsTableBody),
  );

  function selectedJob(): Job | undefined {
    return jobs.find((job) => job.job_id === selectedJobId);
  }

  function renderDetailHeader(): void {
    const job = selectedJob();
    if (!job) return;
    const cancellable = job.state === 'pending' || job.state === 'running';
    replaceChildren(
      detailHeader,
      el('div', { class: 'job-title' },
        el('span', { class: 'mono' }, job.job_id),
        stateBadge(job.state),
        el('span', { class: 'muted mono' }, job.kind),
        job.label ? el('span', { class: 'muted' }, job.label) : null,
      ),
      el('div', { class: 'header-actions' },
        el('a', { class: 'mono muted', href: `#/designs?run=${encodeURIComponent(job.run_id)}` }, job.run_id),
        cancellable
          ? el('button', {
              class: 'btn btn-danger',
              onclick: () => {
                void api.cancelJob(job.job_id).then(() => toast(`Cancel requested for ${job.job_id}`, 'info')).catch(() => undefined);
              },
            }, 'Cancel')
          : null,
      ),
    );
  }

  function handleProgress(payload: ProgressPayload): void {
    progressStage.textContent = payload.stage;
    progressNote.textContent = payload.note ?? '';
    const pct = Math.max(0, Math.min(100, payload.percent));
    progressPct.textContent = `${fmtNum(pct, 0)}%`;
    progressFill.style.width = `${pct}%`;
  }

  function handleSweepPoint(payload: SweepPointPayload): void {
    pointsWrap.style.display = '';
    const paramKeys = Object.keys(payload.params).sort();
    if (pointColumns.length === 0 && paramKeys.length > 0) {
      pointColumns = paramKeys;
      replaceChildren(
        pointsTableHead,
        el('tr', {},
          el('th', {}, '#'),
          ...pointColumns.map((key) => el('th', { class: 'mono', title: key }, key.split('.').pop() ?? key)),
          el('th', {}, 'score'),
          el('th', {}, 'feasible'),
        ),
      );
    }
    if (pointRowCount >= MAX_POINT_ROWS) return;
    pointRowCount += 1;
    pointsTableBody.appendChild(
      el('tr', {},
        el('td', { class: 'mono muted' }, `${payload.index + 1}/${payload.total}`),
        ...pointColumns.map((key) => el('td', { class: 'mono' }, fmtNum(payload.params[key]))),
        el('td', { class: 'mono' }, fmtNum(payload.score)),
        el('td', {}, el('span', { class: payload.feasible ? 'badge badge-ok' : 'badge badge-bad' }, payload.feasible ? 'yes' : 'no')),
      ),
    );
    pointsWrap.scrollTop = pointsWrap.scrollHeight;
  }

  function handleResult(payload: Record<string, unknown>): void {
    const job = selectedJob();
    const runId = typeof payload['run_id'] === 'string' ? (payload['run_id'] as string) : job?.run_id;
    const rows: HTMLElement[] = [];
    const best = (payload['best'] ?? payload['best_design']) as Record<string, unknown> | undefined;
    const scalarKeys = ['total_points', 'feasible_points', 'evaluations'] as const;
    for (const key of scalarKeys) {
      const value = payload[key];
      if (typeof value === 'number') {
        rows.push(el('div', { class: 'kv-row' }, el('span', { class: 'mono muted' }, key), el('span', { class: 'mono' }, fmtNum(value))));
      }
    }
    if (best && typeof best === 'object') {
      const score = best['score'];
      const designId = best['design_id'];
      rows.push(el('div', { class: 'kv-row' },
        el('span', { class: 'mono muted' }, 'best'),
        el('span', { class: 'mono' },
          typeof designId === 'string' ? `${designId} ` : '',
          typeof score === 'number' ? `score ${fmtNum(score)}` : '',
        ),
      ));
    }
    resultBox.style.display = '';
    replaceChildren(
      resultBox,
      el('h3', {}, 'Result'),
      ...rows,
      runId
        ? el('a', { class: 'btn btn-accent', href: `#/designs?run=${encodeURIComponent(runId)}` }, 'View designs')
        : null,
    );
  }

  function handleEvent(event: ContractEvent): void {
    switch (event.event) {
      case 'run_info': {
        const payload = event.payload as unknown as RunInfoPayload;
        if (payload.label) progressNote.textContent = payload.label;
        break;
      }
      case 'progress':
        handleProgress(event.payload as unknown as ProgressPayload);
        break;
      case 'sweep_point':
        handleSweepPoint(event.payload as unknown as SweepPointPayload);
        break;
      case 'design':
        break; // Persisted-design notifications are visible via sweep_point/result.
      case 'error': {
        const payload = event.payload as unknown as ErrorPayload;
        errorBox.style.display = '';
        replaceChildren(errorBox,
          el('strong', {}, 'Error'),
          el('span', { class: 'mono' }, ` [${payload.stage}] ${payload.message}`),
        );
        break;
      }
      case 'result':
        handleProgress({ stage: 'done', percent: 100 });
        handleResult(event.payload);
        break;
      default:
        break;
    }
  }

  function openStream(): void {
    stream?.close();
    stream = null;
    if (!selectedJobId) return;
    stream = api.openJobEvents(selectedJobId, handleEvent, () => {
      // Server closes the stream when a job finishes; only surface issues
      // for jobs that are still supposed to be live.
      const job = selectedJob();
      if (job && (job.state === 'running' || job.state === 'pending')) {
        progressNote.textContent = 'event stream interrupted — retrying via polling';
      }
    });
  }

  function renderDetail(): void {
    if (!selectedJobId) {
      replaceChildren(detailSection, el('div', { class: 'panel-loading muted' }, 'Select a job to see live progress.'));
      return;
    }
    replaceChildren(
      detailSection,
      detailHeader,
      el('div', { class: 'progress-block' },
        el('div', { class: 'progress-meta' }, progressStage, progressNote, progressPct),
        el('div', { class: 'progress-bar' }, progressFill),
      ),
      errorBox,
      resultBox,
      pointsWrap,
    );
    renderDetailHeader();
  }

  function renderList(): void {
    replaceChildren(
      listBody,
      ...jobs.map((job) =>
        el('tr', {
          class: job.job_id === selectedJobId ? 'selected' : '',
          onclick: () => navigate({ name: 'jobs', jobId: job.job_id }),
        },
          el('td', { class: 'mono' }, job.job_id),
          el('td', { class: 'mono muted' }, job.kind),
          el('td', {}, stateBadge(job.state)),
          el('td', { class: 'mono muted' }, job.run_id),
        ),
      ),
    );
    if (jobs.length === 0) {
      listBody.appendChild(el('tr', {}, el('td', { colspan: '4', class: 'muted' }, firstLoad ? 'loading…' : 'no jobs yet')));
    }
  }

  async function poll(): Promise<void> {
    try {
      jobs = await api.jobs({ silent: !firstLoad });
      firstLoad = false;
      if (disposed) return;
      renderList();
      renderDetailHeader();
    } catch {
      firstLoad = false;
    }
  }

  const interval = window.setInterval(() => void poll(), POLL_INTERVAL_MS);
  void poll();

  renderDetail();
  openStream();

  const element = el(
    'div',
    { class: 'view view-jobs' },
    el('section', { class: 'panel jobs-list' },
      el('header', { class: 'panel-header' }, el('h2', {}, 'Jobs')),
      el('div', { class: 'table-scroll' },
        el('table', { class: 'data-table' },
          el('thead', {}, el('tr', {}, el('th', {}, 'job'), el('th', {}, 'kind'), el('th', {}, 'state'), el('th', {}, 'run'))),
          listBody,
        ),
      ),
    ),
    detailSection,
  );

  return {
    element,
    destroy(): void {
      disposed = true;
      window.clearInterval(interval);
      stream?.close();
    },
  };
}
