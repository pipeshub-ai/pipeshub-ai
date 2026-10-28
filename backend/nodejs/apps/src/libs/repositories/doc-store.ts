import { Logger } from '../services/logger.service';
import {
  PgRuntime,
  configurePg,
  createPgPool,
  readStoreEpoch,
  waitForSchema,
  type SchemaRequirements,
} from '../db';
import { MongoUnitOfWork } from './mongo';
import { PgUnitOfWork } from './pg';
import { MixedUnitOfWork } from './mixed';
import { shadowEnabled } from './shadow';
import { docStore, fallbackList } from './store-select';
import type { UnitOfWork } from './uow';

export function createUnitOfWork(rsAvailable: boolean): UnitOfWork {
  if (docStore() !== 'postgres') return new MongoUnitOfWork(rsAvailable);
  // repositories kept on Mongo by DOC_STORE_MONGO_REPOS (testing aid) need a Mongo unit beside the PostgreSQL one
  return fallbackList().size
    ? new MixedUnitOfWork(new MongoUnitOfWork(rsAvailable), new PgUnitOfWork())
    : new PgUnitOfWork();
}

/** Whether units of work are atomic (PostgreSQL always; Mongo on a replica set): the check-then-write races are closed in the store, not compensated. */
export function unitsAreAtomic(rsAvailable: boolean): boolean {
  return docStore() === 'postgres' || rsAvailable;
}

/** The newest revisions of each schema branch this code needs. */
const CONVERSATION_EPOCH = 2;
const CONTENT_EPOCH = 2;
const PLATFORM_DATA_EPOCH = 1;
const IDENTITY_EPOCH = 3;
const OAUTH_EPOCH = 2;
const INTEGRATIONS_EPOCH = 2;
const IDENTITY_REPOSITORIES = [
  'identity',
  'credential',
  'user',
  'group',
  'org',
  'org-auth-config',
  'activity',
  'picture',
  'reset-link',
  'org-metrics-job',
];

/** The schema a process needs under `DOC_STORE=postgres`: the fence, and the tables of each repository that is not kept on Mongo. */
export function schemaRequirements(): SchemaRequirements {
  const keep = fallbackList();
  const onPg = (name: string): boolean => !keep.has(name);
  return {
    platform: { requires: 1, code: 1 },
    ...(['document', 'project'].some(onPg)
      ? { content: { requires: CONTENT_EPOCH, code: CONTENT_EPOCH } }
      : {}),
    ...(['notification', 'mail-log', 'outbox', 'connectors-config'].some(onPg)
      ? {
          platform_data: {
            requires: PLATFORM_DATA_EPOCH,
            code: PLATFORM_DATA_EPOCH,
          },
        }
      : {}),
    ...(['oauth-app', 'oauth-token', 'identity-lookup', 'delegation'].some(onPg)
      ? { oauth: { requires: OAUTH_EPOCH, code: OAUTH_EPOCH } }
      : {}),
    ...(onPg('slack-thread')
      ? {
          integrations: {
            requires: INTEGRATIONS_EPOCH,
            code: INTEGRATIONS_EPOCH,
          },
        }
      : {}),
    ...([...IDENTITY_REPOSITORIES, 'oauth-app'].some(onPg)
      ? { identity: { requires: IDENTITY_EPOCH, code: IDENTITY_EPOCH } }
      : {}),
    ...(onPg('conversation') || onPg('semantic-search')
      ? {
          conversation: {
            requires: CONVERSATION_EPOCH,
            code: CONVERSATION_EPOCH,
          },
        }
      : {}),
  };
}

/** Repositories that only work together: the lookup before an org is bound reads the tokens and apps the other two write. */
const OAUTH_REPOSITORIES = ['oauth-app', 'oauth-token', 'identity-lookup'];

/** A split of the OAuth repositories across stores would resolve clients and tokens in one store and issue them in the other. */
export function assertCoherentSplit(): void {
  const kept = OAUTH_REPOSITORIES.filter((n) => fallbackList().has(n));
  if (kept.length !== 0 && kept.length !== OAUTH_REPOSITORIES.length)
    throw new Error(
      `DOC_STORE_MONGO_REPOS must keep ${OAUTH_REPOSITORIES.join(', ')} together on Mongo, or none of them`,
    );
}

