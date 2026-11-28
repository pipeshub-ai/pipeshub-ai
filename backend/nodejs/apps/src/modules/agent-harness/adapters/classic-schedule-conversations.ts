import type { ConversationRepository } from '../../../libs/repositories/conversation/types';
import {
  ASK_EXPIRED,
  type ClassicAnswerPart,
  type ClassicConversations,
  type ClassicConversationState,
} from '../application/classic-schedule-runner';
import type { ChatConversations } from '../application/schedule-service';

/** The newest messages a status read looks through for the latest answer (a resumed ask adds two per decision). */
const RECENT_MESSAGES = 20;

/** An id as text: a Mongo read gives an ObjectId where the type says string. */
const idText = (value: unknown): string => String(value);

const time = (at: Date | undefined | number): number | null =>
  at === undefined ? null : new Date(at).getTime();

/** A classic firing's conversation, through the conversation repository of the configured document store. */
export class RepositoryClassicConversations implements ClassicConversations {
  constructor(private readonly repository: () => ConversationRepository) {}

  async read(
    orgId: string,
    conversationId: string,
  ): Promise<ClassicConversationState | null> {
    const repository = this.repository();
    const session = await repository.findById(conversationId);
    if (session === null || idText(session.orgId) !== orgId) return null;
    const base = {
      startedAt: time(session.createdAt),
      updatedAt: time(session.updatedAt ?? session.lastActivityAt),
    };
    if (session.isDeleted === true) {
      return { status: 'deleted', answer: null, ...base };
    }
    const recent = await repository.listMessages(conversationId, {
      orgId,
      limit: RECENT_MESSAGES,
      order: 'desc',
    });
    const latest = recent
      .map((m) => m.dto)
      .find((m) => m.messageType === 'bot_response');
    const answer =
      latest === undefined
        ? null
        : {
            parts: (latest.parts ?? []) as ClassicAnswerPart[],
            citations: latest.citations?.length ?? 0,
          };
    const status =
      session.status === 'Inprogress'
        ? 'in_progress'
        : session.status === 'Failed'
          ? 'failed'
          : session.status === 'Stopped'
            ? 'stopped'
            : 'complete';
    return {
      status,
      ...(session.failReason !== undefined
        ? { failReason: session.failReason }
        : {}),
      answer,
      ...base,
    };
  }

  async expire(orgId: string, conversationId: string): Promise<void> {
    const session = await this.repository().findById(conversationId);
    if (session === null || idText(session.orgId) !== orgId) return;
    await this.repository().recordRunState(conversationId, {
      status: 'Failed',
      failReason: ASK_EXPIRED,
    });
  }
}

/** The caller's own chats (ADR-SCH-03 D3), through the conversation repository of the configured document store. */
export class RepositoryChatConversations implements ChatConversations {
  constructor(private readonly repository: () => ConversationRepository) {}

  async ownedBy(
    orgId: string,
    userId: string,
    conversationId: string,
  ): Promise<{ agentKey: string | null } | null> {
    const session = await this.repository().findById(conversationId);
    if (
      session === null ||
      session.isDeleted === true ||
      idText(session.orgId) !== orgId ||
      idText(session.userId) !== userId
    ) {
      return null;
    }
    return { agentKey: session.agentKey ?? null };
  }
}
