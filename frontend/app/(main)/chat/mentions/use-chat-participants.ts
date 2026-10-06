import { useMemo } from 'react';
import { isCollaboratorsView, type ConversationRef } from '@/chat/collaboration-types';
import { buildMentionables } from '../components/composer/use-mentionables';
import type { ResolverCandidate } from '../components/composer/typed-mention-resolver';
import { respondModeOf } from './participants-store';
import { useParticipantSources } from './use-participant-sources';
import type { RespondMode } from './classify';

export interface ChatParticipants {
  convId: string | null;
  ref: ConversationRef | null;
  isAgentChat: boolean;
  /** The chat's own agent, the only agent a message may mention until guest agent turns exist. */
  agentId: string | null;
  respondMode: RespondMode;
  /** The owner, and an editor allowed to invite, see everyone and may add a mentioned colleague to the chat. */
  canInvite: boolean;
  /** People and teams a typed `@name` may mean. */
  candidates: ResolverCandidate[];
}

/** The active chat's people, teams and respond mode, fetched once per chat and shared through the store. */
export function useChatParticipants(enabled: boolean): ChatParticipants {
  const { convId, agentId, ref, response, ownAgents, authors, meUserId } = useParticipantSources(enabled);

  const candidates = useMemo(
    () =>
      enabled
        ? buildMentionables({ collaborators: response ?? null, authors, meUserId, assistantLabel: '' }, '', Infinity)
            .filter((m) => m.group === 'people' || m.group === 'teams')
            .map((m) => ({ ref: m.ref, label: m.label }))
        : [],
    [enabled, response, authors, meUserId],
  );

  const agentCandidates = useMemo<ResolverCandidate[]>(
    () =>
      enabled
        ? ownAgents.flatMap((a) => {
            const ref = { type: 'agent' as const, id: a.id };
            return [{ ref, label: a.label }, ...(a.handle ? [{ ref, label: a.handle }] : [])];
          })
        : [],
    [enabled, ownAgents],
  );

  return {
    convId,
    ref,
    isAgentChat: Boolean(agentId),
    agentId: agentId ?? null,
    respondMode: respondModeOf(response),
    canInvite: response !== undefined && isCollaboratorsView(response),
    candidates: useMemo(() => [...candidates, ...agentCandidates], [candidates, agentCandidates]),
  };
}
