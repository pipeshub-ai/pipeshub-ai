import { useMemo } from 'react';
import { isCollaboratorsView, type ConversationRef } from '@/chat/collaboration-types';
import { buildMentionables } from '../components/composer/use-mentionables';
import type { ResolverAgent, ResolverCandidate } from '../components/composer/typed-mention-resolver';
import type { MentionableAgent, MentionScope } from './api';
import { respondModeOf } from './participants-store';
import { useParticipantSources } from './use-participant-sources';
import type { RespondMode } from './classify';

export interface ChatParticipants {
  convId: string | null;
  ref: ConversationRef | null;
  /** Where mentions are asked; set for a new chat too. */
  searchRef: MentionScope | null;
  isAgentChat: boolean;
  /** The chat's own agent (null in a default chat). */
  agentId: string | null;
  /** Agents the caller can run here that a typed `@handle` may name. */
  agents: ResolverAgent[];
  respondMode: RespondMode;
  /** The owner, and an editor allowed to invite, see everyone and may add a mentioned colleague to the chat. */
  canInvite: boolean;
  /** People and teams a typed `@name` may mean. */
  candidates: ResolverCandidate[];
}

export const resolverAgentsOf = (list: readonly MentionableAgent[]): ResolverAgent[] =>
  list.flatMap((a) => (a.handle ? [{ ref: { type: 'agent' as const, id: a.id }, handle: a.handle }] : []));

/** The active chat's people, teams and respond mode, fetched once per chat and shared through the store. */
export function useChatParticipants(enabled: boolean): ChatParticipants {
  const { convId, agentId, ref, searchRef, response, ownAgents, authors, meUserId } = useParticipantSources(enabled);

  const candidates = useMemo(
    () =>
      enabled
        ? buildMentionables({ collaborators: response ?? null, authors, meUserId, assistantLabel: '' }, '', Infinity)
            .filter((m) => m.group === 'people' || m.group === 'teams')
            .map((m) => ({ ref: m.ref, label: m.label }))
        : [],
    [enabled, response, authors, meUserId],
  );

  const agents = useMemo<ResolverAgent[]>(
    () =>
      enabled
        ? resolverAgentsOf(ownAgents)
        : [],
    [enabled, ownAgents],
  );

  return {
    convId,
    ref,
    searchRef,
    isAgentChat: Boolean(agentId),
    agentId: agentId ?? null,
    respondMode: respondModeOf(response),
    canInvite: response !== undefined && isCollaboratorsView(response),
    agents,
    candidates,
  };
}
