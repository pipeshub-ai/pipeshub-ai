import net from 'net';
import tls from 'tls';
import { readFileSync } from 'fs';
import { hardenedProfile } from '../utils/security-profile';

/**
 * SEC-D-06: every store the app uses must refuse a client without a credential. Compose and Helm start the bundled
 * stores with one; this check makes it a property of the product, so an external store, an old .env or a
 * hand-written deployment cannot quietly leave one open. Before any client connects, the app reads which stores
 * are active, checks each has its credential configured, and asks Redis and Qdrant anonymously whether they answer.
 *
 * STORE_AUTH_CHECK=warn logs the same findings instead of refusing to start, for a store protected some other way
 * (a private network a managed service cannot add a login to); `enforce` refuses. Unset follows the security profile.
 */

export type StoreAuthMode = 'enforce' | 'warn';

export interface StoreAuthFinding {
  store:
    'redis' | 'qdrant' | 'mongodb' | 'neo4j' | 'arangodb' | 'etcd' | 'kafka';
  /** What is wrong, and what to set. */
  problem: string;
}

/** An anonymous probe: true when the store answered without a credential, false when it refused, null unknown. */
export type AnonymousProbe = () => Promise<boolean | null>;

export interface StoreAuthProbes {
  redis?: AnonymousProbe;
  qdrant?: AnonymousProbe;
}

type Env = Record<string, string | undefined>;

const PROBE_TIMEOUT_MS = 3000;

/**
 * `STORE_AUTH_CHECK=warn|enforce` decides; unset follows the security profile (ADR-OPS-07): a release without SEC-D-06
 * ran its bundled stores without a login, so the compatibility profile warns and the hardened one refuses.
 */
export function storeAuthMode(env: Env): StoreAuthMode {
  const explicit = (env.STORE_AUTH_CHECK ?? '').trim().toLowerCase();
  if (explicit === 'warn') return 'warn';
  if (explicit === 'enforce') return 'enforce';
  return hardenedProfile(env) ? 'enforce' : 'warn';
}

const empty = (value: string | undefined): boolean => !value?.trim();

