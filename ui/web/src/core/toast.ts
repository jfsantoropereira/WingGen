/** Toast notifications, appended to a fixed overlay container. */

export type ToastKind = 'info' | 'success' | 'error';

const TOAST_TTL_MS = 4500;
let root: HTMLElement | null = null;

function ensureRoot(): HTMLElement {
  if (!root) {
    root = document.createElement('div');
    root.className = 'toast-root';
    document.body.appendChild(root);
  }
  return root;
}

export function toast(message: string, kind: ToastKind = 'info'): void {
  const container = ensureRoot();
  const node = document.createElement('div');
  node.className = `toast toast-${kind}`;
  node.textContent = message;
  node.addEventListener('click', () => node.remove());
  container.appendChild(node);
  window.setTimeout(() => {
    node.classList.add('toast-out');
    window.setTimeout(() => node.remove(), 300);
  }, TOAST_TTL_MS);
  // Keep at most 5 toasts on screen.
  while (container.childElementCount > 5) container.firstElementChild?.remove();
}
