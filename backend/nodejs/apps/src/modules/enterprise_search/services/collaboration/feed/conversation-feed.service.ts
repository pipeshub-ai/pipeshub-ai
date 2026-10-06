import { Logger } from '../../../../../libs/services/logger.service';
import { IClock, systemClock } from '../../../../../libs/types/clock';
import { TtlCache } from '../../../../../libs/utils/ttl-cache';
import { IUserDirectory } from '../../../../user_management/services/user-directory.service';
import {
  FeedActiveRun,
  FeedMessage,
  FeedResponse,
} from '../domain/collaboration-views';
import { ConversationNotFoundError } from '../domain/errors';
import { ConversationAccessGrant } from '../http/conversation-context';
import {
  FeedHead,
  IConversationMessageFeed,
} from '../persistence/message-feed';
import { IReadStateRepository } from '../persistence/read-state.repository';
import { redactAgentDraft } from './draft-redaction';
import { authorIdsNeeded, authorViewOf } from './message-author';

export const FEED_PAGE_SIZE = 100;
export const READ_STATE_INTERVAL_MS = 10_000;
const READ_STATE_CACHE_MAX = 10_000;

const defaultLogger = Logger.getInstance({ service: 'ConversationFeed' });

export type FeedOutcome =
  | { status: 'not_modified' }
  | { status: 'ok'; body: FeedResponse };

export interface IConversationFeedService {
  read(
    grant: ConversationAccessGrant,
    query: { afterSeq: number; rev?: number },
  ): Promise<FeedOutcome>;
}

type StoredMessage = Record<string, unknown> & {
  seq: number;
  messageType: string;
  authorUserId?: { toString(): string };
  requestedBy?: { toString(): string };
  feedback?: Array<{ feedbackProvider?: { toString(): string } }>;
  citations?: Array<{ citationId?: { _id?: unknown } | null }>;
};

const INTERNAL_FIELDS = new Set(['sessionId', 'orgId', 'schemaVersion']);

export class ConversationFeedService implements IConversationFeedService {
  private readonly lastMarked: TtlCache<true>;

  constructor(
    private readonly messages: IConversationMessageFeed,
    private readonly users: IUserDirectory,
    private readonly readState: IReadStateRepository,
    clock: IClock = systemClock,
    private readonly logger: Pick<Logger, 'warn'> = defaultLogger,
  ) {
    this.lastMarked = new TtlCache(
      READ_STATE_INTERVAL_MS,
      READ_STATE_CACHE_MAX,
      clock,
    );
  }

  /** Runs after the guard, so a revoked caller has already been refused and never reaches a 304. */
  async read(
    grant: ConversationAccessGrant,
    query: { afterSeq: number; rev?: number },
  ): Promise<FeedOutcome> {
    const { caller, session } = grant;
    const sessionId = session._id.toString();
    const rev = await this.messages.readRev(sessionId, caller.orgId);
    if (rev === null) {
      throw new ConversationNotFoundError();
    }
    if (query.rev !== undefined && query.rev === rev) {
      return { status: 'not_modified' };
    }
    const [head, rows] = await Promise.all([
      this.messages.readHead(sessionId, caller.orgId),
      this.messages.listAfter(sessionId, query.afterSeq, FEED_PAGE_SIZE + 1),
    ]);
    if (!head) {
      throw new ConversationNotFoundError();
    }
    const page = (rows as unknown as StoredMessage[]).slice(0, FEED_PAGE_SIZE);
    const ownerId = session.userId.toString();
    const names = await this.users.displayNames(
      caller.orgId,
      this.namesNeeded(page, head, ownerId),
    );
    const last = page[page.length - 1];
    const body: FeedResponse = {
      messages: page.map((m) =>
        toFeedMessage(m, caller.userId, ownerId, names),
      ),
      // The revision read before the rows: a change made while they were read shows up as a newer rev on the next poll, never as a lost one.
      rev,
      nextSeq: last ? last.seq : query.afterSeq,
      hasMore: rows.length > FEED_PAGE_SIZE,
      activeRun: toActiveRun(head, names, grant.role !== 'read'),
      lastActivityAt: head.lastActivityAt,
    };
    if (last) {
      await this.markRead(caller.orgId, caller.userId, sessionId, last.seq);
    }
    return { status: 'ok', body };
  }

  private namesNeeded(
    page: readonly StoredMessage[],
    head: FeedHead,
    ownerId: string,
  ): string[] {
    const ids = authorIdsNeeded(page, ownerId);
    if (head.activeRun) {
      ids.add(head.activeRun.userId);
    }
    return [...ids];
  }

  /** In-process interval plus the repository's own `updatedAt` check, which covers other replicas. */
  private async markRead(
    orgId: string,
    userId: string,
    sessionId: string,
    seq: number,
  ): Promise<void> {
    const key = `${userId}:${sessionId}`;
    if (this.lastMarked.get(key)) {
      return;
    }
    this.lastMarked.set(key, true);
    try {
      await this.readState.markRead(orgId, userId, sessionId, seq, {
        minIntervalMs: READ_STATE_INTERVAL_MS,
      });
    } catch (error) {
      this.lastMarked.delete(key);
      this.logger.warn('Failed to record the read position', {
        error: error instanceof Error ? error.message : String(error),
      });
    }
  }
}

function toFeedMessage(
  message: StoredMessage,
  callerId: string,
  ownerId: string,
  names: ReadonlyMap<string, string>,
): FeedMessage {
  const row: Record<string, unknown> = {};
  for (const [field, value] of Object.entries(message)) {
    if (!INTERNAL_FIELDS.has(field)) {
      row[field] = value;
    }
  }
  const mine = (message.feedback ?? []).filter(
    (f) => f.feedbackProvider?.toString() === callerId,
  );
  const author = authorViewOf(message, ownerId, names);
  return redactAgentDraft(
    {
      ...row,
      _id: String(message._id),
      seq: message.seq,
      messageType: message.messageType,
      content: typeof message.content === 'string' ? message.content : '',
      feedback: mine.slice(-1),
      citations: (message.citations ?? []).map((c) => ({
        citationId: c.citationId?._id,
        citationData: c.citationId,
      })),
      ...(author !== undefined && { author }),
    },
    callerId,
  );
}

function toActiveRun(
  head: FeedHead,
  names: ReadonlyMap<string, string>,
  includeRunId: boolean,
): FeedActiveRun | null {
  if (!head.activeRun) {
    return null;
  }
  const { userId, startedAt, runId } = head.activeRun;
  return {
    userId,
    displayName: names.get(userId) ?? '',
    startedAt: startedAt.toISOString(),
    ...(includeRunId && { runId }),
  };
}
