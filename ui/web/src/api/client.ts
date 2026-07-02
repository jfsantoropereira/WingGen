/** Fetch wrapper + typed endpoints for the Studio server API (contract §7). */

import { toast } from '../core/toast';
import type {
  ContractEvent,
  DesignQuery,
  DesignRecord,
  HealthInfo,
  Job,
  JobCreateRequest,
  JobCreated,
  MeshResolution,
  ParamSchemaEntry,
  RunRecord,
} from './types';

export interface EventStreamHandle {
  close(): void;
}

/** Transport seam: the mock mode swaps this out (see src/mock/install.ts). */
export interface Transport {
  fetch(input: string, init?: RequestInit): Promise<Response>;
  openEvents(
    jobId: string,
    onEvent: (event: ContractEvent) => void,
    onError: (message: string) => void,
  ): EventStreamHandle;
}

const realTransport: Transport = {
  fetch: (input, init) => window.fetch(input, init),
  openEvents(jobId, onEvent, onError) {
    const source = new EventSource(`/api/jobs/${encodeURIComponent(jobId)}/events`);
    source.onmessage = (message: MessageEvent<string>) => {
      try {
        onEvent(JSON.parse(message.data) as ContractEvent);
      } catch {
        // Ignore malformed lines.
      }
    };
    source.onerror = () => onError('event stream interrupted');
    return { close: () => source.close() };
  },
};

let transport: Transport = realTransport;

export function setTransport(next: Transport): void {
  transport = next;
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

interface RequestOptions {
  /** Suppress the error toast (used by background pollers). */
  silent?: boolean;
}

async function request<T>(path: string, init?: RequestInit, options?: RequestOptions): Promise<T> {
  let response: Response;
  try {
    response = await transport.fetch(path, init);
  } catch (error) {
    if (!options?.silent) toast(`API unreachable: ${path}`, 'error');
    throw error;
  }
  if (!response.ok) {
    let detail = response.statusText || `HTTP ${response.status}`;
    try {
      const body = (await response.json()) as Record<string, unknown>;
      const candidate = body['detail'] ?? body['message'] ?? body['error'];
      if (typeof candidate === 'string') detail = candidate;
    } catch {
      // Non-JSON error body.
    }
    if (!options?.silent) toast(`API ${response.status}: ${detail}`, 'error');
    throw new ApiError(detail, response.status);
  }
  return (await response.json()) as T;
}

/** Accept either a bare array or a `{key: [...]}` wrapper (defensive). */
function asList<T>(value: unknown, key: string): T[] {
  if (Array.isArray(value)) return value as T[];
  if (value && typeof value === 'object') {
    const inner = (value as Record<string, unknown>)[key];
    if (Array.isArray(inner)) return inner as T[];
  }
  return [];
}

function buildDesignQuery(query: DesignQuery): string {
  const params = new URLSearchParams();
  if (query.run_id) params.set('run_id', query.run_id);
  if (query.feasible) params.set('feasible', '1');
  if (query.sort) params.set('sort', query.sort);
  if (query.order) params.set('order', query.order);
  if (query.limit !== undefined) params.set('limit', String(query.limit));
  return params.toString();
}

let schemaPromise: Promise<ParamSchemaEntry[]> | null = null;

export const api = {
  async health(): Promise<HealthInfo | null> {
    try {
      const response = await transport.fetch('/api/health');
      if (!response.ok) return null;
      return (await response.json()) as HealthInfo;
    } catch {
      return null;
    }
  },

  /** Cached parameter schema. */
  schema(): Promise<ParamSchemaEntry[]> {
    schemaPromise ??= request<unknown>('/api/schema/params')
      .then((value) => asList<ParamSchemaEntry>(value, 'params'))
      .catch((error: unknown) => {
        schemaPromise = null;
        throw error;
      });
    return schemaPromise;
  },

  jobs: (options?: RequestOptions) =>
    request<unknown>('/api/jobs', undefined, options).then((v) => asList<Job>(v, 'jobs')),
  job: (jobId: string, options?: RequestOptions) =>
    request<Job>(`/api/jobs/${encodeURIComponent(jobId)}`, undefined, options),
  createJob: (body: JobCreateRequest) =>
    request<JobCreated>('/api/jobs', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(body),
    }),
  cancelJob: (jobId: string) =>
    request<unknown>(`/api/jobs/${encodeURIComponent(jobId)}/cancel`, { method: 'POST' }),

  runs: (options?: RequestOptions) =>
    request<unknown>('/api/runs', undefined, options).then((v) => asList<RunRecord>(v, 'runs')),

  designs: (query: DesignQuery, options?: RequestOptions) =>
    request<unknown>(`/api/designs?${buildDesignQuery(query)}`, undefined, options).then((v) =>
      asList<DesignRecord>(v, 'designs'),
    ),
  design: (designId: string) => request<DesignRecord>(`/api/designs/${encodeURIComponent(designId)}`),

  meshUrl(designId: string, resolution: MeshResolution): string {
    const params = new URLSearchParams({
      span_sections: String(resolution.span_sections),
      profile_points: String(resolution.profile_points),
    });
    return `/api/designs/${encodeURIComponent(designId)}/mesh.stl?${params.toString()}`;
  },
  exportUrl(designId: string): string {
    return `/api/designs/${encodeURIComponent(designId)}/export.json`;
  },

  async fetchBinary(url: string): Promise<ArrayBuffer> {
    const response = await transport.fetch(url);
    if (!response.ok) {
      toast(`Download failed (${response.status}): ${url}`, 'error');
      throw new ApiError(response.statusText, response.status);
    }
    return response.arrayBuffer();
  },

  openJobEvents(
    jobId: string,
    onEvent: (event: ContractEvent) => void,
    onError: (message: string) => void,
  ): EventStreamHandle {
    return transport.openEvents(jobId, onEvent, onError);
  },
};

/** Download a (possibly mocked) URL through the transport as a named file. */
export async function downloadFile(url: string, filename: string): Promise<void> {
  const buffer = await api.fetchBinary(url);
  const blobUrl = URL.createObjectURL(new Blob([buffer]));
  const anchor = document.createElement('a');
  anchor.href = blobUrl;
  anchor.download = filename;
  anchor.click();
  window.setTimeout(() => URL.revokeObjectURL(blobUrl), 10_000);
}
