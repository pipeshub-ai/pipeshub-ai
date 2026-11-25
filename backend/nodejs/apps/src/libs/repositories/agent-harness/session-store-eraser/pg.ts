import { EnvelopeCipher } from '../../../../modules/agent-harness/application/envelope-cipher';
import { platformRunKeyDigest } from '../../../../modules/agent-harness/adapters/crypto/platform-run-key-digest';
import {
  journalPayloadSealContext,
  journalSealContext,
} from '../../../../modules/agent-harness/domain/journal';
import { parseContentRef } from '../../../../modules/agent-harness/domain/run-content';
import {
  RUN_KEY_DIGEST_PATTERN,
  RunKeyDigest,
} from '../../../../modules/agent-harness/domain/run-key-digest';
import { sessionWorkspaceId } from '../../../../modules/agent-harness/domain/workspace';
import type { SessionStoreEraser } from '../../../../modules/agent-harness/ports/session-erasure';
import { pgPayloadId } from '../payload-store/pg';
import { orgOrJobsUnit } from '../pg-documents/org-or-jobs';
import { decodeDoc, type PgKernelTx } from '../pg-documents/pg';
import type { SessionEventDocument } from '../journal/document';
import { chunks, ERASE_BATCH } from './batches';

const REASON = 'harness session erasure';
const SCAN_BATCH = 500;

/** Visits every string in `value`; deep enough for any event schema. */
const strings = (
  value: unknown,
  visit: (text: string) => void,
  depth = 0,
): void => {
  if (typeof value === 'string') {
    visit(value);
    return;
  }
  if (depth >= 64 || value === null || typeof value !== 'object') return;
  for (const entry of Array.isArray(value)
    ? (value as unknown[])
    : Object.values(value as Record<string, unknown>)) {
    strings(entry, visit, depth + 1);
  }
};

const count = (result: { rowCount?: number | null }): number =>
  result.rowCount ?? 0;

/**
 * The harness tables keyed by a session (E7) on PostgreSQL: the journal (`harness.session_events`, whose rows become
 * tombstones that keep the envelope), its payloads, the workspaces, the runs' receipts, inputs and keys, the fork
 * requests and the snapshot rows. Every statement is a filtered delete or update, so running a step again after a
 * crash finds less to do and never fails on what is already gone. The erasure worker has no org bound: each step
 * runs as the jobs role and names the org in every predicate.
 */
export class PgSessionStoreEraser implements SessionStoreEraser {
  constructor(
    private readonly cipher: EnvelopeCipher,
    private readonly runKeys: RunKeyDigest = platformRunKeyDigest(),
  ) {}

  eraseWorkspaces(
    orgId: string,
    sessionId: string,
  ): Promise<{ manifests: number; heads: number; writers: number }> {
    // The root and every `:`-extension of it (forks, skill bundles); never `ws:{sessionId}0…`, another session's.
    const root = sessionWorkspaceId(sessionId);
    const values = [orgId, root, `${root}:`];
    return this.unit(orgId, async (k) => {
      const manifests = await k.query(
        `/* pipeshub:harness.erase_workspace_manifests */ DELETE FROM harness.workspace_manifests
          WHERE org_id = $1 AND (workspace_id = $2 OR starts_with(workspace_id, $3))`,
        values,
      );
      const heads = await k.query(
        `/* pipeshub:harness.erase_workspace_heads */ DELETE FROM harness.workspace_heads
          WHERE org_id = $1 AND (workspace_id = $2 OR starts_with(workspace_id, $3))`,
        values,
      );
      const writers = await k.query(
        `/* pipeshub:harness.erase_workspace_writers */ DELETE FROM harness.workspace_writers
          WHERE org_id = $1 AND (workspace_id = $2 OR starts_with(workspace_id, $3))`,
        values,
      );
      // The snapshots went with the session's key; their rows name objects nothing can read (OWD-12).
      await k.query(
        `/* pipeshub:harness.erase_sandbox_snapshots */ DELETE FROM harness.sandbox_snapshots
          WHERE org_id = $1 AND session_id = $2`,
        [orgId, sessionId],
      );
      return {
        manifests: count(manifests),
        heads: count(heads),
        writers: count(writers),
      };
    });
  }

