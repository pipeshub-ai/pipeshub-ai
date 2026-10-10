import { ChatTarget } from '../../../utils/ai-chat-payload';
import { MentionRef } from '../mentions/mention.types';

export type ResponderKind = 'assistant' | 'own_agent' | 'guest_agent';

/** Who answers one turn, and where its AI-backend request goes. The session's own identity never changes. */
export interface TurnResponder {
  readonly kind: ResponderKind;
  readonly target: ChatTarget;
  /** Set only for a guest agent; stamped on the answer rows. */
  readonly respondingAgentKey?: string;
}

/**
 * The one agent a message hands this turn to: a mentioned agent other than the chat's own.
 * Text the answer itself contains is never looked at: only the sender's validated mentions count.
 */
export function guestAgentKeyOf(
  mentions: readonly Pick<MentionRef, 'type' | 'id'>[],
  ownAgentKey: string | undefined,
): string | undefined {
  return mentions.find((m) => m.type === 'agent' && m.id !== ownAgentKey)?.id;
}

const strategies: Record<
  ResponderKind,
  (session: ChatTarget, guestAgentKey?: string) => TurnResponder
> = {
  assistant: (session) => ({ kind: 'assistant', target: session }),
  own_agent: (session) => ({ kind: 'own_agent', target: session }),
  guest_agent: (_session, guestAgentKey) => ({
    kind: 'guest_agent',
    target: { kind: 'agent', agentKey: guestAgentKey as string },
    respondingAgentKey: guestAgentKey as string,
  }),
};

export function responderFor(
  session: ChatTarget,
  guestAgentKey?: string,
): TurnResponder {
  const kind: ResponderKind =
    guestAgentKey !== undefined
      ? 'guest_agent'
      : session.kind === 'agent'
        ? 'own_agent'
        : 'assistant';
  return strategies[kind](session, guestAgentKey);
}
