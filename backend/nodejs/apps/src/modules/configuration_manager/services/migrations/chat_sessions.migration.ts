import { Model } from 'mongoose';
import { Logger } from '../../../../libs/services/logger.service';
import { KeyValueStoreService } from '../../../../libs/services/keyValueStore.service';
import { configPaths } from '../../paths/paths';
import { Conversation } from '../../../enterprise_search/schema/conversation.schema';
import { AgentConversation } from '../../../enterprise_search/schema/agent.conversation.schema';
import { ChatSession } from '../../../enterprise_search/schema/chat.session.schema';
import { ChatSessionMessage } from '../../../enterprise_search/schema/chat.session.message.schema';

type LegacyModel = Model<any>;

const DEFAULT_BATCH_SIZE = 10;
/** Bounded sample used for the pre-flight schema-drift key audit. */
const SCHEMA_DRIFT_SAMPLE_SIZE = 50;

interface ChatSessionsMigrationFlag {
  conversationsMigrated?: boolean;
  agentConversationsMigrated?: boolean;
  migratedAt?: Partial<Record<'chat' | 'agent', string>>;
  totals?: Partial<Record<'chat' | 'agent', { sessions: number; messages: number }>>;
}

interface CollectionSpec {
  flagKey: 'conversationsMigrated' | 'agentConversationsMigrated';
  sessionType: 'chat' | 'agent';
  legacyModel: LegacyModel;
  /** Actual Mongo collection name — 'agentconversations', lowercase, not 'agentConversations'. */
  legacyLabel: string;
}

/**
 * Phase 2: one-time backfill that copies every document out of the frozen
 * legacy `conversations` / `agentconversations` collections into
 * `chatSessions` + `chatSessionMessages` (the Phase 1 runtime model — see
 * chat.session.schema.ts / chat.session.message.schema.ts). Phase 1 already
 * stopped writing to the legacy collections, but `chatSessions` /
 * `chatSessionMessages` are live the whole time this runs — Phase 1 sends
 * every new chat straight there. That rules out any collection-level count
 * comparison as a completion gate (`chatSessions.countDocuments({sessionType})`
 * grows from ordinary live usage, independent of this migration, and a user
 * can resume chatting on a session the instant it's migrated, growing
 * `chatSessionMessages` past what the legacy array recorded). The only count
 * check that holds under live traffic is per-document, immediately after
 * that document's own write and before its session exists to be chattable —
 * see the `storedCount` check in `migrateDocument`.
 *
 * Resumable at two granularities:
 *   - per document, via an `isMigrated` flag on the legacy schema (crash
 *     mid-run simply leaves some documents unflagged, retried next boot);
 *   - per collection, via one KV flag object holding a boolean per legacy
 *     collection, set once every document in that collection has been
 *     copied with zero errors.
 *
 * Depends on `ChatKbFiltersMigration` having completed, since that migration
 * rewrites `messages[].appliedFilters` on these same legacy documents — if
 * this migration ran first, that fix would land on documents nobody reads
 * any more. Node migrations run sequentially in `MigrationService`, but the
 * KB-filters migration can defer for minutes waiting on a Python flag and
 * return without completing, so this explicitly re-checks its flag rather
 * than relying on call order alone.
 */
export class ChatSessionsMigration {
  private readonly batchSize: number;

  constructor(
    private readonly logger: Logger,
    private readonly kvStore: KeyValueStoreService,
    batchSize?: number,
  ) {
    this.batchSize =
      batchSize && batchSize > 0 ? batchSize : DEFAULT_BATCH_SIZE;
  }

