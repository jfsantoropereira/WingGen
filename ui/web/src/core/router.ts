/** Hash-based router: #/lab, #/jobs, #/jobs/:id, #/designs[?run=], #/viewer/:designId */

export type Route =
  | { name: 'lab' }
  | { name: 'jobs'; jobId?: string }
  | { name: 'designs'; runId?: string }
  | { name: 'viewer'; designId?: string };

export function parseHash(hash: string): Route {
  const raw = hash.replace(/^#\/?/, '');
  const [pathPart = '', queryPart = ''] = raw.split('?');
  const segments = pathPart.split('/').filter((s) => s.length > 0);
  const query = new URLSearchParams(queryPart);
  switch (segments[0]) {
    case 'jobs':
      return { name: 'jobs', jobId: segments[1] };
    case 'designs':
      return { name: 'designs', runId: query.get('run') ?? undefined };
    case 'viewer':
      return { name: 'viewer', designId: segments[1] };
    default:
      return { name: 'lab' };
  }
}

export function routeToHash(route: Route): string {
  switch (route.name) {
    case 'lab':
      return '#/lab';
    case 'jobs':
      return route.jobId ? `#/jobs/${route.jobId}` : '#/jobs';
    case 'designs':
      return route.runId ? `#/designs?run=${encodeURIComponent(route.runId)}` : '#/designs';
    case 'viewer':
      return route.designId ? `#/viewer/${route.designId}` : '#/viewer';
  }
}

export function navigate(route: Route): void {
  window.location.hash = routeToHash(route);
}

/** Start listening; invokes the callback immediately with the current route. */
export function startRouter(onChange: (route: Route) => void): void {
  window.addEventListener('hashchange', () => onChange(parseHash(window.location.hash)));
  onChange(parseHash(window.location.hash));
}
