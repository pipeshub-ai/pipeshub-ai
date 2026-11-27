/**
 * The erasure census (QA-15, ERA-19): after a session is erased, every store
 * is scanned for anything of it (its ids, and a canary planted in its
 * content), whatever wrote it, and what was found is judged against
 * SESSION_DATA_STORES. A store the table does not list, or one whose step
 * should have erased the session, is a leak; a store that keeps ids on
 * purpose says so (`kept`, or a step's `residue`). The canary is a leak
 * anywhere.
 */
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';
import type { Db } from 'mongodb';
// bson is the driver's own dependency (nested under mongodb), so EJSON comes through the driver, a direct dependency.
import { BSON } from 'mongodb';

const { EJSON } = BSON;
import type { Redis } from 'ioredis';
import {
  SESSION_DATA_STORES,
  SessionDataStore,
} from '../../../../src/modules/agent-harness/domain/session-erasure';
import { channelStreamKey } from '../../../../src/modules/agent-harness/adapters/redis/redis-live-bus';
import { ACTIVITY_LIVE_CHANNEL } from '../../../../src/modules/agent-harness/projection/activity';
import { CHAT_LIVE_CHANNEL } from '../../../../src/modules/agent-harness/projection/frames';

export interface Needle {
  kind: 'id' | 'canary';
  value: string;
}

/** One place a needle was found: the store, and the document, key or file it was in. */
export interface Trace {
  store: string;
  at: string;
  needle: Needle;
}

export type LeakReason = 'unlisted' | 'not_erased' | 'canary';

export interface Leak extends Trace {
  reason: LeakReason;
}

export interface CensusVerdict {
  leaks: Leak[];
  /** Ids a store keeps on purpose (`kept` or `residue`). */
  kept: Trace[];
  /** Traces in a store whose erasure is a recorded open item. */
  deferred: Trace[];
}

export const needlesOf = (
  ids: readonly string[],
  canaries: readonly string[],
): Needle[] => [
  ...ids.map((value) => ({ kind: 'id' as const, value })),
  ...canaries.map((value) => ({ kind: 'canary' as const, value })),
];

const found = (
  text: string,
  needles: readonly Needle[],
  store: string,
  at: string,
): Trace[] =>
  needles
    .filter((needle) => text.includes(needle.value))
    .map((needle) => ({ store, at, needle }));

/** The dotted paths of `value` whose key or text holds `needle`. */
function pathsHolding(value: unknown, needle: string, path = ''): string[] {
  if (typeof value === 'string') return value.includes(needle) ? [path] : [];
  if (value === null || typeof value !== 'object') return [];
  return Object.entries(value as Record<string, unknown>).flatMap(([key, inner]) => {
    const at = path === '' ? key : `${path}.${key}`;
    return key.includes(needle) ? [at] : pathsHolding(inner, needle, at);
  });
}

/** Every document of every collection of the database (as relaxed extended JSON), with the fields that hold a needle. */
export async function scanMongo(
  db: Db,
  needles: readonly Needle[],
): Promise<Trace[]> {
  const traces: Trace[] = [];
  for (const { name } of await db.listCollections({}, { nameOnly: true }).toArray()) {
    if (name.startsWith('system.')) continue;
    for await (const document of db.collection(name).find({})) {
      const plain: unknown = EJSON.serialize(document, { relaxed: true });
      for (const needle of needles) {
        const paths = pathsHolding(plain, needle.value);
        if (paths.length > 0) {
          traces.push({ store: name, at: `${String(document['_id'])} ${paths.join(',')}`, needle });
        }
      }
    }
  }
  return traces;
}

/**
 * Redis keys by the store they belong to. A key of no listed family is its
 * own store, so it is `unlisted` when it holds anything of the session.
 */
export const REDIS_FAMILIES: ReadonlyArray<{ prefix: string; store: string }> = [
  // Both live channels (the chat's deltas and UX5's command output) are one store, erased together.
  { prefix: channelStreamKey(CHAT_LIVE_CHANNEL, ''), store: 'liveBus' },
  { prefix: channelStreamKey(ACTIVITY_LIVE_CHANNEL, ''), store: 'liveBus' },
  { prefix: 'harness:spend:', store: 'spendCounters' },
];

