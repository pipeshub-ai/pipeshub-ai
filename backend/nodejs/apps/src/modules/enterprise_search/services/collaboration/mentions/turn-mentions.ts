import { MentionRef } from './mention.types';

interface Admitted {
  readonly mentions: readonly MentionRef[];
  readonly guestAgentKey?: string;
  readonly nonParticipants?: readonly string[];
}

const admitted = new WeakMap<object, Admitted>();

/** Set by the mention gate once a send's mentions are validated; unset with the flags off, so nothing is stored or forwarded. */
export function setTurnMentions(
  req: object,
  mentions: readonly MentionRef[],
  guestAgentKey?: string,
  nonParticipants?: readonly string[],
): void {
  admitted.set(req, {
    mentions,
    ...(guestAgentKey !== undefined && { guestAgentKey }),
    ...(nonParticipants !== undefined &&
      nonParticipants.length > 0 && { nonParticipants }),
  });
}

/** Org colleagues the send mentions who are not in the chat; the sender is told so they can add them. */
export function turnNonParticipantsOf(req: object): readonly string[] {
  return admitted.get(req)?.nonParticipants ?? [];
}

export function turnMentionsOf(req: object): readonly MentionRef[] {
  return admitted.get(req)?.mentions ?? [];
}

/** The agent this turn is handed to, when the sender mentioned one that is not the chat's own. */
export function turnGuestAgentOf(req: object): string | undefined {
  return admitted.get(req)?.guestAgentKey;
}

/** A resume goes back to the agent that asked the question. */
export function setTurnGuestAgent(req: object, guestAgentKey: string): void {
  admitted.set(req, { ...admitted.get(req), mentions: turnMentionsOf(req), guestAgentKey });
}
