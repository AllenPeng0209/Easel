/** Minimal immutable URL-record boundary for the vendored HostReadStore.
 * This bridge only mutates one top-level cache entry; it is not the general Cordis engine.
 */
export function createSnapshotStore<T extends Record<string, unknown>>(initial: T) {
  let current = initial;
  const listeners = new Set<() => void>();
  const notify = () => { for (const listener of [...listeners]) listener(); };
  return {
    getSnapshot: () => current,
    subscribe: (listener: () => void) => { listeners.add(listener); return () => { listeners.delete(listener); }; },
    update: (mutate: (draft: T) => void) => { const next = { ...current }; mutate(next); current = next; notify(); },
    set: (next: T) => { current = next; notify(); },
  };
}
