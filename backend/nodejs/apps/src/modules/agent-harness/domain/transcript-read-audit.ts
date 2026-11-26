import { canonicalDigest } from '../contracts/canonical-json';
import type { AuditEntry } from './audit';
import { principalRefFor } from './run-spec';

export const TRANSCRIPT_READ_KIND = 'transcript.read_by_non_participant';

/**
 * The views that show part of a run's transcript (a held call's arguments) to
 * someone outside its session, such as the org's approvers in the approvals
 * queue. The session ACL keeps every other
 * transcript route to the session's participants (SEC-LG-02).
 */
export type TranscriptReadView = 'approvals_queue' | 'unknown_effects';

export interface TranscriptRead {
  orgId: string;
  readerId: string;
  view: TranscriptReadView;
  /** The runs whose content the answer carries, each once, with how many of its calls it shows. */
  runs: ReadonlyArray<{
    runId: string;
    sessionId: string;
    actorId: string;
    calls: number;
  }>;
}

/** A session's stream of reads by non-participants: its own, as `wsdl:` is for downloads. */
export const transcriptReadStreamId = (sessionId: string): string =>
  `txr:${sessionId}`;

/**
 * One chain entry per run read: who read whose run, through which view, and
 * how many calls. Never a byte of the content: the chain outlives the
 * session's erasure. `at` (ms) orders the stream.
 */
export function transcriptReadEntries(
  read: TranscriptRead,
  at: number,
): AuditEntry[] {
  return read.runs.map((run) => ({
    kind: TRANSCRIPT_READ_KIND,
    sessionId: run.sessionId,
    streamId: transcriptReadStreamId(run.sessionId),
    runId: run.runId,
    journalSeq: at,
    writer: 'node',
    attemptId: null,
    fence: null,
    fields: {
      by: principalRefFor(read.readerId),
      of: principalRefFor(run.actorId),
      view: read.view,
      calls: run.calls,
    },
    dataDigest: canonicalDigest({
      orgId: read.orgId,
      readerId: read.readerId,
      view: read.view,
      ...run,
      at,
    }),
  }));
}

/** Records a read before its answer is sent; a read that cannot be recorded throws, and nothing is sent. */
export type RecordTranscriptRead = (read: TranscriptRead) => Promise<void>;

/** The runs of `items` not the reader's own, grouped, in first-seen order. */
export function readRuns(
  readerId: string,
  items: ReadonlyArray<{ runId: string; sessionId: string; actorId: string }>,
): TranscriptRead['runs'] {
  const runs = new Map<
    string,
    { runId: string; sessionId: string; actorId: string; calls: number }
  >();
  for (const item of items) {
    if (item.actorId === readerId) continue;
    const seen = runs.get(item.runId);
    if (seen !== undefined) seen.calls += 1;
    else
      runs.set(item.runId, {
        runId: item.runId,
        sessionId: item.sessionId,
        actorId: item.actorId,
        calls: 1,
      });
  }
  return [...runs.values()];
}
