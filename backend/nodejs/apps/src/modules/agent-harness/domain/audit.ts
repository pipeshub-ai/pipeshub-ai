import {
  canonicalDigest,
  canonicalJsonBytes,
} from '../contracts/canonical-json';
import { createHash } from 'node:crypto';
import { EventWriter } from './journal';

/**
 * The audit stream (REPORT 6.16): one append-only chain per org, each record
 * holding the previous record's hash, so a changed, removed or reordered
 * record breaks the chain; periodic anchors of the head in WORM storage catch
 * a rewrite of the whole tail. Records carry ids, reason codes and digests,
 * never content: content is shreddable, the audit stream is not.
 */
export const AUDIT_CHAIN_VERSION = 1;
export const AUDIT_GENESIS_HASH = `sha256:${'0'.repeat(64)}`;
/** How often the anchorer writes each changed org's chain head. */
export const AUDIT_ANCHOR_INTERVAL_MS = 15 * 60 * 1000;
export const AUDIT_ANCHOR_BATCH = 100;
export const AUDIT_ANCHOR_LEASE_NAME = 'agent-harness-audit-anchor';
/**
 * The anchorer renews its lease far more often than it anchors, so a replica
 * that dies without releasing it (SIGKILL, OOM, a lost node) holds back the
 * next one for minutes, not for an anchor interval or two. Two
 * missed renewals still keep it.
 */
export const AUDIT_ANCHOR_RENEW_MS = 60 * 1000;
export const AUDIT_ANCHOR_LEASE_MS = 3 * AUDIT_ANCHOR_RENEW_MS;
const MAX_FIELD_LENGTH = 256;

export type AuditFieldValue = string | number | boolean;
export type AuditFields = Record<string, AuditFieldValue>;

/** What an audit-relevant journal event contributes: metadata and a digest. */
export interface AuditProjection {
  fields: AuditFields;
  /** Canonical digest of the event's data; lets a verifier cross-check the journal. */
  dataDigest: string;
}

/** Journal events are written by Node or the worker; config changes by the control plane (stream `cp:{orgId}`). */
export type AuditWriter = EventWriter | 'control-plane';

export interface AuditEntry extends AuditProjection {
  kind: string;
  /** Null for a config change, which belongs to no session. */
  sessionId: string | null;
  streamId: string;
  runId: string | null;
  /** The journal seq; for a config change, the org head revision it committed as. */
  journalSeq: number;
  writer: AuditWriter;
  attemptId: string | null;
  fence: number | null;
}

export interface AuditRecord extends AuditEntry {
  orgId: string;
  seq: number;
  prevHash: string;
  hash: string;
  /** DB time of the journal append the record belongs to. */
  ts: string;
}

export interface AuditHead {
  orgId: string;
  seq: number;
  hash: string;
  anchoredSeq: number;
}

export interface AuditAnchor {
  orgId: string;
  seq: number;
  hash: string;
  anchoredAt: string;
}

type Data = Record<string, unknown>;

const scalar = (value: unknown): AuditFieldValue | undefined => {
  if (typeof value === 'number' || typeof value === 'boolean') return value;
  if (typeof value === 'string') return value.slice(0, MAX_FIELD_LENGTH);
  return undefined;
};

const pick =
  (...keys: string[]) =>
  (data: Data): AuditFields => {
    const fields: AuditFields = {};
    for (const key of keys) {
      const value = scalar(data[key]);
      if (value !== undefined) fields[key] = value;
    }
    return fields;
  };

