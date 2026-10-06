import type { StreamChatRequest } from '../types';

type CollabSendFields = Pick<StreamChatRequest, 'filesShared' | 'shareToolResults'>;

export interface CollabSendInput {
  /** Flag on and the chat has other people in it. Anything else sends none of these fields. */
  active: boolean;
  hasAttachments: boolean;
  isAgent: boolean;
  shareToolResults: boolean;
}

/** Per-turn consent fields for the send body (D3v2, D4v2). `clientMessageId` and `baseSeq` come from `prepareCollabSend`. */
export function collabSendFields(input: CollabSendInput): CollabSendFields {
  if (!input.active) return {};
  return {
    filesShared: input.hasAttachments,
    ...(input.isAgent ? { shareToolResults: input.shareToolResults } : {}),
  };
}

/** A 1-64 character id; `crypto.randomUUID` is missing on plain-HTTP origins. */
export function newClientMessageId(): string {
  const cryptoApi = typeof globalThis !== 'undefined' ? globalThis.crypto : undefined;
  if (cryptoApi && typeof cryptoApi.randomUUID === 'function') return cryptoApi.randomUUID();
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
}

/** The collaboration fields of a stream request, for the agent body that lists its fields one by one. */
export function pickCollabSendFields(
  request: Pick<StreamChatRequest, 'clientMessageId' | 'baseSeq' | 'filesShared' | 'shareToolResults' | 'resume'>,
): Pick<StreamChatRequest, 'clientMessageId' | 'baseSeq' | 'filesShared' | 'shareToolResults' | 'resume'> {
  return {
    ...(request.clientMessageId !== undefined ? { clientMessageId: request.clientMessageId } : {}),
    ...(request.baseSeq !== undefined ? { baseSeq: request.baseSeq } : {}),
    ...(request.filesShared !== undefined ? { filesShared: request.filesShared } : {}),
    ...(request.shareToolResults !== undefined ? { shareToolResults: request.shareToolResults } : {}),
    ...(request.resume ? { resume: request.resume } : {}),
  };
}
