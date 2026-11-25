import { idOperator } from '../../id-filter-guard/mongo';
import { asMongoSession } from '../durable-transaction/mongo';
import type { Tx } from '../../uow';
import { AsyncResource } from 'node:async_hooks';
import mongoose, { ClientSession } from 'mongoose';
import type { Db } from 'mongodb';
import {
  AuditEntry,
  AuditHead,
  AuditRecord,
  AuditUnlinked,
} from '../../../../modules/agent-harness/domain/audit';
import { AuditChainStore } from '../../../../modules/agent-harness/ports/audit';
import {
  AuditChainQueries,
  AuditKindQuery,
} from '../../../../modules/agent-harness/ports/audit-queries';
import {
  harnessLogMeta,
  HarnessLogger,
  silentHarnessLogger,
} from '../../../../modules/agent-harness/application/harness-log';
import { CoalescedWork } from '../../../../modules/agent-harness/application/coalesced-work';
import { MAJORITY_WRITE } from '../durable-transaction/mongo';
import {
  AUDIT_PENDING,
  AuditPendingDocument,
  linkAllPendingAudit,
  linkAuditAppends,
  pendingAuditAppend,
  pendingAuditOrgs,
  unlinkedAudit,
} from '../audit-linking/mongo';
import {
  AgentAuditHeadModel,
  AgentAuditRecordModel,
} from './schema/audit.schema';
import { transactionWrites } from '../transaction-writes/mongo';

const toRecord = (document: AuditRecord & { _id?: unknown }): AuditRecord => {
  const { _id, ...record } = document;
  return { ...record, fields: { ...record.fields } };
};

/** How many of the newest matching records `sessionsOf` groups. */
const SESSIONS_SCAN = 5_000;

const FIELD_NAME = /^[A-Za-z][A-Za-z0-9]{0,63}$/;

/** A field name is the code's, never the request's; it still never becomes an operator or a path. */
function fieldPath(name: string): string {
  if (!FIELD_NAME.test(name)) throw new Error(`Not an audit field: ${name}`);
  return `fields.${name}`;
}

function kindFilter(
  orgId: string,
  query: Pick<
    AuditKindQuery,
    | 'kinds'
    | 'beforeSeq'
    | 'since'
    | 'until'
    | 'sessionIds'
    | 'runIds'
    | 'fields'
    | 'anyOf'
  >,
): Record<string, unknown> {
  const filter: Record<string, unknown> = {
    orgId,
    kind: { $in: [...query.kinds] },
  };
  if (query.beforeSeq !== null) filter['seq'] = { $lt: query.beforeSeq };
  if (query.since !== undefined || query.until !== undefined) {
    filter['ts'] = {
      ...(query.since !== undefined ? { $gte: query.since } : {}),
      ...(query.until !== undefined ? { $lte: query.until } : {}),
    };
  }
  if (query.sessionIds !== undefined) {
    filter['sessionId'] = { $in: [...query.sessionIds] };
  }
  if (query.runIds !== undefined) {
    filter['runId'] = { $in: [...query.runIds] };
  }
  for (const [name, values] of Object.entries(query.fields ?? {})) {
    filter[fieldPath(name)] = { $in: [...values] };
  }
  if (query.anyOf !== undefined) {
    const parts: Record<string, unknown>[] = [];
    if (query.anyOf.runIds !== undefined && query.anyOf.runIds.length > 0) {
      parts.push({ runId: { $in: [...query.anyOf.runIds] } });
    }
    for (const [name, values] of Object.entries(query.anyOf.fields ?? {})) {
      if (values.length > 0)
        parts.push({ [fieldPath(name)]: { $in: [...values] } });
    }
    // An empty part list matches nothing, as the in-memory store's `some` does.
    filter['$or'] = parts.length > 0 ? parts : [{ _id: { $exists: false } }];
  }
  return filter;
}

const database = (): Db => {
  const db = mongoose.connection.db;
  if (!db) throw new Error('MongoDB is not connected');
  return db;
};

/**
 * A majority read that waits for what the primary holds when it starts: the
 * majority's view trails an acknowledged commit for a moment, and a link
 * that just committed must be in what the reader gets.
 */
async function caughtUp<T>(
  read: (session: ClientSession) => Promise<T>,
): Promise<T> {
  const session = await mongoose.startSession();
  try {
    await database().command({ ping: 1 }, { session });
    return await read(session);
  } finally {
    await session.endSession();
  }
}

