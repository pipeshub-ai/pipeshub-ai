export const MENTION_TYPES = ['assistant', 'agent', 'user', 'team'] as const;
export type MentionType = (typeof MENTION_TYPES)[number];

/** Ids only: a label is never stored or trusted. The assistant aliases all resolve to `{ assistant, self }`. */
export interface MentionRef {
  readonly type: MentionType;
  readonly id: string;
}

export const ASSISTANT_MENTION_ID = 'self';
export const MENTIONS_MAX = 10;
export const MENTION_ID_MAX_LENGTH = 128;

export const RESPOND_MODES = ['smart', 'mention_only', 'always'] as const;
export type RespondMode = (typeof RESPOND_MODES)[number];
export const DEFAULT_RESPOND_MODE: RespondMode = 'smart';

export type SessionKind = 'chat' | 'agent';
/** Who answers a message: the assistant, the chat's own agent, or nobody (a note). */
export type Responder = 'assistant' | 'own_agent' | 'note';

export const mentionKey = (m: MentionRef): string => `${m.type}:${m.id}`;

export function dedupeMentions(
  mentions: readonly MentionRef[],
): readonly MentionRef[] {
  const seen = new Set<string>();
  return mentions.filter((m) => {
    const key = mentionKey(m);
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

export const addressesAi = (mentions: readonly MentionRef[]): boolean =>
  mentions.some((m) => m.type === 'assistant' || m.type === 'agent');
