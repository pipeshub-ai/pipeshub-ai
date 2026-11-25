import type { MessagePart } from './types';

/**
 * ADR-RUN-03 D4 (ANSWER-CONTRACT-FU-2): the subject a request named was not found in the person's knowledge, as Node
 * sends it on the answer's final part. `line` is the English the answer text starts with, for a surface with no UI;
 * the chat words the rest in the reader's language and leaves the line out. `near`: the closest records' names, when
 * the person could not be asked (a schedule's run); `proceeded`: they were asked and chose to go on.
 */
export interface NotFound {
  subject: string;
  hits: number;
  line: string;
  near: string[];
  proceeded: boolean;
  /** The connected sources searched besides the person's PipesHub knowledge (ANSWER-CONTRACT-SOURCES). */
  places: string[];
}

function notFound(value: unknown): NotFound | null {
  if (value === null || typeof value !== 'object') return null;
  const raw = value as Record<string, unknown>;
  const { subject, hits, line } = raw;
  if (typeof subject !== 'string' || !subject || typeof line !== 'string' || !line) return null;
  if (typeof hits !== 'number' || !Number.isInteger(hits) || hits < 0) return null;
  const strings = (v: unknown) => (Array.isArray(v) ? v.filter((n): n is string => typeof n === 'string' && n !== '') : []);
  return { subject, hits, line, near: strings(raw.near), proceeded: raw.proceeded === true, places: strings(raw.places) };
}

/**
 * The answer's not-found fact: the saved message's own, else its final text part's (a shared viewer keeps only that
 * part). A reply streamed into an answered card's row holds live parts, which never carry it.
 */
export function notFoundOf(parts: MessagePart[] | undefined, own?: unknown): NotFound | null {
  return notFound(own) ?? notFound(parts?.find((part) => part.type === 'text' && part.isFinal === true)?.notFound);
}

/** Every place looked in, the person's PipesHub knowledge first, as one list in the reader's language. */
export function placesText(places: string[], knowledge: string, locale?: string): string {
  const every = [knowledge, ...places];
  try {
    return new Intl.ListFormat(locale, { style: 'long', type: 'conjunction' }).format(every);
  } catch {
    return every.join(', ');
  }
}

/** The answer without the English line it starts with, which the chat words itself; as it is when it does not. */
export function withoutNotFoundLine(answer: string, fact: NotFound): string {
  const lead = answer.trimStart();
  return lead.startsWith(fact.line) ? lead.slice(fact.line.length).replace(/^\s+/, '') : answer;
}
