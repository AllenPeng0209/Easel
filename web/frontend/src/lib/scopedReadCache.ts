type Flight = { controller: AbortController; promise: Promise<unknown>; users: number; settled: boolean; epoch: number };
const aborted = () => Object.assign(new Error('读取已取消'), { name: 'AbortError' });

/** Browser-only cache. Callers include the verified user and IP in every key. */
export class ScopedReadCache {
  private values = new Map<string, { value: unknown; expires: number }>();
  private flights = new Map<string, Flight>();
  private epoch = 0;

  invalidate(): void {
    this.epoch++;
    this.values.clear();
    for (const owner of this.flights.values()) owner.controller.abort();
    this.flights.clear();
  }

  read<T>(key: string, loader: (signal: AbortSignal) => Promise<T>, options: { signal?: AbortSignal; ttl?: number } = {}): Promise<T> {
    if (options.signal?.aborted) return Promise.reject(aborted());
    const saved = this.values.get(key);
    if (saved && saved.expires > Date.now()) return Promise.resolve(saved.value as T);
    let owner = this.flights.get(key);
    if (!owner) {
      owner = { controller: new AbortController(), promise: Promise.resolve(), users: 0, settled: false, epoch: this.epoch };
      const created = owner;
      created.promise = Promise.resolve().then(() => loader(created.controller.signal)).then(value => {
        if (created.epoch !== this.epoch || created.controller.signal.aborted) throw aborted();
        if ((options.ttl ?? 0) > 0) {
          this.values.delete(key);
          this.values.set(key, { value, expires: Date.now() + options.ttl! });
          if (this.values.size > 128) this.values.delete(this.values.keys().next().value!);
        }
        return value;
      }).finally(() => {
        created.settled = true;
        if (this.flights.get(key) === created) this.flights.delete(key);
      });
      this.flights.set(key, created);
    }
    const flight = owner;
    flight.users++;
    return new Promise<T>((resolve, reject) => {
      let finished = false;
      const release = () => {
        if (finished) return false;
        finished = true;
        options.signal?.removeEventListener('abort', cancel);
        flight.users--;
        if (!flight.users && !flight.settled) flight.controller.abort();
        return true;
      };
      const cancel = () => { if (release()) reject(aborted()); };
      options.signal?.addEventListener('abort', cancel, { once: true });
      flight.promise.then(value => { if (release()) resolve(value as T); }, error => { if (release()) reject(error); });
    });
  }
}
