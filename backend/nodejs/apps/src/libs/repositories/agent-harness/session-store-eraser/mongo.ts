import { idOperator } from '../../id-filter-guard/mongo';
import { escapeRegExp } from '../../../../utils/escape-regexp';
import { EnvelopeCipher } from '../../../../modules/agent-harness/application/envelope-cipher';
import {
  journalPayloadSealContext,
  journalSealContext,
} from '../../../../modules/agent-harness/domain/journal';
import { parseContentRef } from '../../../../modules/agent-harness/domain/run-content';
import { sessionWorkspaceId } from '../../../../modules/agent-harness/domain/workspace';
import {
  RUN_KEY_DIGEST_PATTERN,
  RunKeyDigest,
} from '../../../../modules/agent-harness/domain/run-key-digest';
import { platformRunKeyDigest } from '../../../../modules/agent-harness/adapters/crypto/platform-run-key-digest';
import { SessionStoreEraser } from '../../../../modules/agent-harness/ports/session-erasure';
import { MAJORITY_WRITE } from '../durable-transaction/mongo';
import { chunks, ERASE_BATCH } from './batches';
import { SESSION_RUNS_BATCH } from '../session-erasure/mongo';
import {
  AgentAdmissionReceiptModel,
  AgentCommandReceiptModel,
  AgentEventModel,
  AgentRunModel,
  AgentWorkspaceHeadModel,
} from '../execution/schema/execution.schema';
import {
  AgentJournalPayloadModel,
  SessionEventDocument,
  SessionEventModel,
} from '../journal/schema/journal.schema';
import { mongoPayloadId } from '../payload-store/mongo';
import { AgentSessionForkModel } from '../session-forks/schema/session-forks.schema';
import { AgentSandboxSnapshotModel } from '../sandbox-snapshots/schema/sandbox-snapshots.schema';
import {
  AgentWorkspaceManifestModel,
  AgentWorkspaceWriterModel,
} from '../workspace/schema/workspace.schema';

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

const binaryBytes = (bytes: unknown): Buffer | null => {
  if (Buffer.isBuffer(bytes)) return bytes;
  const binary = bytes as { buffer?: Uint8Array } | null;
  return binary?.buffer ? Buffer.from(binary.buffer) : null;
};

/**
 * The harness collections keyed by a session (E7). Every operation is a
 * filtered delete or update, so running one again after a crash finds less to
 * do and never fails on what is already gone.
 */
export class MongoSessionStoreEraser implements SessionStoreEraser {
  constructor(
    private readonly cipher: EnvelopeCipher,
    private readonly runKeys: RunKeyDigest = platformRunKeyDigest(),
  ) {}

  async eraseWorkspaces(
    orgId: string,
    sessionId: string,
  ): Promise<{ manifests: number; heads: number; writers: number }> {
    // The root and every `:`-extension of it (forks, skill bundles); never
    // `ws:{sessionId}0…`, another session's workspace.
    const workspaceId = idOperator({
      $regex: `^${escapeRegExp(sessionWorkspaceId(sessionId))}(?::|$)`,
    });
    const manifests = await AgentWorkspaceManifestModel.deleteMany(
      { orgId, workspaceId },
      MAJORITY_WRITE,
    );
    const heads = await AgentWorkspaceHeadModel.deleteMany(
      { orgId, workspaceId },
      MAJORITY_WRITE,
    );
    const writers = await AgentWorkspaceWriterModel.deleteMany(
      { orgId, workspaceId },
      MAJORITY_WRITE,
    );
    // The snapshots went with the session's key; their rows name objects nothing can read (OWD-12).
    await AgentSandboxSnapshotModel.deleteMany(
      { orgId, sessionId },
      MAJORITY_WRITE,
    );
    return {
      manifests: manifests.deletedCount,
      heads: heads.deletedCount,
      writers: writers.deletedCount,
    };
  }