  async run(): Promise<{
    sessionsMigrated: number;
    messagesMigrated: number;
    errored: number;
  }> {
    const kbFiltersDone = await this.isKbFiltersMigrationDone();
    if (!kbFiltersDone) {
      this.logger.info(
        'Chat KB-filters migration has not completed yet; deferring chat sessions migration to next boot',
      );
      return { sessionsMigrated: 0, messagesMigrated: 0, errored: 0 };
    }

    const flag = await this.readFlag();

    const specs: CollectionSpec[] = [
      {
        flagKey: 'conversationsMigrated',
        sessionType: 'chat',
        legacyModel: Conversation,
        legacyLabel: 'conversations',
      },
      {
        flagKey: 'agentConversationsMigrated',
        sessionType: 'agent',
        legacyModel: AgentConversation,
        legacyLabel: 'agentconversations',
      },
    ];

    let sessionsMigrated = 0;
    let messagesMigrated = 0;
    let errored = 0;

    for (const spec of specs) {
      if (flag[spec.flagKey]) {
        this.logger.info(
          `Chat sessions migration for ${spec.legacyLabel} already completed; skipping`,
        );
        continue;
      }

      const result = await this.migrateCollection(spec);
      sessionsMigrated += result.sessionsMigrated;
      messagesMigrated += result.messagesMigrated;
      errored += result.errored;

      if (result.errored === 0) {
        flag[spec.flagKey] = true;
        flag.migratedAt = {
          ...flag.migratedAt,
          [spec.sessionType]: new Date().toISOString(),
        };
        flag.totals = {
          ...flag.totals,
          [spec.sessionType]: {
            sessions: result.sessionsMigrated,
            messages: result.messagesMigrated,
          },
        };
        await this.writeFlag(flag);
      }
    }

    return { sessionsMigrated, messagesMigrated, errored };
  }

  // ---------------------------------------------------------------------
  // Per-collection orchestration
  // ---------------------------------------------------------------------

  private async migrateCollection(spec: CollectionSpec): Promise<{
    sessionsMigrated: number;
    messagesMigrated: number;
    errored: number;
  }> {
    this.logger.info(
      `Starting chat sessions migration for ${spec.legacyLabel}`,
    );
    const startedAt = Date.now();

    // Pre-flight: a document with no orgId would migrate to messages with no
    // orgId, invisible to org-scoped content search and impossible to detect
    // after the fact via the native-driver copy. Hard-stop instead.
    const missingOrgIdCount = await spec.legacyModel.countDocuments({
      $or: [{ orgId: { $exists: false } }, { orgId: null }],
    });
    if (missingOrgIdCount > 0) {
      this.logger.error(
        `Chat sessions migration aborted for ${spec.legacyLabel}: ${missingOrgIdCount} document(s) missing orgId`,
        { legacyLabel: spec.legacyLabel, missingOrgIdCount },
      );
      return {
        sessionsMigrated: 0,
        messagesMigrated: 0,
        errored: missingOrgIdCount,
      };
    }

    await this.auditSchemaDrift(spec);

    let sessionsMigrated = 0;
    let messagesMigrated = 0;
    let errored = 0;

    // Advance by `_id` rather than relying on the `isMigrated` flag alone to
    // drop rows out of the filter: a document that fails is deliberately left
    // unflagged, so without a cursor it sorts back to the front of the very
    // next scan and this loop spins on it forever. Failures are retried on the
    // next boot instead, when the cursor restarts from the beginning.
    let lastId: unknown = null;
    for (;;) {
      const batch = await spec.legacyModel
        .find({
          isMigrated: { $ne: true },
          ...(lastId ? { _id: { $gt: lastId } } : {}),
        })
        .sort({ _id: 1 })
        .limit(this.batchSize)
        .lean();

      if (batch.length === 0) {
        break;
      }

      const batchLastId = batch[batch.length - 1]?._id;
      if (!batchLastId) {
        this.logger.error(
          `Chat sessions migration for ${spec.legacyLabel} stopped: scanned a document without an _id, cannot advance the cursor safely`,
        );
        errored += 1;
        break;
      }
      lastId = batchLastId;

      for (const doc of batch) {
        try {
          const migratedMessageCount = await this.migrateDocument(doc, spec);
          sessionsMigrated += 1;
          messagesMigrated += migratedMessageCount;
        } catch (error) {
          errored += 1;
          this.logger.error(
            `Failed to migrate a ${spec.legacyLabel} document — left unmigrated, will retry on next boot`,
            {
              documentId: doc._id?.toString(),
              error: error instanceof Error ? error.message : String(error),
            },
          );
        }
      }

      this.logger.info(`Chat sessions migration progress for ${spec.legacyLabel}`, {
        sessionsMigrated,
        messagesMigrated,
        errored,
        elapsedMs: Date.now() - startedAt,
      });
    }

    if (errored > 0) {
      this.logger.warn(
        `Chat sessions migration for ${spec.legacyLabel} finished with errors — completion flag NOT written, unmigrated documents retried on next boot`,
        { sessionsMigrated, messagesMigrated, errored },
      );
      return { sessionsMigrated, messagesMigrated, errored };
    }

    this.logger.info(`✅ Chat sessions migration for ${spec.legacyLabel} complete`, {
      sessionsMigrated,
      messagesMigrated,
      elapsedMs: Date.now() - startedAt,
    });

    return { sessionsMigrated, messagesMigrated, errored };
  }