  async erasePayloads(
    orgId: string,
    sessionId: string,
    runIds: readonly string[],
  ): Promise<{ payloads: number }> {
    const owned = await this.unit(orgId, async (k) =>
      count(
        await k.query(
          `/* pipeshub:harness.erase_owned_payloads */ DELETE FROM harness.journal_payloads
            WHERE org_id = $1 AND owner_session_id = $2`,
          [orgId, sessionId],
        ),
      ),
    );
    // Payloads stored with no owner are found through the events that point at them, read before the journal step
    // tombstones those events.
    const unownedIds = [
      ...(await this.unownedPayloadIds(orgId, sessionId, new Set(runIds))),
    ];
    let unowned = 0;
    for (let start = 0; start < unownedIds.length; start += SCAN_BATCH) {
      const batch = unownedIds.slice(start, start + SCAN_BATCH);
      unowned += await this.unit(orgId, async (k) =>
        count(
          await k.query(
            `/* pipeshub:harness.erase_unowned_payloads */ DELETE FROM harness.journal_payloads
              WHERE org_id = $1 AND id = ANY($2::text[]) AND owner_session_id IS NULL`,
            [orgId, batch],
          ),
        ),
      );
    }
    return { payloads: owned + unowned };
  }

  async tombstoneJournal(
    orgId: string,
    sessionId: string,
    runIds: readonly string[],
  ): Promise<{
    events: number;
    v1Events: number;
    receipts: number;
    admissionReceipts: number;
    forkRecords: number;
    runInputs: number;
    runKeys: number;
  }> {
    const counts = await this.unit(orgId, async (k) => {
      const events = await k.query(
        `/* pipeshub:harness.tombstone_session_events */ UPDATE harness.session_events
            SET doc = (doc - 'sealed' - 'data' - 'labels')
              || jsonb_build_object('erased', true, 'contentBytes', octet_length(COALESCE(doc->'sealed'->>'ct', '')))
          WHERE org_id = $1 AND session_id = $2 AND (doc->>'erased') IS DISTINCT FROM 'true'`,
        [orgId, sessionId],
      );
      const v1Events = await k.query(
        `/* pipeshub:harness.erase_run_events */ DELETE FROM harness.run_events WHERE org_id = $1 AND session_id = $2`,
        [orgId, sessionId],
      );
      const receipts = await k.query(
        `/* pipeshub:harness.erase_command_receipts */ DELETE FROM harness.command_receipts
          WHERE org_id = $1 AND run_id = ANY($2::text[])`,
        [orgId, [...runIds]],
      );
      const admissionReceipts = await k.query(
        `/* pipeshub:harness.erase_admission_receipts */ DELETE FROM harness.admission_receipts
          WHERE org_id = $1 AND session_id = $2`,
        [orgId, sessionId],
      );
      // Fork requests name both sessions; either side's erasure takes them (SS3).
      const forkRecords = await k.query(
        `/* pipeshub:harness.erase_session_forks */ DELETE FROM harness.session_forks
          WHERE org_id = $1 AND (source_session_id = $2 OR fork_session_id = $2)`,
        [orgId, sessionId],
      );
      // A run's turn label names the records its answer drew on (P2.5a): it goes with the content. Its follow-up
      // suggestions are emptied, not removed, because `storeFollowups` writes only where none are stored, so a
      // suggestion call still in flight cannot put them back (CLEANUP-1).
      const runInputs = await k.query(
        `/* pipeshub:harness.erase_run_inputs */ UPDATE harness.runs
            SET doc = (doc - 'label') || '{"input": null, "agentDefinition": null, "followups": []}'::jsonb
          WHERE org_id = $1 AND session_id = $2 AND (
            COALESCE(jsonb_typeof(doc->'input'), 'null') <> 'null'
            OR COALESCE(jsonb_typeof(doc->'agentDefinition'), 'null') <> 'null'
            OR doc ? 'label'
            OR doc->'followups' IS DISTINCT FROM '[]'::jsonb)`,
        [orgId, sessionId],
      );
      return {
        events: count(events),
        v1Events: count(v1Events),
        receipts: count(receipts),
        admissionReceipts: count(admissionReceipts),
        forkRecords: count(forkRecords),
        runInputs: count(runInputs),
      };
    });
    return {
      ...counts,
      runKeys: await this.digestLegacyRunKeys(orgId, sessionId),
    };
  }

