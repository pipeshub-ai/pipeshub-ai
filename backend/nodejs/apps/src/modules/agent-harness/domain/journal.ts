import {
  EventWriter,
  JOURNAL_EVENT_SCHEMAS,
  JOURNAL_EVENT_WRITERS,
} from '../contracts/event-catalog';
import {
  assertNoReservedKeys,
  canonicalJsonBytes,
  CanonicalJsonError,
} from '../contracts/canonical-json';
import type { Visibility } from '../contracts/generated/core/enums';
import { JournalRejectedError } from './errors';
import { SealContext, SealedContent } from './data-protection';
import type { AuditFields, AuditProjection } from './audit';

export type { EventWriter };

/** Larger payloads are claim-checked to the payload store [R-5]. */
export const JOURNAL_INLINE_PAYLOAD_MAX_BYTES = 256 * 1024;

/** An event whose data is stored claim-checked (sealed, by ref) rather than inline. */
export const isClaimChecked = (
  data: Record<string, unknown>,
  inlineMaxBytes = JOURNAL_INLINE_PAYLOAD_MAX_BYTES,
): boolean => canonicalJsonBytes(data).length > inlineMaxBytes;
export const JOURNAL_PAYLOAD_MISSING = 'journal_payload_missing';

export type JournalVisibility = Visibility;

export interface PayloadRef {
  ref: string;
  /** `sha256:<hex>` over the canonical JSON bytes of `data`. */
  hash: string;
  size: number;
}

/** What a writer supplies; the store assigns the rest of the envelope. */
export interface JournalEventInput {
  type: string;
  schemaVersion: number;
  critical: boolean;
  visibility: JournalVisibility;
  labels?: Record<string, unknown>;
  data: Record<string, unknown>;
  /**
   * What Node asserts about the event for the audit chain only (OWD-2: who
   * the run acts as): recorded with the event's audit record, never stored
   * in the journal, so no worker reads it and no stream changes shape.
   */
  auditFields?: AuditFields;
}

/** The stream an append goes to and the authority it was made under. */
export interface JournalPosition {
  orgId: string;
  sessionId: string;
  /** sessionId for the root run, childRunId for a child run. */
  streamId: string;
  /** Absent for a session's own events (participants, fork), which belong to no run. */
  runId?: string;
  attemptId?: string;
  fence?: number;
}

export interface JournalAppend extends JournalPosition {
  writer: EventWriter;
  events: JournalEventInput[];
}

/**
 * Validated and claim-checked: exactly one of `data` / `payloadRef`. The
 * sealing journal store replaces `data` with `sealed` at append; a stored
 * event never keeps plaintext `data` (REPORT 6.16).
 */
export interface PreparedJournalEvent extends Omit<JournalEventInput, 'data'> {
  data?: Record<string, unknown>;
  payloadRef?: PayloadRef;
  sealed?: SealedContent;
  /** Audit metadata of a claim-checked event, taken while its data was at hand; never stored. */
  audit?: AuditProjection;
  /** Payload store refs a claim-checked event's data points at, taken while it was at hand; never stored. */
  references?: string[];
  /** What each journal recorder took of a claim-checked event, by its name, while its data was at hand; never stored. */
  taken?: Readonly<Record<string, unknown>>;
}

export interface PreparedJournalAppend extends JournalPosition {
  writer: EventWriter;
  events: PreparedJournalEvent[];
}

/** One stored envelope (REPORT 6.1.2); `ts` is DB time. */
export interface JournalEvent extends PreparedJournalEvent {
  orgId: string;
  sessionId: string;
  streamId: string;
  seq: number;
  runId?: string;
  attemptId?: string;
  fence?: number;
  ts: string;
  writer: EventWriter;
  /** E7: the session was erased; this is the envelope's tombstone, its content is gone. */
  erased?: boolean;
  contentBytes?: number;
}

export interface JournalAppendResult {
  streamId: string;
  firstSeq: number;
  lastSeq: number;
  ts: string;
}

const VALIDATION_TS = '1970-01-01T00:00:00.000Z';

/**
 * The envelope a sealed event's content is bound to (its AAD): everything
 * known before the append. `seq` is not: the store assigns it inside the
 * append, after sealing.
 */
export function journalSealContext(
  position: Pick<
    JournalEvent,
    'sessionId' | 'streamId' | 'runId' | 'attemptId' | 'fence' | 'writer'
  >,
  type: string,
): SealContext {
  return {
    sessionId: position.sessionId,
    streamId: position.streamId,
    runId: position.runId ?? '',
    attemptId: position.attemptId ?? '',
    fence: position.fence ?? 0,
    writer: position.writer,
    type,
  };
}

/**
 * A claim-checked payload is sealed when the append is prepared, which can be
 * before its attempt and fence are known (the spec pin at claim), so it is
 * bound to the stream, run and type only.
 */
export function journalPayloadSealContext(
  position: Pick<JournalEvent, 'sessionId' | 'streamId' | 'runId'>,
  type: string,
): SealContext {
  return {
    sessionId: position.sessionId,
    streamId: position.streamId,
    runId: position.runId ?? '',
    type,
  };
}

const definedEntries = (
  value: Record<string, unknown>,
): Record<string, unknown> =>
  Object.fromEntries(
    Object.entries(value).filter(([, entry]) => entry !== undefined),
  );

/**
 * Rejects an event whose type this build does not know, whose writer the
 * catalog does not allow, or whose envelope/data fails the generated schema.
 * Store-assigned fields (seq, ts) get placeholders here: they are ours.
 */
export function validateJournalAppend(append: JournalAppend): void {
  append.events.forEach((event, index) => {
    const schema = JOURNAL_EVENT_SCHEMAS[event.type];
    const writers = JOURNAL_EVENT_WRITERS[event.type];
    if (schema === undefined || writers === undefined) {
      throw new JournalRejectedError(
        'UNKNOWN_EVENT_TYPE',
        index,
        `Unknown journal event type ${JSON.stringify(event.type)}`,
      );
    }
    if (!writers.includes(append.writer)) {
      throw new JournalRejectedError(
        'WRITER_NOT_ALLOWED',
        index,
        `${append.writer} may not append ${event.type}`,
      );
    }
    const candidate = definedEntries({
      orgId: append.orgId,
      sessionId: append.sessionId,
      streamId: append.streamId,
      seq: 1,
      runId: append.runId,
      attemptId: append.attemptId,
      fence: append.fence,
      type: event.type,
      schemaVersion: event.schemaVersion,
      critical: event.critical,
      ts: VALIDATION_TS,
      writer: append.writer,
      labels: event.labels,
      visibility: event.visibility,
      data: event.data,
    });
    try {
      assertNoReservedKeys(candidate);
    } catch (err) {
      if (!(err instanceof CanonicalJsonError)) throw err;
      throw new JournalRejectedError(
        'INVALID_EVENT',
        index,
        `Invalid ${event.type}: ${err.message}`,
      );
    }
    const parsed = schema.safeParse(candidate);
    if (!parsed.success) {
      const issue = parsed.error.issues[0];
      throw new JournalRejectedError(
        'INVALID_EVENT',
        index,
        `Invalid ${event.type}: ${issue ? `${issue.path.join('.')} ${issue.message}` : 'schema mismatch'}`,
      );
    }
    // Who else sees an event is Node's decision (I3); S6 derives it per type.
    if (append.writer === 'worker' && event.visibility !== 'owner') {
      throw new JournalRejectedError(
        'VISIBILITY_NOT_ALLOWED',
        index,
        `worker may not set visibility ${JSON.stringify(event.visibility)}`,
      );
    }
  });
}
