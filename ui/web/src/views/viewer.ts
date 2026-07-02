/** Viewer: Three.js STL viewer with orbit controls, grid/axes, wireframe. */

import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import { STLLoader } from 'three/examples/jsm/loaders/STLLoader.js';
import { api, downloadFile } from '../api/client';
import type { DesignRecord, MeshResolution } from '../api/types';
import { el, fmtNum, replaceChildren } from '../core/dom';
import { appState } from '../core/store';
import type { View } from './types';

interface ResolutionPreset {
  key: string;
  label: string;
  resolution: MeshResolution;
}

const RESOLUTIONS: ResolutionPreset[] = [
  { key: 'draft', label: 'draft (61×121)', resolution: { span_sections: 61, profile_points: 121 } },
  { key: 'standard', label: 'standard (121×241)', resolution: { span_sections: 121, profile_points: 241 } },
  { key: 'high', label: 'high (161×321)', resolution: { span_sections: 161, profile_points: 321 } },
];
const DEFAULT_RESOLUTION_KEY = 'standard';

function kvList(title: string, record: Record<string, unknown>): HTMLElement {
  const keys = Object.keys(record).sort();
  return el('div', { class: 'meta-block' },
    el('h3', {}, title),
    ...keys.map((key) =>
      el('div', { class: 'kv-row' },
        el('span', { class: 'mono muted', title: key }, key),
        el('span', { class: 'mono' }, fmtNum(record[key])),
      ),
    ),
  );
}