/** WP-96: a result's provenance flattened to scalars: readers, integrity, the rule, and a count per source kind. */
function provenanceAudit(data: Data): AuditFields {
  const fields = pick('invocationId', 'toolId', 'rule')(data);
  const provenance = data['provenance'];
  if (
    provenance === null ||
    typeof provenance !== 'object' ||
    Array.isArray(provenance)
  )
    return fields;
  const { readers, integrity, sources } = provenance as Record<string, unknown>;
  if (typeof readers === 'string') fields['readers'] = readers;
  if (typeof integrity === 'string') fields['integrity'] = integrity;
  const list = Array.isArray(sources) ? sources : [];
  fields['sources'] = list.length;
  for (const source of list) {
    const kind =
      source !== null && typeof source === 'object'
        ? (source as Record<string, unknown>)['kind']
        : undefined;
    if (typeof kind !== 'string' || !/^[a-z]{1,16}$/.test(kind)) continue;
    const key = `${kind}Sources`;
    fields[key] = (typeof fields[key] === 'number' ? fields[key] : 0) + 1;
  }
  return fields;
}

/** WP-63: an allowed website's path, access and use, flattened as the audit stream keeps scalars only. */
const withSite =
  (picker: (data: Data) => AuditFields) =>
  (data: Data): AuditFields => {
    const fields = picker(data);
    const site = data['site'];
    if (site !== null && typeof site === 'object' && !Array.isArray(site)) {
      const { path, access, use } = site as Record<string, unknown>;
      if (typeof path === 'string') fields['sitePath'] = path;
      if (typeof access === 'string') fields['siteAccess'] = access;
      if (typeof use === 'string') fields['siteUse'] = use;
    }
    return fields;
  };

/**
 * The audit-relevant event types and the only fields each contributes. Free
 * text (`reason`, `message`) and content refs are never picked.
 */
export const AUDIT_EVENT_FIELDS: Readonly<
  Record<string, (data: Data) => AuditFields>
