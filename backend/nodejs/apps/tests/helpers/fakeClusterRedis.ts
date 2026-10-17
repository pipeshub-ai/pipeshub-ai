/**
 * In-memory Redis Cluster double for unit tests (R5, R17).
 *
 * A minimal hand-rolled Redis command surface, enforcing the one rule real
 * Redis Cluster / AWS MemoryDB enforce that the plain `FakeRedis` in
 * `mock-ioredis-global.ts` does not: a command touching more than one key
 * must have every key land in the same hash slot, or the cluster node
 * itself rejects it with CROSSSLOT. `cluster-key-slot` (already a
 * transitive `ioredis` dependency, promoted to an explicit devDependency
 * for this file) computes the slot exactly the way `ioredis.Cluster` and
 * `redis.crc.key_slot` (Python) do, including `{hashtag}` support.
 *
 * This is what makes a unit test fail if shared Node.js code regresses back
 * to a multi-key command that only breaks in production against a real
 * cluster or MemoryDB endpoint.
 *
 * Two instances built with the same backing `FakeClusterStore` (via the
 * `store` constructor option) behave like two client connections to one
 * real server -- mirrors `IRedisConnectionProvider.getClient()` and
 * `.createClient()` handing out different objects for the same endpoint.
 *
 * Usage:
 * ```ts
 * const fake = new FakeClusterRedis();
 * await fake.set('a', '1');
 * await fake.set('b', '2');
 * await fake.mget(['a', 'b']); // rejects with a CrossSlotError
 * ```
 */
import { EventEmitter } from 'events';

import calcSlot from 'cluster-key-slot';

export class CrossSlotError extends Error {
  constructor(keys: string[], slots: number[]) {
    super(
      `CROSSSLOT Keys in request don't hash to the same slot: ${JSON.stringify(
        keys,
      )} -> slots [${slots.join(', ')}]`,
    );
    this.name = 'CrossSlotError';
  }
}

function assertSameSlot(keys: string[]): void {
  if (keys.length <= 1) {
    return;
  }
  const slots = Array.from(new Set(keys.map((key) => calcSlot(key))));
  if (slots.length > 1) {
    throw new CrossSlotError(keys, slots);
  }
}

/** Backing dataset, shareable across several `FakeClusterRedis` instances. */
export class FakeClusterStore {
  readonly strings = new Map<string, string>();
  readonly hashes = new Map<string, Map<string, string>>();
  readonly scripts = new Map<string, string>();
}

interface FakePipelineCommand {
  name: string;
  args: unknown[];
}

class FakeClusterPipeline {
  private readonly commands: FakePipelineCommand[] = [];

  constructor(private readonly client: FakeClusterRedis) {}

  private queue(name: string, ...args: unknown[]): this {
    this.commands.push({ name, args });
    return this;
  }

  get(...args: unknown[]): this {
    return this.queue('get', ...args);
  }

  set(...args: unknown[]): this {
    return this.queue('set', ...args);
  }

  del(...args: unknown[]): this {
    return this.queue('del', ...args);
  }

  hget(...args: unknown[]): this {
    return this.queue('hget', ...args);
  }

  hset(...args: unknown[]): this {
    return this.queue('hset', ...args);
  }

  /**
   * Each queued command is dispatched independently -- exactly like a real
   * `ClusterPipeline`, which routes every command to the node owning its
   * own key(s) rather than requiring the whole batch to share a slot.
   */
  async exec(): Promise<Array<[Error | null, unknown]>> {
    const results: Array<[Error | null, unknown]> = [];
    for (const { name, args } of this.commands) {
      try {
        const value = await (this.client as any)[name](...args);
        results.push([null, value]);
      } catch (error) {
        results.push([error as Error, null]);
      }
    }
    return results;
  }
}

/** Same command surface, without the `[error, value]` tuple wrapping --
 * matches `ioredis`'s non-transactional pipeline result shape used by
 * callers that pass `{ transaction: false }`-style plain arrays. */
class FakeClusterMulti extends FakeClusterPipeline {}

export class FakeClusterRedis extends EventEmitter {
  status = 'ready';

  private readonly store: FakeClusterStore;

  constructor(store: FakeClusterStore = new FakeClusterStore()) {
    super();
    this.store = store;
    process.nextTick(() => {
      this.emit('connect');
      this.emit('ready');
    });
  }

