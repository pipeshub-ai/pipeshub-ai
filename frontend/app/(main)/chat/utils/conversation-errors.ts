import type { TFunction } from 'i18next';
import {
  conversationErrorCode,
  conversationErrorDetails,
  conversationErrorStatus,
} from '../collaboration-api';
import { isConversationErrorCode, type ConversationErrorCode } from '../collaboration-types';

/** Where the UI shows an error: a banner over the composer, a toast, next to the control, or nowhere. */
export type ConversationErrorAction = 'banner' | 'toast' | 'inline' | 'silent';

export interface ConversationErrorInfo {
  code: ConversationErrorCode | null;
  i18nKey: string;
  action: ConversationErrorAction;
  /** Transient: the same call may succeed shortly, so keep the slot. */
  retryable: boolean;
}

export const GENERIC_CONVERSATION_ERROR_KEY = 'chat.collab.errors.GENERIC';

const ERROR_ACTIONS: Record<ConversationErrorCode, { action: ConversationErrorAction; retryable?: true }> = {
  CONVERSATION_NOT_FOUND: { action: 'banner' },
  CONVERSATION_READ_ONLY: { action: 'banner' },
  CONVERSATION_OWNER_ONLY: { action: 'toast' },
  REGENERATE_NOT_ALLOWED: { action: 'toast' },
  RESUME_NOT_ALLOWED: { action: 'inline' },
  OWNER_INACTIVE: { action: 'banner' },
  OWNER_STATUS_UNAVAILABLE: { action: 'banner', retryable: true },
  PROJECT_ACCESS_REQUIRED: { action: 'banner' },
  TEAM_RESOLUTION_UNAVAILABLE: { action: 'banner', retryable: true },
  CONVERSATION_BUSY: { action: 'silent' },
  CONVERSATION_CHANGED: { action: 'inline' },
  DUPLICATE_MESSAGE: { action: 'silent' },
  RUN_LOST: { action: 'toast' },
  COLLABORATOR_LIMIT: { action: 'inline' },
  MUTED_SESSIONS_LIMIT: { action: 'toast' },
  CONNECTOR_SETUP_REQUIRED: { action: 'banner' },
  INVALID_PRINCIPAL: { action: 'inline' },
  RATE_LIMITED: { action: 'inline', retryable: true },
  ORG_WIDE_CONFIRMATION_REQUIRED: { action: 'inline' },
  AGENT_UNAVAILABLE: { action: 'banner' },
  MENTION_NOT_ALLOWED: { action: 'inline' },
  MENTION_SA_AGENT_SHARED: { action: 'inline' },
  MENTION_DIRECTORY_UNAVAILABLE: { action: 'inline', retryable: true },
  MESSAGE_IS_NOTE: { action: 'inline' },
  MESSAGE_NOT_NOTE: { action: 'inline' },
};

export function conversationErrorKey(code: ConversationErrorCode): string {
  return `chat.collab.errors.${code}`;
}

/** Maps a `ProcessedError`, `StreamError` or bare code to its message key and UI action. */
export function describeConversationError(errorOrCode: unknown): ConversationErrorInfo {
  const code = typeof errorOrCode === 'string' ? errorOrCode : conversationErrorCode(errorOrCode);
  if (!isConversationErrorCode(code)) {
    return { code: null, i18nKey: GENERIC_CONVERSATION_ERROR_KEY, action: 'toast', retryable: false };
  }
  const { action, retryable } = ERROR_ACTIONS[code];
  return { code, i18nKey: conversationErrorKey(code), action, retryable: retryable === true };
}

function asRecord(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === 'object' ? (value as Record<string, unknown>) : undefined;
}

/** Interpolation values the messages use, read from `error.details`. */
export function conversationErrorParams(error: unknown): Record<string, string | number> {
  const details = conversationErrorDetails(error);
  const params: Record<string, string | number> = {};
  if (!details) return params;
  if (typeof details.max === 'number') params.max = details.max;
  if (typeof details.retryAfter === 'number') params.retryAfter = details.retryAfter;
  if (typeof details.newerCount === 'number') params.count = details.newerCount;
  const name = asRecord(details.activeRun)?.displayName;
  if (typeof name === 'string') params.name = name;
  if (Array.isArray(details.toolsets)) params.toolsets = details.toolsets.join(', ');
  return params;
}

export function conversationErrorMessage(t: TFunction, error: unknown): string {
  const { i18nKey } = describeConversationError(error);
  return t(i18nKey, conversationErrorParams(error));
}

/**
 * A poll or load answered "no such conversation" or "not yours": access is gone for good (D-b).
 * 503/429 are transient and never count.
 */
export function isAccessLostError(error: unknown): boolean {
  const status = conversationErrorStatus(error);
  return status === 404 || status === 403;
}

/** Seconds to wait before retrying a 429, from `details.retryAfter`; undefined when absent. */
export function retryAfterSecondsOf(error: unknown): number | undefined {
  const value = conversationErrorDetails(error)?.retryAfter;
  return typeof value === 'number' && value > 0 ? value : undefined;
}
