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
const ANY_TOKEN_TEST = new RegExp(ANY_TOKEN.source);

/** The text with every mention token, live or escaped, blanked: what an HTML-tag filter should look at. */
export const withoutMentionTokens = (text: string): string =>
  text.replace(ANY_TOKEN, ' ');

export const CHAT_TITLE_MAX_LENGTH = 100;

// Stands in for the space inside a label so a cut never splits `@Joke Buddy`.
const LABEL_SPACE = '';
// A dropped token right before punctuation: eats the space in front of it.
const GLUE_LEFT = '\uE001';
const TITLE_LEADING_JUNK = /^[\s;,:.\-–—|/\\]+/;
const TITLE_TRAILING_JUNK = /[\s;,:\-–—|/\\]+$/;
// Titles saved before this clean-up were cut at 100 characters, which can leave half a token at the end.
const CUT_TOKEN_TAIL = /<\\?@\w*(?::[\w.-]*)?$/;
const ATTACHES_LEFT = /^[,;:.!?)]/;

/**
 * A chat title from the first message: user, team and agent tokens become `@label` when `labels`
 * (keyed `type:id`, see `mentionKey`) knows them and vanish otherwise; the assistant token and
 * escaped literals always vanish. Whitespace is collapsed, left-over edge punctuation trimmed, and
 * the cut at `CHAT_TITLE_MAX_LENGTH` falls on a word boundary. Empty when nothing is left.
 */
export function titleFromQuery(
  query: string,
  labels: ReadonlyMap<string, string> = new Map(),
): string {
  const text = query.replace(
    ANY_TOKEN,
    (token: string, ...rest: unknown[]): string => {
      const offset = rest[rest.length - 2] as number;
      const whole = rest[rest.length - 1] as string;
      const escaped = token.startsWith('<\\');
      const [type, id] = token.slice(escaped ? 3 : 2, -1).split(':') as [
        string,
        string,
      ];
      const label = escaped || type === 'assistant' ? undefined : labels.get(`${type}:${id}`);
      const clean = label?.replace(/\s+/g, ' ').trim();
      const next = whole.charAt(offset + token.length);
      const closesUp = ATTACHES_LEFT.test(next);
      if (!clean) return closesUp ? GLUE_LEFT : ' ';
      return ` @${clean.replace(/ /g, LABEL_SPACE)}${closesUp ? '' : ' '}`;
    },
  );
  const tidy = text
    .replace(/\s*\uE001/g, '')
    .replace(/\s+/g, ' ')
    .replace(TITLE_LEADING_JUNK, '')
    .replace(TITLE_TRAILING_JUNK, '');
  return cutOnWord(tidy, CHAT_TITLE_MAX_LENGTH)
    .replace(TITLE_TRAILING_JUNK, '')
    .split(LABEL_SPACE).join(' ');
}

function cutOnWord(text: string, max: number): string {
  const chars = Array.from(text);
  if (chars.length <= max) return text;
  if (/\s/.test(chars[max] as string)) return chars.slice(0, max).join('');
  const head = chars.slice(0, max).join('');
  const space = head.search(/\s\S*$/);
  return space > 0 ? head.slice(0, space) : head;
}

/** Read-time clean-up of a stored title: tokens removed (no lookups), then the same tidying. */
export function displayTitle(title: string): string;
export function displayTitle(title: string | undefined | null): string | undefined;
export function displayTitle(title: string | undefined | null): string | undefined {
  if (typeof title !== 'string') return undefined;
  if (!ANY_TOKEN_TEST.test(title) && !CUT_TOKEN_TAIL.test(title)) return title;
  const text = title
    .replace(ANY_TOKEN, ' ')
    .replace(CUT_TOKEN_TAIL, ' ')
    .replace(/\s+/g, ' ');
  return text.replace(TITLE_LEADING_JUNK, '').replace(TITLE_TRAILING_JUNK, '');
}