  /** Pass to `new FakeClusterRedis(store)` so both wrap the same dataset. */
  get sharedStore(): FakeClusterStore {
    return this.store;
  }

  keySlot(key: string): number {
    return calcSlot(key);
  }

  async get(key: string): Promise<string | null> {
    return this.store.strings.get(key) ?? null;
  }

  async set(key: string, value: string): Promise<'OK'> {
    this.store.strings.set(key, value);
    return 'OK';
  }

  async del(...keys: string[]): Promise<number> {
    assertSameSlot(keys);
    let deleted = 0;
    for (const key of keys) {
      if (this.store.strings.delete(key) || this.store.hashes.delete(key)) {
        deleted += 1;
      }
    }
    return deleted;
  }

  async mget(keys: string[]): Promise<Array<string | null>> {
    assertSameSlot(keys);
    return keys.map((key) => this.store.strings.get(key) ?? null);
  }

  async exists(...keys: string[]): Promise<number> {
    assertSameSlot(keys);
    return keys.filter(
      (key) => this.store.strings.has(key) || this.store.hashes.has(key),
    ).length;
  }

  async incr(key: string): Promise<number> {
    const current = Number(this.store.strings.get(key) ?? '0') + 1;
    this.store.strings.set(key, String(current));
    return current;
  }

  async expire(_key: string, _seconds: number): Promise<number> {
    return 1;
  }

  async hget(key: string, field: string): Promise<string | null> {
    return this.store.hashes.get(key)?.get(field) ?? null;
  }

  async hset(key: string, field: string, value: string): Promise<number> {
    let hash = this.store.hashes.get(key);
    if (!hash) {
      hash = new Map();
      this.store.hashes.set(key, hash);
    }
    const isNew = !hash.has(field);
    hash.set(field, value);
    return isNew ? 1 : 0;
  }

  async hdel(key: string, field: string): Promise<number> {
    return this.store.hashes.get(key)?.delete(field) ? 1 : 0;
  }

  /** Single-node SCAN (R17): sufficient for exercising `scanKeys` fan-out
   * logic in tests, which only needs the returned keys to be correct, not
   * genuine multi-node cursor semantics. */
  async scan(
    cursor: string,
    _match: string,
    pattern: string,
  ): Promise<[string, string[]]> {
    if (cursor !== '0') {
      return ['0', []];
    }
    const regex = new RegExp(`^${pattern.replace(/\*/g, '.*')}$`);
    const keys = [...this.store.strings.keys(), ...this.store.hashes.keys()].filter(
      (key) => regex.test(key),
    );
    return ['0', keys];
  }

  async script(action: string, body: string): Promise<string> {
    if (action.toUpperCase() !== 'LOAD') {
      throw new Error(`FakeClusterRedis.script only supports LOAD, got ${action}`);
    }
    const sha = `sha-${this.store.scripts.size}-${body.length}`;
    this.store.scripts.set(sha, body);
    return sha;
  }

  async eval(_script: string, numkeys: number, ...rest: unknown[]): Promise<unknown> {
    assertSameSlot(rest.slice(0, numkeys) as string[]);
    return null;
  }

  async evalsha(sha: string, numkeys: number, ...rest: unknown[]): Promise<unknown> {
    assertSameSlot(rest.slice(0, numkeys) as string[]);
    if (!this.store.scripts.has(sha)) {
      const error = new Error(`NOSCRIPT No matching script. Please use EVAL.`);
      error.name = 'NOSCRIPT';
      throw error;
    }
    return null;
  }

  pipeline(_options?: { transaction?: boolean }): FakeClusterPipeline {
    return new FakeClusterPipeline(this);
  }

  multi(_options?: { transaction?: boolean }): FakeClusterMulti {
    return new FakeClusterMulti(this);
  }

  async ping(): Promise<'PONG'> {
    return 'PONG';
  }

  async quit(): Promise<'OK'> {
    return 'OK';
  }

  async disconnect(): Promise<void> {}

  nodes(_role?: 'master' | 'all' | 'slave'): FakeClusterRedis[] {
    return [this];
  }

  defineCommand(name: string, _definition?: unknown): void {
    (this as any)[name] = async () => null;
  }
}
