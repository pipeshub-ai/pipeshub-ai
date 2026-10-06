import { IProjectDocument } from '../../projects/types/project.interfaces';
import { WireMention } from '../services/collaboration/turn/mention-refs';
import { CollaborationPayload } from '../services/collaboration/turn/participant-roster';
import { applyProjectScope } from './project-context';

export const parseChatMode = (
  requestChatMode?: string,
): { chatMode: string; agentMode: boolean } => {
  let chatMode: string = requestChatMode || 'quick';
  let agentMode: boolean = false;

  if (chatMode.includes('agent')) {
    chatMode = chatMode.split(':')[1] || 'quick';
    agentMode = true;
  }

  return { chatMode, agentMode };
};

// Forwards the user-selected tool list to the AI payload when the client
// explicitly sent `tools`. Omitting it tells Python to use all configured
// tools (Python receives None); sending `[]` disables tools entirely.
export const assignToolsToPayload = (
  payload: Record<string, unknown>,
  tools: unknown,
): void => {
  if (tools !== undefined) {
    payload.tools = Array.isArray(tools) ? tools : [];
  }
};

/** Forward Slack / internal caller display name and email to the AI backend for LLM user context.
 * Does not change retrieval ACL — the Python agent still keys permissions on the service-account
 * agent creator's userId/orgId.
 */
export const assignCallerContextToAiPayload = (
  payload: Record<string, unknown>,
  body: Record<string, unknown>,
): void => {
  const rawName = body.callerDisplayName;
  if (typeof rawName === 'string' && rawName.trim()) {
    payload.callerDisplayName = rawName.trim();
  }
  const rawEmail = body.callerEmail;
  if (typeof rawEmail === 'string' && rawEmail.trim()) {
    payload.callerEmail = rawEmail.trim();
  }
};

/**
 * Forward `agentCapabilities` from the request body to the AI backend payload.
 * Only passes through when the value is a non-null object — ignores scalars and arrays.
 * Capability booleans narrow what the Python backend enables; they never expand permissions.
 */
export const assignAgentCapabilitiesToPayload = (
  payload: Record<string, unknown>,
  body: Record<string, unknown>,
): void => {
  const caps = body.agentCapabilities;
  if (
    caps !== null &&
    caps !== undefined &&
    typeof caps === 'object' &&
    !Array.isArray(caps)
  ) {
    payload.agentCapabilities = caps;
  }
};

/** Which AI-backend chat a turn is for: the assistant, or one saved agent. */
export type ChatTarget =
  | { kind: 'assistant' }
  | { kind: 'agent'; agentKey: string };

export interface AiChatTurnContext {
  conversationId?: string;
  previousConversations: unknown[];
  /** Set on the turn that creates the conversation; follow-ups drop `recordIds`. */
  isNewConversation: boolean;
  /** The chat's ACL version; the AI backend keys its chat-content decision cache by it. */
  aclVersion: number;
  project?: IProjectDocument;
  /** Only a leased turn of a shared chat with two or more people has one; solo and flag-off turns send none. */
  collaboration?: CollaborationPayload;
  /** Roster refs of who the question mentions; never sent without `collaboration`. */
  mentions?: WireMention[];
}

export interface AiChatRequest {
  /** Path on the AI backend, without the `/stream` suffix. */
  path: string;
  payload: Record<string, unknown>;
}

const nullable = (value: unknown): unknown => value || null;

/** The card a follow-up answers; the AI backend ignores it until it reads it. */
const resumeOf = (
  body: Record<string, unknown>,
  context: AiChatTurnContext,
): { resume?: { toolCallMessageId: string } } => {
  const id = (body.resume as { toolCallMessageId?: unknown } | undefined)
    ?.toolCallMessageId;
  return !context.isNewConversation && typeof id === 'string'
    ? { resume: { toolCallMessageId: id } }
    : {};
};

/**
 * The AI-backend request for one chat turn, shared by the streaming and
 * non-streaming routes so both send Python the same fields.
 *
 * An assistant turn whose `chatMode` is `agent` / `agent:<mode>` runs on the
 * universal agent (`agentIdPlaceholder`); every other assistant turn runs on
 * `/chat`. The project's tool scope is applied after the request's own
 * `tools`, so a project always narrows what the caller asked for.
 */
export const buildAiChatRequest = (
  target: ChatTarget,
  body: Record<string, unknown>,
  context: AiChatTurnContext,
): AiChatRequest => {
  const payload: Record<string, unknown> = {
    query: body.query,
    previousConversations: context.previousConversations,
    ...(context.isNewConversation ? { recordIds: body.recordIds || [] } : {}),
    filters: body.filters || {},
    attachments: body.attachments || [],
    modelKey: nullable(body.modelKey),
    modelName: nullable(body.modelName),
    modelFriendlyName: nullable(body.modelFriendlyName),
    reasoningEffort: nullable(body.reasoningEffort),
    timezone: nullable(body.timezone),
    currentTime: nullable(body.currentTime),
    conversationId: nullable(context.conversationId),
    runId: nullable(body.runId),
    aclVersion: context.aclVersion,
    ...resumeOf(body, context),
    ...(context.collaboration && { collaboration: context.collaboration }),
    ...(context.collaboration &&
      context.mentions &&
      context.mentions.length > 0 && { mentions: context.mentions }),
  };

  let path: string;
  if (target.kind === 'agent') {
    path = `/api/v1/agent/${encodeURIComponent(target.agentKey)}/chat`;
    payload.chatMode = body.chatMode || 'quick';
    if (context.isNewConversation) payload.quickMode = body.quickMode || false;
    assignToolsToPayload(payload, body.tools);
    assignCallerContextToAiPayload(payload, body);
    assignAgentCapabilitiesToPayload(payload, body);
  } else {
    const { chatMode, agentMode } = parseChatMode(
      body.chatMode as string | undefined,
    );
    path = agentMode ? '/api/v1/agent/agentIdPlaceholder/chat' : '/api/v1/chat';
    payload.chatMode = chatMode;
    if (agentMode) {
      assignToolsToPayload(payload, body.tools);
      assignAgentCapabilitiesToPayload(payload, body);
    }
  }
  applyProjectScope(payload, context.project);
  return { path, payload };
};
