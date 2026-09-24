/**
 * Configuration types for the Redis connection-provider layer.
 *
 * Kept free of any `ioredis` import so it can be constructed by callers
 * (config loaders, tests) that never touch a client directly.
 */

export type ScaleReads = 'master' | 'slave' | 'all';

export interface Credentials {
  username?: string;
  password: string;
}

/** Portable client knobs; each provider maps these onto its own client (R12). */
export interface ClientOptions {
  /** Long-lived connection (BLOCKING XREADGROUP, BullMQ) that must not be
   * reclaimed by a bounded/shared pool. */
  blocking?: boolean;
  connectTimeoutMs?: number;
  maxRetriesPerRequest?: number;
  enableOfflineQueue?: boolean;
}

export const DEFAULT_CLIENT_OPTIONS: Required<ClientOptions> = {
  blocking: false,
  connectTimeoutMs: 10000,
  maxRetriesPerRequest: 3,
  enableOfflineQueue: true,
};

/**
 * Everything a connection provider needs to build clients.
 *
 * `db` is DEPRECATED (R4): honoured only by `StandaloneRedisProvider`. The
 * factory rejects `db !== 0` when the selected mode is not `standalone` so
 * an upgrade never silently falls back to an empty database. New
 * deployments should isolate tenants via `keyNamespace` instead, applied
 * inside explicit key builders (R9) -- never as an ioredis `keyPrefix`.
 */
export interface RedisConnectionConfig {
  host: string;
  port: number;
  username?: string;
  password?: string;
  /** Rotating credentials (e.g. MemoryDB IAM auth); takes precedence over
   * username/password when set (R21). Signature intentionally stable so an
   * EE provider never needs an interface change to support IAM rotation. */
  credentialsProvider?: () => Promise<Credentials>;
  tls: boolean;
  tlsRejectUnauthorized: boolean;
  tlsCaPath?: string;
  db: number;
  keyNamespace: string;
  connectTimeoutMs: number;
  /** Cluster-specific; ignored by StandaloneRedisProvider (R21). */
  clusterEndpoints: string[];
  scaleReads: ScaleReads;
  /** Cluster-only. Maps an advertised `host:port` to the address it is
   * actually reachable at (ioredis `ClusterOptions.natMap`); ignored by
   * StandaloneRedisProvider (R21). Kubernetes-fronted self-hosted clusters
   * need this as much as MemoryDB's private-VPC addressing does. */
  natMap?: Record<string, { host: string; port: number }>;
}

function parseClusterEndpoints(raw: string | undefined): string[] {
  if (!raw) {
    return [];
  }
  return raw
    .split(',')
    .map((e) => e.trim())
    .filter((e) => e.length > 0);
}

/**
 * Parse `internalHost:internalPort=externalHost:externalPort,...` (R3, F3)
 * into ioredis' `NatMap` shape. Kept free of any `ioredis` import: the type
 * shape (`{ [key: string]: { host, port } }`) is stable and duplicating it
 * here avoids pulling the client library into a config module every caller
 * (including tests that never touch a client) imports.
 */
function parseNatMap(
  raw: string | undefined,
): Record<string, { host: string; port: number }> | undefined {
  if (!raw) {
    return undefined;
  }
  const natMap: Record<string, { host: string; port: number }> = {};
  for (const rawEntry of raw.split(',')) {
    const entry = rawEntry.trim();
    if (!entry) {
      continue;
    }
    const eqIndex = entry.indexOf('=');
    if (eqIndex === -1) {
      throw new Error(
        `Invalid REDIS_CLUSTER_NAT_MAP entry '${entry}'; expected ` +
          "'internalHost:internalPort=externalHost:externalPort'.",
      );
    }
    const internal = entry.slice(0, eqIndex).trim();
    const external = entry.slice(eqIndex + 1).trim();
    const errMsg =
      `Invalid REDIS_CLUSTER_NAT_MAP entry '${entry}'; expected ` +
      "'internalHost:internalPort=externalHost:externalPort'.";

    const internalColonIndex = internal.lastIndexOf(':');
    const internalHost = internalColonIndex === -1 ? '' : internal.slice(0, internalColonIndex);
    const internalPortStr = internalColonIndex === -1 ? '' : internal.slice(internalColonIndex + 1);
    if (!internalHost || !/^\d+$/.test(internalPortStr)) {
      throw new Error(errMsg);
    }
    const internalPort = parseInt(internalPortStr, 10);
    if (internalPort < 1 || internalPort > 65535) {
      throw new Error(errMsg);
    }

    const colonIndex = external.lastIndexOf(':');
    const externalHost = colonIndex === -1 ? '' : external.slice(0, colonIndex);
    const externalPortStr = colonIndex === -1 ? '' : external.slice(colonIndex + 1);
    if (!externalHost || !/^\d+$/.test(externalPortStr)) {
      throw new Error(errMsg);
    }
    const externalPort = parseInt(externalPortStr, 10);
    if (externalPort < 1 || externalPort > 65535) {
      throw new Error(errMsg);
    }
    natMap[internal] = { host: externalHost, port: externalPort };
  }
  return Object.keys(natMap).length > 0 ? natMap : undefined;
}

