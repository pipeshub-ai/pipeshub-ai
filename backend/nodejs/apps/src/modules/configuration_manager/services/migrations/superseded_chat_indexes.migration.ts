import { Logger } from '../../../../libs/services/logger.service';
import { ChatSession } from '../../../enterprise_search/schema/chat.session.schema';
import { ChatSessionMessage } from '../../../enterprise_search/schema/chat.session.message.schema';

const MONGO_NAMESPACE_NOT_FOUND = 26;
const MONGO_INDEX_NOT_FOUND = 27;

type ChatCollection = 'chatSessions' | 'chatSessionMessages';

/** An index the chat schemas no longer declare, and the index that now serves the reads it served. */
export interface SupersededIndex {
  collection: ChatCollection;
  /** Mongo's default name for `key`, as `autoIndex` built it. */
  name: string;
  key: Record<string, 1 | -1>;
  /** Dropped only once this index is built, so no read is left without one. */
  replacedBy: string;
}

/**
 * The indexes earlier chat schemas declared and these do not. Mongoose builds the
 * indexes a schema declares and never drops one, so an upgraded database kept
 * them, while a fresh install never has them.
 */
export const SUPERSEDED_CHAT_INDEXES: readonly SupersededIndex[] = [
  {
    collection: 'chatSessions',
    name: 'userId_1',
    key: { userId: 1 },
    replacedBy: 'userId_1_agentKey_1',
  },
  {
    collection: 'chatSessions',
    name: 'agentKey_1',
    key: { agentKey: 1 },
    replacedBy: 'agentKey_1_orgId_1',
  },
  {
    collection: 'chatSessions',
    name: 'projectId_1',
    key: { projectId: 1 },
    replacedBy: 'projectId_1_orgId_1_isDeleted_1_lastActivityAt_-1__id_-1',
  },
  {
    collection: 'chatSessions',
    name: 'sessionType_1_orgId_1_userId_1_lastActivityAt_-1',
    key: { sessionType: 1, orgId: 1, userId: 1, lastActivityAt: -1 },
    replacedBy: 'sessionType_1_orgId_1_userId_1_lastActivityAt_-1__id_-1',
  },
  {
    collection: 'chatSessions',
    name: 'projectId_1_orgId_1_isDeleted_1_lastActivityAt_-1',
    key: { projectId: 1, orgId: 1, isDeleted: 1, lastActivityAt: -1 },
    replacedBy: 'projectId_1_orgId_1_isDeleted_1_lastActivityAt_-1__id_-1',
  },
  {
    collection: 'chatSessionMessages',
    name: 'sessionId_1',
    key: { sessionId: 1 },
    replacedBy: 'sessionId_1_seq_1',
  },
];

export interface SupersededChatIndexesResult {
  dropped: string[];
  /** Kept on this start: the index that replaces each is not built yet. */
  waiting: string[];
  errored: number;
}

interface IndexInfo {
  name?: string;
  key?: Record<string, unknown>;
}

interface IndexedCollection {
  indexes(): Promise<IndexInfo[]>;
  dropIndex(name: string): Promise<unknown>;
}

const COLLECTIONS: Record<ChatCollection, () => IndexedCollection> = {
  chatSessions: () => ChatSession.collection,
  // row-writer-exempt: indexes only: it lists and drops them, and writes no row
  chatSessionMessages: () => ChatSessionMessage.collection,
};

const codeOf = (error: unknown): unknown =>
  (error as { code?: unknown } | null)?.code;

const messageOf = (error: unknown): string =>
  error instanceof Error ? error.message : String(error);

/**
 * Drops the chat indexes the schemas no longer declare, so an upgraded database
 * holds the indexes a fresh install has. Each goes only once the index that
 * replaces it is built (by `autoIndex`, or by hand where MONGO_AUTO_INDEX is
 * false), and only when it is keyed as expected. Runs every boot: after the
 * first time it reads two index lists and stops. Logs its own outcome and never
 * throws.
 */
export class SupersededChatIndexesMigration {
  constructor(private readonly logger: Logger) {}

  async run(): Promise<SupersededChatIndexesResult> {
    const result: SupersededChatIndexesResult = {
      dropped: [],
      waiting: [],
      errored: 0,
    };
    for (const [collection, collectionOf] of Object.entries(COLLECTIONS)) {
      try {
        await this.dropFrom(
          collection,
          collectionOf(),
          SUPERSEDED_CHAT_INDEXES.filter((i) => i.collection === collection),
          result,
        );
      } catch (error) {
        result.errored += 1;
        this.logger.error('Dropping superseded chat indexes failed', {
          collection,
          error: messageOf(error),
        });
      }
    }
    if (result.dropped.length > 0) {
      this.logger.info('Dropped chat indexes the schemas no longer declare', {
        dropped: result.dropped,
      });
    }
    if (result.waiting.length > 0) {
      this.logger.info(
        'Kept chat indexes until the indexes that replace them are built',
        { waiting: result.waiting },
      );
    }
    return result;
  }

  private async dropFrom(
    collection: string,
    handle: IndexedCollection,
    superseded: readonly SupersededIndex[],
    result: SupersededChatIndexesResult,
  ): Promise<void> {
    let indexes: IndexInfo[];
    try {
      indexes = await handle.indexes();
    } catch (error) {
      // A fresh install: no collection yet, so none of its old indexes either.
      if (codeOf(error) === MONGO_NAMESPACE_NOT_FOUND) return;
      throw error;
    }
    const built = new Set(indexes.map((index) => index.name));
    for (const old of superseded) {
      const found = indexes.find((index) => index.name === old.name);
      // An index someone built under the same name on another key is not this one.
      if (
        found === undefined ||
        JSON.stringify(found.key) !== JSON.stringify(old.key)
      ) {
        continue;
      }
      const label = `${collection}.${old.name}`;
      if (!built.has(old.replacedBy)) {
        result.waiting.push(label);
        continue;
      }
      try {
        await handle.dropIndex(old.name);
      } catch (error) {
        // Another replica dropped it first.
        if (codeOf(error) === MONGO_INDEX_NOT_FOUND) continue;
        throw error;
      }
      result.dropped.push(label);
    }
  }
}
