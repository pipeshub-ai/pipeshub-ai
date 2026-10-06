import { Types } from 'mongoose';
import { CallerIdentity } from '../../../../../libs/types/caller-identity';
import { ChatSession } from '../../../schema/chat.session.schema';
import { ChatSessionMessage } from '../../../schema/chat.session.message.schema';
import { IMessage } from '../../../types/conversation.interfaces';
import { appendMessages } from '../../../utils/utils';
import { ConversationAccessGrant } from '../http/conversation-context';
import { assertNotDuplicate } from '../http/turn-preconditions';
import {
  conversationEventProducers,
  IConversationEventProducers,
} from '../notify/conversation-event-producers';
import { notifiableMentions } from '../notify/mention-principals';
import { inShortTransaction } from '../turn/turn-run';
import { MessageNotNoteError } from './mention.errors';
import { claimedMentions, inertUnlistedTokens } from './mention.parser';
import { DEFAULT_RESPOND_MODE, MentionRef, SessionKind } from './mention.types';
import { IMentionValidator } from './mention.validator';
import { classifyResponder } from './responder-router';

export interface NoteInput {
  readonly query: string;
  readonly mentions: readonly MentionRef[];
  readonly clientMessageId: string;
}

export interface NoteView {
  readonly id: string;
  readonly seq: number;
  readonly messageType: 'note';
  readonly content: string;
  readonly mentions: readonly MentionRef[];
  readonly authorUserId: string;
  readonly clientMessageId: string;
  readonly createdAt: Date | undefined;
}

export interface NoteOutcome {
  readonly note: NoteView;
  /** True when `clientMessageId` had already been posted and this is that note. */
  readonly duplicate: boolean;
  /** User ids mentioned who are not in the chat: stored as chips, never notified, offered to the owner as "add them". */
  readonly nonParticipants: readonly string[];
}

export interface INoteService {
  post(
    grant: ConversationAccessGrant,
    identity: CallerIdentity,
    kind: SessionKind,
    input: NoteInput,
  ): Promise<NoteOutcome>;
}

interface NoteRow extends IMessage {
  _id: Types.ObjectId;
  seq: number;
}

const isDuplicateKey = (error: unknown): boolean =>
  (error as { code?: unknown } | null)?.code === 11000 &&
  /clientMessageId/.test(
    JSON.stringify((error as { keyPattern?: unknown }).keyPattern ?? {}) +
      String((error as { message?: unknown }).message),
  );

const viewOf = (row: NoteRow): NoteView => ({
  id: row._id.toString(),
  seq: row.seq,
  messageType: 'note',
  content: row.content,
  mentions: row.mentions ?? [],
  authorUserId: String(row.authorUserId),
  clientMessageId: String(row.clientMessageId),
  createdAt: row.createdAt,
});

/**
 * A note is a `seq`-ordered row with no run: it takes no lease, checks no readiness and calls no AI
 * backend, so it can land while someone else's run streams (that run's history ends before its own question).
 */
export class NoteService implements INoteService {
  constructor(
    private readonly validator: IMentionValidator,
    private readonly producers: () => IConversationEventProducers = conversationEventProducers,
  ) {}

  async post(
    grant: ConversationAccessGrant,
    identity: CallerIdentity,
    kind: SessionKind,
    input: NoteInput,
  ): Promise<NoteOutcome> {
    const { session, caller } = grant;
    const claimed = claimedMentions(input.query, input.mentions);
    const { mentions, nonParticipants } =
      claimed.length === 0
        ? { mentions: [], nonParticipants: [] }
        : await this.validator.validate(claimed, { session, identity });
    const responder = classifyResponder({
      mentions,
      respondMode: session.settings?.respondMode ?? DEFAULT_RESPOND_MODE,
      sessionKind: kind,
    });
    if (responder !== 'note') throw new MessageNotNoteError();

    const scope = {
      sessionId: session._id,
      orgId: new Types.ObjectId(caller.orgId),
      callerId: caller.userId,
      ownerId: session.userId.toString(),
    };
    const earlier = await this.existing(scope, input.clientMessageId);
    if (earlier) {
      await this.notify(grant, identity, earlier);
      return { note: viewOf(earlier), duplicate: true, nonParticipants };
    }

    const now = new Date();
    const message: IMessage = {
      messageType: 'note',
      content: inertUnlistedTokens(input.query, mentions),
      contentFormat: 'MARKDOWN',
      authorUserId: new Types.ObjectId(caller.userId),
      clientMessageId: input.clientMessageId,
      mentions: [...mentions],
      createdAt: now,
      updatedAt: now,
    };
    let row: NoteRow;
    try {
      row = await inShortTransaction(async (dbSession) => {
        const [inserted] = await appendMessages(
          scope.sessionId,
          scope.orgId,
          [message],
          dbSession,
        );
        await ChatSession.updateOne(
          { _id: scope.sessionId, orgId: scope.orgId, isDeleted: false },
          { $set: { lastActivityAt: now.getTime() } },
          dbSession ? { session: dbSession } : undefined,
        );
        return inserted as unknown as NoteRow;
      });
    } catch (error) {
      if (!isDuplicateKey(error)) throw error;
      // A concurrent post of the same id won; answer with that note.
      const winner = await this.existing(scope, input.clientMessageId);
      if (!winner) throw error;
      await this.notify(grant, identity, winner);
      return { note: viewOf(winner), duplicate: true, nonParticipants };
    }
    await this.notify(grant, identity, row);
    return { note: viewOf(row), duplicate: false, nonParticipants };
  }

  /**
   * After the commit, and also for a replayed `clientMessageId`: a failure here fails the request, the
   * client retries the same id, and the per-recipient dedupe key makes the second publish a no-op.
   */
  private async notify(
    grant: ConversationAccessGrant,
    identity: CallerIdentity,
    row: NoteRow,
  ): Promise<void> {
    const mentions = notifiableMentions(row);
    if (mentions.length === 0) return;
    await this.producers().mentioned({
      session: grant.session,
      identity,
      actorUserId: grant.caller.userId,
      messageId: row._id.toString(),
      mentions,
    });
  }

  /** The caller's note with this id; any other row with it is a duplicate send (409). */
  private async existing(
    scope: {
      sessionId: Types.ObjectId;
      orgId: Types.ObjectId;
      callerId: string;
      ownerId: string;
    },
    clientMessageId: string,
  ): Promise<NoteRow | undefined> {
    const row = await ChatSessionMessage.findOne({
      sessionId: scope.sessionId,
      orgId: scope.orgId,
      authorUserId: new Types.ObjectId(scope.callerId),
      clientMessageId: { $eq: clientMessageId },
    }).lean<NoteRow>();
    if (!row) return undefined;
    if (row.messageType === 'note') return row;
    await assertNotDuplicate(scope, clientMessageId);
    return undefined;
  }
}
