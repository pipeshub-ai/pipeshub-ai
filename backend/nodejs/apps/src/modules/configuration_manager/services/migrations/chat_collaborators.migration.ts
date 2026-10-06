import { isDeepStrictEqual } from 'util';
import { Types } from 'mongoose';
import type { AnyBulkWriteOperation } from 'mongodb';
import { Logger } from '../../../../libs/services/logger.service';
import { KeyValueStoreService } from '../../../../libs/services/keyValueStore.service';
import { configPaths } from '../../paths/paths';
import { ChatSession } from '../../../enterprise_search/schema/chat.session.schema';

const DEFAULT_BATCH_SIZE = 200;

export interface ChatCollaboratorsMigrationResult {
  scanned: number;
  normalized: number;
  downgradedWrites: number;
  raced: number;
  errored: number;
}

export interface NormalizedCollaborators {
  sharedWith: Record<string, unknown>[];
  isShared: boolean;
  downgradedWrites: number;
  droppedMalformed: number;
}

type Row = Record<string, unknown>;

const isRecord = (value: unknown): value is Row =>
  value !== null && typeof value === 'object' && !Array.isArray(value);

const idKey = (value: unknown): string | undefined =>
  value === undefined || value === null
    ? undefined
    : (value as string | Types.ObjectId).toString();

/**
 * Pure normalisation of a legacy `sharedWith` array (51 §1.4, 74 §1).
 * Legacy user rows (no `principalType`) become read-only `user` rows (D9: every
 * legacy `write` is downgraded before collaboration goes live); duplicates
 * collapse to the highest level; the owner's own row and any `_id` are dropped.
 * Rows we do not understand (team rows, unknown principal types) are kept as-is:
 * a migration never deletes what it cannot interpret. Only user-shaped rows with
 * no `userId` are dropped, since they grant nothing.
 * Idempotent: re-normalising its own output is a no-op.
 */
export function normalizeCollaborators(
  rows: unknown,
  ownerId: unknown,
  updatedAt: Date,
): NormalizedCollaborators {
  const owner = idKey(ownerId);
  const byUser = new Map<string, Row>();
  const kept: Row[] = [];
  let downgradedWrites = 0;
  let droppedMalformed = 0;

  for (const raw of Array.isArray(rows) ? rows : []) {
    if (!isRecord(raw)) {
      droppedMalformed += 1;
      continue;
    }
    const { _id: _drop, ...row } = raw;
    const type = row.principalType;
    const hasUser = row.userId !== undefined && row.userId !== null;

    const isUserRow =
      type === 'user' || (type === undefined && row.teamId === undefined);
    if (!isUserRow) {
      kept.push(row);
      continue;
    }
    if (!hasUser) {
      droppedMalformed += 1;
      continue;
    }
    const key = idKey(row.userId) as string;
    if (key === owner) {
      continue;
    }

    const isLegacy = type === undefined;
    let accessLevel = row.accessLevel === 'write' ? 'write' : 'read';
    if (isLegacy && accessLevel === 'write') {
      accessLevel = 'read';
      downgradedWrites += 1;
    }

    const existing = byUser.get(key);
    if (existing) {
      if (accessLevel === 'write') {
        existing.accessLevel = 'write';
      }
      continue;
    }
    byUser.set(key, {
      principalType: 'user',
      userId: row.userId,
      accessLevel,
      addedBy: isLegacy ? ownerId : (row.addedBy ?? ownerId),
      addedAt: isLegacy ? updatedAt : (row.addedAt ?? updatedAt),
    });
  }

  const sharedWith = [...byUser.values(), ...kept];
  return {
    sharedWith,
    isShared: sharedWith.length > 0,
    downgradedWrites,
    droppedMalformed,
  };
}

const plain = (value: unknown): unknown =>
  JSON.parse(JSON.stringify(value ?? null));

/**
 * Rewrites every shared `chatSessions` row into the collaborator shape
 * (`/migrations/chat_collaborators_v1`). Must finish before the collaboration
 * flag is enabled so no legacy `write` survives (D9).
 *
 * Skips (no flag, retried next boot) until `chat_sessions_v1` marks both legacy
 * collections copied. Writes are optimistic (`{_id, sharedWith: <original>}`):
 * a concurrent share/unshare makes the write a miss, counted `raced`, retried
 * next boot. The completion flag is written only when `errored` and `raced` are 0.
 */
export class ChatCollaboratorsMigration {
  constructor(
    private readonly logger: Logger,
    private readonly kvStore: KeyValueStoreService,
    private readonly batchSize: number = DEFAULT_BATCH_SIZE,
  ) {}

