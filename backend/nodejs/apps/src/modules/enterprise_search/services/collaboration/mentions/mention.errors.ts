import { DomainHttpError } from '../../../../../libs/errors/domain-http.error';

export const MENTION_ERROR_CODES = {
  NOT_ALLOWED: 'MENTION_NOT_ALLOWED',
  SA_AGENT_SHARED: 'MENTION_SA_AGENT_SHARED',
  DIRECTORY_UNAVAILABLE: 'MENTION_DIRECTORY_UNAVAILABLE',
  MESSAGE_IS_NOTE: 'MESSAGE_IS_NOTE',
  MESSAGE_NOT_NOTE: 'MESSAGE_NOT_NOTE',
} as const;

export type MentionDenyReason =
  | 'unknown_user'
  | 'not_a_chat_team'
  | 'agent_not_in_chat'
  | 'agent_not_allowed'
  | 'invalid_assistant';

/** Names the offending entry by index and why; never echoes another user's data. */
export class MentionNotAllowedError extends DomainHttpError {
  constructor(
    readonly mentionIndex: number,
    readonly reason: MentionDenyReason,
    statusCode: 400 | 403 = 400,
  ) {
    super(
      MENTION_ERROR_CODES.NOT_ALLOWED,
      'A mention in this message is not allowed',
      statusCode,
      { mentionIndex, reason },
    );
  }
}

export class MentionSaAgentSharedError extends DomainHttpError {
  constructor(readonly mentionIndex: number) {
    super(
      MENTION_ERROR_CODES.SA_AGENT_SHARED,
      'This agent runs with its creator’s access and cannot be used in a shared chat',
      403,
      { mentionIndex },
    );
  }
}

export class MentionDirectoryUnavailableError extends DomainHttpError {
  constructor() {
    super(
      MENTION_ERROR_CODES.DIRECTORY_UNAVAILABLE,
      'Mentions could not be checked; try again shortly',
      503,
    );
  }
}

/** The stream route is for messages the AI answers; a note goes to the notes route. */
export class MessageIsNoteError extends DomainHttpError {
  constructor() {
    super(
      MENTION_ERROR_CODES.MESSAGE_IS_NOTE,
      'This message does not ask the AI; post it as a note',
      422,
    );
  }
}

/** The notes route would skip an AI answer the room’s respond mode asks for. */
export class MessageNotNoteError extends DomainHttpError {
  constructor() {
    super(
      MENTION_ERROR_CODES.MESSAGE_NOT_NOTE,
      'This message is answered by the AI; send it as a message',
      422,
    );
  }
}