  // ---------------------------------------------------------------------
  // Per-document copy
  // ---------------------------------------------------------------------

  /**
   * Copies one legacy document's messages then the session itself, via the
   * native driver (never `new ChatSession(doc)` / `new ChatSessionMessage(doc)`
   * — Mongoose strict mode silently drops any path not declared on the
   * target schema, which would be silent data loss here). `.lean()` reads
   * mean the values are already BSON-correct going back out.
   *
   * Write order is messages, then session, then the `isMigrated` flag: a
   * crash between steps leaves an interrupted document simply invisible
   * (no session row yet) rather than visible with missing history.
   *
   * Every write is an `_id`-keyed `$setOnInsert` upsert, never `$set` /
   * `replaceOne` — users keep chatting on already-migrated sessions, so a
   * stray re-run must not revert `status`, `lastActivityAt`, or title edits
   * made after the copy.
   */
  private async migrateDocument(
    doc: any,
    spec: CollectionSpec,
  ): Promise<number> {
    const messages: any[] = Array.isArray(doc.messages) ? doc.messages : [];

    if (messages.length > 0) {
      // Both checks below have to happen before the bulkWrite: an `_id`-keyed
      // upsert per message silently collapses duplicates (and, with a missing
      // `_id`, collapses every message onto one `{_id: undefined}` filter),
      // which is unrecoverable data loss once written.
      const messageIds = messages.map((message) => message._id);
      if (messageIds.some((id) => id === undefined || id === null)) {
        throw new Error(
          `Session ${String(doc._id)} has message(s) without an _id; refusing to copy (an _id-keyed upsert would collapse them)`,
        );
      }
      const distinctMessageIdCount = new Set(
        messageIds.map((id) => String(id)),
      ).size;
      if (distinctMessageIdCount !== messages.length) {
        throw new Error(
          `Session ${String(doc._id)} has duplicate message _id(s): ${messages.length} messages but only ${distinctMessageIdCount} distinct ids`,
        );
      }

      const messageOps = messages.map((message, index) => {
        const { _id, ...rest } = message;
        return {
          updateOne: {
            filter: { _id },
            update: {
              $setOnInsert: {
                ...rest,
                _id,
                sessionId: doc._id,
                orgId: doc.orgId,
                seq: index + 1, // array order, not createdAt — see plan fix #2
              },
            },
            upsert: true,
          },
        };
      });

      await ChatSessionMessage.collection.bulkWrite(messageOps, {
        ordered: true,
      });

      // Verify against actually stored rows rather than the bulkWrite
      // result's matched/upserted counters, which report a no-op matched
      // upsert as "written".
      //
      // Scoped to the ids just copied rather than every row for this
      // sessionId: if an earlier attempt crashed after the ChatSession upsert
      // but before the `isMigrated` write, that session is already live and
      // may have accumulated newer messages. An unscoped count would then
      // exceed messages.length on every retry and strand the document
      // permanently unmigrated.
      const storedCount = await ChatSessionMessage.countDocuments({
        sessionId: doc._id,
        _id: { $in: messageIds },
      });
      if (storedCount !== messages.length) {
        throw new Error(
          `Message count mismatch for session ${String(doc._id)}: expected ${messages.length}, found ${storedCount}`,
        );
      }
    }

    const { messages: _messages, isMigrated: _isMigrated, __v: _v, ...sessionFields } = doc;
    await ChatSession.collection.updateOne(
      { _id: doc._id },
      {
        $setOnInsert: {
          ...sessionFields,
          _id: doc._id,
          sessionType: spec.sessionType,
          nextSeq: messages.length,
          __v: 0,
        },
      },
      { upsert: true },
    );

    await spec.legacyModel.collection.updateOne(
      { _id: doc._id },
      { $set: { isMigrated: true } },
    );

    return messages.length;
  }

