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
  getBuffer(_key: string) { return Promise.resolve(null); }
  scan(..._args: any[]) { return Promise.resolve(['0', []]); }
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
// Patch require.cache so every subsequent `require('ioredis')` or
// `import { Redis } from 'ioredis'` gets FakeRedis.
// ---------------------------------------------------------------------------
const ioredisPath = require.resolve('ioredis');

// Force-load the real module first (so require.cache has an entry),
// then overwrite its exports.
try { require(ioredisPath); } catch { /* ignore */ }

const cached = require.cache[ioredisPath];
if (cached) {
  cached.exports = { Redis: FakeRedis, default: FakeRedis };
} else {
  // Shouldn't happen, but just in case — create a synthetic entry
  require.cache[ioredisPath] = {
    id: ioredisPath,
    filename: ioredisPath,
    loaded: true,
    exports: { Redis: FakeRedis, default: FakeRedis },
    children: [],
    paths: [],
    parent: null,
  } as any;
}