  /**
   * A run an older build wrote keeps the caller's key; its tombstone keeps the digest (QA15-F3). One statement per
   * batch of runs, not per run: each row still names the key it read, so a key changed since is left alone.
   */
  private digestLegacyRunKeys(
    orgId: string,
    sessionId: string,
  ): Promise<number> {
    return this.unit(orgId, async (k) => {
      const { rows } = await k.query<{ run_id: string; key: string }>(
        `/* pipeshub:harness.runs_idempotency_keys */ SELECT run_id, doc->>'idempotencyKey' AS key FROM harness.runs
          WHERE org_id = $1 AND session_id = $2 AND doc->>'idempotencyKey' IS NOT NULL`,
        [orgId, sessionId],
      );
      const legacy = rows.filter(
        (run) => !RUN_KEY_DIGEST_PATTERN.test(run.key),
      );
      let digested = 0;
      for (const batch of chunks(legacy, ERASE_BATCH)) {
        const updated = await k.query(
          `/* pipeshub:harness.runs_digest_keys */ UPDATE harness.runs AS r
              SET doc = jsonb_set(r.doc, '{idempotencyKey}', to_jsonb(d.digest))
             FROM unnest($2::text[], $3::text[], $4::text[]) AS d(run_id, key, digest)
            WHERE r.org_id = $1 AND r.run_id = d.run_id AND r.doc->>'idempotencyKey' = d.key`,
          [
            orgId,
            batch.map((run) => run.run_id),
            batch.map((run) => run.key),
            batch.map((run) => this.runKeys.digest(orgId, run.key)),
          ],
        );
        digested += count(updated);
      }
      return digested;
    });
  }

  /** Unowned payloads the session's untombstoned events point at, written by its own runs. */
  private async unownedPayloadIds(
    orgId: string,
    sessionId: string,
    runIds: ReadonlySet<string>,
  ): Promise<Set<string>> {
    const ids = new Set<string>();
    const keep = (text: string): void => {
      const ref = parseContentRef(text);
      if (ref === null || !runIds.has(ref.writerRunId)) return;
      const id = pgPayloadId(orgId, ref.storeRef);
      if (id !== null) ids.add(id);
    };
    let after: { stream: string; seq: number } | null = null;
    for (;;) {
      const cursor: { stream: string; seq: number } | null = after;
      const rows: Array<{ stream_id: string; seq: string; doc: unknown }> =
        await this.unit(orgId, async (k) => {
          const result = await k.query<{
            stream_id: string;
            seq: string;
            doc: unknown;
          }>(
            `/* pipeshub:harness.erasure_scan_events */ SELECT stream_id, seq::text, doc FROM harness.session_events
              WHERE org_id = $1 AND session_id = $2 AND (doc->>'erased') IS DISTINCT FROM 'true'
                AND ($3::text IS NULL OR (stream_id, seq) > ($3::text, $4::bigint))
              ORDER BY stream_id, seq LIMIT $5`,
            [
              orgId,
              sessionId,
              cursor?.stream ?? null,
              cursor?.seq ?? 0,
              SCAN_BATCH,
            ],
          );
          return result.rows;
        });
      for (const row of rows) {
        const event = decodeDoc<SessionEventDocument>(row.doc);
        if (event.payloadRef !== undefined) {
          const id = pgPayloadId(orgId, event.payloadRef.ref);
          if (id !== null) ids.add(id);
          strings(await this.openPayload(orgId, event, id), keep);
        }
        if (event.sealed !== undefined)
          strings(await this.openData(event), keep);
      }
      const last = rows.at(-1);
      if (rows.length < SCAN_BATCH || last === undefined) return ids;
      after = { stream: last.stream_id, seq: Number(last.seq) };
    }
  }

  /** null when unreadable: a shredded org key leaves nothing to find in it. */
  private async openData(event: SessionEventDocument): Promise<unknown> {
    if (event.sealed === undefined) return null;
    try {
      const plain = await this.cipher.open(
        event.orgId,
        'journal.data',
        journalSealContext(event, event.type),
        event.sealed,
      );
      return JSON.parse(plain.toString('utf8')) as unknown;
    } catch {
      return null;
    }
  }

  private async openPayload(
    orgId: string,
    event: SessionEventDocument,
    id: string | null,
  ): Promise<unknown> {
    if (id === null) return null;
    // An owned payload's refs are owned by the session too and already gone.
    const bytes = await this.unit(orgId, async (k) => {
      const { rows } = await k.query<{ bytes: Buffer }>(
        `/* pipeshub:harness.erasure_unowned_payload */ SELECT bytes FROM harness.journal_payloads
          WHERE org_id = $1 AND id = $2 AND owner_session_id IS NULL`,
        [orgId, id],
      );
      return rows[0]?.bytes ?? null;
    });
    if (bytes === null) return null;
    try {
      const plain = await this.cipher.openBlob(
        orgId,
        'journal.payload',
        journalPayloadSealContext(event, event.type),
        bytes,
      );
      return JSON.parse(plain.toString('utf8')) as unknown;
    } catch {
      return null;
    }
  }

  private unit<T>(
    orgId: string,
    fn: (k: PgKernelTx) => Promise<T>,
  ): Promise<T> {
    return orgOrJobsUnit(undefined, orgId, REASON, { write: true }, fn);
  }
}
