/** Mock transport for standalone QA (`npm run dev:mock`) — no server required.
 *
 * Swaps the API client's transport for an in-memory implementation that
 * serves fixture data, simulates job execution with SSE-like scripted
 * events, and returns a tiny valid binary STL for mesh requests.
 */

import { setTransport, type EventStreamHandle, type Transport } from '../api/client';
import type {
  ContractEvent,
  DesignRecord,
  Job,
  JobCreateRequest,
  JobState,
  ParamValue,
  RunRecord,
  SweepSpec,
} from '../api/types';
import { MOCK_DESIGNS, MOCK_RUNS, MOCK_SCHEMA } from './fixtures';
import { buildBoxStl } from './stl';

const EVENT_INTERVAL_MS = 350;
const CONTRACT_VERSION = '1.1.0';

// ------------------------------------------------------------------- state

const runs: RunRecord[] = [...MOCK_RUNS];
const designs: DesignRecord[] = [...MOCK_DESIGNS];

interface MockJob {
  record: Job;
  /** Scripted events not yet emitted. */
  script: ContractEvent[];
  /** Already-emitted events (SSE replay buffer). */
  emitted: ContractEvent[];
  listeners: Set<(event: ContractEvent) => void>;
  timer: number | null;
  designSeq: number;
}

const jobs = new Map<string, MockJob>();
let jobCounter = 0;

function contractEvent(event: string, payload: Record<string, unknown>): ContractEvent {
  return { contract_version: CONTRACT_VERSION, event, payload };
}

// ---------------------------------------------------------- job simulation

function pseudoScore(params: Record<string, ParamValue>, index: number): number {
  let acc = 0.55;
  for (const value of Object.values(params)) {
    if (typeof value === 'number') acc += Math.sin(value * 3.7) * 0.06;
    else acc += (String(value).length % 5) * 0.01;
  }
  acc += Math.sin(index * 1.31) * 0.04;
  return Number(Math.min(0.95, Math.max(0.15, acc)).toFixed(3));
}

function persistDesign(job: MockJob, source: string, params: Record<string, ParamValue>, score: number, feasible: boolean): DesignRecord {
  job.designSeq += 1;
  const record: DesignRecord = {
    design_id: `${job.record.run_id}-d${String(job.designSeq).padStart(4, '0')}`,
    run_id: job.record.run_id,
    source,
    label: null,
    params: { 'geometry.root_chord_m': 0.28, 'geometry.airfoil': 'mh60', ...params },
    metrics: {
      cruise_ld: Number((14 + score * 10).toFixed(2)),
      range_km: Number((score * 75).toFixed(1)),
      endurance_h: Number((score * 2.1).toFixed(2)),
      total_mass_g: Number((900 + score * 300).toFixed(0)),
      combined_score: score,
    },
    score,
    feasible,
    artifacts: null,
  };
  designs.push(record);
  return record;
}

/** Expand a SweepSpec into grid points (cartesian product, contract §6). */
export function expandSweepGrid(spec: SweepSpec): Record<string, ParamValue>[] {
  let grid: Record<string, ParamValue>[] = [{}];
  for (const axis of spec.parameters) {
    const values: ParamValue[] = [];
    if (axis.values && axis.values.length > 0) {
      values.push(...axis.values);
    } else if (
      typeof axis.min === 'number' &&
      typeof axis.max === 'number' &&
      typeof axis.steps === 'number' &&
      axis.steps >= 2
    ) {
      for (let i = 0; i < axis.steps; i += 1) {
        values.push(Number((axis.min + (i * (axis.max - axis.min)) / (axis.steps - 1)).toFixed(6)));
      }
    }
    grid = grid.flatMap((point) => values.map((value) => ({ ...point, [axis.path]: value })));
  }
  return grid;
}

function scriptSimulate(job: MockJob, body: JobCreateRequest): void {
  const stages = ['geometry', 'aero', 'structures', 'propulsion'];
  if (body.simulate?.disable_organic !== true) stages.push(`organic:${body.simulate?.organic_engine ?? 'proxy'}`);
  stages.forEach((stage, index) => {
    job.script.push(contractEvent('progress', { stage, percent: Math.round(((index + 1) / (stages.length + 1)) * 100) }));
  });
  const params: Record<string, ParamValue> = {
    'geometry.wingspan_m': 1.5,
    'geometry.sweep_deg': 26,
    ...(body.config_overrides ?? {}),
  };
  const score = pseudoScore(params, 0);
  const design = persistDesign(job, 'simulate', params, score, true);
  job.script.push(contractEvent('design', { design_id: design.design_id, source: 'simulate', score, feasible: true }));
  job.script.push(contractEvent('result', { run_id: job.record.run_id, best_design: { design_id: design.design_id, score } }));
}

