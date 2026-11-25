/**
 * The run rows every PostgreSQL execution store shares (`harness.runs`, `harness.run_events`,
 * `harness.session_counters`), and the units their calls run in.
 *
 * A run is its Mongo document in `doc`; the queried fields are generated columns of it. Every write takes the row with
 * `SELECT ... FOR UPDATE`, checks the guard the Mongo store puts in its filter (revision, fence, attempt, lease and
 * deadline on database time) against the row as locked, and writes the document whole. Under READ COMMITTED a second
 * writer of the row waits for the first to commit and then reads what it wrote, so the guards serialize as Mongo's
 * snapshot transactions with write conflicts do.
 */
import { inBoundOrg } from '../durable-transaction/bound-org';
import { UniqueConstraintError } from '../../../errors/database.errors';
import { InternalServerError } from '../../../errors/http.errors';
import { kernelTx } from '../../pg';
import type { Tx } from '../../uow';
import type {
  HarnessEvent,
  HarnessRun,
} from '../../../../modules/agent-harness/domain/execution';
import { inPgDurableTransaction } from '../durable-transaction/pg';
import {
  decodeDoc,
  docParam,
  orgUnit,
  rowRegion,
  type PgKernelTx,
} from '../pg-documents/pg';
import { runFromDocument } from './document';

/** A run as stored: the Mongo run document (`RunDocument`), store-only fields included. */
export type RunDoc = Record<string, unknown>;

/** A locked or read row: its document and the region it was written in (OWD-12). */
export interface RunRow {
  doc: RunDoc;
  region: string;
}

export const activeSessionKey = (orgId: string, sessionId: string): string =>
  JSON.stringify([orgId, sessionId]);

export const toRun = (doc: RunDoc): HarnessRun => runFromDocument(doc);

/** The run as `missingFor` places it: in the region its row was written in. */
export const placedRun = (row: RunRow): HarnessRun => ({
  ...toRun(row.doc),
  region: row.region,
});

/** Runs `fn` for the org a store call names, which must be the one its entry point bound (`requireBoundOrg`). */
export function inRunOrg<T>(orgId: string, fn: () => Promise<T>): Promise<T> {
  return inBoundOrg(orgId, fn);
}

export { forEnumeratedOrg } from '../../../context/orgContext';

/** A durable transaction (its own unit) as `orgId`, with the kernel unit beside the handle collaborators take. */
export function inOrgTransaction<T>(
  orgId: string,
  work: (tx: Tx, k: PgKernelTx) => Promise<T>,
): Promise<T> {
  return inRunOrg(orgId, () =>
    inPgDurableTransaction((tx) => {
      const k = kernelTx(tx);
      if (k.orgId !== orgId)
        throw new InternalServerError(
          'the organisation of the call differs from the one the unit of work runs as',
        );
      return work(tx, k);
    }),
  );
}

/** A read (or a single-statement write) as `orgId`: the open unit if any, else one of its own. */
export function inOrgRead<T>(
  orgId: string,
  fn: (k: PgKernelTx) => Promise<T>,
  write = false,
): Promise<T> {
  return inRunOrg(orgId, () => orgUnit(undefined, orgId, { write }, fn));
}

const rowOf = (row: { doc: unknown; region: string }): RunRow => ({
  doc: decodeDoc<RunDoc>(row.doc),
  region: row.region,
});

/** The run, locked to the unit's commit; null when the org has no such run. */
export async function lockRun(
  k: PgKernelTx,
  orgId: string,
  runId: string,
): Promise<RunRow | null> {
  const { rows } = await k.query<{ doc: unknown; region: string }>(
    `/* pipeshub:harness.runs_lock */ SELECT doc, region FROM harness.runs WHERE org_id = $1 AND run_id = $2 FOR UPDATE`,
    [orgId, runId],
  );
  return rows[0] === undefined ? null : rowOf(rows[0]);
}

export async function readRun(
  k: PgKernelTx,
  orgId: string,
  runId: string,
): Promise<RunRow | null> {
  const { rows } = await k.query<{ doc: unknown; region: string }>(
    `/* pipeshub:harness.runs_read */ SELECT doc, region FROM harness.runs WHERE org_id = $1 AND run_id = $2`,
    [orgId, runId],
  );
  return rows[0] === undefined ? null : rowOf(rows[0]);
}

