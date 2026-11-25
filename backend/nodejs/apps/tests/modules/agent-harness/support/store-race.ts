/**
 * Deterministic interleavings and failpoints at the Mongo store seam (QA-21).
 *
 * Every operation the stores issue reaches the driver's `Collection`; a probe
 * installed there sees each one with its collection, method and whether it
 * runs inside a transaction. Work runs as a named party (AsyncLocalStorage),
 * so a probe acts on one racer's operations only.
 *
 * `StoreRace` holds each party at the first operation a gate selects (by
 * default its first inside a transaction) until every party is there: after
 * a read, which fixes the snapshot of a `snapshot` transaction, and before a
 * write, so a held party never holds a write another one would conflict on.
 * Then it releases them all at once, or one at a time in a scripted order,
 * each after the one before it settled. In that order a later party whose
 * transaction read first decides on a snapshot the earlier one's commit made
 * stale: the interleaving a lost update needs.
 *
 * `failAt` throws from a party's n-th operation inside a transaction, before
 * or after the operation ran (ADM-17).
 */
import { AsyncLocalStorage } from 'node:async_hooks';
import { Collection, MongoClient, MongoServerError } from 'mongodb';

export interface StoreOp {
  collection: string;
  /** Every collection it writes: one, or a transaction's batch (PERF-E2, PERF-E3), whose `collection` is `bulkWrite`. */
  collections: string[];
  method: string;
  write: boolean;
  inTransaction: boolean;
  /** The party whose work issued it; null outside any. */
  party: string | null;
}

export type OpPhase = 'before' | 'after';

/** Runs before and after each operation; a throw fails the operation. */
export type OpProbe = (op: StoreOp, phase: OpPhase) => Promise<void> | void;

const WRITES = [
  'insertOne',
  'insertMany',
  'bulkWrite',
  'updateOne',
  'updateMany',
  'replaceOne',
  'deleteOne',
  'deleteMany',
  'findOneAndUpdate',
  'findOneAndReplace',
  'findOneAndDelete',
] as const;
const READS = ['findOne', 'countDocuments', 'distinct'] as const;
const CURSORS = ['find', 'aggregate'] as const;

const parties = new AsyncLocalStorage<string>();
/** Set while a probed method runs, so the calls it makes itself are not counted again. */
const nested = new AsyncLocalStorage<true>();
const probes = new Set<OpProbe>();

type Method = (...args: unknown[]) => unknown;
type Patched = Record<string, Method>;

function sessionOf(args: unknown[]): { inTransaction(): boolean } | null {
  for (const arg of args) {
    const session = (arg as { session?: unknown } | null)?.session;
    if (
      session !== undefined &&
      session !== null &&
      typeof (session as { inTransaction?: unknown }).inTransaction ===
        'function'
    ) {
      return session as { inTransaction(): boolean };
    }
  }
  return null;
}

function opOf(collection: Collection, method: string, args: unknown[]): StoreOp {
  return {
    collection: collection.collectionName,
    collections: [collection.collectionName],
    method,
    write: (WRITES as readonly string[]).includes(method),
    inTransaction: sessionOf(args)?.inTransaction() === true,
    party: parties.getStore() ?? null,
  };
}

async function notify(op: StoreOp, phase: OpPhase): Promise<void> {
  for (const probe of [...probes]) await probe(op, phase);
}

let originals: Patched | null = null;

