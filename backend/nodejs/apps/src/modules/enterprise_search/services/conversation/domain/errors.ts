import { DomainHttpError } from '../../../../../libs/errors/domain-http.error';

export const CONVERSATION_ERROR_CODES = {
  NOT_FOUND: 'CONVERSATION_NOT_FOUND',
  RUN_LOST: 'RUN_LOST',
  DUPLICATE_MESSAGE: 'DUPLICATE_MESSAGE',
} as const;

/** No details: a 404 must not tell a stranger whether the conversation exists. */
export class ConversationNotFoundError extends DomainHttpError {
  constructor() {
    super(CONVERSATION_ERROR_CODES.NOT_FOUND, 'Conversation not found', 404);
  }
}

/** A non-streaming turn lost its lease mid-run, so its answer was dropped. */
export class RunLostError extends DomainHttpError {
  constructor() {
    super(
      CONVERSATION_ERROR_CODES.RUN_LOST,
      'This turn was stopped because another run took over the conversation',
      409,
    );
  }
}

export class DuplicateMessageError extends DomainHttpError {
  /** `conversationId` is set for a repeated first send, where the retry has no conversation id of its own. */
  constructor(
    messageId: string | null,
    answered: boolean,
    conversationId?: string,
  ) {
    super(
      CONVERSATION_ERROR_CODES.DUPLICATE_MESSAGE,
      'This message was already received',
      409,
      { ...(conversationId && { conversationId }), messageId, answered },
    );
  }
}
