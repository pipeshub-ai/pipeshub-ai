/**
 * Global ioredis mock — loaded via .mocharc.yaml `require` BEFORE any test file.
 *
 * Replaces the real ioredis Redis constructor with a fake EventEmitter-based
 * class so that `new Redis(...)` never opens a real TCP connection.
 *
 * Individual test files that need finer control (e.g. redis.service.test.ts)
 * can still override require.cache[ioredisPath] in their own beforeEach;
 * this file simply acts as the safety net for every other file that
 * transitively imports ioredis.
 */

import { EventEmitter } from 'events';

// ---------------------------------------------------------------------------
// Fake Redis class
// ---------------------------------------------------------------------------
class FakeRedis extends EventEmitter {
  status = 'ready';

  // Common commands — return sensible no-op values
  get(_key: string) { return Promise.resolve(null); }
  set(..._args: any[]) { return Promise.resolve('OK'); }
  del(..._args: any[]) { return Promise.resolve(1); }
  exists(..._args: any[]) { return Promise.resolve(0); }
  incr(_key: string) { return Promise.resolve(1); }
  expire(..._args: any[]) { return Promise.resolve(1); }
  hset(..._args: any[]) { return Promise.resolve(1); }
  hget(..._args: any[]) { return Promise.resolve(null); }
  hgetall(_key: string) { return Promise.resolve({}); }
  hdel(..._args: any[]) { return Promise.resolve(1); }
  eval(..._args: any[]) { return Promise.resolve(null); }
  quit() { return Promise.resolve('OK'); }
  disconnect() { return Promise.resolve(); }
  // Needed by redis-streams.service.ts, which uses `lazyConnect: true` and
  // explicitly awaits `redis.connect()` before treating the client as ready.
  connect() { return Promise.resolve(); }
  getBuffer(_key: string) { return Promise.resolve(null); }
  scan(..._args: any[]) { return Promise.resolve(['0', []]); }
  script(..._args: any[]) { return Promise.resolve('fakesha0000000000000000000000000000000'); }
  watch(..._args: any[]) { return Promise.resolve(); }
  unwatch() { return Promise.resolve(); }
  multi() {
    const chain: any = {
      set: () => chain,
      del: () => chain,
      exec: () => Promise.resolve([]),
    };
    return chain;
  }
  ping() { return Promise.resolve('PONG'); }
  publish(..._args: any[]) { return Promise.resolve(0); }
  subscribe(..._args: any[]) { return Promise.resolve(); }
  // ioredis dynamically attaches a method per `defineCommand(name, def)` call
  // (custom Lua commands). BullMQ's `RedisConnection.init()` calls this on
  // every client it is handed, including ones we construct via the
  // connection provider, so the fake must support it too.
  defineCommand(name: string, _definition?: any) {
    (this as any)[name] = (..._args: any[]) => Promise.resolve(null);
  }

  constructor(_options?: any) {
    super();
    // Emit connect/ready on next tick so event listeners registered in the
    // same constructor call (e.g. RedisService.initializeClient) can fire.
    process.nextTick(() => {
      this.emit('connect');
      this.emit('ready');
    });
  }
}

// ---------------------------------------------------------------------------
// Fake Cluster class
//
// A single-node stand-in for `ioredis.Cluster` so `ClusterRedisProvider`
// (backend/nodejs/apps/src/libs/services/redis/clusterRedisProvider.ts) can
// be constructed in unit tests without a real 3-master cluster. It is
// intentionally NOT slot-aware; tests that need CROSSSLOT / multi-node
// fan-out behaviour use `tests/helpers/fakeClusterRedis.ts` instead, which
// wraps several of these nodes.
// ---------------------------------------------------------------------------
class FakeCluster extends EventEmitter {
  status = 'ready';
  private readonly node = new FakeRedis();

  constructor(_startupNodes?: any[], _options?: any) {
    super();
    process.nextTick(() => {
      this.emit('connect');
      this.emit('ready');
    });
  }

  nodes(_role?: 'master' | 'all' | 'slave') {
    return [this.node];
  }

  get(...args: any[]) { return (this.node.get as any)(...args); }
  set(...args: any[]) { return (this.node.set as any)(...args); }
  del(...args: any[]) { return (this.node.del as any)(...args); }
  scan(...args: any[]) { return (this.node.scan as any)(...args); }
  script(...args: any[]) { return (this.node as any).script?.(...args) ?? Promise.resolve('fakesha'); }
  ping() { return Promise.resolve('PONG'); }
  quit() { return Promise.resolve('OK'); }
  disconnect() { return Promise.resolve(); }
  defineCommand(name: string, definition?: any) {
    this.node.defineCommand(name, definition);
    (this as any)[name] = (...args: any[]) => (this.node as any)[name](...args);
  }
}

// ---------------------------------------------------------------------------
// Patch require.cache so every subsequent `require('ioredis')` or
// `import { Redis, Cluster } from 'ioredis'` gets the fakes.
// ---------------------------------------------------------------------------
const ioredisPath = require.resolve('ioredis');

// Force-load the real module first (so require.cache has an entry),
// then overwrite its exports.
try { require(ioredisPath); } catch { /* ignore */ }

const fakeExports = { Redis: FakeRedis, Cluster: FakeCluster, default: FakeRedis };

/**
 * The real `ioredis` exports, captured before they are overwritten below.
 *
 * The cluster integration spec needs an actual client to talk to the live
 * 3-master cluster; without this the global fake would silently answer every
 * command in-process and the suite would pass against nothing.
 */
export const realIoredis = require.cache[require.resolve('ioredis')]?.exports;

const cached = require.cache[ioredisPath];
if (cached) {
  cached.exports = fakeExports;
} else {
  // Shouldn't happen, but just in case — create a synthetic entry
  require.cache[ioredisPath] = {
    id: ioredisPath,
    filename: ioredisPath,
    loaded: true,
    exports: fakeExports,
    children: [],
    paths: [],
    parent: null,
  } as any;
}
