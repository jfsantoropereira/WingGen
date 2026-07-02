/** View lifecycle contract used by the router shell. */

export interface View {
  element: HTMLElement;
  destroy(): void;
}