function scriptSweep(job: MockJob, spec: SweepSpec): void {
  const grid = expandSweepGrid(spec);
  const total = grid.length;
  let feasibleCount = 0;
  let best: DesignRecord | null = null;
  grid.forEach((params, index) => {
    const score = pseudoScore(params, index);
    const feasible = score > 0.4;
    if (feasible) feasibleCount += 1;
    const design = persistDesign(job, 'sweep_point', params, score, feasible);
    if (feasible && (best === null || score > best.score)) best = design;
    job.script.push(contractEvent('progress', { stage: 'sweep', percent: Math.round(((index + 1) / total) * 100) }));
    job.script.push(contractEvent('sweep_point', { index, total, params, metrics: design.metrics, score, feasible, design_id: design.design_id }));
  });
  job.script.push(contractEvent('result', {
    run_id: job.record.run_id,
    total_points: total,
    feasible_points: feasibleCount,
    best: best ?? undefined,
  }));
}

function scriptOptimize(job: MockJob, body: JobCreateRequest): void {
  const spec = body.optimize;
  const budget = spec?.budget.max_evaluations ?? 100;
  const variables = Object.entries(spec?.variables ?? {});
  const chunks = 10;
  let best: DesignRecord | null = null;
  for (let step = 1; step <= chunks; step += 1) {
    const evaluations = Math.round((step / chunks) * budget);
    job.script.push(contractEvent('progress', { stage: 'optimize', percent: step * 10, note: `${evaluations}/${budget} evaluations` }));
    if (step % 3 === 0 || step === chunks) {
      const params: Record<string, ParamValue> = {};
      for (const [path, bounds] of variables) {
        const [lo, hi] = bounds;
        params[path] = Number((lo + (hi - lo) * (0.4 + 0.05 * Math.sin(step * 2.3))).toFixed(4));
      }
      const score = Number(Math.min(0.95, 0.6 + step * 0.03).toFixed(3));
      const design = persistDesign(job, 'optimize', params, score, true);
      best = design;
      job.script.push(contractEvent('design', { design_id: design.design_id, source: 'optimize', score, feasible: true }));
    }
  }
  job.script.push(contractEvent('result', {
    run_id: job.record.run_id,
    evaluations: budget,
    best: best ?? undefined,
  }));
}

function setJobState(job: MockJob, state: JobState, exitCode?: number): void {
  job.record.state = state;
  if (exitCode !== undefined) job.record.exit_code = exitCode;
  const run = runs.find((candidate) => candidate.run_id === job.record.run_id);
  if (run) run.status = state === 'completed' ? 'completed' : state === 'running' || state === 'pending' ? 'running' : state;
}

function startJob(job: MockJob): void {
  setJobState(job, 'running');
  job.timer = window.setInterval(() => {
    const next = job.script.shift();
    if (!next) {
      stopJob(job, 'completed', 0);
      return;
    }
    job.emitted.push(next);
    for (const listener of [...job.listeners]) listener(next);
    if (next.event === 'error') stopJob(job, 'failed', 1);
  }, EVENT_INTERVAL_MS);
}

function stopJob(job: MockJob, state: JobState, exitCode: number): void {
  if (job.timer !== null) {
    window.clearInterval(job.timer);
    job.timer = null;
  }
  if (job.record.state === 'running' || job.record.state === 'pending') setJobState(job, state, exitCode);
}

function createJob(body: JobCreateRequest): Job {
  jobCounter += 1;
  const jobId = `job-${String(jobCounter).padStart(3, '0')}`;
  const runId = `run-mock-${String(jobCounter).padStart(3, '0')}-${body.kind}`;
  const record: Job = { job_id: jobId, run_id: runId, kind: body.kind, state: 'pending', label: body.label ?? null };
  const job: MockJob = { record, script: [], emitted: [], listeners: new Set(), timer: null, designSeq: 0 };

  runs.unshift({ run_id: runId, kind: body.kind, label: body.label ?? null, status: 'running', created_at: new Date().toISOString(), summary: null });
  job.script.push(contractEvent('run_info', { run_id: runId, kind: body.kind, label: body.label ?? undefined }));

  if (body.label?.includes('fail')) {
    // QA hook: a label containing "fail" produces a failing job.
    job.script.push(contractEvent('progress', { stage: 'setup', percent: 10 }));
    job.script.push(contractEvent('error', { message: 'mock failure requested via label', stage: 'setup' }));
  } else if (body.kind === 'sweep' && body.sweep) {
    scriptSweep(job, body.sweep);
  } else if (body.kind === 'optimize') {
    scriptOptimize(job, body);
  } else {
    scriptSimulate(job, body);
  }

  jobs.set(jobId, job);
  window.setTimeout(() => {
    if (job.record.state === 'pending') startJob(job);
  }, 600);
  return record;
}

// ------------------------------------------------------------ fetch router

function json(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), { status, headers: { 'content-type': 'application/json' } });
}

