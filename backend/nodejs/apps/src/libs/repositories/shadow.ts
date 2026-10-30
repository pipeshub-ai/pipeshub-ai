/**
 * Shadow reads (`DOC_STORE=shadow`): MongoDB stays the store the application reads and writes. A sampled read is also run on the
 * PostgreSQL implementation, after the answer has been returned, and the two answers are compared. The answer to the caller never
 * depends on PostgreSQL: the shadow read is not awaited, runs in a read-only unit of work, is bounded by a timeout and by the number
 * in flight, and any failure of it is only counted. A difference is logged with ids and field paths, never with content, and is
 * counted per repository (`docstore_shadow_reads_total{repo,result}`).
 *
 * A difference is re-checked once after a settle delay (the copy lags the application by a moment); only a difference that is still
 * there is reported. Reads that are given a transaction are not compared when that transaction is open (they see writes PostgreSQL
 * has not got), and a call that is not a read by name is passed through untouched.
 */
import { Logger } from '../services/logger.service';
import { jsonDiff } from './json-compare';
import { docstoreMetrics } from './metrics';

export const shadowEnabled = (): boolean =>
  (process.env.DOC_STORE ?? '').trim().toLowerCase() === 'shadow';

/**
 * The repository methods that only read, listed by name (a name prefix is not enough: `issueAccess` starts with "is" and writes).
 * A call not in the list is passed through untouched, so a new read method is shadowed only once it is added here; the shadow
 * test fails for a method of a repository interface that looks like a read and is missing.
 */
export const READ_METHODS: ReadonlySet<string> = new Set([
  'count',
  'countActiveAdmins',
  'countByProject',
  'countFor',
  'countLiveAccessForUser',
  'countMessages',
  'countSince',
  'countUsersByOrg',
  'counts',
  'existsLiveEmail',
  'find',
  'findActiveByEmail',
  'findAgentWithAccess',
  'findArchivable',
  'findByClientId',
  'findById',
  'findCitations',
  'findCode',
  'findDeletable',
  'findDeviceByHash',
  'findDeviceByUserCode',
  'findExistingIds',
  'findForFeedback',
  'findForShare',
  'findForTurn',
  'findForUnshare',
  'findIdsByContent',
  'findLiveAllowedScopes',
  'findLiveByClientId',
  'findLiveIdentity',
  'findMessage',
  'findOpen',
  'findOwned',
  'findOwnedByInitiator',
  'findRefreshByHash',
  'findVisible',
  'findVisibleSummary',
  'get',
  'getAccountState',
  'getAccountType',
  'getAny',
  'getAnyById',
  'getAuthSnapshot',
  'getByEmail',
  'getByIdOrEmail',
  'getCitations',
  'getContact',
  'getConversationId',
  'getDeleted',
  'getEmail',
  'getFirstLive',
  'getForApprover',
  'getForEdit',
  'getLive',
  'getLiveByEmail',
  'getLiveById',
  'getLiveFields',
  'getLogo',
  'getNames',
  'getOldestId',
  'getOnboardingStatus',
  'getRoleSnapshot',
  'getServiceAccount',
  'hasRemovedAccount',
  'isAllowed',
  'list',
  'listAccessibleIds',
  'listAdminIds',
  'listAgentSharedWithMe',
  'listAllLive',
  'listArchivedAgentGroups',
  'listArtifactMarkerContents',
  'listArtifactSets',
  'listAttachmentsAndCitedRecords',
  'listByEmails',
  'listByFolders',
  'listByProject',
  'listByVirtualRecord',
  'listClientIdsActingAs',
  'listClientIdsCreatedBy',
  'listDirectory',
  'listEnvelopes',
  'listEvents',
  'listFieldsByIds',
  'listFor',
  'listForApprover',
  'listForRelocate',
  'listLiveAccessForClientPage',
  'listLiveAccessForUser',
  'listLiveAuthConfigs',
  'listLiveByEmail',
  'listLiveByIds',
  'listLiveFieldsByIds',
  'listLiveForClient',
  'listLiveMembershipsByEmail',
  'listLiveOrgs',
  'listMembersPage',
  'listMessages',
  'listMessagesOfSessions',
  'listNamesByIds',
  'listOwned',
  'listRemovedIdsByEmail',
  'listServiceAccounts',
  'listSessionEndingSince',
  'listSessions',
  'listSet',
  'listSignInEligible',
  'listSummariesByIds',
  'listVisible',
  'listVisibleSummaries',
  'listWithGroups',
  'listWithLinkedKbForMember',
  'pageByConnector',
  'pageFlatEnvelopes',
  'resolveClient',
  'resolveCodeOrg',
  'resolveRefresh',
  'resolveRefreshOrg',
  'resolveToken',
  'searchArchived',
  'stats',
]);
export const isReadMethod = (name: string): boolean => READ_METHODS.has(name);

export interface ShadowConfig {
  sample: number;
  timeoutMs: number;
  settleMs: number;
  maxInFlight: number;
}

export function shadowConfig(): ShadowConfig {
  const n = (v: string | undefined, d: number): number =>
    v !== undefined && v !== '' && Number.isFinite(Number(v)) ? Number(v) : d;
  return {
    sample: Math.min(
      1,
      Math.max(0, n(process.env.DOC_STORE_SHADOW_SAMPLE, 0.1)),
    ),
    timeoutMs: n(process.env.DOC_STORE_SHADOW_TIMEOUT_MS, 2000),
    settleMs: n(process.env.DOC_STORE_SHADOW_SETTLE_MS, 2000),
    maxInFlight: n(process.env.DOC_STORE_SHADOW_MAX_IN_FLIGHT, 8),
  };
}