function mongoUriLacksCredential(uri: string): boolean {
  const match = /^mongodb(?:\+srv)?:\/\/([^/?#]*)/i.exec(uri.trim());
  if (!match) return false;
  const authority = match[1] ?? '';
  // X.509, AWS IAM and Kerberos sign in without a password in the URI.
  if (/[?&]authMechanism=/i.test(uri)) return false;
  const at = authority.lastIndexOf('@');
  if (at < 0) return true;
  const userinfo = authority.slice(0, at);
  const colon = userinfo.indexOf(':');
  return colon < 0 || colon === userinfo.length - 1 || colon === 0;
}

/** The configuration half: each active store has the credential its client needs. */
export function configuredStoreAuthFindings(env: Env): StoreAuthFinding[] {
  const findings: StoreAuthFinding[] = [];
  const vector = (env.VECTOR_DB_TYPE ?? 'qdrant').trim().toLowerCase();
  if (vector === 'qdrant' && empty(env.QDRANT_API_KEY)) {
    findings.push({
      store: 'qdrant',
      problem:
        'QDRANT_API_KEY is empty: set it to a random value (openssl rand -hex 24), the same one Qdrant starts with.',
    });
  }
  if (env.MONGO_URI && mongoUriLacksCredential(env.MONGO_URI)) {
    findings.push({
      store: 'mongodb',
      problem:
        'MONGO_URI carries no password: add the user and password (or an authMechanism) to it.',
    });
  }
  const graph = (env.DATA_STORE ?? 'neo4j').trim().toLowerCase();
  if (graph === 'neo4j' && empty(env.NEO4J_PASSWORD)) {
    findings.push({
      store: 'neo4j',
      problem:
        'NEO4J_PASSWORD is empty: set it to the password Neo4j starts with.',
    });
  }
  if (graph === 'arangodb' && empty(env.ARANGO_PASSWORD)) {
    findings.push({
      store: 'arangodb',
      problem:
        'ARANGO_PASSWORD is empty: an ArangoDB started with it empty lets anyone in as root. Set it.',
    });
  }
  if ((env.KV_STORE_TYPE ?? 'redis').trim().toLowerCase() === 'etcd') {
    const url = (env.ETCD_URL ?? env.ETCD_HOST ?? '').trim();
    if (
      !/^https:\/\//i.test(url) ||
      empty(env.ETCD_CERT_FILE) ||
      empty(env.ETCD_KEY_FILE) ||
      empty(env.ETCD_CA_FILE)
    ) {
      findings.push({
        store: 'etcd',
        problem:
          "etcd is not reached over TLS with a client certificate: set ETCD_URL=https://…, ETCD_CA_FILE, ETCD_CERT_FILE and ETCD_KEY_FILE (Compose's etcd-tls service writes them).",
      });
    }
  }
  if (
    (env.MESSAGE_BROKER ?? 'redis').trim().toLowerCase() === 'kafka' &&
    (empty(env.KAFKA_USERNAME) || empty(env.KAFKA_PASSWORD))
  ) {
    findings.push({
      store: 'kafka',
      problem:
        "Kafka has no SASL login: set KAFKA_USERNAME and KAFKA_PASSWORD (the bundled broker's SCRAM user).",
    });
  }
  return findings;
}

/** Sends one inline PING and reads the first reply line: "+PONG" answered anonymously, "-NOAUTH" refused. */
export function redisAnonymousProbe(env: Env): AnonymousProbe {
  return () =>
    new Promise((resolve) => {
      const host = (env.REDIS_HOST ?? 'localhost').trim();
      const port = parseInt(env.REDIS_PORT ?? '6379', 10);
      const useTls = [env.REDIS_TLS_ENABLED, env.REDIS_TLS].some(
        (v) => (v ?? '').toLowerCase() === 'true',
      );
      let ca: Buffer | undefined;
      try {
        ca = env.REDIS_TLS_CA_PATH
          ? readFileSync(env.REDIS_TLS_CA_PATH)
          : undefined;
      } catch {
        resolve(null);
        return;
      }
      let settled = false;
      let reply = '';
      const done = (answer: boolean | null): void => {
        if (settled) return;
        settled = true;
        socket.destroy();
        resolve(answer);
      };
      const socket: net.Socket = useTls
        ? tls.connect({
            host,
            port,
            servername: host,
            ca,
            rejectUnauthorized:
              (env.REDIS_TLS_REJECT_UNAUTHORIZED ?? 'true') !== 'false',
          })
        : net.connect({ host, port });
      socket.setTimeout(PROBE_TIMEOUT_MS, () => {
        done(null);
      });
      socket.on(useTls ? 'secureConnect' : 'connect', () =>
        socket.write('PING\r\n'),
      );
      socket.on('data', (chunk) => {
        reply += chunk.toString('latin1');
        const line = reply.split('\r\n', 1)[0] ?? '';
        if (!reply.includes('\r\n')) return;
        if (line.startsWith('+PONG')) done(true);
        else if (/^-(NOAUTH|NOPERM|WRONGPASS)/.test(line)) done(false);
        else done(null);
      });
      socket.on('error', () => {
        done(null);
      });
      socket.on('close', () => {
        done(null);
      });
    });
}

/** Lists collections without the API key: 200 answered anonymously, 401/403 refused. */
export function qdrantAnonymousProbe(
  env: Env,
  fetchImpl: typeof fetch = fetch,
): AnonymousProbe {
  return async () => {
    const host = (env.QDRANT_HOST ?? 'localhost').trim();
    const port = (env.QDRANT_PORT ?? '6333').trim();
    const scheme =
      (env.QDRANT_HTTPS ?? '').toLowerCase() === 'true' ? 'https' : 'http';
    try {
      const response = await fetchImpl(
        `${scheme}://${host}:${port}/collections`,
        {
          signal: AbortSignal.timeout(PROBE_TIMEOUT_MS),
        },
      );
      if (response.status === 401 || response.status === 403) return false;
      return response.ok ? true : null;
    } catch {
      return null;
    }
  };
}

export function defaultStoreAuthProbes(env: Env): StoreAuthProbes {
  const vector = (env.VECTOR_DB_TYPE ?? 'qdrant').trim().toLowerCase();
  return {
    redis: redisAnonymousProbe(env),
    ...(vector === 'qdrant' ? { qdrant: qdrantAnonymousProbe(env) } : {}),
  };
}

const ANSWERED_ANONYMOUSLY: Record<keyof StoreAuthProbes, string> = {
  redis:
    'Redis answered a PING without a password: start it with --requirepass (REDIS_PASSWORD; Compose and install.sh do) and give the app the same REDIS_PASSWORD.',
  qdrant:
    'Qdrant listed its collections without an API key: start it with QDRANT__SERVICE__API_KEY and give the app the same QDRANT_API_KEY.',
};

export async function storeAuthFindings(
  env: Env,
  probes: StoreAuthProbes = defaultStoreAuthProbes(env),
): Promise<StoreAuthFinding[]> {
  const findings = configuredStoreAuthFindings(env);
  const entries = Object.entries(probes) as [
    keyof StoreAuthProbes,
    AnonymousProbe | undefined,
  ][];
  const answers = await Promise.all(
    entries.map(
      async ([store, probe]) => [store, probe ? await probe() : null] as const,
    ),
  );
  for (const [store, anonymous] of answers) {
    if (anonymous === true && !findings.some((f) => f.store === store)) {
      findings.push({ store, problem: ANSWERED_ANONYMOUSLY[store] });
    }
  }
  return findings;
}

export class StoreAuthRefused extends Error {
  constructor(readonly findings: StoreAuthFinding[]) {
    super(
      [
        'PipesHub will not start: a store it uses accepts clients without a credential (SEC-D-06).',
        ...findings.map((f) => `  - ${f.store}: ${f.problem}`),
        'install.sh generates every store credential (./install.sh --upgrade fills in any that are missing).',
        'For a store protected another way (a private network with no login), set STORE_AUTH_CHECK=warn to start anyway.',
      ].join('\n'),
    );
    this.name = 'StoreAuthRefused';
  }
}

interface WarnLogger {
  warn(message: string, meta?: Record<string, unknown>): void;
}

/** Refuses (throws StoreAuthRefused) or, with STORE_AUTH_CHECK=warn, logs each finding. */
export async function enforceStoreAuth(
  env: Env,
  logger: WarnLogger,
  probes?: StoreAuthProbes,
): Promise<StoreAuthFinding[]> {
  const findings = await storeAuthFindings(
    env,
    probes ?? defaultStoreAuthProbes(env),
  );
  if (findings.length === 0) return findings;
  if (storeAuthMode(env) === 'enforce') throw new StoreAuthRefused(findings);
  for (const finding of findings) {
    logger.warn('store_auth.unauthenticated_store', {
      store: finding.store,
      problem: finding.problem,
      mode: 'warn',
    });
  }
  return findings;
}