function install(): void {
  if (originals !== null) return;
  const proto = Collection.prototype as unknown as Patched;
  const saved: Patched = {};
  for (const method of [...WRITES, ...READS]) {
    const original = proto[method] as Method;
    saved[method] = original;
    proto[method] = async function (this: Collection, ...args: unknown[]) {
      if (nested.getStore() === true) return original.apply(this, args);
      const op = opOf(this, method, args);
      await notify(op, 'before');
      const result = await nested.run(true, () => original.apply(this, args));
      await notify(op, 'after');
      return result;
    };
  }
  for (const method of CURSORS) {
    const original = proto[method] as Method;
    saved[method] = original;
    proto[method] = function (this: Collection, ...args: unknown[]) {
      const cursor = original.apply(this, args) as {
        toArray: () => Promise<unknown[]>;
      };
      if (nested.getStore() === true) return cursor;
      const op = opOf(this, method, args);
      const toArray = cursor.toArray.bind(cursor);
      cursor.toArray = async () => {
        await notify(op, 'before');
        const rows = await nested.run(true, toArray);
        await notify(op, 'after');
        return rows;
      };
      return cursor;
    };
  }
  // A transaction's batch: the client's own command, over every collection it writes.
  const client = MongoClient.prototype as unknown as Patched;
  const batch = client['bulkWrite'] as Method;
  saved[CLIENT_BULK_WRITE] = batch;
  client['bulkWrite'] = async function (this: MongoClient, ...args: unknown[]) {
    if (nested.getStore() === true) return batch.apply(this, args);
    const models = args[0] as Array<{ namespace: string }>;
    const op: StoreOp = {
      collection: 'bulkWrite',
      collections: [...new Set(models.map((model) => model.namespace.slice(model.namespace.indexOf('.') + 1)))],
      method: 'bulkWrite',
      write: true,
      inTransaction: sessionOf(args)?.inTransaction() === true,
      party: parties.getStore() ?? null,
    };
    await notify(op, 'before');
    const result = await nested.run(true, () => batch.apply(this, args));
    await notify(op, 'after');
    return result;
  };
  originals = saved;
}

const CLIENT_BULK_WRITE = 'client.bulkWrite';

function uninstall(): void {
  if (originals === null || probes.size > 0) return;
  const { [CLIENT_BULK_WRITE]: batch, ...collection } = originals;
  Object.assign(Collection.prototype as unknown as Patched, collection);
  (MongoClient.prototype as unknown as Patched)['bulkWrite'] = batch as Method;
  originals = null;
}

/** Adds a probe; the returned function removes it (and the patch, with the last probe). */
export function probeStore(probe: OpProbe): () => void {
  install();
  probes.add(probe);
  return () => {
    probes.delete(probe);
    uninstall();
  };
}

/** Runs `work` as `party`: the probes see its operations under that name. */
export const asParty = <T>(party: string, work: () => Promise<T>): Promise<T> =>
  parties.run(party, work);

/** Where a racer is held: right after the first operation this selects. */
export type Gate = (op: StoreOp) => boolean;

/** The first operation inside a transaction: a snapshot transaction's view is fixed by its first read. */
export const firstTransactionOp: Gate = (op) => op.inTransaction;

/** A gate on one collection's first operation of the given kind, in a transaction or not. */
export const firstOn =
  (collection: string, kind: 'read' | 'write' | 'any' = 'read'): Gate =>
  (op) =>
    op.collection === collection &&
    (kind === 'any' || (kind === 'write') === op.write);

export type RaceOutcome<T> =
  | { party: string; ok: true; value: T }
  | { party: string; ok: false; error: unknown };

/**
 * `together`: every held party goes at once. An array: that order, each
 * party released once the one before it settled (parties not named go last,
 * together).
 */
export type ReleaseOrder = 'together' | readonly string[];

export interface RaceOptions {
  gate?: Gate;
  release?: ReleaseOrder;
}

interface Deferred {
  promise: Promise<void>;
  resolve: () => void;
}

const deferred = (): Deferred => {
  let resolve!: () => void;
  const promise = new Promise<void>((done) => {
    resolve = done;
  });
  return { promise, resolve };
};

/**
 * One race between named parties at the store seam. `run` resolves once
 * every party settled; `held` says which parties the gate actually caught,
 * so a test can prove the interleaving happened rather than assume it.
 */
