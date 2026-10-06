import type { MentionRef } from '../components/composer/composer-input.types';

export type RespondMode = 'smart' | 'mention_only' | 'always';
export type SessionKind = 'chat' | 'agent';
export type Responder = 'assistant' | 'own_agent' | 'note';

export const DEFAULT_RESPOND_MODE: RespondMode = 'smart';

export interface ClassifyInput {
  mentions: ReadonlyArray<Pick<MentionRef, 'type'>>;
  respondMode: RespondMode;
  sessionKind: SessionKind;
}

/**
 * Twin of the Node `classifyResponder` (`mentions/responder-router.ts`); both run
 * `backend/nodejs/apps/tests/fixtures/respond-mode-cases.json`. Keep the two in step.
 */
export function classifyResponder({ mentions, respondMode, sessionKind }: ClassifyInput): Responder {
  const own: Responder = sessionKind === 'agent' ? 'own_agent' : 'assistant';
  if (respondMode === 'always') return own;
  if (mentions.some((m) => m.type === 'assistant' || m.type === 'agent')) return own;
  if (respondMode === 'mention_only') return 'note';
  return mentions.length === 0 ? own : 'note';
}