const redisStore = (key: string, namespace: string): string => {
  const bare = namespace !== '' && key.startsWith(`${namespace}:`) ? key.slice(namespace.length + 1) : key;
  return REDIS_FAMILIES.find((family) => bare.startsWith(family.prefix))?.store ?? `redis:${bare}`;
};

async function redisValue(client: Redis, key: string): Promise<string> {
  switch (await client.type(key)) {
    case 'string':
      return (await client.get(key)) ?? '';
    case 'hash':
      return JSON.stringify(await client.hgetall(key));
    case 'list':
      return JSON.stringify(await client.lrange(key, 0, -1));
    case 'set':
      return JSON.stringify(await client.smembers(key));
    case 'zset':
      return JSON.stringify(await client.zrange(key, 0, -1));
    case 'stream':
      return JSON.stringify(await client.xrange(key, '-', '+'));
    default:
      return '';
  }
}

async function scanKeys(client: Redis, match: string): Promise<string[]> {
  const keys: string[] = [];
  let cursor = '0';
  do {
    const [next, batch] = await client.scan(cursor, 'MATCH', match, 'COUNT', 1000);
    keys.push(...batch);
    cursor = next;
  } while (cursor !== '0');
  return keys;
}

/**
 * Keys naming the session (by any id needle) and every key of the harness's
 * families, with their values. The Redis may be shared with other tests, so
 * keys are found by pattern, not by listing the whole keyspace.
 */
export async function scanRedis(
  client: Redis,
  namespace: string,
  needles: readonly Needle[],
): Promise<Trace[]> {
  const prefix = namespace === '' ? '' : `${namespace}:`;
  const patterns = [
    ...needles.filter((n) => n.kind === 'id').map((n) => `*${n.value}*`),
    ...REDIS_FAMILIES.map((family) => `${prefix}${family.prefix}*`),
  ];
  const keys = new Set<string>();
  for (const pattern of patterns) {
    for (const key of await scanKeys(client, pattern)) keys.add(key);
  }
  const traces: Trace[] = [];
  for (const key of keys) {
    const text = `${key}\n${await redisValue(client, key)}`;
    traces.push(...found(text, needles, redisStore(key, namespace), key));
  }
  return traces;
}

/** Every path and file under `root` (a sandbox broker's data volume). */
export function scanFolder(
  root: string,
  store: string,
  needles: readonly Needle[],
): Trace[] {
  const traces: Trace[] = [];
  const walk = (directory: string): void => {
    for (const name of readdirSync(directory)) {
      const path = join(directory, name);
      const at = relative(root, path);
      const isDirectory = statSync(path).isDirectory();
      const text = isDirectory ? at : `${at}\n${readFileSync(path, 'utf8')}`;
      traces.push(...found(text, needles, store, at));
      if (isDirectory) walk(path);
    }
  };
  walk(root);
  return traces;
}

/** What a census found, judged against the coverage table. */
export function judgeCensus(
  traces: readonly Trace[],
  stores: readonly SessionDataStore[] = SESSION_DATA_STORES,
): CensusVerdict {
  const verdict: CensusVerdict = { leaks: [], kept: [], deferred: [] };
  const byName = new Map(stores.map((entry) => [entry.store, entry]));
  for (const trace of traces) {
    const entry = byName.get(trace.store);
    if (trace.needle.kind === 'canary') {
      verdict.leaks.push({ ...trace, reason: 'canary' });
    } else if (entry === undefined) {
      verdict.leaks.push({ ...trace, reason: 'unlisted' });
    } else if ('deferred' in entry.coverage) {
      verdict.deferred.push(trace);
    } else if ('kept' in entry.coverage || entry.residue !== undefined) {
      verdict.kept.push(trace);
    } else {
      verdict.leaks.push({ ...trace, reason: 'not_erased' });
    }
  }
  return verdict;
}

/** A leak as one line a failing assertion can print: store, document and what was in it. */
export const describeLeak = (leak: Leak): string =>
  `${leak.reason}: ${leak.store} ${leak.at} holds ${leak.needle.kind} ${leak.needle.value}`;