/** Several of the org's runs, unlocked. */
export async function readRuns(
  k: PgKernelTx,
  orgId: string,
  runIds: readonly string[],
): Promise<RunDoc[]> {
  if (runIds.length === 0) return [];
  const { rows } = await k.query<{ doc: unknown }>(
    `/* pipeshub:harness.runs_read_many */ SELECT doc FROM harness.runs WHERE org_id = $1 AND run_id = ANY($2::text[])`,
    [orgId, [...runIds]],
  );
  return rows.map((row) => decodeDoc<RunDoc>(row.doc));
}

/** The documents a statement selecting `doc` answers. */
export async function runDocs(
  k: PgKernelTx,
  text: string,
  values: unknown[],
): Promise<RunDoc[]> {
  const { rows } = await k.query<{ doc: unknown }>(text, values);
  return rows.map((row) => decodeDoc<RunDoc>(row.doc));
}

/** Writes a locked run's document back. */
export async function saveRun(k: PgKernelTx, doc: RunDoc): Promise<void> {
  const { rowCount } = await k.query(
    `/* pipeshub:harness.runs_save */ UPDATE harness.runs SET doc = $3::jsonb WHERE org_id = $1 AND run_id = $2`,
    [doc['orgId'], doc['runId'], docParam(doc)],
  );
  if (rowCount !== 1)
    throw new InternalServerError('a locked run was not there to write');
}

/** The constraint a second active run of a session breaks. */
export const ONE_ACTIVE_RUN = 'runs_one_active_per_session';
const RUN_KEY = 'runs_pkey';

/** What the Mongo run schema fills in on insert (`runSchema` defaults), so a run reads back the same on both. */
const RUN_DEFAULTS: Readonly<RunDoc> = {
  outcome: null,
  harnessMajor: 1,
  attemptId: null,
  workerId: null,
  leaseExpiresAt: null,
  checkpoint: null,
  attempts: 0,
  claimedFrom: null,
  notBefore: null,
  checkpointRef: null,
  input: null,
  waitingForWorker: null,
  activeSessionKey: null,
  agentDefinition: null,
  engineProfile: null,
  modelAccess: null,
  via: 'session',
  pinned: null,
};

export async function insertRun(k: PgKernelTx, doc: RunDoc): Promise<void> {
  const stored: RunDoc = { ...doc };
  for (const [field, value] of Object.entries(RUN_DEFAULTS)) {
    if (stored[field] === undefined) stored[field] = value;
  }
  await k.query(
    `/* pipeshub:harness.runs_insert */ INSERT INTO harness.runs (org_id, run_id, doc, region) VALUES ($1, $2, $3::jsonb, $4)`,
    [stored['orgId'], stored['runId'], docParam(stored), rowRegion()],
  );
}

/** A unique violation of one of these constraints (it aborted the unit it happened in). */
export function isUniqueViolation(
  error: unknown,
  constraints: readonly string[] = [ONE_ACTIVE_RUN, RUN_KEY],
): boolean {
  if (!(error instanceof UniqueConstraintError)) return false;
  const constraint = (error as { metadata?: { constraint?: unknown } }).metadata
    ?.constraint;
  return typeof constraint === 'string' && constraints.includes(constraint);
}

export interface SessionCounter {
  nextEventSeq: number;
  journalFeatures: string[];
  actors: string[];
  compactRequest: Record<string, unknown> | null;
}

interface CounterRow {
  next_event_seq: string;
  journal_features: string[];
  actors: string[];
  compact_request: unknown;
}

const counterOf = (row: CounterRow): SessionCounter => ({
  nextEventSeq: Number(row.next_event_seq),
  journalFeatures: row.journal_features,
  actors: row.actors,
  compactRequest:
    row.compact_request === null || row.compact_request === undefined
      ? null
      : decodeDoc<Record<string, unknown>>(row.compact_request),
});

/**
 * `$inc` / `$addToSet` on the session's counter, as one upsert: the row lock it takes is held to commit, so the
 * session's writers allocate event seqs in commit order. Added names keep the order they were first added in.
 */
