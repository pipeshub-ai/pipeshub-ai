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

export interface MentionableAgent {
  id: string;
  label: string;
  handle?: string;
}

interface MentionablesDto {
  items: Array<{ type: string; id: string; label: string; handle?: string }>;
}

export const MentionsApi = {
  /** The caller's own agents the server offers in this chat; empty when it offers none. */
  async listAgents(ref: ConversationRef): Promise<MentionableAgent[]> {
    const { data } = await apiClient.get<MentionablesDto>(`${conversationApiPath(ref)}/mentionables`, {
      params: { limit: 20 },
      suppressErrorToast: true,
    });
    return (data?.items ?? [])
      .filter((i) => i.type === 'agent')
      .map((i) => ({ id: i.id, label: i.label, ...(i.handle ? { handle: i.handle } : {}) }));
  },

  /** A note asks nobody: no run, no lease, so it is allowed while someone else's run streams. */
  async postNote(ref: ConversationRef, input: PostNoteInput): Promise<PostNoteResponse> {
    const { data } = await apiClient.post<PostNoteResponse>(`${conversationApiPath(ref)}/notes`, input, {
      suppressErrorToast: true,
    });
    return data;
  },
};