> = Object.freeze({
  'run.admitted': pick('runKind', 'principalRef', 'parentRunId', 'via'),
  // Attempt-token issuance: a token is issued for exactly this claim.
  'run.claimed': pick('attempt', 'fence', 'leaseMs'),
  'run.spec_pinned': (data: Data) => {
    const spec = data['spec'] as Data | undefined;
    const fields = pick('specDigest')(data);
    const capabilities = scalar(spec?.['capabilitiesDigest']);
    if (capabilities !== undefined) fields['capabilitiesDigest'] = capabilities;
    return fields;
  },
  'run.cancel_requested': pick('by'),
  'run.cancelled': pick('by'),
  'run.completed': pick('outcome'),
  'run.failed': pick('errorType'),
  'run.quarantined': pick('attempts', 'lastErrorType'),
  // OB-03: every tool call, by its intent (before it runs) and its outcome; a digest of the arguments, never them.
  'tool.intent': pick('invocationId', 'toolId', 'effect', 'argsDigest'),
  'tool.outcome': pick('invocationId', 'status'),
  'tool.denied': pick('invocationId', 'rule'),
  // OB-03: an artifact the run published, by its ids and version; never its name or content.
  'x.pipeshub.artifact.published': pick(
    'intentId',
    'artifactId',
    'publishedVersion',
  ),
  // SEC2 O-2: a sandbox gateway's refusal; a host or field path, never a URL path, query or value.
  // WP-63: an allowed website's path (no query, tokens masked) is the one path an egress record holds.
  'gateway.refused': withSite(
    pick('gateway', 'reason', 'status', 'host', 'param'),
  ),
  // OB-03: a destination the egress proxy let the run's sandbox reach; a bare host, never a path or query, but for
  // an allowed website (WP-63) its `site`: the path the run's activity shows.
  'egress.allowed': withSite(pick('host', 'via')),
  // C6.12: an unusual egress volume of the run; counts and an alias, never a host.
  'egress.volume_alert': pick(
    'kind',
    'direction',
    'bytes',
    'thresholdBytes',
    'baselineBytes',
    'alias',
  ),
  // WP-103: a credential shape a gateway hid from the run's model call; its kind, never its value.
  'gateway.secret_hidden': pick('gateway', 'kind'),
  // P2.9: how an unknown effect was decided (by a read or by an operator); never its evidence.
  'tool.reconciled': pick('invocationId', 'verdict', 'reason', 'source'),
  // J-18: an approval asked, so the audit search shows the asks as well as the decisions; its kind only, never the question.
  'interaction.requested': pick('interactionId', 'kind'),
  'interaction.resolved': pick('interactionId', 'decision', 'by', 'channel'),
  // One approval of an ask that needs several, before the last one resolves it.
  'x.pipeshub.approval.counted': pick(
    'interactionId',
    'by',
    'counted',
    'required',
  ),
  // A grant that widens what the conversation may do without asking again (R2 F1).
  'interaction.granted': pick(
    'interactionId',
    'scope',
    'toolId',
    'key',
    'sensitivity',
    'fromRunId',
    'by',
  ),
  // ADR-GRANT-01 D9 (R38-AUTHZ): a person's decline for their runs in the conversation; never the reason they wrote.
  'interaction.declined': pick(
    'interactionId',
    'scope',
    'toolId',
    'key',
    'by',
    'fromRunId',
  ),
  // ADR-GRANT-01 D8 (UI-B1 G5): the owner took a conversation grant back; its tool, destination key and who.
  'interaction.grant_revoked': pick('toolId', 'key', 'by'),
  'label.declassified': (data: Data) => ({
    ...pick('by', 'interactionId')(data),
    items: Array.isArray(data['items']) ? data['items'].length : 0,
  }),
  // WP-63 part A: a repository the worker fetched for the run; the host and path, never a URL or commit.
  'x.pipeshub.repository.fetched': pick(
    'host',
    'repository',
    'bytes',
    'cached',
    'full',
  ),
  // WP-63: a clone refused because its host is not on the org's list; the host and path only.
  'x.pipeshub.repository.unavailable': pick('host', 'path', 'reason'),
  // WP-96 (ADR-RUN-01 D3): a tool result's provenance by who may read it, who wrote it and how many sources of each
  // kind; never a URL, a path or a record id.
  'x.pipeshub.provenance.recorded': (data: Data) => provenanceAudit(data),
  // G-35: a knowledge export by its counts and a digest of its arguments, never a record.
  'x.pipeshub.knowledge.exported': pick(
    'taskId',
    'phase',
    'records',
    'requested',
    'exported',
    'outcome',
    'argsDigest',
  ),
  'child.spawned': pick('childRunId', 'taskId', 'grantDigest'),
  'session.participant_changed': pick('by', 'subject', 'role', 'prevRole'),
  'session.forked': pick('by', 'fromSessionId', 'atSeq', 'intoSessionId'),
  'session.owner_changed': pick('by', 'from', 'to', 'reason'),
  // Erasure execution (REPORT 6.16): who, why and how many; counts only.
  'session.erased': (data: Data) => {
    const fields = pick('by', 'reason')(data);
    const counts = data['counts'];
    if (counts !== null && typeof counts === 'object') {
      for (const [step, count] of Object.entries(counts as Data)) {
        if (typeof count === 'number') fields[`count_${step}`] = count;
      }
    }
    return fields;
  },
});

// Looked up through a Map: an event type is data, and an inherited name (`constructor`) must not resolve.
const BUILT_IN_AUDIT_EVENTS: ReadonlyMap<string, (data: Data) => AuditFields> =
  new Map(Object.entries(AUDIT_EVENT_FIELDS));
const addedAuditEvents = new Map<string, (data: Data) => AuditFields>();
const addedAuditFields = new Map<string, readonly string[]>();

/**
 * Audits an event type written by code outside this module (an overlay's
 * event) by the named scalar fields of its data. A built-in type, or one
 * added twice with other fields, is refused: either would change what an
 * existing audit record holds.
 */
export function addAuditedEvent(type: string, fields: readonly string[]): void {
  const added = addedAuditFields.get(type);
  if (
    BUILT_IN_AUDIT_EVENTS.has(type) ||
    (added !== undefined && added.join('\u0000') !== fields.join('\u0000'))
  ) {
    throw new Error(`Audit event ${type} is already audited`);
  }
  addedAuditFields.set(type, [...fields]);
  addedAuditEvents.set(type, pick(...fields));
}