export async function bumpSessionCounter(
  k: PgKernelTx,
  orgId: string,
  sessionId: string,
  change: {
    events?: number;
    features?: readonly string[];
    actors?: readonly string[];
  },
): Promise<SessionCounter> {
  const features = [...new Set(change.features ?? [])];
  const actors = [...new Set(change.actors ?? [])];
  const { rows } = await k.query<CounterRow>(
    `/* pipeshub:harness.session_counters_bump */ INSERT INTO harness.session_counters AS c
       (org_id, session_id, next_event_seq, journal_features, actors, region) VALUES ($1, $2, $3, $4::text[], $5::text[], $6)
     ON CONFLICT (org_id, session_id) DO UPDATE SET
       next_event_seq = c.next_event_seq + excluded.next_event_seq,
       journal_features = c.journal_features
         || ARRAY(SELECT f FROM unnest(excluded.journal_features) WITH ORDINALITY AS a(f, i) WHERE f <> ALL (c.journal_features) ORDER BY i),
       actors = c.actors
         || ARRAY(SELECT f FROM unnest(excluded.actors) WITH ORDINALITY AS a(f, i) WHERE f <> ALL (c.actors) ORDER BY i)
     RETURNING next_event_seq::text, journal_features, actors, compact_request`,
    [orgId, sessionId, change.events ?? 0, features, actors, rowRegion()],
  );
  const row = rows[0];
  if (row === undefined)
    throw new InternalServerError('the session counter did not answer');
  return counterOf(row);
}

export async function readSessionCounter(
  k: PgKernelTx,
  orgId: string,
  sessionId: string,
  lock = false,
): Promise<SessionCounter | null> {
  const { rows } = await k.query<CounterRow>(
    lock
      ? `/* pipeshub:harness.session_counters_lock */ SELECT next_event_seq::text, journal_features, actors, compact_request
           FROM harness.session_counters WHERE org_id = $1 AND session_id = $2 FOR UPDATE`
      : `/* pipeshub:harness.session_counters_read */ SELECT next_event_seq::text, journal_features, actors, compact_request
           FROM harness.session_counters WHERE org_id = $1 AND session_id = $2`,
    [orgId, sessionId],
  );
  return rows[0] === undefined ? null : counterOf(rows[0]);
}

/** Sets or clears the person's waiting /compact; false when the session has no counter (no run was admitted there). */
export async function setCompactRequest(
  k: PgKernelTx,
  orgId: string,
  sessionId: string,
  request: Record<string, unknown> | null,
): Promise<boolean> {
  const { rowCount } = await k.query(
    `/* pipeshub:harness.session_counters_compact */ UPDATE harness.session_counters SET compact_request = $3::jsonb
      WHERE org_id = $1 AND session_id = $2`,
    [orgId, sessionId, request === null ? null : docParam(request)],
  );
  return rowCount === 1;
}

/** v1 events of one session at the seqs its counter allocated. */
export async function insertRunEvents(
  k: PgKernelTx,
  events: readonly HarnessEvent[],
): Promise<void> {
  if (events.length === 0) return;
  await k.query(
    `/* pipeshub:harness.run_events_insert */ INSERT INTO harness.run_events
       (org_id, session_id, event_seq, run_id, commit_seq, doc, region)
     SELECT $1, $2, e.seq, $3, e.commit_seq, e.doc::jsonb, $4
       FROM unnest($5::bigint[], $6::bigint[], $7::text[]) AS e(seq, commit_seq, doc)`,
    [
      events[0]?.orgId,
      events[0]?.sessionId,
      events[0]?.runId,
      rowRegion(),
      events.map((event) => event.eventSeq),
      events.map((event) => event.commitSeq),
      events.map((event) => docParam(event)),
    ],
  );
}

/** One v1 event, its seq allocated now on the session's counter. */
export async function appendRunEvent(
  k: PgKernelTx,
  run: Pick<HarnessRun, 'orgId' | 'sessionId' | 'runId' | 'revision'>,
  type: string,
  data: Record<string, unknown>,
  now: number,
  commitSeq = run.revision + 1,
): Promise<number> {
  const counter = await bumpSessionCounter(k, run.orgId, run.sessionId, {
    events: 1,
  });
  await insertRunEvents(k, [
    {
      orgId: run.orgId,
      sessionId: run.sessionId,
      runId: run.runId,
      eventSeq: counter.nextEventSeq,
      commitSeq,
      type,
      data,
      createdAt: now,
    },
  ]);
  return counter.nextEventSeq;
}

/** `typeof value === 'number' && value > now`: Mongo's `$isNumber` guards. */
export const after = (value: unknown, now: number): boolean =>
  typeof value === 'number' && value > now;

export const notAfter = (value: unknown, now: number): boolean =>
  typeof value === 'number' && value <= now;
