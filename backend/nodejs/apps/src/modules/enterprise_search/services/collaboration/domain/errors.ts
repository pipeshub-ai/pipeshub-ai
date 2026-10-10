import {
  DomainHttpError,
  PublicDetailValue,
} from '../../../../../libs/errors/domain-http.error';
import { PrincipalKey } from './types';

export const COLLAB_ERROR_CODES = {
  NOT_FOUND: 'CONVERSATION_NOT_FOUND',
  READ_ONLY: 'CONVERSATION_READ_ONLY',
  OWNER_ONLY: 'CONVERSATION_OWNER_ONLY',
  REGENERATE_NOT_ALLOWED: 'REGENERATE_NOT_ALLOWED',
  RESUME_NOT_ALLOWED: 'RESUME_NOT_ALLOWED',
  OWNER_INACTIVE: 'OWNER_INACTIVE',
  PROJECT_ACCESS_REQUIRED: 'PROJECT_ACCESS_REQUIRED',
  BUSY: 'CONVERSATION_BUSY',
  RUN_LOST: 'RUN_LOST',
  CHANGED: 'CONVERSATION_CHANGED',
  DUPLICATE_MESSAGE: 'DUPLICATE_MESSAGE',
  COLLABORATOR_LIMIT: 'COLLABORATOR_LIMIT',
  CONNECTOR_SETUP_REQUIRED: 'CONNECTOR_SETUP_REQUIRED',
  INVALID_PRINCIPAL: 'INVALID_PRINCIPAL',
  TEAM_RESOLUTION_UNAVAILABLE: 'TEAM_RESOLUTION_UNAVAILABLE',
  OWNER_STATUS_UNAVAILABLE: 'OWNER_STATUS_UNAVAILABLE',
  RATE_LIMITED: 'RATE_LIMITED',
  ORG_WIDE_CONFIRMATION_REQUIRED: 'ORG_WIDE_CONFIRMATION_REQUIRED',
  AGENT_DRAFT_ALREADY_CREATED: 'AGENT_DRAFT_ALREADY_CREATED',
} as const;

export type CollabErrorCode =
  (typeof COLLAB_ERROR_CODES)[keyof typeof COLLAB_ERROR_CODES];

/** No details: a 404 must not tell a stranger whether the conversation exists. */
export class ConversationNotFoundError extends DomainHttpError {
  constructor() {
    super(COLLAB_ERROR_CODES.NOT_FOUND, 'Conversation not found', 404);
  }
}

export class ConversationReadOnlyError extends DomainHttpError {
  constructor() {
    super(
      COLLAB_ERROR_CODES.READ_ONLY,
      'You have read-only access to this conversation',
      403,
    );
  }
}

export class ConversationOwnerOnlyError extends DomainHttpError {
  constructor() {
    super(
      COLLAB_ERROR_CODES.OWNER_ONLY,
      'Only the conversation owner can do this',
      403,
    );
  }
}

/** Asker-only, owner included (O-1). */
export class RegenerateNotAllowedError extends DomainHttpError {
  constructor() {
    super(
      COLLAB_ERROR_CODES.REGENERATE_NOT_ALLOWED,
      'Only the person who asked a question can regenerate its answer',
      403,
    );
  }
}

export class ResumeNotAllowedError extends DomainHttpError {
  constructor() {
    super(
      COLLAB_ERROR_CODES.RESUME_NOT_ALLOWED,
      'Only the person who was asked can answer this question',
      403,
    );
  }
}

export class OwnerInactiveError extends DomainHttpError {
  constructor() {
    super(
      COLLAB_ERROR_CODES.OWNER_INACTIVE,
      'The owner of this conversation is no longer active',
      403,
    );
  }
}

export class ProjectAccessRequiredError extends DomainHttpError {
  constructor(readonly projectId: string) {
    super(
      COLLAB_ERROR_CODES.PROJECT_ACCESS_REQUIRED,
      'You need access to the project to do this',
      403,
    );
  }
}

export class TeamResolutionUnavailableError extends DomainHttpError {
  constructor() {
    super(
      COLLAB_ERROR_CODES.TEAM_RESOLUTION_UNAVAILABLE,
      'Team membership could not be resolved; try again shortly',
      503,
    );
  }
}

export class OwnerStatusUnavailableError extends DomainHttpError {
  constructor() {
    super(
      COLLAB_ERROR_CODES.OWNER_STATUS_UNAVAILABLE,
      "The conversation owner's status could not be checked; try again shortly",
      503,
    );
  }
}

export class ConversationBusyError extends DomainHttpError {
  constructor(activeRun: {
    userId: string;
    displayName?: string;
    startedAt: Date;
  }) {
    super(
      COLLAB_ERROR_CODES.BUSY,
      'Another turn is already running in this conversation',
      409,
      {
        activeRun: {
          userId: activeRun.userId,
          displayName: activeRun.displayName ?? null,
          startedAt: activeRun.startedAt.toISOString(),
        },
      },
    );
  }
}

/** A non-streaming turn lost its lease mid-run, so its answer was dropped. */
export class RunLostError extends DomainHttpError {
  constructor() {
    super(
      COLLAB_ERROR_CODES.RUN_LOST,
      'This turn was stopped because another run took over the conversation',
      409,
    );
  }
}

export class ConversationChangedError extends DomainHttpError {
  constructor(newerCount: number) {
    super(
      COLLAB_ERROR_CODES.CHANGED,
      'The conversation changed since you last loaded it',
      409,
      { newerCount },
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
      COLLAB_ERROR_CODES.DUPLICATE_MESSAGE,
      'This message was already received',
      409,
      { ...(conversationId && { conversationId }), messageId, answered },
    );
  }
}

export class CollaboratorLimitError extends DomainHttpError {
  constructor(max: number) {
    super(
      COLLAB_ERROR_CODES.COLLABORATOR_LIMIT,
      'The collaborator limit was reached',
      409,
      { max },
    );
  }
}

export class ConnectorSetupRequiredError extends DomainHttpError {
  constructor(toolsets: readonly string[]) {
    super(
      COLLAB_ERROR_CODES.CONNECTOR_SETUP_REQUIRED,
      'Connect the required tools before sending',
      412,
      { toolsets: [...toolsets] },
    );
  }
}

export class InvalidPrincipalError extends DomainHttpError {
  constructor(invalid: ReadonlyArray<{ key: PrincipalKey; reason: string }>) {
    super(
      COLLAB_ERROR_CODES.INVALID_PRINCIPAL,
      'One or more people or teams cannot be added',
      400,
      { principalIds: invalid.map((i) => i.key) },
    );
  }
}

export class RateLimitedError extends DomainHttpError {
  constructor(retryAfter: number) {
    super(COLLAB_ERROR_CODES.RATE_LIMITED, 'Too many requests', 429, {
      retryAfter,
    });
  }
}

export class OrgWideConfirmationRequiredError extends DomainHttpError {
  constructor(details?: Readonly<Record<string, PublicDetailValue>>) {
    super(
      COLLAB_ERROR_CODES.ORG_WIDE_CONFIRMATION_REQUIRED,
      'Sharing with the whole organization needs confirmation',
      400,
      details,
    );
  }
}

/** Only ever raised to the draft's own requester, so naming the agent leaks nothing. */
export class AgentDraftAlreadyCreatedError extends DomainHttpError {
  constructor(agent: { agentKey: string; handle: string }) {
    super(
      COLLAB_ERROR_CODES.AGENT_DRAFT_ALREADY_CREATED,
      'An agent was already created from this draft',
      409,
      { agentKey: agent.agentKey, handle: agent.handle },
    );
  }
}