/**
 * `asserted`: what Node states about the event for the audit chain only
 * (`JournalEventInput.auditFields`), added beside what the data contributes;
 * the data's own fields win, and only an audited event type records either.
 */
export function auditProjection(
  type: string,
  data: Data,
  asserted?: AuditFields,
): AuditProjection | null {
  const fields = BUILT_IN_AUDIT_EVENTS.get(type) ?? addedAuditEvents.get(type);
  if (typeof fields !== 'function') return null;
  return {
    fields: {
      ...pick(...Object.keys(asserted ?? {}))(asserted ?? {}),
      ...fields(data),
    },
    dataDigest: `sha256:${createHash('sha256').update(canonicalJsonBytes(data)).digest('hex')}`,
  };
}

export function auditRecordHash(record: Omit<AuditRecord, 'hash'>): string {
  return canonicalDigest({
    v: AUDIT_CHAIN_VERSION,
    orgId: record.orgId,
    seq: record.seq,
    prevHash: record.prevHash,
    ts: record.ts,
    kind: record.kind,
    sessionId: record.sessionId,
    streamId: record.streamId,
    runId: record.runId,
    journalSeq: record.journalSeq,
    writer: record.writer,
    attemptId: record.attemptId,
    fence: record.fence,
    fields: record.fields,
    dataDigest: record.dataDigest,
  });
}

/**
 * What an entry says, without its place in the chain. Taken in the journal's
 * transaction and kept beside the entry until it is linked, so the link can
 * only add `seq`, `prevHash` and `hash`: an entry changed in between does
 * not match its digest and is not linked.
 */
export function auditEntryDigest(
  orgId: string,
  ts: string,
  entry: AuditEntry,
): string {
  return canonicalDigest({
    v: AUDIT_CHAIN_VERSION,
    orgId,
    ts,
    kind: entry.kind,
    sessionId: entry.sessionId,
    streamId: entry.streamId,
    runId: entry.runId,
    journalSeq: entry.journalSeq,
    writer: entry.writer,
    attemptId: entry.attemptId,
    fence: entry.fence,
    fields: entry.fields,
    dataDigest: entry.dataDigest,
  });
}

/** Links entries onto a chain whose head is `after` (null: a new chain). */
export function chainAuditRecords(
  orgId: string,
  after: Pick<AuditHead, 'seq' | 'hash'> | null,
  entries: readonly AuditEntry[],
  ts: string,
): AuditRecord[] {
  let seq = after?.seq ?? 0;
  let prevHash = after?.hash ?? AUDIT_GENESIS_HASH;
  return entries.map((entry) => {
    seq += 1;
    const unhashed = { ...entry, orgId, seq, prevHash, ts };
    const record = { ...unhashed, hash: auditRecordHash(unhashed) };
    prevHash = record.hash;
    return record;
  });
}

/**
 * `batches` (appends in the order they were read) with every stream's appends
 * in journal order: a stream keeps the places it had, so streams interleave
 * as they were read and no stream's records are linked out of order.
 */
export function inStreamOrder<
  Batch extends { streamId: string; journalSeq: number },
>(batches: readonly Batch[]): Batch[] {
  const byStream = new Map<string, Batch[]>();
  for (const batch of batches) {
    const stream = byStream.get(batch.streamId);
    if (stream === undefined) byStream.set(batch.streamId, [batch]);
    else stream.push(batch);
  }
  for (const stream of byStream.values()) {
    stream.sort((left, right) => left.journalSeq - right.journalSeq);
  }
  const taken = new Map<string, number>();
  return batches.map((batch) => {
    const index = taken.get(batch.streamId) ?? 0;
    taken.set(batch.streamId, index + 1);
    return (byStream.get(batch.streamId) as Batch[])[index] as Batch;
  });
}

