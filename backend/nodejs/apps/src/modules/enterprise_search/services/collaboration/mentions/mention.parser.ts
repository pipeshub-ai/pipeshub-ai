import {
  ASSISTANT_MENTION_ID,
  MENTION_ID_MAX_LENGTH,
  MENTION_TYPES,
  MENTIONS_MAX,
  MentionRef,
  MentionType,
  dedupeMentions,
  mentionKey,
} from './mention.types';
import { ASSISTANT_ALIASES } from './alias-table';

// A pasted `<\@type:id>` has a backslash after `<`, so it never matches: escaped literals stay inert.
const TOKEN = new RegExp(
  `<@(${MENTION_TYPES.join('|')}):([\\w.-]{1,${String(MENTION_ID_MAX_LENGTH)}})>`,
  'g',
);

// Not preceded by a word character, `@`, `<` or `/` (addresses, tokens, paths); not followed by more word, or `.word` (`@ai.com`).
const ALIAS = new RegExp(
  `(?<![\\w@<\\\\/])@(?:${ASSISTANT_ALIASES.join('|')})(?![\\w@-]|\\.\\w)`,
  'i',
);

export interface ParsedMentions {
  /** Unescaped `<@type:id>` tokens in the text, de-duplicated, at most `MENTIONS_MAX`. */
  readonly tokens: readonly MentionRef[];
  readonly overflow: boolean;
  /** The text addresses the assistant by a reserved alias (`@PipesHub`, `@assistant`, ...). */
  readonly assistantAlias: boolean;
}

/** The only mentions the server infers from free text are the reserved assistant aliases. */
export function parseMentions(text: string): ParsedMentions {
  const all = dedupeMentions(
    [...text.matchAll(TOKEN)].map((m) => ({
      type: m[1] as MentionType,
      id: m[2] as string,
    })),
  );
  const withoutTokens = text.replace(TOKEN, ' ');
  return {
    tokens: all.slice(0, MENTIONS_MAX),
    overflow: all.length > MENTIONS_MAX,
    assistantAlias: ALIAS.test(withoutTokens),
  };
}

export const ASSISTANT_MENTION: MentionRef = {
  type: 'assistant',
  id: ASSISTANT_MENTION_ID,
};

/** What a send claims: the body's mentions, plus the assistant when the text uses a reserved alias. */
export function claimedMentions(
  query: unknown,
  mentions: readonly MentionRef[] | undefined,
): readonly MentionRef[] {
  const typed =
    typeof query === 'string' && parseMentions(query).assistantAlias
      ? [ASSISTANT_MENTION]
      : [];
  // The route schema caps the array; the typed alias can only add one more.
  return dedupeMentions([...(mentions ?? []), ...typed]).slice(0, MENTIONS_MAX);
}

/**
 * Only the validated `mentions` array mentions anyone. A `<@type:id>` token in the text that is not in
 * it (typed through the API, or dropped by validation) is stored as the inert literal `<\@type:id>`,
 * so no reader renders it as a chip.
 */
export function inertUnlistedTokens(
  text: string,
  mentions: readonly MentionRef[],
): string {
  const listed = new Set(mentions.map(mentionKey));
  return text.replace(TOKEN, (token, type: MentionType, id: string) =>
    listed.has(mentionKey({ type, id })) ? token : `<\\@${token.slice(2)}`,
  );
}

const ANY_TOKEN = new RegExp(
  `<\\\\?@(?:${MENTION_TYPES.join('|')}):[\\w.-]{1,${String(MENTION_ID_MAX_LENGTH)}}>`,
  'g',
);

/** The text with every mention token, live or escaped, blanked: what an HTML-tag filter should look at. */
export const withoutMentionTokens = (text: string): string =>
  text.replace(ANY_TOKEN, ' ');
