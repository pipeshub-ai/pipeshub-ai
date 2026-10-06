import { useEffect, useMemo } from 'react';
import { useChatStore } from '@/chat/store';
import type { CollaboratorsResponse, ConversationRef } from '@/chat/collaboration-types';
import { useUserStore } from '@/lib/store/user-store';
import { useParticipantsStore } from './participants-store';
import type { MentionableAgent } from './api';

const STALE_AFTER_MS = 30_000;
const NO_AGENTS: MentionableAgent[] = [];

export interface FeedAuthor {
  userId: string;
  displayName: string | null;
}

export interface ParticipantSources {
  convId: string | null;
  agentId: string | null;
  ref: ConversationRef | null;
  response: CollaboratorsResponse | undefined;
  ownAgents: MentionableAgent[];
  /** People seen as authors in the feed: the only other names a non-manager's summary view leaves. */
  authors: FeedAuthor[];
  meUserId: string | null;
  loading: boolean;
}

/** The active chat's collaborators list (one fetch per chat, shared through the store) and its feed authors. */
export function useParticipantSources(enabled: boolean): ParticipantSources {
  const convId = useChatStore((s) => (s.activeSlotId ? s.slots[s.activeSlotId]?.convId ?? null : null));
  const agentId = useChatStore((s) => (s.activeSlotId ? s.slots[s.activeSlotId]?.threadAgentId?.trim() || null : null));
  const messages = useChatStore((s) => (s.activeSlotId ? s.slots[s.activeSlotId]?.messages : undefined));
  const meUserId = useUserStore((s) => s.profile?.userId ?? null);
  const response = useParticipantsStore((s) => (convId ? s.byConv[convId]?.response : undefined));
  const ownAgents = useParticipantsStore((s) => (convId ? s.agentsByConv[convId] : undefined)) ?? NO_AGENTS;
  const loading = useParticipantsStore((s) => (convId ? s.loading[convId] === true : false));
  const load = useParticipantsStore((s) => s.load);

  const ref = useMemo<ConversationRef | null>(
    () => (convId ? (agentId ? { kind: 'agent', agentKey: agentId, id: convId } : { kind: 'chat', id: convId }) : null),
    [convId, agentId],
  );
  useEffect(() => {
    if (!enabled || !ref) return;
    const current = useParticipantsStore.getState().byConv[ref.id];
    if (!current || Date.now() - current.at >= STALE_AFTER_MS) void load(ref);
  }, [enabled, ref, load]);

  const authors = useMemo(() => {
    const seen = new Map<string, FeedAuthor>();
    for (const m of messages ?? []) {
      const author = (m.metadata?.custom as { author?: { userId?: string; displayName?: string | null } } | undefined)?.author;
      if (author?.userId && !seen.has(author.userId)) {
        seen.set(author.userId, { userId: author.userId, displayName: author.displayName ?? null });
      }
    }
    return [...seen.values()];
  }, [messages]);
  // Feed authors are the only names a non-manager has for people beyond the owner; chips in the history need them too.
  useEffect(() => {
    if (!enabled) return;
    useParticipantsStore
      .getState()
      .remember(authors.flatMap((a) => (a.displayName ? [{ ref: { type: 'user' as const, id: a.userId }, label: a.displayName }] : [])));
  }, [enabled, authors]);

  return { convId, agentId, ref, response, ownAgents, authors, meUserId, loading: enabled && loading };
}
