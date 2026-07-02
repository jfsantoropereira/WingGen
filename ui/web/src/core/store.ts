/** Tiny pub/sub store — the only state primitive in the app. */

export type Unsubscribe = () => void;

export class Emitter<T> {
  private readonly listeners = new Set<(value: T) => void>();

  on(listener: (value: T) => void): Unsubscribe {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  emit(value: T): void {
    for (const listener of [...this.listeners]) listener(value);
  }
}

export interface Store<T extends object> {
  get(): T;
  set(patch: Partial<T>): void;
  subscribe(listener: (state: T) => void): Unsubscribe;
}

export function createStore<T extends object>(initial: T): Store<T> {
  let state = initial;
  const emitter = new Emitter<T>();
  return {
    get: () => state,
    set(patch: Partial<T>): void {
      state = { ...state, ...patch };
      emitter.emit(state);
    },
    subscribe: (listener) => emitter.on(listener),
  };
}

/** Cross-view app state. */
export interface AppState {
  /** Design most recently opened in the viewer (used by the nav link). */
  lastDesignId: string | null;
}

export const appState = createStore<AppState>({ lastDesignId: null });
