/**
 * Seams of the fingerprint integration tests. Requests are tagged with the actor that sent them (`x-test-actor`); a request carries
 * its actor through every await in the server (AsyncLocalStorage), so a gate, a fault or a dropped call can be aimed at one writer
 * or reader of a schedule while others run through the same controller and the same stores.
 *
 *   Gate         a place in the code where one actor stops until the test lets it go (`W:afterPrune`, `W:afterBegin`,
 *                `W:afterBytes`, `R:afterRow`, `R:afterStat`, `R:afterReread`)
 *   FaultRepository / FaultAdapter   wrap the REAL repository and the REAL adapter of the leg
 */
import { AsyncLocalStorage } from 'node:async_hooks';
import type { Request, Response, NextFunction } from 'express';
import type { DocumentRepository } from '../../src/libs/repositories/document';
import type { StorageServiceAdapter } from '../../src/modules/storage/adapter/base-storage.adapter';

export const actorStore = new AsyncLocalStorage<string>();
export const actorOf = (): string => actorStore.getStore() ?? '-';
export const tagActor = (req: Request, _res: Response, next: NextFunction): void => {
  const actor = req.headers['x-test-actor'];
  if (typeof actor === 'string') actorStore.run(actor, next);
  else next();
};

interface Deferred<T> {
  promise: Promise<T>;
  resolve(v: T): void;
  reject(e: unknown): void;
}
function deferred<T = void>(): Deferred<T> {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((a, b) => {
    resolve = a;
    reject = b;
  });
  return { promise, resolve, reject };
}

export class Gate {
  private readonly arrived = deferred();
  private readonly go = deferred();
  /** Test side: resolves when the code reached the gate. */
  reached(): Promise<void> {
    return this.arrived.promise;
  }
  /** Test side: lets the code go on; `fail` makes it throw there instead (an abandoned writer). */
  release(fail?: Error): void {
    if (fail) this.go.reject(fail);
    else this.go.resolve();
  }
  /** Code side. */
  async hit(): Promise<void> {
    this.arrived.resolve();
    await this.go.promise;
  }
}

/** Gates by `<actor>/<point>`; a point nobody armed is a no-op. Also signals (non-blocking notifications). */
export class Gates {
  private readonly gates = new Map<string, Gate>();
  private readonly signals = new Map<string, Deferred<void>>();
  arm(actor: string, point: string): Gate {
    const key = `${actor}/${point}`;
    const g = new Gate();
    this.gates.set(key, g);
    return g;
  }
  async hit(point: string): Promise<void> {
    const g = this.gates.get(`${actorOf()}/${point}`);
    if (g) await g.hit();
  }
  /** Test side: resolves when `point` was signalled for `actor`. */
  expectSignal(actor: string, point: string): Promise<void> {
    const key = `${actor}/${point}`;
    let d = this.signals.get(key);
    if (!d) this.signals.set(key, (d = deferred()));
    return d.promise;
  }
  signal(point: string): void {
    const key = `${actorOf()}/${point}`;
    let d = this.signals.get(key);
    if (!d) this.signals.set(key, (d = deferred()));
    d.resolve();
  }
  /** Releases every gate still closed (cleanup after a schedule); the abandoned code fails there. */
  releaseAll(fail?: Error): void {
    for (const g of this.gates.values()) g.release(fail);
  }
}

export interface Faults {
  gates: Gates;
  /** Actors whose `endWrite` is dropped: a writer whose process is gone. */
  crashed: Set<string>;
  /** `endWrite` fails (rejects) for this many calls. */
  failEnd: number;
  /** `beginWrite` / `endWrite` / token calls observed, by actor. */
  calls: { actor: string; method: string; token?: string }[];
  /** Methods of the real repository that throw while set. */
  failRepo: Map<string, Error>;
  /** Methods of the adapter that throw (the write did not happen) or throw AFTER the real call (the write landed). */
  failAdapterBefore: Map<string, Error>;
  failAdapterAfter: Map<string, Error>;
  /** Fails the `n`th call (1-based) of an adapter method, before it reaches the store. */
  failAdapterNth: Map<string, { n: number; error: Error }>;
  adapterCalls: Record<string, number>;
  /** adoptDirectUpload calls that did the adoption (returned true). */
  adopted: number;
}

export const newFaults = (): Faults => ({
  gates: new Gates(),
  crashed: new Set(),
  failEnd: 0,
  calls: [],
  failRepo: new Map(),
  failAdapterBefore: new Map(),
  failAdapterAfter: new Map(),
  failAdapterNth: new Map(),
  adapterCalls: {},
  adopted: 0,
});

/** The real repository, with the gate points and faults of the tests around the write-state calls. */
export function faultRepository(real: DocumentRepository, f: Faults): DocumentRepository {
  return new Proxy(real, {
    get(target, prop, receiver) {
      const v = Reflect.get(target, prop, receiver) as unknown;
      if (typeof v !== 'function' || typeof prop !== 'string') return v;
      const fn = (v as (...a: unknown[]) => Promise<unknown>).bind(target);
      return async (...args: unknown[]) => {
        const injected = f.failRepo.get(prop);
        if (injected) throw injected;
        if (prop === 'beginWrite') {
          await f.gates.hit('W:afterPrune');
          const out = await fn(...args);
          f.calls.push({ actor: actorOf(), method: prop, token: String(args[2]) });
          await f.gates.hit('W:afterBegin');
          return out;
        }
        if (prop === 'endWrite') {
          f.calls.push({ actor: actorOf(), method: prop, token: String(args[2]) });
          if (f.crashed.has(actorOf())) return undefined; // the process is gone: the end never happens
          if (f.failEnd > 0) {
            f.failEnd -= 1;
            throw new Error('endWrite failed (test fault)');
          }
          const out = await fn(...args);
          f.gates.signal('W:afterEnd');
          return out;
        }
        if (prop === 'adoptDirectUpload') {
          const out = await fn(...args);
          if (out === true) f.adopted += 1;
          await f.gates.hit('W:afterAdopt');
          return out;
        }
        return fn(...args);
      };
    },
  });
}

const SYNC_ADAPTER_METHODS: ReadonlySet<string> = new Set(['getObjectUrl', 'signObjectUrl']);

/** The real adapter of the leg, with the gate points and faults of the tests around its byte writes and its stat. */
export function faultAdapter(real: StorageServiceAdapter, f: Faults): StorageServiceAdapter {
  return new Proxy(real, {
    get(target, prop, receiver) {
      const v = Reflect.get(target, prop, receiver) as unknown;
      if (typeof v !== 'function' || typeof prop !== 'string') return v;
      // synchronous methods stay synchronous (a Promise where a URL string is expected is not a fault, it is a broken wrapper)
      if (SYNC_ADAPTER_METHODS.has(prop)) return (v as (...a: unknown[]) => unknown).bind(target);
      const fn = (v as (...a: unknown[]) => Promise<unknown>).bind(target);
      return async (...args: unknown[]) => {
        f.adapterCalls[prop] = (f.adapterCalls[prop] ?? 0) + 1;
        const before = f.failAdapterBefore.get(prop);
        if (before) throw before;
        const nth = f.failAdapterNth.get(prop);
        if (nth && f.adapterCalls[prop] === nth.n) throw nth.error;
        if (prop === 'uploadDocumentToStorageService') await f.gates.hit('W:beforeUpload');
        const out = await fn(...args);
        const after = f.failAdapterAfter.get(prop);
        if (after) throw after;
        if (prop === 'updateBuffer') await f.gates.hit('W:afterBytes');
        return out;
      };
    },
  });
}