function notFound(detail: string): Response {
  return json({ detail }, 404);
}

function filterDesigns(search: URLSearchParams): DesignRecord[] {
  let result = [...designs];
  const runId = search.get('run_id');
  if (runId) result = result.filter((design) => design.run_id === runId);
  if (search.get('feasible') === '1') result = result.filter((design) => design.feasible);
  const sort = search.get('sort') ?? 'score';
  const order = search.get('order') ?? 'desc';
  const keyOf = (design: DesignRecord): number =>
    sort === 'score' ? design.score : (design.metrics[sort] ?? Number.NEGATIVE_INFINITY);
  result.sort((a, b) => (order === 'asc' ? keyOf(a) - keyOf(b) : keyOf(b) - keyOf(a)));
  const limit = Number(search.get('limit') ?? '50');
  if (Number.isFinite(limit) && limit > 0) result = result.slice(0, limit);
  return result;
}

async function mockFetch(input: string, init?: RequestInit): Promise<Response> {
  const url = new URL(input, 'http://mock.local');
  const path = url.pathname;
  const method = (init?.method ?? 'GET').toUpperCase();

  if (path === '/api/health') return json({ status: 'ok', version: 'mock-0.1.0', metal_available: true });
  if (path === '/api/schema/params') return json(MOCK_SCHEMA);
  if (path === '/api/runs') return json(runs);

  const runMatch = /^\/api\/runs\/([^/]+)$/.exec(path);
  if (runMatch) {
    const run = runs.find((candidate) => candidate.run_id === runMatch[1]);
    return run ? json(run) : notFound('run not found');
  }

  if (path === '/api/jobs' && method === 'POST') {
    const body = JSON.parse(String(init?.body ?? '{}')) as JobCreateRequest;
    const record = createJob(body);
    return json({ job_id: record.job_id, run_id: record.run_id }, 202);
  }
  if (path === '/api/jobs') return json([...jobs.values()].map((job) => job.record).reverse());

  const cancelMatch = /^\/api\/jobs\/([^/]+)\/cancel$/.exec(path);
  if (cancelMatch && method === 'POST') {
    const job = jobs.get(cancelMatch[1] ?? '');
    if (!job) return notFound('job not found');
    stopJob(job, 'cancelled', 143);
    return json({ ok: true });
  }

  const jobMatch = /^\/api\/jobs\/([^/]+)$/.exec(path);
  if (jobMatch) {
    const job = jobs.get(jobMatch[1] ?? '');
    return job ? json(job.record) : notFound('job not found');
  }

  const meshMatch = /^\/api\/designs\/([^/]+)\/mesh\.stl$/.exec(path);
  if (meshMatch) {
    const design = designs.find((candidate) => candidate.design_id === meshMatch[1]);
    if (!design) return notFound('design not found');
    const span = design.params['geometry.wingspan_m'];
    const chord = design.params['geometry.root_chord_m'];
    const stl = buildBoxStl(
      typeof span === 'number' ? span : 1.5,
      typeof chord === 'number' ? chord : 0.28,
      0.03,
    );
    return new Response(stl, { status: 200, headers: { 'content-type': 'model/stl' } });
  }

  const exportMatch = /^\/api\/designs\/([^/]+)\/export\.json$/.exec(path);
  if (exportMatch) {
    const design = designs.find((candidate) => candidate.design_id === exportMatch[1]);
    return design ? json(design) : notFound('design not found');
  }

  const designMatch = /^\/api\/designs\/([^/]+)$/.exec(path);
  if (designMatch) {
    const design = designs.find((candidate) => candidate.design_id === designMatch[1]);
    return design ? json(design) : notFound('design not found');
  }

  if (path === '/api/designs') return json(filterDesigns(url.searchParams));

  return notFound(`no mock route for ${method} ${path}`);
}

function mockOpenEvents(
  jobId: string,
  onEvent: (event: ContractEvent) => void,
  onError: (message: string) => void,
): EventStreamHandle {
  const job = jobs.get(jobId);
  if (!job) {
    window.setTimeout(() => onError('job not found'), 0);
    return { close: () => undefined };
  }
  // Replay the buffer asynchronously (SSE replays events.ndjson), then tail.
  let closed = false;
  window.setTimeout(() => {
    if (closed) return;
    for (const event of job.emitted) onEvent(event);
  }, 0);
  const listener = (event: ContractEvent): void => onEvent(event);
  job.listeners.add(listener);
  return {
    close(): void {
      closed = true;
      job.listeners.delete(listener);
    },
  };
}

/** Install the mock transport (called from main.ts when MODE === "mock"). */
export function installMocks(): void {
  const transport: Transport = { fetch: mockFetch, openEvents: mockOpenEvents };
  setTransport(transport);
  console.info('[winggen-studio] mock mode active — all /api traffic served in-memory');
}