/**
 * One link at a time per org in this process, whichever store asked. It runs
 * as the process's work, outside the async context of the request whose
 * commit asked for it.
 */
const links = new CoalescedWork((orgId) =>
  linkAllPendingAudit(database(), orgId),
);
const background = new AsyncResource('AuditChainLink');

/**
 * How long a commit's link waits for the org's next commits, so that one link
 * takes them all (PERF-E3): sessions in step would otherwise each pay a link's
 * six commands on the loop their commits share. Well inside the link watch's
 * interval and the stale threshold; a read links at once whatever is waiting.
 * Mutable so a test can hold or hurry the link without touching the clock.
 */
export const AUDIT_LINK_DEFER_MS = 1_000;
export const auditLinkDefer = { ms: AUDIT_LINK_DEFER_MS };

/** The org's link that is waiting, and how to start it now. */
const deferred = new Map<
  string,
  { timer: NodeJS.Timeout; start: () => void }
>();

const linkSoon = (orgId: string, failed: (error: unknown) => void): void => {
  if (deferred.has(orgId)) return;
  const start = (): void => {
    const waiting = deferred.get(orgId);
    if (waiting === undefined) return;
    clearTimeout(waiting.timer);
    deferred.delete(orgId);
    background.runInAsyncScope(() => links.request(orgId)).catch(failed);
  };
  const timer = background.runInAsyncScope(() =>
    setTimeout(start, auditLinkDefer.ms),
  );
  timer.unref();
  deferred.set(orgId, { timer, start });
};

/** No link is under way, queued or waiting in this process (shutdown, and tests that compare stored state); a waiting one starts now. */
export const auditLinksIdle = async (): Promise<void> => {
  do {
    for (const { start } of [...deferred.values()]) start();
    await links.idle();
  } while (deferred.size > 0);
};

/**
 * An append made in a transaction only stores its entries: no document the
 * org shares is written, so commits of one org do not abort each other
 * (PERFB-F1). They are linked onto the chain in a short transaction of their
 * own, at most `auditLinkDefer.ms` after the transaction's session ends,
 * with the org's other commits of that second, and every read links first. An
 * append whose link did not run (the process died after the commit) is
 * linked by the next read or by the link watch; one that cannot be linked is
 * what `unlinked` reports.
 */
