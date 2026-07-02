/** Minimal typed DOM construction helpers (no framework). */

export type Child = Node | string | number | null | undefined | false;

/** Create an element with attributes/listeners and children. */
export function el<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  attrs: Record<string, unknown> = {},
  ...children: Child[]
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === undefined || value === null || value === false) continue;
    if (key.startsWith('on') && typeof value === 'function') {
      node.addEventListener(key.slice(2).toLowerCase(), value as EventListener);
    } else if (key === 'value') {
      (node as unknown as { value: string }).value = String(value);
    } else if (key === 'checked' || key === 'disabled' || key === 'selected' || key === 'multiple') {
      (node as unknown as Record<string, unknown>)[key] = value === true;
    } else if (value === true) {
      node.setAttribute(key, '');
    } else {
      node.setAttribute(key, String(value));
    }
  }
  append(node, children);
  return node;
}

/** Append children, skipping null/undefined/false. */
export function append(parent: Node, children: Child[]): void {
  for (const child of children) {
    if (child === null || child === undefined || child === false) continue;
    parent.appendChild(
      typeof child === 'string' || typeof child === 'number'
        ? document.createTextNode(String(child))
        : child,
    );
  }
}

/** Remove all children of a node. */
export function clear(node: Node): void {
  while (node.firstChild) node.removeChild(node.firstChild);
}

/** Replace all children of a node. */
export function replaceChildren(node: Node, ...children: Child[]): void {
  clear(node);
  append(node, children);
}

/** Compact numeric formatting for dense tables. */
export function fmtNum(value: unknown, digits?: number): string {
  if (typeof value === 'string') return value;
  if (typeof value === 'boolean') return value ? 'yes' : 'no';
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—';
  if (Number.isInteger(value) && Math.abs(value) < 1e7) return String(value);
  if (digits !== undefined) return value.toFixed(digits);
  const abs = Math.abs(value);
  if (abs >= 1000) return value.toFixed(0);
  if (abs >= 100) return value.toFixed(1);
  if (abs >= 1) return value.toFixed(2);
  return value.toFixed(3);
}