export function createViewerView(designId?: string): View {
  const resolved = designId ?? appState.get().lastDesignId ?? undefined;

  if (!resolved) {
    return {
      element: el('div', { class: 'view view-viewer' },
        el('section', { class: 'panel panel-loading' },
          el('p', { class: 'muted' }, 'No design selected. Pick one from the '),
          el('a', { href: '#/designs' }, 'Designs view'),
        ),
      ),
      destroy: () => undefined,
    };
  }
  appState.set({ lastDesignId: resolved });

  let resolutionKey = DEFAULT_RESOLUTION_KEY;
  let disposed = false;
  let rafHandle = 0;
  let wireframe = false;
  let mesh: THREE.Mesh | null = null;
  let grid: THREE.GridHelper | null = null;
  let axes: THREE.AxesHelper | null = null;
  let loadToken = 0;

  const currentResolution = (): MeshResolution =>
    (RESOLUTIONS.find((preset) => preset.key === resolutionKey) ?? RESOLUTIONS[1]!).resolution;

  // ------------------------------------------------------------ three setup

  const canvasHost = el('div', { class: 'viewer-canvas' });
  const overlay = el('div', { class: 'viewer-overlay mono' }, 'loading mesh…');
  canvasHost.appendChild(overlay);

  const renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.domElement.className = 'viewer-webgl';
  canvasHost.appendChild(renderer.domElement);

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x0b0e13);

  const camera = new THREE.PerspectiveCamera(45, 1, 0.001, 100);
  camera.position.set(1.5, 1.0, 1.8);

  const hemisphere = new THREE.HemisphereLight(0x9fb4c8, 0x141922, 1.1);
  scene.add(hemisphere);
  const keyLight = new THREE.DirectionalLight(0xffffff, 1.6);
  keyLight.position.set(2, 3, 2.5);
  scene.add(keyLight);
  const fillLight = new THREE.DirectionalLight(0x6688aa, 0.5);
  fillLight.position.set(-2.5, 1, -2);
  scene.add(fillLight);

  const material = new THREE.MeshStandardMaterial({
    color: 0xb9c0ca,
    metalness: 0.35,
    roughness: 0.42,
    side: THREE.DoubleSide,
  });

  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.dampingFactor = 0.08;
  controls.screenSpacePanning = true;
  controls.zoomSpeed = 0.9;
  controls.rotateSpeed = 0.9;

  function fitCamera(radius: number, center: THREE.Vector3): void {
    const distance = (radius / Math.tan((camera.fov * Math.PI) / 360)) * 1.35;
    camera.near = Math.max(radius / 1000, 1e-4);
    camera.far = radius * 100;
    camera.position.copy(center).add(new THREE.Vector3(0.55, 0.4, 0.8).normalize().multiplyScalar(distance));
    camera.updateProjectionMatrix();
    controls.target.copy(center);
    controls.minDistance = radius * 0.2;
    controls.maxDistance = radius * 15;
    controls.update();
  }

  function addHelpers(span: number, floorY: number): void {
    if (grid) {
      scene.remove(grid);
      grid.geometry.dispose();
      (grid.material as THREE.Material).dispose();
    }
    if (axes) {
      scene.remove(axes);
      axes.dispose();
    }
    const size = Math.max(span * 1.6, 0.5);
    grid = new THREE.GridHelper(size, 24, 0x33415a, 0x1b2331);
    grid.position.y = floorY;
    scene.add(grid);
    axes = new THREE.AxesHelper(span * 0.55);
    axes.position.y = floorY;
    scene.add(axes);
  }

  function disposeMesh(): void {
    if (!mesh) return;
    scene.remove(mesh);
    mesh.geometry.dispose();
    mesh = null;
  }

  function setOverlay(text: string | null, isError = false): void {
    overlay.style.display = text === null ? 'none' : '';
    overlay.textContent = text ?? '';
    overlay.classList.toggle('error-text', isError);
  }

  let record: DesignRecord | null = null;

  async function loadMesh(): Promise<void> {
    const token = ++loadToken;
    setOverlay('loading mesh…');
    let buffer: ArrayBuffer;
    try {
      buffer = await api.fetchBinary(api.meshUrl(resolved!, currentResolution()));
    } catch {
      if (!disposed && token === loadToken) setOverlay('failed to load mesh — is the studio server running?', true);
      return;
    }
    if (disposed || token !== loadToken) return;

    const geometry = new STLLoader().parse(buffer);
    geometry.computeVertexNormals();
    geometry.center();
    geometry.computeBoundingSphere();
    const sphere = geometry.boundingSphere ?? new THREE.Sphere(new THREE.Vector3(), 1);

    disposeMesh();
    mesh = new THREE.Mesh(geometry, material);
    // STL export is Z-up; rotate into three.js Y-up.
    mesh.rotation.x = -Math.PI / 2;
    scene.add(mesh);

    const bounds = new THREE.Box3().setFromObject(mesh);
    const spanParam = record?.params['geometry.wingspan_m'];
    const span = typeof spanParam === 'number' ? spanParam : bounds.max.x - bounds.min.x;
    addHelpers(span, bounds.min.y - span * 0.02);
    fitCamera(sphere.radius, new THREE.Vector3(0, 0, 0));
    setOverlay(null);
  }

  // --------------------------------------------------------------- sidebar

  const metaBox = el('div', { class: 'viewer-meta' }, el('div', { class: 'muted' }, 'loading design…'));

  const wireframeButton = el('button', {
    class: 'btn btn-ghost',
    onclick: () => {
      wireframe = !wireframe;
      material.wireframe = wireframe;
      wireframeButton.classList.toggle('active', wireframe);
    },
  }, 'Wireframe') as HTMLButtonElement;

  const resolutionSelect = el('select', {
    onchange: () => {
      resolutionKey = resolutionSelect.value;
      void loadMesh();
    },
  }, ...RESOLUTIONS.map((preset) =>
    el('option', { value: preset.key, selected: preset.key === resolutionKey }, preset.label),
  )) as HTMLSelectElement;

  const sidebar = el('aside', { class: 'panel viewer-side' },
    el('header', { class: 'panel-header' },
      el('h2', {}, 'Viewer'),
      el('span', { class: 'mono muted' }, resolved),
    ),
    el('div', { class: 'controls-bar controls-col' },
      el('label', { class: 'inline-field' }, el('span', { class: 'mono muted' }, 'resolution'), resolutionSelect),
      wireframeButton,
      el('button', {
        class: 'btn btn-accent',
        onclick: () => void downloadFile(api.meshUrl(resolved, currentResolution()), `${resolved}.stl`).catch(() => undefined),
      }, 'Export STL'),
      el('button', {
        class: 'btn btn-accent',
        onclick: () => void downloadFile(api.exportUrl(resolved), `${resolved}.json`).catch(() => undefined),
      }, 'Export JSON'),
    ),
    metaBox,
  );

  void api.design(resolved).then((design) => {
    if (disposed) return;
    record = design;
    replaceChildren(
      metaBox,
      el('div', { class: 'meta-block' },
        el('div', { class: 'kv-row' }, el('span', { class: 'mono muted' }, 'run'), el('span', { class: 'mono' }, design.run_id)),
        el('div', { class: 'kv-row' }, el('span', { class: 'mono muted' }, 'source'), el('span', { class: 'mono' }, design.source)),
        el('div', { class: 'kv-row' }, el('span', { class: 'mono muted' }, 'score'), el('span', { class: 'mono strong' }, fmtNum(design.score))),
        el('div', { class: 'kv-row' },
          el('span', { class: 'mono muted' }, 'feasible'),
          el('span', { class: design.feasible ? 'badge badge-ok' : 'badge badge-bad' }, design.feasible ? 'yes' : 'no'),
        ),
      ),
      kvList('Metrics', design.metrics),
      kvList('Parameters', design.params),
    );
    // Reload helpers scaled by the actual wingspan if the mesh landed first.
    if (mesh) {
      const bounds = new THREE.Box3().setFromObject(mesh);
      const spanParam = design.params['geometry.wingspan_m'];
      const span = typeof spanParam === 'number' ? spanParam : bounds.max.x - bounds.min.x;
      addHelpers(span, bounds.min.y - span * 0.02);
    }
  }).catch(() => {
    if (!disposed) replaceChildren(metaBox, el('div', { class: 'error-text' }, 'failed to load design record'));
  });

  // ------------------------------------------------------------- lifecycle

  const resizeObserver = new ResizeObserver(() => {
    const width = canvasHost.clientWidth;
    const height = canvasHost.clientHeight;
    if (width === 0 || height === 0) return;
    renderer.setSize(width, height);
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
  });
  resizeObserver.observe(canvasHost);

  function animate(): void {
    if (disposed) return;
    rafHandle = requestAnimationFrame(animate);
    controls.update();
    renderer.render(scene, camera);
  }
  animate();
  void loadMesh();

  const element = el('div', { class: 'view view-viewer' }, sidebar, canvasHost);

  return {
    element,
    destroy(): void {
      disposed = true;
      cancelAnimationFrame(rafHandle);
      resizeObserver.disconnect();
      controls.dispose();
      disposeMesh();
      if (grid) {
        grid.geometry.dispose();
        (grid.material as THREE.Material).dispose();
      }
      axes?.dispose();
      material.dispose();
      renderer.dispose();
      renderer.domElement.remove();
    },
  };
}
