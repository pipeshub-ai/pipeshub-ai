import { AsyncResource } from 'node:async_hooks';
import {
  AuditEntry,
  AuditFieldValue,
  AuditHead,
  AuditRecord,
  AuditUnlinked,
} from '../../../../modules/agent-harness/domain/audit';
import type { AuditChainStore } from '../../../../modules/agent-harness/ports/audit';
import type {
  AuditChainQueries,
  AuditKindQuery,
} from '../../../../modules/agent-harness/ports/audit-queries';
import {
  harnessLogMeta,
  HarnessLogger,
  silentHarnessLogger,
} from '../../../../modules/agent-harness/application/harness-log';
import { CoalescedWork } from '../../../../modules/agent-harness/application/coalesced-work';
import { kernelTx } from '../../pg';
import { Params } from '../../pg-util';
import type { Tx } from '../../uow';
import {
  insertPendingAudit,
  linkAllPendingAuditPg,
  pendingAuditOrgsPg,
  unlinkedAuditPg,
} from '../audit-linking/pg';
import { durableCommits } from '../durable-transaction/commits';
import { decodeDoc, jobsUnit, type PgKernelTx } from '../pg-documents/pg';
import { orgOrJobsUnit } from '../pg-documents/org-or-jobs';
import { auditLinkDefer } from './mongo';

/** How many of the newest matching records `sessionsOf` groups. */
const SESSIONS_SCAN = 5_000;

const FIELD_NAME = /^[A-Za-z][A-Za-z0-9]{0,63}$/;

/** A field name is the code's, never the request's; it still only ever reaches the statement as a parameter. */
const fieldName = (name: string): string => {
  if (!FIELD_NAME.test(name)) throw new Error(`Not an audit field: ${name}`);
  return name;
};

const fieldIn = (
  p: Params,
  name: string,
  values: readonly AuditFieldValue[],
): string =>
  `(doc -> 'fields' -> ${p.add(fieldName(name))}::text) IN (SELECT jsonb_array_elements(${p.add(JSON.stringify(values))}::jsonb))`;

/** The predicate of a read by kind, over `p`'s parameters; the org is always the first. */
function kindWhere(
  p: Params,
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
): string {
  const where = [
    `org_id = ${p.add(orgId)}`,
    `kind = ANY(${p.add([...query.kinds])}::text[])`,
  ];
  if (query.beforeSeq !== null) where.push(`seq < ${p.add(query.beforeSeq)}`);
  if (query.since !== undefined) where.push(`ts >= ${p.add(query.since)}`);
  if (query.until !== undefined) where.push(`ts <= ${p.add(query.until)}`);
  if (query.sessionIds !== undefined)
    where.push(`session_id = ANY(${p.add([...query.sessionIds])}::text[])`);
  if (query.runIds !== undefined)
    where.push(`run_id = ANY(${p.add([...query.runIds])}::text[])`);
  for (const [name, values] of Object.entries(query.fields ?? {}))
    where.push(fieldIn(p, name, values));
  if (query.anyOf !== undefined) {
    const parts: string[] = [];
    if (query.anyOf.runIds !== undefined && query.anyOf.runIds.length > 0)
      parts.push(`run_id = ANY(${p.add([...query.anyOf.runIds])}::text[])`);
    for (const [name, values] of Object.entries(query.anyOf.fields ?? {}))
      if (values.length > 0) parts.push(fieldIn(p, name, values));
    // An empty part list matches nothing, as the in-memory store's `some` does.
    where.push(parts.length > 0 ? `(${parts.join(' OR ')})` : 'false');
  }
  return where.join(' AND ');
}

const toRecord = (doc: unknown): AuditRecord => {
  const record = decodeDoc<AuditRecord>(doc);
  return { ...record, fields: { ...record.fields } };
};

interface HeadRow {
  org_id: string;
  seq: string;
  hash: string;
  anchored_seq: string;
}

const headOf = (row: HeadRow): AuditHead => ({
  orgId: row.org_id,
  seq: Number(row.seq),
  hash: row.hash,
  anchoredSeq: Number(row.anchored_seq),
});

/**
 * One link at a time per org in this process, outside the async context (and so the unit of work) of whoever asked:
 * a link is its own unit, as the jobs role.
 */
const background = new AsyncResource('PgAuditChainLink');
const links = new CoalescedWork((orgId) =>
  background.runInAsyncScope(() => linkAllPendingAuditPg(orgId)),
);

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
    links.request(orgId).catch(failed);
  };
  const timer = background.runInAsyncScope(() =>
    setTimeout(start, auditLinkDefer.ms),
  );
  timer.unref();
  deferred.set(orgId, { timer, start });
};

/** No PostgreSQL link is under way, queued or waiting in this process; a waiting one starts now. */
export const pgAuditLinksIdle = async (): Promise<void> => {
  do {
    for (const { start } of [...deferred.values()]) start();
    await links.idle();
  } while (deferred.size > 0);
};

/**
 * The audit chain on PostgreSQL. An append made in a unit only stores its entries (`harness.audit_pending`): no row
 * the org's other commits write is touched, so concurrent commits of one org never wait on one another. Once the unit
 * has committed, the entries are linked onto the chain in a short unit of their own under the head's row lock, with
 * the org's other commits of that moment, and every read links first. An append whose link did not run (the process
 * died after the commit) is linked by the next read or by the link watch.
 */
