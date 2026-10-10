import {
  MentionRef,
  RespondMode,
  Responder,
  SessionKind,
  addressesAi,
} from './mention.types';

export interface ClassifyInput {
  readonly mentions: readonly Pick<MentionRef, 'type'>[];
  readonly respondMode: RespondMode;
  readonly sessionKind: SessionKind;
}

/**
 * Pure and shared with the composer: both sides run `tests/fixtures/respond-mode-cases.json`.
 * `always` runs the AI whatever is mentioned. `mention_only` runs it only when the assistant or
 * the agent is mentioned. `smart` runs it unless the message mentions only people or teams.
 */
export function classifyResponder(input: ClassifyInput): Responder {
  const own = input.sessionKind === 'agent' ? 'own_agent' : 'assistant';
  if (input.respondMode === 'always') return own;
  if (addressesAi(input.mentions as readonly MentionRef[])) return own;
  if (input.respondMode === 'mention_only') return 'note';
  return input.mentions.length === 0 ? own : 'note';
}
