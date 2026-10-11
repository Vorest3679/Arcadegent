export type Updater<T> = T | ((previous: T) => T);

export function resolveUpdater<T>(next: Updater<T>, previous: T): T {
  return typeof next === "function" ? (next as (value: T) => T)(previous) : next;
}
