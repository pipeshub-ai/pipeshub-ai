import { UNKNOWN_EFFECT_PREFIX } from '../projection/unknown-effects';

/**
 * An `ix.unknown.*` interaction (P2.9): a call whose outcome nobody knows, parked for a person. The person the run
 * acts for says whether it happened (`answered`) or not (`denied`) from its card in the chat or the Inbox.
 */
export const isUnknownEffectInteraction = (interactionId: string): boolean =>
  interactionId.startsWith(UNKNOWN_EFFECT_PREFIX) &&
  interactionId.length > UNKNOWN_EFFECT_PREFIX.length;