  async run(): Promise<ChatCollaboratorsMigrationResult> {
    const result: ChatCollaboratorsMigrationResult = {
      scanned: 0,
      normalized: 0,
      downgradedWrites: 0,
      raced: 0,
      errored: 0,
    };

    if (await this.isDone()) {
      this.logger.info(
        'Chat collaborators migration already completed; skipping',
      );
      return result;
    }
    if (!(await this.isChatSessionsMigrationDone())) {
      this.logger.info(
        'Chat sessions migration has not completed yet; deferring chat collaborators migration to next boot',
      );
      return result;
    }

    let lastId: Types.ObjectId | null = null;
    for (;;) {
      const batch: Row[] = await ChatSession.collection
        .find(
          {
            $or: [{ 'sharedWith.0': { $exists: true } }, { isShared: true }],
            ...(lastId ? { _id: { $gt: lastId } } : {}),
          },
          {
            projection: {
              userId: 1,
              sharedWith: 1,
              isShared: 1,
              updatedAt: 1,
              createdAt: 1,
            },
          },
        )
        .sort({ _id: 1 })
        .limit(this.batchSize)
        .toArray();
      if (batch.length === 0) {
        break;
      }
      lastId = batch[batch.length - 1]?._id as Types.ObjectId;

      try {
        await this.processBatch(batch, result);
      } catch (error) {
        result.errored += batch.length;
        this.logger.error(
          'Chat collaborators migration batch failed; retrying next boot',
          {
            error: error instanceof Error ? error.message : String(error),
          },
        );
      }
    }

    this.logger.info('Chat collaborators migration pass finished', {
      ...result,
    });
    if (result.errored === 0 && result.raced === 0) {
      await this.writeFlag(result);
    } else {
      this.logger.warn(
        'Chat collaborators migration incomplete; completion flag NOT written, retrying next boot',
        { ...result },
      );
    }
    return result;
  }

  private async processBatch(
    batch: Row[],
    result: ChatCollaboratorsMigrationResult,
  ): Promise<void> {
    const ops: AnyBulkWriteOperation[] = [];
    let downgradedInOps = 0;

    for (const doc of batch) {
      result.scanned += 1;
      const stamp = doc.updatedAt ?? doc.createdAt;
      const normalized = normalizeCollaborators(
        doc.sharedWith,
        doc.userId,
        stamp instanceof Date ? stamp : new Date(),
      );
      const unchanged =
        isDeepStrictEqual(
          plain(normalized.sharedWith),
          plain(doc.sharedWith ?? []),
        ) && (doc.isShared === true) === normalized.isShared;
      if (unchanged) {
        continue;
      }
      downgradedInOps += normalized.downgradedWrites;
      ops.push({
        updateOne: {
          filter: {
            _id: doc._id as Types.ObjectId,
            // `[]` never matches a missing field; `null` matches missing or null, else the row would race forever.
            sharedWith: doc.sharedWith ?? null,
          },
          update: {
            $set: {
              sharedWith: normalized.sharedWith,
              isShared: normalized.isShared,
            },
            $inc: { aclVersion: 1 },
          },
        },
      });
    }

    if (ops.length === 0) {
      return;
    }
    const written = await ChatSession.collection.bulkWrite(ops, {
      ordered: false,
    });
    const missed = ops.length - written.matchedCount;
    result.raced += missed;
    result.normalized += written.matchedCount;
    // On a race the flag is withheld and the missed rows are re-counted next boot.
    result.downgradedWrites += downgradedInOps;
  }

  private async isChatSessionsMigrationDone(): Promise<boolean> {
    try {
      const raw = await this.kvStore.get<string>(
        configPaths.chatSessionsMigration,
      );
      if (raw === null || raw === '') {
        return false;
      }
      const flag = JSON.parse(raw) as {
        conversationsMigrated?: boolean;
        agentConversationsMigrated?: boolean;
      };
      return (
        flag.conversationsMigrated === true &&
        flag.agentConversationsMigrated === true
      );
    } catch (error) {
      this.logger.warn(
        'Failed to read chat sessions migration flag; deferring chat collaborators migration to next boot',
        { error: error instanceof Error ? error.message : String(error) },
      );
      return false;
    }
  }

  private async isDone(): Promise<boolean> {
    try {
      return Boolean(
        await this.kvStore.get<string>(configPaths.chatCollaboratorsMigration),
      );
    } catch {
      return false;
    }
  }

  private async writeFlag(
    result: ChatCollaboratorsMigrationResult,
  ): Promise<void> {
    try {
      await this.kvStore.set(
        configPaths.chatCollaboratorsMigration,
        JSON.stringify(result),
      );
    } catch (error) {
      this.logger.warn(
        'Chat collaborators migration succeeded but failed to persist the completion flag; will retry on next boot',
        { error: error instanceof Error ? error.message : String(error) },
      );
    }
  }
}
