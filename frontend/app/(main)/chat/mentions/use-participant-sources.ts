import { useEffect, useMemo } from 'react';
import { useChatStore } from '@/chat/store';
import type { CollaboratorsResponse, ConversationRef } from '@/chat/collaboration-types';
import { useUserStore } from '@/lib/store/user-store';
import { useParticipantsStore } from './participants-store';
import type { MentionableAgent, MentionScope } from './api';
import { useDraftShareStore, draftUserIds } from '../draft-share-store';

/** A new chat opened for an agent (`?agentId=`) has no slot yet, so the URL says whose chat it will be. */
function newChatAgentInUrl(): boolean {
  if (typeof window === 'undefined') return false;
  return Boolean(new URLSearchParams(window.location.search).get('agentId')?.trim());
}

const STALE_AFTER_MS = 30_000;
const NEW_CHAT_KEY = 'new';
const NO_AGENTS: MentionableAgent[] = [];

export interface FeedAuthor {
  userId: string;
  displayName: string | null;
}

export interface ParticipantSources {
  /** Changes whenever the chat's people changed, so per-query results held elsewhere are stale. */
  epoch: number;
  convId: string | null;
  agentId: string | null;
  /** The chat's own ref; null while the chat has no id yet. */
  ref: ConversationRef | null;
  /** Where mentions are asked: `ref`, or the new-chat scope that counts the draft collaborators as in the chat. */
  searchRef: MentionScope | null;
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
  const isNewChat = enabled && !convId && !agentId && !newChatAgentInUrl();
  const agentsKey = convId ?? (isNewChat ? NEW_CHAT_KEY : null);
  const ownAgents = useParticipantsStore((s) => (agentsKey ? s.agentsByConv[agentsKey] : undefined)) ?? NO_AGENTS;
  const loading = useParticipantsStore((s) => (convId ? s.loading[convId] === true : false));
  const loadedAt = useParticipantsStore((s) => (convId ? s.byConv[convId]?.at : undefined));
  const load = useParticipantsStore((s) => s.load);
  const draftPrincipals = useDraftShareStore((s) => s.principals);
  const meDisplay = useUserStore((s) => s.profile?.fullName ?? '');

  const ref = useMemo<ConversationRef | null>(
    () => (convId ? (agentId ? { kind: 'agent', agentKey: agentId, id: convId } : { kind: 'chat', id: convId }) : null),
    [convId, agentId],
  );
  // A default chat that has no id yet: the picker still works, against the organization.
  const draftKey = draftUserIds(draftPrincipals).join(',');
  const searchRef = useMemo<MentionScope | null>(
    () => ref ?? (isNewChat ? { kind: 'new', include: draftKey ? draftKey.split(',') : [] } : null),
    [ref, isNewChat, draftKey],
  );
  const draftResponse = useMemo<CollaboratorsResponse | undefined>(
    () =>
      isNewChat
        ? {
            owner: { userId: meUserId ?? '', displayName: meDisplay },
            collaborators: draftPrincipals.map((p) => ({
              principalType: p.type,
              principalId: p.id,
              displayName: p.name,
              accessLevel: p.level,
              state: 'active' as const,
            })),
            collaboratorCount: draftPrincipals.length,
            settings: { editorsCanInvite: false, ownerContentShared: false },
          }
        : undefined,
    [isNewChat, meUserId, meDisplay, draftPrincipals],
  );

  useEffect(() => {
    if (isNewChat) void useParticipantsStore.getState().loadAgents({ kind: 'new' });
  }, [isNewChat]);

  useEffect(() => {
    if (!enabled || !ref) return;
    const current = useParticipantsStore.getState().byConv[ref.id];
    if (!current || Date.now() - current.at >= STALE_AFTER_MS) void load(ref);
    // `loadedAt` goes undefined when the list is invalidated, which is what brings the refetch.
  }, [enabled, ref, load, loadedAt === undefined]);

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

  const epoch = useParticipantsStore((s) => (convId ? s.epoch[convId] ?? 0 : 0));

  return { epoch, convId, agentId, ref, searchRef, response: response ?? draftResponse, ownAgents, authors, meUserId, loading: enabled && loading };
}