/**
 * The outbox dispatcher and the TTL sweeper run as the jobs role. Without a jobs pool events would pile up unsent and expired tokens
 * would stay: refuse to start rather than run that way. (Keep `outbox` on Mongo with `DOC_STORE_MONGO_REPOS` for a test run that
 * has no jobs login; there is no other exception.)
 */
export function assertJobsPool(hasPool: boolean): void {
  if (!hasPool && !fallbackList().has('outbox'))
    throw new Error(
      'DOC_STORE=postgres needs PG_JOBS_URL (the jobs login): without it the outbox dispatcher and the TTL sweeper cannot run',
    );
}

/**
 * Under `DOC_STORE=postgres`: connect through the pooler (`PG_NODE_URL`, the node login), wait for the migrate job,
 * and bind the runtime with the store fence at the epoch the fence row holds now. A no-op for Mongo.
 */
export async function initDocStore(
  version: string,
  opts: { jobs?: boolean } = {},
): Promise<void> {
  if (shadowEnabled()) {
    initShadow(version);
    return;
  }
  if (docStore() !== 'postgres') return;
  (await import('./all')).assertAllRepositoriesReady();
  assertCoherentSplit();
  const url = process.env.PG_NODE_URL;
  if (!url) throw new Error('DOC_STORE=postgres needs PG_NODE_URL');
  if (opts.jobs !== false) assertJobsPool(Boolean(process.env.PG_JOBS_URL));
  const pool = createPgPool({
    connectionString: url,
    service: 'node',
    version,
    max: Number(process.env.PG_NODE_POOL_MAX ?? 10),
  });
  // Background reads across organisations (the gauges) run as the jobs role; a process without PG_JOBS_URL has none.
  const jobsUrl = process.env.PG_JOBS_URL;
  const jobsPool = jobsUrl
    ? createPgPool({
        connectionString: jobsUrl,
        service: 'jobs',
        version,
        max: Number(process.env.PG_JOBS_POOL_MAX ?? 2),
      })
    : undefined;
  const unfenced = new PgRuntime({ pool, defaultService: 'node' });
  // Wait for the migrations first: the epoch function does not exist before them, and reading it is not retried.
  await waitForSchema(unfenced, schemaRequirements());
  const epoch = await readStoreEpoch(unfenced);
  const rt = new PgRuntime({
    pool,
    ...(jobsPool && { jobsPool }),
    defaultService: 'node',
    pooler:
      (process.env.PG_POOLER as 'pgbouncer_txn' | undefined) ?? 'pgbouncer_txn',
    fence: { fn: 'kernel.assert_store_epoch', epoch },
  });
  configurePg(rt);
  Logger.getInstance({ service: 'DocStore' }).info('document store: postgres', {
    epoch,
  });
}

/**
 * `DOC_STORE=shadow`: MongoDB stays the store; a read-only PostgreSQL runtime (node login, no store fence: nothing writes) serves
 * the sampled comparison reads. PostgreSQL being down never stops the application: the shadow reads just count as errors.
 */
function initShadow(version: string): void {
  const url = process.env.PG_NODE_URL;
  if (!url) throw new Error('DOC_STORE=shadow needs PG_NODE_URL');
  const pool = createPgPool({
    connectionString: url,
    service: 'node',
    version,
    max: Number(process.env.PG_NODE_POOL_MAX ?? 4),
  });
  const rt = new PgRuntime({
    pool,
    defaultService: 'node',
    readOnly: true, // a method that is not a read by its name could otherwise write PostgreSQL
    pooler:
      (process.env.PG_POOLER as 'pgbouncer_txn' | undefined) ?? 'pgbouncer_txn',
  });
  configurePg(rt);
  Logger.getInstance({ service: 'DocStore' }).info(
    'document store: mongo, with shadow reads on postgres',
  );
}
