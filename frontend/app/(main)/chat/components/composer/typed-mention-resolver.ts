import aliases from '@/chat/mentions/reserved-aliases.json';
import type { MentionRef } from './composer-input.types';
import { mentionKey, toToken } from './mention-serializer';

export const MENTIONS_MAX = 10;

const ASSISTANT_ALIASES: readonly string[] = aliases.assistant;
const INERT_ALIASES: readonly string[] = aliases.inert;
const ASSISTANT_MENTION: MentionRef = { type: 'assistant', id: 'self' };

export interface ResolverCandidate {
  ref: MentionRef;
  label: string;
}

export interface ResolveInput {
  /** Wire text: picked mentions are already `<@type:id>` tokens; typed ones are still `@word`. */
  text: string;
  mentions: readonly MentionRef[];
  /** People and teams of the chat that a typed `@name` may mean. */
  candidates: readonly ResolverCandidate[];
  /** Picks from the chooser, by lowercased typed text. */
  choices?: Readonly<Record<string, MentionRef>>;
}

export type ResolveResult =
  | { status: 'ok'; text: string; mentions: MentionRef[] }
  | { status: 'ambiguous'; typed: string; candidates: ResolverCandidate[] };

// ES5 target: no `\p{L}`. A word character is anything that is not whitespace or ASCII punctuation, `_` apart.
const PUNCT = '!-/:-@\\[-^`{-~';
const WORD_CHAR = new RegExp(`[^\\s${PUNCT}]`);
const WORD = new RegExp(`^(?:[^\\s${PUNCT}]|-)+`);
const DOTTED_WORD = new RegExp(`^\\.[^\\s${PUNCT}]`);
const isBeforeBlocker = (c: string): boolean => WORD_CHAR.test(c) || c === '@' || c === '<' || c === '\\' || c === '/';

const norm = (s: string): string => s.toLowerCase();

interface Match {
  length: number;
  hits: ResolverCandidate[];
}

function uniqueByRef(list: ResolverCandidate[]): ResolverCandidate[] {
  const seen = new Set<string>();
  return list.filter((c) => {
    const key = mentionKey(c.ref);
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

/** The longest full label the text continues with, then candidates with a word equal to the typed word, then (2+ chars) a word prefix. */
function match(rest: string, word: string, candidates: readonly ResolverCandidate[]): Match | null {
  const lower = norm(rest);
  let best = 0;
  let hits: ResolverCandidate[] = [];
  for (const c of candidates) {
    const label = norm(c.label.trim());
    if (!label || !lower.startsWith(label)) continue;
    const next = rest.slice(label.length, label.length + 1);
    if (next && WORD_CHAR.test(next)) continue;
    if (label.length > best) {
      best = label.length;
      hits = [c];
    } else if (label.length === best) hits.push(c);
  }
  if (hits.length > 0) return { length: best, hits: uniqueByRef(hits) };

  const w = norm(word);
  const words = (c: ResolverCandidate) => norm(c.label).split(/\s+/);
  const exact = uniqueByRef(candidates.filter((c) => words(c).includes(w)));
  if (exact.length > 0) return { length: word.length, hits: exact };
  if (w.length >= 2) {
    const prefix = uniqueByRef(candidates.filter((c) => words(c).some((x) => x.startsWith(w))));
    if (prefix.length > 1) return { length: word.length, hits: prefix };
  }
  return null;
}

/** Where typed reserved assistant aliases (`@assistant`, `@ai`, ...) sit in already-sent text; display only. */
export function findAssistantAliases(text: string): Array<{ start: number; end: number }> {
  const found: Array<{ start: number; end: number }> = [];
  for (let at = text.indexOf('@'); at !== -1; at = text.indexOf('@', at + 1)) {
    if (at > 0 && isBeforeBlocker(text[at - 1])) continue;
    const rest = text.slice(at + 1);
    const word = WORD.exec(rest)?.[0] ?? '';
    if (!word) continue;
    const afterWord = rest.slice(word.length);
    if (ASSISTANT_ALIASES.includes(norm(word)) && !afterWord.startsWith('@') && !DOTTED_WORD.test(afterWord)) {
      found.push({ start: at, end: at + 1 + word.length });
    }
  }
  return found;
}

/**
 * Resolves what the user typed before it is sent: a reserved alias always addresses the assistant; an exact,
 * unique person or team becomes an id token; an ambiguous name asks first (nothing is sent); anything else
 * stays text. `@everyone`, `@here` and `@all` are reserved and do nothing yet.
 */
export function resolveTypedMentions(input: ResolveInput): ResolveResult {
  const mentions: MentionRef[] = [...input.mentions];
  const seen = new Set(mentions.map(mentionKey));
  const add = (ref: MentionRef) => {
    if (seen.has(mentionKey(ref)) || mentions.length >= MENTIONS_MAX) return;
    seen.add(mentionKey(ref));
    mentions.push(ref);
  };
  const { text } = input;
  let out = '';
  let last = 0;

  for (let at = text.indexOf('@'); at !== -1; at = text.indexOf('@', at + 1)) {
    if (at > 0 && isBeforeBlocker(text[at - 1])) continue;
    const rest = text.slice(at + 1);
    const word = WORD.exec(rest)?.[0] ?? '';
    if (!word) continue;
    const afterWord = rest.slice(word.length);
    const lowerWord = norm(word);
    const bare = !afterWord.startsWith('@') && !DOTTED_WORD.test(afterWord);

    if (ASSISTANT_ALIASES.includes(lowerWord) && bare) {
      add(ASSISTANT_MENTION);
      continue;
    }
    if (INERT_ALIASES.includes(lowerWord)) continue;

    const found = match(rest, word, input.candidates);
    if (!found) continue;
    const typed = rest.slice(0, found.length);
    let pick: MentionRef | undefined;
    if (found.hits.length === 1) pick = found.hits[0].ref;
    else {
      const chosen = input.choices?.[norm(typed)];
      pick = found.hits.find((c) => mentionKey(c.ref) === (chosen && mentionKey(chosen)))?.ref;
      if (!pick) return { status: 'ambiguous', typed, candidates: found.hits };
    }
    if (mentions.length >= MENTIONS_MAX && !seen.has(mentionKey(pick))) continue;
    add(pick);
    out += text.slice(last, at) + toToken(pick);
    last = at + 1 + found.length;
    at = last - 1;
  }
  return { status: 'ok', text: out + text.slice(last), mentions };
}