const isTx = (
  a: unknown,
): a is { store: string; session?: { inTransaction?: () => boolean } } =>
  typeof a === 'object' &&
  a !== null &&
  (a as { store?: unknown }).store === 'mongo' &&
  'session' in a;

/** `_id`s of a result: of the object itself, or of the first elements of a list. Ids only, nothing else is read from the result. */
export function idsOf(v: unknown, max = 20): string[] {
  const one = (x: unknown): string[] =>
    x && typeof x === 'object' && '_id' in x ? [String(x._id)] : [];
  return Array.isArray(v) ? v.slice(0, max).flatMap(one) : one(v);
}

/** The paths of a difference, without the values that differ. */
export const pathsOf = (diffs: string[], max = 10): string[] =>
  diffs.slice(0, max).map((d) => d.split(/: /)[0] as string);

/** Runs a read in a PostgreSQL unit of work that is read-only; `tx` stands in for the Mongo transaction argument (only calls that were given one). */
export type RunPg = <T>(fn: (tx: unknown) => Promise<T>) => Promise<T>;

const defaultRunPg: RunPg = async (fn) => {
  const { PgUnitOfWork } = await import('./pg');
  const { getCurrentOrgId } = await import('../context/orgContext');
  return new PgUnitOfWork().run((tx) => fn(tx), {
    readOnly: true,
    preOrg: !getCurrentOrgId(),
  });
};

let inFlight = 0;
export const shadowInFlight = (): number => inFlight;

const call = (
  o: Record<string, (...a: unknown[]) => Promise<unknown>>,
  method: string,
  ...a: unknown[]
): Promise<unknown> => {
  const fn = o[method];
  if (typeof fn !== 'function') throw new Error(`no method ${method}`);
  return fn.apply(o, a);
};

const timed = async <T>(p: Promise<T>, ms: number): Promise<T | 'timeout'> => {
  let t: NodeJS.Timeout | undefined;
  try {
    return await Promise.race([
      p,
      new Promise<'timeout'>((r) => {
        t = setTimeout(() => {
          r('timeout');
        }, ms);
      }),
    ]);
  } finally {
    if (t) clearTimeout(t);
  }
};

async function shadowRead(
  repo: string,
  method: string,
  args: unknown[],
  first: unknown,
  mongo: Record<string, (...a: unknown[]) => Promise<unknown>>,
  pg: () => Record<string, (...a: unknown[]) => Promise<unknown>>,
  runPg: RunPg,
  cfg: ShadowConfig,
): Promise<void> {
  const count = (result: string): void => {
    docstoreMetrics.shadowReads.inc({ repo, result });
  };
  const onPg = async (): Promise<unknown> => {
    const impl = pg();
    // a repository opens its own unit from its arguments (the organisation is one of them); only a transaction handed in from the
    // Mongo side needs a PostgreSQL one to stand in for it
    if (!args.some(isTx)) return call(impl, method, ...args);
    return runPg((tx) =>
      call(impl, method, ...args.map((a) => (isTx(a) ? tx : a))),
    );
  };
  try {
    let left = first;
    let right = await timed(onPg(), cfg.timeoutMs);
    if (right === 'timeout') {
      count('timeout');
      return;
    }
    let diffs = jsonDiff(left, right);
    if (diffs.length > 0 && !args.some(isTx)) {
      // the copy may be a moment behind: look again, on both sides, before calling it a difference
      await new Promise((r) => setTimeout(r, cfg.settleMs));
      left = await call(mongo, method, ...args);
      right = await timed(onPg(), cfg.timeoutMs);
      if (right === 'timeout') {
        count('timeout');
        return;
      }
      diffs = jsonDiff(left, right);
    }
    if (diffs.length === 0) {
      count('equal');
      return;
    }
    count('diff');
    Logger.getInstance({ service: 'DocStoreShadow' }).warn(
      'shadow read differs between MongoDB and PostgreSQL',
      {
        repo,
        method,
        differences: diffs.length,
        fields: pathsOf(diffs),
        ids: idsOf(left),
        idsOnPostgres: idsOf(right),
      },
    );
  } catch (e) {
    count('error');
    Logger.getInstance({ service: 'DocStoreShadow' }).debug(
      'shadow read failed',
      { repo, method, error: (e as Error).name },
    );
  }
}

/** The MongoDB repository, with sampled shadow reads on `pg()` (built on first use). */
export function shadowWrap<T extends object>(
  repo: string,
  mongo: T,
  pg: () => T,
  runPg: RunPg = defaultRunPg,
): T {
  let impl: T | undefined;
  const lazy = (): T => (impl ??= pg());
  return new Proxy(mongo, {
    get(target, prop, receiver) {
      const v = Reflect.get(target, prop, receiver) as unknown;
      if (typeof prop !== 'string' || typeof v !== 'function')
        return v as never;
      if (!isReadMethod(prop))
        return (v as (...a: unknown[]) => unknown).bind(target) as never;
      return ((...args: unknown[]) => {
        const result = (v as (...a: unknown[]) => unknown).apply(target, args);
        // A synchronous method keeps its synchronous answer: only a promise enters the shadow read.
        if (typeof (result as { then?: unknown } | null)?.then !== 'function')
          return result;
        return (result as Promise<unknown>).then((out) => {
          const cfg = shadowConfig();
          const open = args.some(
            (a) => isTx(a) && a.session?.inTransaction?.() === true,
          );
          if (
            !open &&
            inFlight < cfg.maxInFlight &&
            Math.random() < cfg.sample
          ) {
            inFlight += 1;
            void shadowRead(
              repo,
              prop,
              args,
              out,
              target as never,
              lazy as never,
              runPg,
              cfg,
            ).finally(() => {
              inFlight -= 1;
            });
          }
          return out;
        });
      }) as never;
    },
  });
}
