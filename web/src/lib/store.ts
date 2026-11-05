import { useSyncExternalStore } from "react";

/** Minimal observable store used with useSyncExternalStore. */
export class Store<T> {
  private listeners = new Set<() => void>();
  constructor(private value: T) {}

  get = (): T => this.value;

  set(next: T | ((prev: T) => T)): void {
    this.value = typeof next === "function" ? (next as (p: T) => T)(this.value) : next;
    for (const l of this.listeners) l();
  }

  patch(partial: Partial<T>): void {
    this.set((prev) => ({ ...prev, ...partial }));
  }

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };
}

export function useStore<T>(store: Store<T>): T {
  return useSyncExternalStore(store.subscribe, store.get, store.get);
}
