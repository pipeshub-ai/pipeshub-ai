import { MentionRef } from '../mentions/mention.types';

/** A mention as the AI backend gets it: roster refs only, never an id (`collaboration/mentions.py`). */
export interface WireMention {
  type: 'participant' | 'agent';
  ref: string;
}

export const SELF_AGENT_REF = 'agent:self';

/**
 * The assistant and the chat's own agent are both `agent:self`; a user is their roster ref, and a
 * user without one (past the roster cap) or a team, which has no ref, is dropped. No id survives.
 */
export function toWireMentions(
  mentions: readonly MentionRef[] | undefined,
  refs: ReadonlyMap<string, string>,
): WireMention[] {
  const out: WireMention[] = [];
  const seen = new Set<string>();
  for (const m of mentions ?? []) {
    const wire: WireMention | undefined =
      m.type === 'assistant' || m.type === 'agent'
        ? { type: 'agent', ref: SELF_AGENT_REF }
        : m.type === 'user' && refs.has(m.id)
          ? { type: 'participant', ref: refs.get(m.id) as string }
          : undefined;
    if (wire && !seen.has(wire.ref)) {
      seen.add(wire.ref);
      out.push(wire);
    }
  }
  return out;
}