  // ---------------------------------------------------------------------
  // Pre-flight audits
  // ---------------------------------------------------------------------

  /**
   * Sample legacy documents and log any top-level field the chatSessions
   * schema doesn't know about. A verbatim copy can't detect schema drift on
   * its own — this is a warning, not a hard stop, since an unexpected field
   * is still copied through (only fields explicitly excluded above are
   * dropped on purpose).
   */
  private async auditSchemaDrift(spec: CollectionSpec): Promise<void> {
    const sample = await spec.legacyModel
      .find({})
      .sort({ _id: -1 })
      .limit(SCHEMA_DRIFT_SAMPLE_SIZE)
      .lean();

    if (sample.length === 0) {
      return;
    }

    const known = new Set<string>();
    for (const path of Object.keys(ChatSession.schema.paths)) {
      known.add(path.split('.')[0] || path);
    }
    known.add('messages'); // handled separately, excluded from the session copy on purpose
    known.add('isMigrated'); // legacy-only bookkeeping, excluded from the session copy on purpose

    const unknown = new Set<string>();
    for (const legacyDoc of sample) {
      for (const key of Object.keys(legacyDoc)) {
        if (!known.has(key)) {
          unknown.add(key);
        }
      }
    }

    if (unknown.size > 0) {
      this.logger.warn(
        `Chat sessions migration: sampled ${spec.legacyLabel} documents contain fields not present on the chatSessions schema`,
        {
          legacyLabel: spec.legacyLabel,
          unknownFields: Array.from(unknown),
          sampleSize: sample.length,
        },
      );
    }
  }

  // ---------------------------------------------------------------------
  // KV flags
  // ---------------------------------------------------------------------

  private async isKbFiltersMigrationDone(): Promise<boolean> {
    try {
      const flag = await this.kvStore.get<string>(
        configPaths.chatKbFiltersMigration,
      );
      return flag === 'true';
    } catch (error) {
      this.logger.warn(
        'Failed to read chat KB-filters migration flag; deferring chat sessions migration to next boot',
        { error: error instanceof Error ? error.message : String(error) },
      );
      return false;
    }
  }

  private async readFlag(): Promise<ChatSessionsMigrationFlag> {
    try {
      const raw = await this.kvStore.get<string>(
        configPaths.chatSessionsMigration,
      );
      if (!raw) {
        return {};
      }
      const parsed = JSON.parse(raw);
      if (!parsed || typeof parsed !== 'object') {
        return {};
      }
      return parsed as ChatSessionsMigrationFlag;
    } catch (error) {
      this.logger.warn(
        'Failed to read/parse chat sessions migration flag; treating both collections as not-yet-migrated',
        { error: error instanceof Error ? error.message : String(error) },
      );
      return {};
    }
  }

  private async writeFlag(flag: ChatSessionsMigrationFlag): Promise<void> {
    try {
      await this.kvStore.set(
        configPaths.chatSessionsMigration,
        JSON.stringify(flag),
      );
    } catch (error) {
      this.logger.warn(
        'Chat sessions migration succeeded but failed to persist the completion flag; will retry on next boot',
        { error: error instanceof Error ? error.message : String(error) },
      );
    }
  }
}