  async erasePayloads(
    orgId: string,
    sessionId: string,
    runIds: readonly string[],
  ): Promise<{ payloads: number }> {
    const owned = await AgentJournalPayloadModel.deleteMany(
      { orgId, 'owner.sessionId': sessionId },
      MAJORITY_WRITE,
    );
    // Payloads stored before owners existed are found through the events that
    // point at them, read before the journal step tombstones those events.
    const legacy = await this.legacyPayloadIds(
      orgId,
      sessionId,
      new Set(runIds),
    );
    let unowned = 0;
    for (const batch of chunks([...legacy], ERASE_BATCH)) {
      const deleted = await AgentJournalPayloadModel.deleteMany(
        { _id: { $in: batch }, orgId, owner: idOperator({ $exists: false }) },
        MAJORITY_WRITE,
      );
      unowned += deleted.deletedCount;
    }
    return { payloads: owned.deletedCount + unowned };
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
    const events = await SessionEventModel.updateMany(
      { orgId, sessionId, erased: { $ne: true } },
      [
        {
          $set: {
            erased: true,
            contentBytes: {
              $strLenBytes: { $ifNull: ['$sealed.ct', ''] },
            },
          },
        },
        { $unset: ['sealed', 'data', 'labels'] },
      ],
      { ...MAJORITY_WRITE, updatePipeline: true },
    );
    const v1Events = await AgentEventModel.deleteMany(
      { orgId, sessionId },
      MAJORITY_WRITE,
    );
    let receipts = 0;
    for (const batch of chunks(runIds, ERASE_BATCH)) {
      const deleted = await AgentCommandReceiptModel.deleteMany(
        { orgId, runId: { $in: batch } },
        MAJORITY_WRITE,
      );
      receipts += deleted.deletedCount;
    }
    const admissionReceipts = await AgentAdmissionReceiptModel.deleteMany(
      { 'result.sessionId': sessionId, 'result.run.orgId': orgId },
      MAJORITY_WRITE,
    );
    // Fork requests name both sessions; either side's erasure takes them (SS3).
    const forkRecords = await AgentSessionForkModel.deleteMany(
      {
        orgId,
        $or: [{ sourceSessionId: sessionId }, { forkSessionId: sessionId }],
      },
      MAJORITY_WRITE,
    );
    // A run's turn label names the records its answer drew on (P2.5a): it goes with the content. Its follow-up
    // suggestions are model text drawn from the conversation (CLEANUP-1): they are emptied, not unset, because
    // `storeFollowups` writes only where none are stored, so a suggestion call still in flight cannot put them back.
    const runInputs = await AgentRunModel.updateMany(
      {
        orgId,
        sessionId,
        $or: [
          { input: { $ne: null } },
          { agentDefinition: { $ne: null } },
          { label: { $exists: true } },
          { followups: { $ne: [] } },
        ],
      },
      {
        $set: { input: null, agentDefinition: null, followups: [] },
        $unset: { label: '' },
      },
      MAJORITY_WRITE,
    );
    return {
      events: events.modifiedCount,
      v1Events: v1Events.deletedCount,
      receipts,
      admissionReceipts: admissionReceipts.deletedCount,
      forkRecords: forkRecords.deletedCount,
      runInputs: runInputs.modifiedCount,
      runKeys: await this.digestLegacyRunKeys(orgId, sessionId),
    };
  }

  /**
   * A run an older build wrote keeps the caller's key; its tombstone keeps the digest (QA15-F3). One write per batch
   * of runs, not per run: each update still names the key it read, so a key changed since is left alone.
   */
  private async digestLegacyRunKeys(
    orgId: string,
    sessionId: string,
  ): Promise<number> {
    const cursor = AgentRunModel.find(
      {
        orgId,
        sessionId,
        idempotencyKey: { $not: RUN_KEY_DIGEST_PATTERN },
      },
      { _id: 0, runId: 1, idempotencyKey: 1 },
    )
      .read('primary')
      .lean()
      .batchSize(SESSION_RUNS_BATCH)
      .cursor();
    let digested = 0;
    let batch: Array<{ runId: string; idempotencyKey: string }> = [];
    const flush = async (): Promise<void> => {
      if (batch.length === 0) return;
      const written = await AgentRunModel.bulkWrite(
        batch.map((run) => ({
          updateOne: {
            filter: {
              orgId,
              runId: run.runId,
              idempotencyKey: run.idempotencyKey,
            },
            update: {
              $set: {
                idempotencyKey: this.runKeys.digest(orgId, run.idempotencyKey),
              },
            },
          },
        })),
        { ordered: false, ...MAJORITY_WRITE },
      );
      digested += written.modifiedCount;
      batch = [];
    };
    for await (const run of cursor) {
      batch.push(run);
      if (batch.length >= ERASE_BATCH) await flush();
    }
    await flush();
    return digested;
  }

  /** Unowned payloads the session's untombstoned events point at, written by its own runs. */
  private async legacyPayloadIds(
    orgId: string,
    sessionId: string,
    runIds: ReadonlySet<string>,
  ): Promise<Set<string>> {
    const ids = new Set<string>();
    const keep = (text: string): void => {
      const ref = parseContentRef(text);
      if (ref === null || !runIds.has(ref.writerRunId)) return;
      const id = mongoPayloadId(orgId, ref.storeRef);
      if (id !== null) ids.add(id);
    };
    const cursor = SessionEventModel.find({
      orgId,
      sessionId,
      erased: { $ne: true },
    })
      .read('primary')
      .lean()
      .batchSize(SCAN_BATCH)
      .cursor();
    for await (const document of cursor) {
      const event = document as SessionEventDocument;
      if (event.payloadRef !== undefined) {
        const id = mongoPayloadId(orgId, event.payloadRef.ref);
        if (id !== null) ids.add(id);
        strings(await this.openPayload(orgId, event, id), keep);
      }
      if (event.sealed !== undefined) {
        strings(await this.openData(event), keep);
      }
    }
    return ids;
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
    const stored = await AgentJournalPayloadModel.findOne({
      _id: id,
      orgId,
      owner: idOperator({ $exists: false }),
    })
      .read('primary')
      .lean();
    const bytes = binaryBytes(stored?.bytes);
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
}