export class PgAuditChainStore
  implements AuditChainStore<Tx>, AuditChainQueries
{
  constructor(private readonly logger: HarnessLogger = silentHarnessLogger) {}

  async append(
    orgId: string,
    entries: readonly AuditEntry[],
    ts: string,
    tx: Tx,
  ): Promise<void> {
    const first = entries[0];
    if (first === undefined) return;
    await orgOrJobsUnit(tx, orgId, 'audit-append', { write: true }, (k) =>
      insertPendingAudit(k, orgId, [first, ...entries.slice(1)], ts),
    );
    durableCommits.defer(kernelTx(tx), () => {
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
      return Promise.resolve();
    });
  }

  /** Links every append of the org committed before the call. */
  settle(orgId: string): Promise<void> {
    return links.request(orgId);
  }

  /** What a read does first; a link that cannot run is logged and `unlinked` names what the chain lacks. */
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
    return unlinkedAuditPg(orgId);
  }

  private readIn<T>(
    orgId: string,
    fn: (k: PgKernelTx) => Promise<T>,
  ): Promise<T> {
    return orgOrJobsUnit(undefined, orgId, 'audit-read', { write: false }, fn);
  }

  async read(
    orgId: string,
    afterSeq: number,
    limit: number,
  ): Promise<AuditRecord[]> {
    await this.linked(orgId);
    return this.readIn(orgId, async (k) => {
      const { rows } = await k.query<{ doc: unknown }>(
        `/* pipeshub:harness.audit_records_read */ SELECT doc FROM harness.audit_records
          WHERE org_id = $1 AND seq > $2 ORDER BY seq LIMIT $3`,
        [orgId, afterSeq, limit],
      );
      return rows.map((row) => toRecord(row.doc));
    });
  }

  async readKind(orgId: string, query: AuditKindQuery): Promise<AuditRecord[]> {
    await this.linked(orgId);
    const p = new Params();
    const where = kindWhere(p, orgId, query);
    return this.readIn(orgId, async (k) => {
      const { rows } = await k.query<{ doc: unknown }>(
        `/* pipeshub:harness.audit_records_by_kind */ SELECT doc FROM harness.audit_records WHERE ${where}
          ORDER BY seq DESC LIMIT ${p.add(query.limit)}`,
        p.values,
      );
      return rows.map((row) => toRecord(row.doc));
    });
  }

  async readAt(orgId: string, seqs: readonly number[]): Promise<AuditRecord[]> {
    if (seqs.length === 0) return [];
    await this.linked(orgId);
    return this.readIn(orgId, async (k) => {
      const { rows } = await k.query<{ doc: unknown }>(
        `/* pipeshub:harness.audit_records_at */ SELECT doc FROM harness.audit_records
          WHERE org_id = $1 AND seq = ANY($2::bigint[]) ORDER BY seq`,
        [orgId, [...new Set(seqs)]],
      );
      return rows.map((row) => toRecord(row.doc));
    });
  }

  async sessionsOf(
    orgId: string,
    query: Pick<AuditKindQuery, 'kinds' | 'since' | 'until' | 'limit'>,
  ): Promise<string[]> {
    await this.linked(orgId);
    const p = new Params();
    const where = kindWhere(p, orgId, { ...query, beforeSeq: null });
    return this.readIn(orgId, async (k) => {
      // Bounds the scan: the newest records name the sessions a filter offers first.
      const { rows } = await k.query<{ session_id: string }>(
        `/* pipeshub:harness.audit_records_sessions */ SELECT session_id FROM (
           SELECT session_id, seq FROM harness.audit_records WHERE ${where} AND session_id IS NOT NULL
            ORDER BY seq DESC LIMIT ${p.add(SESSIONS_SCAN)}) newest
          GROUP BY session_id ORDER BY max(seq) DESC LIMIT ${p.add(query.limit)}`,
        p.values,
      );
      return rows.map((row) => row.session_id);
    });
  }

  async head(orgId: string): Promise<AuditHead | null> {
    await this.linked(orgId);
    return this.readIn(orgId, async (k) => {
      const { rows } = await k.query<HeadRow>(
        `/* pipeshub:harness.audit_heads_read */ SELECT org_id, seq, hash, anchored_seq
           FROM harness.audit_heads WHERE org_id = $1 AND seq > 0`,
        [orgId],
      );
      return rows[0] === undefined ? null : headOf(rows[0]);
    });
  }

  async unanchored(limit: number): Promise<AuditHead[]> {
    for (const orgId of await pendingAuditOrgsPg()) await this.linked(orgId);
    return jobsUnit('audit-anchor', { write: false }, async (k) => {
      const { rows } = await k.query<HeadRow>(
        `/* pipeshub:harness.audit_heads_unanchored */ SELECT org_id, seq, hash, anchored_seq
           FROM harness.audit_heads WHERE seq > anchored_seq ORDER BY anchored_seq, org_id LIMIT $1`,
        [limit],
      );
      return rows.map(headOf);
    });
  }

  async markAnchored(orgId: string, seq: number): Promise<void> {
    await this.linked(orgId);
    await orgOrJobsUnit(
      undefined,
      orgId,
      'audit-anchor',
      { write: true },
      (k) =>
        k.query(
          `/* pipeshub:harness.audit_heads_anchored */ UPDATE harness.audit_heads SET anchored_seq = $2
          WHERE org_id = $1 AND anchored_seq < $2`,
          [orgId, seq],
        ),
    );
  }
}
