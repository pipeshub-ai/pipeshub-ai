import { Response } from 'express';
import { Types } from 'mongoose';
import { AuthenticatedUserRequest } from '../../../../../libs/middlewares/types';
import { IProjectDocument } from '../../../../projects/types/project.interfaces';
import { AppConfig } from '../../../../tokens_manager/config/config';
import {
  AIServiceResponse,
  IChatAttachmentRef,
  IChatSessionDocument,
  IChatSessionMessageDocument,
  IMessage,
} from '../../../types/conversation.interfaces';
import { ChatTarget } from '../../../utils/ai-chat-payload';
import { ResolvedProjectLink } from '../../../utils/project-context';
import { TurnGate } from './turn-gate';
import { TurnRun } from './turn-run';
import { ConversationTurnDeps } from './turn-deps';

/** Who answers one turn, and where its AI-backend request goes. The session's own identity never changes. */
export interface TurnResponder {
  readonly target: ChatTarget;
  /** Set when an agent other than the conversation's own answers; stamped on the answer rows. */
  readonly respondingAgentKey?: string;
}

/** The request fields that describe the user's message. */
export interface TurnBody {
  query: string;
  attachments?: IChatAttachmentRef[];
  resume?: { toolCallMessageId: string };
  [field: string]: unknown;
}

export interface FirstSendPreparation {
  readonly responder: TurnResponder;
  /** Whatever the extension needs again in its later first-send hooks; opaque to the handlers. */
  readonly state?: unknown;
}

export type TurnMode = 'stream' | 'plain';

export interface FirstTurnInput {
  deps: ConversationTurnDeps;
  req: AuthenticatedUserRequest;
  target: ChatTarget;
  userId: Types.ObjectId | string;
  orgId: Types.ObjectId | string;
  body: TurnBody;
  attachments?: IChatAttachmentRef[];
  link: ResolvedProjectLink;
  preparation: FirstSendPreparation;
}

export interface FollowUpAppendInput {
  req: AuthenticatedUserRequest;
  target: ChatTarget;
  userId: Types.ObjectId | string;
  orgId: Types.ObjectId | string;
  body: TurnBody;
  attachments?: IChatAttachmentRef[];
  gate: TurnGate;
}

export interface AppendedTurn {
  conversation: IChatSessionDocument;
  userRow: IChatSessionMessageDocument;
  run: TurnRun;
}

export interface FollowUpContext {
  previousConversations: Array<Record<string, unknown>>;
  project?: IProjectDocument;
  /** Fields added to the AI-backend payload of this turn. */
  aiPayload?: Record<string, unknown>;
}

/** A first send the extension answered itself, with no AI run: the handler only returns. */
export interface NoRunReply {
  req: AuthenticatedUserRequest;
  res: Response;
  deps: ConversationTurnDeps;
  turn: AppendedTurn;
  mode: TurnMode;
  projectId?: string;
  startTime: number;
  requestId?: string;
}

/** What the stream relay may ask about a turn. */
export interface TurnStreamExtension {
  /** Describes the agent that answered, for the final frame; undefined when the conversation's own agent did. */
  respondingAgentView(
    run: TurnRun,
  ): Promise<Record<string, unknown> | undefined>;
}

/**
 * The turn pipeline's extension points. The defaults are a single-author conversation: no
 * preconditions beyond the route guard, the conversation's own assistant or agent answers, and
 * nothing is added to the AI-backend request.
 */
export interface TurnExtension {
  /** Before a streaming first send opens its stream. */
  screenFirstSend(req: AuthenticatedUserRequest, body: TurnBody): void;
  /** Before a first send writes anything; may reject. */
  prepareFirstSend(args: {
    req: AuthenticatedUserRequest;
    deps: ConversationTurnDeps;
    target: ChatTarget;
    body: TurnBody;
    mode: TurnMode;
  }): Promise<FirstSendPreparation>;
  /** Creates the conversation with the caller's first `user_query`. */
  createFirstTurn(input: FirstTurnInput): Promise<AppendedTurn>;
  /** True when the extension already answered the request and no AI run follows. */
  replyWithoutRun(reply: NoRunReply): Promise<boolean>;
  /** Who answers a follow-up turn. */
  responderFor(
    req: AuthenticatedUserRequest,
    target: ChatTarget,
  ): TurnResponder;
  /** The agent, other than the conversation's own, that answers this request; undefined for the conversation's own. */
  respondingAgentKeyOf(req: AuthenticatedUserRequest): string | undefined;
  /** Appends the caller's `user_query` to an existing conversation. */
  appendFollowUp(input: FollowUpAppendInput): Promise<AppendedTurn>;
  /** What the AI backend is told about a follow-up. */
  followUpContext(
    req: AuthenticatedUserRequest,
    turn: AppendedTurn,
    deps: ConversationTurnDeps,
  ): Promise<FollowUpContext>;
  /** What the AI backend is told when an answer is generated again: the rows before its question. */
  regenerationContext(args: {
    req: AuthenticatedUserRequest;
    deps: ConversationTurnDeps;
    conversation: IChatSessionDocument;
    question: IMessage;
    history: IMessage[];
    gate: TurnGate;
  }): Promise<{
    previousConversations: Array<Record<string, unknown>>;
    aiPayload: Record<string, unknown>;
  }>;
  /** Extra fields of the `conversation_created` frame and of a non-streaming reply. */
  createdFrameFields(req: AuthenticatedUserRequest): Record<string, unknown>;
  /** Fields every AI-backend request for this conversation carries. */
  aiPayloadFields(conversation: IChatSessionDocument): Record<string, unknown>;
  /**
   * Stops a run the caller named. Null when there is nothing to cancel; otherwise the AI backend's
   * answer.
   */
  cancelRun(args: {
    req: AuthenticatedUserRequest;
    appConfig: AppConfig;
    conversationId: string;
    runId: string;
  }): Promise<AIServiceResponse<unknown> | null>;
  /** The tool name a custom AI-backend event is stored under as a `tool_call` card; undefined, it is not stored. */
  toolCardFor(eventName: string): string | undefined;
  /** The stream relay's questions for this request. */
  streamFor(
    req: AuthenticatedUserRequest,
    deps: ConversationTurnDeps,
  ): TurnStreamExtension;
  /** The query text the markup checks run on. */
  queryForValidation(query: string): string;
}
