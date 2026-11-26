import { asMongoSession } from '../durable-transaction/mongo';
import type { Tx } from '../../uow';
import { ClientSession } from 'mongoose';
import { PayloadStoreUnavailableError } from '../../../../modules/agent-harness/domain/errors';
import {
  PayloadOwner,
  PayloadReferences,
  PayloadStore,
  TransactionalPayloadStore,
} from '../../../../modules/agent-harness/ports/payload-store';
import { MAJORITY_WRITE } from '../durable-transaction/mongo';
import { AgentJournalPayloadModel } from '../journal/schema/journal.schema';
import { transactionWrites } from '../transaction-writes/mongo';

import { payloadRowId } from './names';

const REF_PREFIX = 'mongo:';

/** The document id behind a payload name of this org (a `pg:` name a rollback carried back too, `names.ts`); null for another store's or org's. */
export const mongoPayloadId = payloadRowId;

/**
 * Claim-check payloads in their own collection, content-addressed per org.
 * Works on every topology the journal runs on (no shared-volume requirement
 * for local disk [R-5b]) and keeps them out of the event documents a fold
 * scans; a blob backend can replace it behind PayloadStore.
 */
export class MongoPayloadStore
  implements PayloadStore, PayloadReferences<Tx>, TransactionalPayloadStore<Tx>
{
  async put(
    orgId: string,
    hash: string,
    bytes: Buffer,
    owner?: PayloadOwner,
  ): Promise<string> {
    try {
      return await this.upsert(orgId, hash, bytes, owner, MAJORITY_WRITE);
    } catch (error) {
      throw new PayloadStoreUnavailableError('put', error);
    }
  }

  /** Unwrapped: a write conflict must reach `withTransaction` as the transient error it is. */
  putIn(
    txHandle: Tx | ClientSession,
    orgId: string,
    hash: string,
    bytes: Buffer,
    owner?: PayloadOwner,
  ): Promise<string> {
    const tx = asMongoSession(txHandle);
    return this.upsert(orgId, hash, bytes, owner, { session: tx });
  }

  private async upsert(
    orgId: string,
    hash: string,
    bytes: Buffer,
    owner: PayloadOwner | undefined,
    options: { session: ClientSession } | typeof MAJORITY_WRITE,
  ): Promise<string> {
    const id = `${orgId}:${hash}`;
    const filter = { _id: id };
    const update = {
      $setOnInsert: {
        orgId,
        hash,
        size: bytes.length,
        bytes,
        createdAt: new Date(),
        ...(owner !== undefined
          ? { owner: structuredClone(owner), referenced: false }
          : {}),
      },
    };
    const write = (): Promise<unknown> =>
      AgentJournalPayloadModel.updateOne(filter, update, {
        upsert: true,
        ...options,
      });
    // In a transaction that batches its writes, the upsert joins the batch (PERF-E3): it is idempotent, and the
    // mark that follows it in the transaction joins it after it.
    await ('session' in options
      ? transactionWrites.update(
          options.session,
          AgentJournalPayloadModel,
          { name: 'updateOne', filter, update, upsert: true },
          write,
        )
      : write());
    return `${REF_PREFIX}${id}`;
  }

  async get(orgId: string, ref: string): Promise<Buffer | null> {
    const id = mongoPayloadId(orgId, ref);
    if (id === null) return null;
    let document: { bytes?: unknown } | null;
    try {
      document = await AgentJournalPayloadModel.findOne({ _id: id, orgId })
        .read('primary')
        .readConcern('majority')
        .lean();
    } catch (error) {
      throw new PayloadStoreUnavailableError('get', error);
    }
    if (!document) return null;
    const bytes: unknown = document.bytes;
    // lean() returns BSON Binary for Buffer paths.
    if (Buffer.isBuffer(bytes)) return bytes;
    const binary = bytes as { buffer?: Uint8Array };
    return binary.buffer ? Buffer.from(binary.buffer) : null;
  }

  async markReferenced(
    orgId: string,
    refs: readonly string[],
    txHandle: Tx | ClientSession,
  ): Promise<void> {
    const tx = asMongoSession(txHandle);
    const ids = refs.flatMap((ref) => {
      const id = mongoPayloadId(orgId, ref);
      return id === null ? [] : [id];
    });
    if (ids.length === 0) return;
    // Only documents still unmarked are written, so two commits that cite one
    // payload conflict at most once.
    const filter = { _id: { $in: ids }, orgId, referenced: false };
    const update = { $set: { referenced: true } };
    await transactionWrites.update(
      tx,
      AgentJournalPayloadModel,
      { name: 'updateMany', filter, update },
      () =>
        AgentJournalPayloadModel.updateMany(filter, update, { session: tx }),
    );
  }
}