export class StoreRace {
  readonly held = new Set<string>();
  private readonly passed = new Set<string>();
  private readonly settled = new Set<string>();
  private readonly releases = new Map<string, Deferred>();
  private readonly arrivals = new Map<string, Deferred>();
  private readonly gate: Gate;
  private readonly release: ReleaseOrder;
  private readonly everyoneThere = deferred();

  constructor(
    private readonly names: readonly string[],
    options: RaceOptions = {},
  ) {
    this.gate = options.gate ?? firstTransactionOp;
    this.release = options.release ?? 'together';
    for (const name of names) {
      this.releases.set(name, deferred());
      this.arrivals.set(name, deferred());
    }
  }

  /** Resolves once `party` is held at the gate: a party's work may wait for another's to get there. */
  reached(party: string): Promise<void> {
    const arrival = this.arrivals.get(party);
    if (arrival === undefined) throw new Error(`No party ${party}`);
    return arrival.promise;
  }

  async run<T>(
    work: Readonly<Record<string, () => Promise<T>>>,
  ): Promise<Array<RaceOutcome<T>>> {
    const remove = probeStore(async (op, phase) => {
      const party = op.party;
      if (phase !== (op.write ? 'before' : 'after') || party === null) return;
      if (!this.releases.has(party) || this.passed.has(party)) return;
      if (!this.gate(op)) return;
      this.passed.add(party);
      this.held.add(party);
      this.arrivals.get(party)?.resolve();
      this.checkEveryone();
      await this.releases.get(party)?.promise;
    });
    try {
      const running = this.names.map((name) =>
        asParty(name, async (): Promise<RaceOutcome<T>> => {
          const job = work[name];
          if (job === undefined) throw new Error(`No work for ${name}`);
          try {
            return { party: name, ok: true, value: await job() };
          } catch (error) {
            return { party: name, ok: false, error };
          } finally {
            this.passed.add(name);
            this.settled.add(name);
            this.checkEveryone();
          }
        }),
      );
      void this.sequence(running);
      return await Promise.all(running);
    } finally {
      remove();
    }
  }

  private checkEveryone(): void {
    if (this.names.every((name) => this.passed.has(name))) {
      this.everyoneThere.resolve();
    }
  }

  private async sequence(
    running: ReadonlyArray<Promise<RaceOutcome<unknown>>>,
  ): Promise<void> {
    await this.everyoneThere.promise;
    if (this.release === 'together') {
      for (const gate of this.releases.values()) gate.resolve();
      return;
    }
    const order = [
      ...this.release,
      ...this.names.filter((name) => !this.release.includes(name)),
    ];
    for (const name of order) {
      this.releases.get(name)?.resolve();
      const index = this.names.indexOf(name);
      if (index >= 0 && this.release.includes(name)) {
        await running[index];
      }
    }
  }
}

/** The error a failpoint throws: a transient one is retried by `withTransaction`. */
export function injectedFailure(transient: boolean): Error {
  const error = new MongoServerError({
    message: transient
      ? 'injected transient transaction failure'
      : 'injected failure',
    code: transient ? 112 : 8000,
    codeName: transient ? 'WriteConflict' : 'AtlasError',
  });
  if (transient) error.addErrorLabel('TransientTransactionError');
  return error;
}

export interface Failpoint {
  /** In-transaction operations the party issued while armed. */
  readonly ops: StoreOp[];
  /** Whether it fired. */
  readonly fired: boolean;
  disarm(): void;
}

/**
 * Throws `error` at `party`'s `nth` (1-based) operation inside a
 * transaction, before or after that operation ran; once.
 */
export function failAt(
  party: string,
  nth: number,
  phase: OpPhase,
  error: () => Error,
): Failpoint {
  const ops: StoreOp[] = [];
  let fired = false;
  const remove = probeStore((op, at) => {
    if (op.party !== party || !op.inTransaction) return;
    if (at === 'before') ops.push(op);
    if (fired || at !== phase || ops.length !== nth) return;
    fired = true;
    throw error();
  });
  return {
    ops,
    get fired() {
      return fired;
    },
    disarm: remove,
  };
}