/**
 * The order a set of appends is linked in: by the time of their journal
 * append, and every stream in journal order whatever the clocks said.
 */
export function linkOrder<
  Append extends { ts: string; streamId: string; journalSeq: number },
>(appends: readonly Append[]): Append[] {
  return inStreamOrder(
    [...appends].sort(
      (left, right) =>
        left.ts.localeCompare(right.ts) ||
        left.streamId.localeCompare(right.streamId) ||
        left.journalSeq - right.journalSeq,
    ),
  );
}

/** An entry committed and not linked for this long is reported and holds its org's anchor back. */
export const AUDIT_LINK_STALE_MS = 60_000;
/** How often each Node looks for appends whose link did not run, and links them. */
export const AUDIT_LINK_WATCH_MS = 30_000;

/** An org's appends that are committed and not yet on its chain. */
export interface AuditUnlinked {
  orgId: string;
  appends: number;
  /** `ts` of the oldest one. */
  oldestTs: string;
}

export const unlinkedAgeMs = (unlinked: AuditUnlinked, now: number): number =>
  Math.max(0, now - Date.parse(unlinked.oldestTs));

export type AuditProblemKind =
  | 'tampered'
  | 'broken_link'
  | 'gap'
  | 'reordered'
  | 'head_mismatch'
  | 'anchor_missing'
  | 'anchor_mismatch';

export interface AuditProblem {
  kind: AuditProblemKind;
  seq: number;
}

export interface AuditVerification {
  ok: boolean;
  records: number;
  lastSeq: number;
  problems: AuditProblem[];
  /**
   * Appends committed and not yet linked: a state of their own, not a break
   * (the chain that is there verifies or does not on its own).
   */
  unlinked: number;
}

/**
 * Checks `records` (as stored, ordered by seq) link from genesis, each hash
 * recomputes, the last one matches the head, and every anchor matches the
 * record at its seq. A deleted tail shows against the head or an anchor; a
 * rewritten tail with a rewritten head only against an anchor.
 */
export function verifyAuditChain(
  records: readonly AuditRecord[],
  checks: {
    head?: Pick<AuditHead, 'seq' | 'hash'> | null;
    anchors?: readonly AuditAnchor[];
    unlinked?: number;
  } = {},
): AuditVerification {
  const problems: AuditProblem[] = [];
  let expected = 1;
  let prevHash = AUDIT_GENESIS_HASH;
  const bySeq = new Map<number, AuditRecord>();
  for (const record of records) {
    if (record.seq > expected) {
      problems.push({ kind: 'gap', seq: expected });
    } else if (record.seq < expected) {
      problems.push({ kind: 'reordered', seq: record.seq });
    }
    if (record.prevHash !== prevHash) {
      problems.push({ kind: 'broken_link', seq: record.seq });
    }
    const { hash, ...unhashed } = record;
    if (auditRecordHash(unhashed) !== hash) {
      problems.push({ kind: 'tampered', seq: record.seq });
    }
    bySeq.set(record.seq, record);
    prevHash = record.hash;
    expected = Math.max(expected, record.seq + 1);
  }
  const last = records.at(-1);
  const lastSeq = last?.seq ?? 0;
  const head = checks.head;
  if (
    head !== undefined &&
    head !== null &&
    (head.seq !== lastSeq || head.hash !== (last?.hash ?? AUDIT_GENESIS_HASH))
  ) {
    problems.push({ kind: 'head_mismatch', seq: head.seq });
  }
  for (const anchor of checks.anchors ?? []) {
    const record = bySeq.get(anchor.seq);
    if (record === undefined) {
      problems.push({ kind: 'anchor_missing', seq: anchor.seq });
    } else if (record.hash !== anchor.hash) {
      problems.push({ kind: 'anchor_mismatch', seq: anchor.seq });
    }
  }
  return {
    ok: problems.length === 0,
    records: records.length,
    lastSeq,
    problems,
    unlinked: checks.unlinked ?? 0,
  };
}
