import { apiClient } from '@/lib/api/axios-instance';
import { conversationApiPath } from '@/chat/collaboration-api';
import type { ConversationRef } from '@/chat/collaboration-types';
import type { MentionRef } from '../components/composer/composer-input.types';

export interface PostNoteInput {
  query: string;
  mentions: MentionRef[];
  clientMessageId: string;
}

export interface NoteDto {
  id: string;
  seq: number;
  messageType: 'note';
  content: string;
  mentions: MentionRef[];
  authorUserId: string;
  clientMessageId: string;
  createdAt?: string;
}

export interface PostNoteResponse {
  note: NoteDto;
  /** True when `clientMessageId` had already been posted and `note` is that earlier note. */
  duplicate: boolean;
  /** Mentioned colleagues who are not in the chat: kept as chips, never notified. */
  nonParticipants: string[];
}

/** Where a mention list is asked: an existing chat, or the new chat that has no id yet (its draft collaborators count as in the chat). */
export type MentionScope = ConversationRef | { kind: 'new'; include?: readonly string[] };

const mentionablesUrl = (scope: MentionScope): string =>
  scope.kind === 'new' ? '/api/v1/conversations/mentionables' : `${conversationApiPath(scope)}/mentionables`;

const includeParam = (scope: MentionScope) =>
  scope.kind === 'new' && scope.include?.length ? { include: scope.include.join(',') } : {};

export const scopeKey = (scope: MentionScope): string => (scope.kind === 'new' ? 'new' : `${scope.kind}:${scope.id}`);

export interface MentionableAgent {
  id: string;
  label: string;
  handle?: string;
}

interface MentionablesDto {
  items: Array<{ type: string; id: string; label: string; handle?: string; email?: string; inChat?: boolean }>;
}

export interface MentionSearchResult {
  agents: MentionableAgent[];
  /** Organization members matching the query by first, middle or last name or email; `inChat` is false for outsiders. */
  people: Array<{ id: string; label: string; email?: string; inChat: boolean }>;
}

const toAgent = (i: MentionablesDto['items'][number]): MentionableAgent => ({
  id: i.id,
  label: i.label,
  ...(i.handle ? { handle: i.handle } : {}),
});

export const MentionsApi = {
  /** Agents the caller can run that the server offers in this chat (narrowed to `q` when given); empty when it offers none. */
  async listAgents(ref: MentionScope, q?: string): Promise<MentionableAgent[]> {
    const { data } = await apiClient.get<MentionablesDto>(mentionablesUrl(ref), {
      params: { limit: 20, ...(q ? { q } : {}), ...includeParam(ref) },
      suppressErrorToast: true,
    });
    return (data?.items ?? []).filter((i) => i.type === 'agent').map(toAgent);
  },

  /** What the server offers for a typed query: the query goes through unchanged, spaces included. */
  async search(ref: MentionScope, q: string): Promise<MentionSearchResult> {
    const { data } = await apiClient.get<MentionablesDto>(mentionablesUrl(ref), {
      params: { limit: 20, q, ...includeParam(ref) },
      suppressErrorToast: true,
    });
    const items = data?.items ?? [];
    return {
      agents: items.filter((i) => i.type === 'agent').map(toAgent),
      people: items
        .filter((i) => i.type === 'user' && i.label?.trim())
        .map((i) => ({ id: i.id, label: i.label, ...(i.email ? { email: i.email } : {}), inChat: i.inChat !== false })),
    };
  },

  /** A note asks nobody: no run, no lease, so it is allowed while someone else's run streams. */
  async postNote(ref: ConversationRef, input: PostNoteInput): Promise<PostNoteResponse> {
    const { data } = await apiClient.post<PostNoteResponse>(`${conversationApiPath(ref)}/notes`, input, {
      suppressErrorToast: true,
    });
    return data;
  },
};
