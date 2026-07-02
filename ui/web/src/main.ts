/** App shell: left nav + hash router mounting one view at a time. */

import './style.css';
import { api } from './api/client';
import { el, replaceChildren } from './core/dom';
import { parseHash, routeToHash, startRouter, type Route } from './core/router';
import { appState } from './core/store';
import { createDesignsView } from './views/designs';
import { createJobsView } from './views/jobs';
import { createLabView } from './views/lab';
import { createViewerView } from './views/viewer';
import type { View } from './views/types';

const HEALTH_POLL_MS = 15_000;

interface NavItem {
  route: Route;
  label: string;
  match: Route['name'];
}

const NAV_ITEMS: NavItem[] = [
  { route: { name: 'lab' }, label: 'Design Lab', match: 'lab' },
  { route: { name: 'jobs' }, label: 'Jobs', match: 'jobs' },
  { route: { name: 'designs' }, label: 'Designs', match: 'designs' },
  { route: { name: 'viewer' }, label: 'Viewer', match: 'viewer' },
];

function createView(route: Route): View {
  switch (route.name) {
    case 'lab':
      return createLabView();
    case 'jobs':
      return createJobsView(route.jobId);
    case 'designs':
      return createDesignsView(route.runId);
    case 'viewer':
      return createViewerView(route.designId);
  }
}

async function boot(): Promise<void> {
  if (import.meta.env.MODE === 'mock') {
    const { installMocks } = await import('./mock/install');
    installMocks();
  }

  const app = document.getElementById('app');
  if (!app) throw new Error('missing #app root');

  const navLinks = new Map<Route['name'], HTMLAnchorElement>();
  const healthDot = el('span', { class: 'health-dot' });
  const healthText = el('span', { class: 'mono muted' }, 'api: …');

  const nav = el('nav', { class: 'sidenav' },
    el('div', { class: 'brand' },
      el('span', { class: 'brand-mark' }, '△'),
      el('div', {},
        el('div', { class: 'brand-name' }, 'WingGen'),
        el('div', { class: 'brand-sub mono' }, 'STUDIO'),
      ),
    ),
    el('div', { class: 'nav-links' },
      ...NAV_ITEMS.map((item) => {
        const anchor = el('a', { class: 'nav-link', href: routeToHash(item.route) }, item.label);
        navLinks.set(item.match, anchor);
        return anchor;
      }),
    ),
    el('div', { class: 'nav-footer' }, healthDot, healthText),
  );

  const viewHost = el('main', { class: 'view-host' });
  replaceChildren(app, el('div', { class: 'app-shell' }, nav, viewHost));

  // Viewer nav link follows the last opened design.
  appState.subscribe((state) => {
    const link = navLinks.get('viewer');
    if (link && state.lastDesignId) link.href = `#/viewer/${state.lastDesignId}`;
  });

  let currentView: View | null = null;
  startRouter((route) => {
    currentView?.destroy();
    currentView = createView(route);
    replaceChildren(viewHost, currentView.element);
    for (const [match, link] of navLinks) link.classList.toggle('active', match === route.name);
  });

  // Normalize an empty hash so links/back-button behave predictably.
  if (!window.location.hash) window.location.hash = routeToHash(parseHash(''));

  const pollHealth = async (): Promise<void> => {
    const health = await api.health();
    const ok = health !== null && health.status === 'ok';
    healthDot.classList.toggle('ok', ok);
    healthDot.classList.toggle('bad', !ok);
    healthText.textContent = ok
      ? `api ${health.version}${health.metal_available ? ' · metal' : ''}`
      : 'api offline';
  };
  void pollHealth();
  window.setInterval(() => void pollHealth(), HEALTH_POLL_MS);
}

void boot();
