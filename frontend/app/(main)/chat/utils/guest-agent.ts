import type { ThreadMessageLike } from '@assistant-ui/react';
import { useParticipantsStore, labelOfMention } from '../mentions/participants-store';
import type { StreamChatRequest } from '../types';

/**
 * The stream's final conversation may not say which agent answered (the feed and a reload do). A message that
 * mentioned an agent other than the chat's own was answered by that agent, so the answer row is stamped from what
 * the picker knew until the server's own value arrives. Never overrides a value the server sent.
 */
export function stampGuestAgent(
  rows: ThreadMessageLike[],
  request: Pick<StreamChatRequest, 'mentions' | 'agentId'>,
  conversationKey: string | null,
): void {
  const mention = request.mentions?.find((m) => m.type === 'agent' && m.id !== request.agentId);
  if (!mention) return;
  const last = [...rows].reverse().find((m) => m.role === 'assistant');
  if (!last) return;
  const custom = (last.metadata?.custom ?? {}) as Record<string, unknown>;
  if (custom.respondingAgent) return;
  const state = useParticipantsStore.getState();
  const known = conversationKey ? state.agentsByConv[conversationKey]?.find((a) => a.id === mention.id) : undefined;
  const name = known?.label ?? labelOfMention(state.labels, mention);
  Object.assign(last, {
    metadata: {
      ...last.metadata,
      custom: {
        ...custom,
        respondingAgent: {
          key: mention.id,
          ...(name ? { name } : {}),
          ...(known?.handle ? { handle: known.handle } : {}),
        },
      },
    },
  });
}