/**
 * Accepts the spellings operators actually write. Matching only `'true'` meant
 * `REDIS_TLS_ENABLED=1` silently produced a *plaintext* connection still
 * carrying the Redis password -- a failure with no error to notice.
 */
const TRUTHY = new Set(['true', '1', 'yes', 'on']);
const FALSY = new Set(['false', '0', 'no', 'off']);

/**
 * Throws on an unrecognized non-empty value rather than falling back.
 *
 * These two settings decide whether a credential-bearing Redis connection is
 * encrypted and whether its certificate is checked, and a typo has no safe
 * reading: `REDIS_TLS_ENABLED=ture` would silently mean plaintext, and
 * `REDIS_TLS_REJECT_UNAUTHORIZED=yse` would silently mean unverified. Refusing
 * to start beats guessing either one.
 */
function truthyEnv(
  name: string,
  value: string | undefined,
  fallback: boolean,
): boolean {
  const normalized = value?.trim().toLowerCase();
  if (normalized === undefined || normalized === '') {
    return fallback;
  }
  if (TRUTHY.has(normalized)) {
    return true;
  }
  if (FALSY.has(normalized)) {
    return false;
  }
  throw new Error(
    `${name}='${value}' is not a valid boolean. Use one of ` +
      `${[...TRUTHY].join('/')} or ${[...FALSY].join('/')}. Refusing to guess: ` +
      'this setting controls whether the Redis connection is encrypted and ' +
      'verified.',
  );
}

/** Build config from the standard `REDIS_*` environment variables. */
export function redisConnectionConfigFromEnv(
  prefix = 'REDIS_',
): RedisConnectionConfig {
  const env = process.env;
  const password = env[`${prefix}PASSWORD`];
  return {
    host: env[`${prefix}HOST`] || 'localhost',
    port: parseInt(env[`${prefix}PORT`] || '6379', 10),
    username: env[`${prefix}USERNAME`] || undefined,
    password: password && password.trim() !== '' ? password : undefined,
    // Report whichever name actually supplied the value, so the error names
    // the variable the operator set (TLS_ENABLED or the legacy TLS alias).
    tls: truthyEnv(
      env[`${prefix}TLS_ENABLED`] !== undefined
        ? `${prefix}TLS_ENABLED`
        : `${prefix}TLS`,
      env[`${prefix}TLS_ENABLED`] ?? env[`${prefix}TLS`],
      false,
    ),
    tlsRejectUnauthorized: truthyEnv(
      `${prefix}TLS_REJECT_UNAUTHORIZED`,
      env[`${prefix}TLS_REJECT_UNAUTHORIZED`],
      true,
    ),
    tlsCaPath: env[`${prefix}TLS_CA_PATH`] || undefined,
    db: parseInt(env[`${prefix}DB`] || '0', 10),
    keyNamespace: env[`${prefix}KEY_NAMESPACE`] || '',
    connectTimeoutMs: parseInt(env[`${prefix}TIMEOUT`] || '10000', 10),
    clusterEndpoints: parseClusterEndpoints(env[`${prefix}CLUSTER_ENDPOINTS`]),
    scaleReads: (env[`${prefix}CLUSTER_SCALE_READS`] as ScaleReads) || 'master',
    natMap: parseNatMap(env[`${prefix}CLUSTER_NAT_MAP`]),
  };
}

/**
 * Adapt the legacy `host`/`port`/`password`/`db` shape used throughout the
 * KV store, messaging, and cache modules (`RedisConfig`,
 * `RedisBrokerConfig`) into a full connection config. Process-wide settings
 * that shape predates -- TLS, cluster endpoints, key namespace -- are
 * layered on top from the environment, since every one of those call sites
 * talks to the same Redis deployment as everything else in the process.
 */
export function redisConnectionConfigFromHostPort(params: {
  host: string;
  port: number;
  password?: string;
  db?: number;
  username?: string;
  /**
   * TLS as recorded in the stored (admin-UI) Redis config. OR-ed with
   * `REDIS_TLS_ENABLED` rather than replacing it: an install that turned TLS
   * on through the UI has no env var set, and dropping its flag would
   * downgrade it to plaintext on upgrade. The env still supplies the CA path
   * and `rejectUnauthorized`, which the stored config has no field for.
   */
  tls?: boolean;
}): RedisConnectionConfig {
  const base = redisConnectionConfigFromEnv();
  return {
    ...base,
    host: params.host,
    port: params.port,
    // `??`, not bare assignment, for both credentials: a caller that omits
    // one would otherwise null the value `REDIS_PASSWORD` / `REDIS_USERNAME`
    // supplied. Callers such as `RedisDistributedKeyValueStore` and
    // `streamsProvider` pass only the stored admin-UI fields, so a deployment
    // that provides credentials through the environment would lose them and
    // fail with NOAUTH.
    password: params.password ?? base.password,
    username: params.username ?? base.username,
    db: params.db ?? 0,
    tls: base.tls || params.tls === true,
  };
}