export class MongoAuditChainStore
  implements AuditChainStore<Tx>, AuditChainQueries
{
  private readonly announced = new WeakMap<ClientSession, Set<string>>();

  constructor(private readonly logger: HarnessLogger = silentHarnessLogger) {}

  async append(
    orgId: string,
    entries: readonly AuditEntry[],
    ts: string,
    sessionHandle: Tx | ClientSession,
  ): Promise<void> {
    const session = asMongoSession(sessionHandle);
    const first = entries[0];
    if (first === undefined) return;
    // Standalone MongoDB passes no transaction to commit with: link at once.
    if ((session as ClientSession | undefined)?.inTransaction() !== true) {
      await linkAuditAppends(database(), orgId, [{ entries, ts }], session);
      return;
    }
    const pending = pendingAuditAppend(orgId, [first, ...entries.slice(1)], ts);
    await transactionWrites.insertRaw(session, AUDIT_PENDING, pending, () =>
      database()
        .collection<Omit<AuditPendingDocument, '_id'>>(AUDIT_PENDING)
        .insertOne(pending, { session }),
    );
    this.linkWhenEnded(session, orgId);
  }

  /** Links every append of the org committed before the call. */
  settle(orgId: string): Promise<void> {
    return links.request(orgId);
  }

  /**
   * What a read does first. A link that cannot run does not fail the read
   * (one org's must not keep every other org's head from its anchor): it is
   * logged, and `unlinked` names what the chain lacks.
   */
  private async linked(orgId: string): Promise<void> {
    try {
      await this.settle(orgId);
    } catch (error) {
      this.logger.error(
        'audit.link_failed',
        harnessLogMeta('audit.link_failed', {
          orgId,
          errorType: error instanceof Error ? error.name : typeof error,
          choice: 'reported_unlinked',
        }),
      );
    }
  }

  unlinked(orgId?: string): Promise<AuditUnlinked[]> {
    return unlinkedAudit(database(), orgId);
  }

  /** A session ends after its transaction committed or aborted, whoever ran it. */
  private linkWhenEnded(
    sessionHandle: Tx | ClientSession,
    orgId: string,
  ): void {
    const session = asMongoSession(sessionHandle);
    const orgs = this.announced.get(session) ?? new Set<string>();
    this.announced.set(session, orgs);
    if (orgs.has(orgId)) return;
    orgs.add(orgId);
    session.once('ended', () => {
      linkSoon(orgId, (error: unknown) => {
        this.logger.warn(
          'audit.link_failed',
          harnessLogMeta('audit.link_failed', {
            orgId,
            errorType: error instanceof Error ? error.name : typeof error,
            choice: 'next_read_or_anchor',
          }),
        );
      });
    });
  }

  async read(
    orgId: string,
    afterSeq: number,
    limit: number,
  ): Promise<AuditRecord[]> {
    await this.linked(orgId);
    const documents = await caughtUp((session) =>
      AgentAuditRecordModel.find({ orgId, seq: { $gt: afterSeq } })
        .sort({ seq: 1 })
        .limit(limit)
        .session(session)
        .read('primary')
        .readConcern('majority')
        .lean(),
    );
    return documents.map(toRecord);
  }

  /** Served by `orgId_1_kind_1_seq_-1` (AUDITUI): the kinds' records of the org, newest first. */
  async readKind(orgId: string, query: AuditKindQuery): Promise<AuditRecord[]> {
    await this.linked(orgId);
    const documents = await caughtUp((session) =>
      AgentAuditRecordModel.find(kindFilter(orgId, query))
        .sort({ seq: -1 })
        .limit(query.limit)
        .session(session)
        .read('primary')
        .readConcern('majority')
        .lean(),
    );
    return documents.map(toRecord);
  }

  async readAt(orgId: string, seqs: readonly number[]): Promise<AuditRecord[]> {
    if (seqs.length === 0) return [];
    await this.linked(orgId);
    const documents = await caughtUp((session) =>
      AgentAuditRecordModel.find({ orgId, seq: { $in: [...new Set(seqs)] } })
        .sort({ seq: 1 })
        .session(session)
        .read('primary')
        .readConcern('majority')
        .lean(),
    );
    return documents.map(toRecord);
  }

  async sessionsOf(
    orgId: string,
    query: Pick<AuditKindQuery, 'kinds' | 'since' | 'until' | 'limit'>,
  ): Promise<string[]> {
    await this.linked(orgId);
    const groups = await caughtUp((session) =>
      AgentAuditRecordModel.aggregate<{ _id: string }>([
        {
          $match: {
            ...kindFilter(orgId, { ...query, beforeSeq: null }),
            sessionId: idOperator({ $type: 'string' }),
          },
        },
        { $sort: { seq: -1 } },
        // Bounds the scan: the newest records name the sessions a filter offers first.
        { $limit: SESSIONS_SCAN },
        { $group: { _id: '$sessionId', last: { $max: '$seq' } } },
        { $sort: { last: -1 } },
        { $limit: query.limit },
      ])
        .session(session)
        .read('primary'),
    );
    return groups.map((group) => group._id);
  }

  async head(orgId: string): Promise<AuditHead | null> {
    await this.linked(orgId);
    const head = await caughtUp((session) =>
      AgentAuditHeadModel.findOne({ orgId })
        .session(session)
        .read('primary')
        .readConcern('majority')
        .lean(),
    );
    return head === null
      ? null
      : {
          orgId: head.orgId,
          seq: head.seq,
          hash: head.hash,
          anchoredSeq: head.anchoredSeq,
        };
  }

  async unanchored(limit: number): Promise<AuditHead[]> {
    for (const orgId of await pendingAuditOrgs(database())) {
      await this.linked(orgId);
    }
    const heads = await caughtUp((session) =>
      AgentAuditHeadModel.find({ $expr: { $gt: ['$seq', '$anchoredSeq'] } })
        .sort({ anchoredSeq: 1 })
        .limit(limit)
        .session(session)
        .read('primary')
        .readConcern('majority')
        .lean(),
    );
    return heads.map((head) => ({
      orgId: head.orgId,
      seq: head.seq,
      hash: head.hash,
      anchoredSeq: head.anchoredSeq,
    }));
  }

  async markAnchored(orgId: string, seq: number): Promise<void> {
    await this.linked(orgId);
    await AgentAuditHeadModel.updateOne(
      { orgId, anchoredSeq: { $lt: seq } },
      { $set: { anchoredSeq: seq } },
      MAJORITY_WRITE,
    );
  }
}
