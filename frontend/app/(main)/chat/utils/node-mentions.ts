import type { ChatKnowledgeFilters, CollectionMeta } from '@/chat/types';
import { bucketFor } from '@/chat/utils/tree-selection';

/** A knowledge-hub node embedded in a composer via `@`. */
export interface MentionNode {
  id: string;
  name: string;
  nodeType: string;
  connector: string;
  extension?: string | null;
  mimeType?: string | null;
  /** What the composer shows for it, when not the name cut short (see `withUniqueLabel`). */
  label?: string;
}

export interface MentionSpan {
  node: MentionNode;
  start: number;
  end: number;
}

export interface ActiveMention {
  /** Index of the `@` that opened this query. */
  start: number;
  query: string;
}

// The word joiner in a mention's opening pad is not whitespace, but it does end a word.
const BOUNDARY = /[\s\u2060]/;

function boundaryBefore(text: string, index: number): boolean {
  return index === 0 || BOUNDARY.test(text[index - 1] ?? '');
}

function boundaryAfter(text: string, index: number): boolean {
  return index >= text.length || BOUNDARY.test(text[index] ?? '');
}

// Longer than any name worth searching for; past it, an `@` is just text.
const MAX_QUERY_LENGTH = 60;

/**
 * The `@query` the caret is inside, if the user is still typing a mention.
 * Names have spaces, so a query may too; a line break, a leading space or two
 * spaces in a row end it.
 */
export function activeMentionQuery(text: string, caret: number): ActiveMention | null {
  const safeCaret = Math.max(0, Math.min(caret, text.length));
  const upto = text.slice(0, safeCaret);
  const at = upto.lastIndexOf('@');
  if (at < 0 || !boundaryBefore(text, at)) return null;
  const query = upto.slice(at + 1);
  if (query.length > MAX_QUERY_LENGTH || /^\s|\s\s|[\n\u2060]/.test(query)) return null;
  return { start: at, query };
}

// Characters of a name shown in its chip, the ellipsis included.
export const MENTION_LABEL_MAX = 24;

type Segmenter = new (locale?: string, options?: { granularity: 'grapheme' }) => {
  segment: (text: string) => Iterable<{ segment: string }>;
};

// Whole characters, so an emoji or an accented letter is never cut in half.
function characters(text: string): string[] {
  const Segmenter = (Intl as unknown as { Segmenter?: Segmenter }).Segmenter;
  if (!Segmenter) return Array.from(text);
  return Array.from(new Segmenter(undefined, { granularity: 'grapheme' }).segment(text), (part) => part.segment);
}

// Cut at a word's end where one is near, so no half word is left (and flagged by spellcheck).
function cutName(name: string): string {
  const chars = characters(name);
  if (chars.length <= MENTION_LABEL_MAX) return name;
  let kept = chars.slice(0, MENTION_LABEL_MAX - 1);
  if (!/\s/.test(chars[kept.length] ?? '')) {
    const space = kept.map((char) => /\s/.test(char)).lastIndexOf(true);
    if (space >= MENTION_LABEL_MAX / 2) kept = kept.slice(0, space);
  }
  return `${kept.join('').trimEnd()}\u2026`;
}

/** A mention as its chip shows it: the name cut to `MENTION_LABEL_MAX`, ending in an ellipsis. */
export function mentionLabel(node: MentionNode): string {
  return node.label ?? cutName(node.name);
}

/**
 * The node to insert, with its full name as its label where its cut name is
 * another mention's label too; two chips of one label would be told apart by
 * position only, and lose track of which node is which as text is edited.
 */
export function withUniqueLabel(node: MentionNode, mentions: readonly MentionNode[]): MentionNode {
  const label = cutName(node.name);
  const clash =
    label !== node.name &&
    mentions.some((other) => other.id !== node.id && other.name !== node.name && mentionLabel(other) === label);
  return clash ? { ...node, label: node.name } : node;
}

function locate(
  text: string,
  mentions: readonly MentionNode[],
  shown: (node: MentionNode) => string,
): MentionSpan[] {
  const kept: MentionSpan[] = [];
  const used = new Set<string>();
  const taken = (start: number, end: number) => kept.some((span) => start < span.end && end > span.start);
  const byLength = [...mentions].sort((a, b) => shown(b).length - shown(a).length);
  for (const padded of [true, false]) {
    for (const node of byLength) {
      const label = shown(node);
      if (!label || used.has(node.id)) continue;
      for (let index = text.indexOf(label); index >= 0; index = text.indexOf(label, index + 1)) {
        const end = index + label.length;
        if (!boundaryBefore(text, index) || !boundaryAfter(text, end) || taken(index, end)) continue;
        if (padded && text.slice(index - MENTION_PAD_OPEN.length, index) !== MENTION_PAD_OPEN) continue;
        kept.push({ node, start: index, end });
        used.add(node.id);
        break;
      }
    }
  }
  return kept.sort((a, b) => a.start - b.start);
}

/**
 * Composer mentions whose labels are still intact, left to right, on word
 * boundaries. A padded occurrence (what the composer inserted) is taken before
 * a plain one, so the same words typed earlier are not mistaken for the
 * mention; longer labels go first, and no two mentions share text, so two
 * nodes of one name each keep their own occurrence.
 */
export function locateMentions(text: string, mentions: readonly MentionNode[]): MentionSpan[] {
  return locate(text, mentions, mentionLabel);
}

/** The same, for a sent message, which carries each mention's full name. */
export function locateMentionNames(text: string, mentions: readonly MentionNode[]): MentionSpan[] {
  return locate(text, mentions, (node) => node.name);
}

export function retainedMentions(text: string, mentions: readonly MentionNode[]): MentionNode[] {
  return locateMentions(text, mentions).map((span) => span.node);
}

/**
 * Padding inside a mention's chip, the opening one wide enough for its icon.
 * The composer draws its text behind a transparent textarea, so room inside
 * the chip's border has to be real characters; CSS padding would move the
 * caret off the text. A line never breaks between the en and em space, and the
 * word joiner keeps it from breaking before the name. Neither pad is sent.
 */
export const MENTION_PAD_OPEN = '\u2002\u2003\u2060';
export const MENTION_PAD_CLOSE = '\u2004';

/** Replace the active `@query` with the node's padded label and a trailing space. */
export function insertMention(text: string, atStart: number, caret: number, node: MentionNode): string {
  return `${text.slice(0, atStart)}${MENTION_PAD_OPEN}${mentionLabel(node)}${MENTION_PAD_CLOSE} ${text.slice(caret)}`;
}

export function caretAfterMention(atStart: number, node: MentionNode): number {
  return atStart + MENTION_PAD_OPEN.length + mentionLabel(node).length + MENTION_PAD_CLOSE.length + 1;
}

/** A mention's span widened to the pads around it, where they are still there. */
export function mentionChipBounds(text: string, span: MentionSpan): { start: number; end: number } {
  const open = text.slice(span.start - MENTION_PAD_OPEN.length, span.start) === MENTION_PAD_OPEN;
  const close = text.slice(span.end, span.end + MENTION_PAD_CLOSE.length) === MENTION_PAD_CLOSE;
  return {
    start: open ? span.start - MENTION_PAD_OPEN.length : span.start,
    end: close ? span.end + MENTION_PAD_CLOSE.length : span.end,
  };
}

// Pads left behind once a mention's name was edited, or its word joiner deleted.
const ORPHAN_PAD = /\u2002\u2003\u2060?|\u2060|\u2004/g;

/**
 * The message as sent: each mention's pads removed and its label back to the
 * full name, everything else as typed.
 * A pad whose mention is gone becomes a space where it was the only thing
 * between two words, and disappears elsewhere.
 */
export function stripMentionPads(text: string, mentions: readonly MentionNode[]): string {
  let out = '';
  let cursor = 0;
  for (const span of locateMentions(text, mentions)) {
    const chip = mentionChipBounds(text, span);
    out += text.slice(cursor, chip.start) + span.node.name;
    cursor = chip.end;
  }
  out += text.slice(cursor);
  return out.replace(ORPHAN_PAD, (pad: string, at: number, whole: string) => {
    const before = whole[at - 1];
    const after = whole[at + pad.length];
    return before && after && !/\s/.test(before) && !/\s/.test(after) ? ' ' : '';
  });
}

// A chip's range, with the space after it where one before it (or the start) would leave two in a row.
function chipWithSpace(text: string, chip: { start: number; end: number }): { start: number; end: number } {
  const lone = chip.start === 0 || /\s/.test(text[chip.start - 1] ?? '');
  return { start: chip.start, end: lone && text[chip.end] === ' ' ? chip.end + 1 : chip.end };
}

/** The range removing a mention's chip takes: its pads, and a space that would be left doubled. */
export function mentionChipRemoval(text: string, span: MentionSpan): { start: number; end: number } {
  return chipWithSpace(text, mentionChipBounds(text, span));
}

/**
 * The range a Backspace or Delete over `[from, to)` removes, widened so no chip
 * is left part-deleted: a key that would take part of a chip takes all of it.
 * Null where the key touches no chip only in part, and its own edit stands.
 */
export function chipDeletionRange(
  text: string,
  mentions: readonly MentionNode[],
  from: number,
  to: number,
  key: 'Backspace' | 'Delete',
): { start: number; end: number } | null {
  const chips = locateMentions(text, mentions).map((span) => mentionChipBounds(text, span));
  if (from === to) {
    const hit = chips.find((chip) =>
      key === 'Backspace' ? chip.start < from && from <= chip.end : chip.start <= from && from < chip.end,
    );
    return hit ? chipWithSpace(text, hit) : null;
  }
  let start = from;
  let end = to;
  let partial = false;
  for (const chip of chips) {
    if (chip.start >= to || chip.end <= from) continue;
    if (chip.start < from || chip.end > to) partial = true;
    start = Math.min(start, chip.start);
    end = Math.max(end, chip.end);
  }
  return partial ? { start, end } : null;
}

/** The text with one mention back to plain words: its full name, without pads. */
export function unembedMention(text: string, mentions: readonly MentionNode[], id: string): string {
  const span = locateMentions(text, mentions).find((candidate) => candidate.node.id === id);
  if (!span) return text;
  const chip = mentionChipBounds(text, span);
  return text.slice(0, chip.start) + span.node.name + text.slice(chip.end);
}

/** Add mentioned nodes to the chat filters the next message is sent with. */
export function mergeMentionFilters(
  current: ChatKnowledgeFilters,
  nodes: readonly MentionNode[],
): ChatKnowledgeFilters {
  const next: ChatKnowledgeFilters = {
    apps: [...(current.apps ?? [])],
    kb: [...(current.kb ?? [])],
    recordGroups: [...(current.recordGroups ?? [])],
    records: [...(current.records ?? [])],
    ...(current.recordsExact?.length ? { recordsExact: [...current.recordsExact] } : {}),
  };
  for (const node of nodes) {
    const bucket = bucketFor(node.nodeType);
    const list = next[bucket] ?? [];
    if (!list.includes(node.id)) next[bucket] = [...list, node.id];
  }
  return next;
}

export function mentionMeta(nodes: readonly MentionNode[]): {
  names: Record<string, string>;
  meta: Record<string, CollectionMeta>;
} {
  const names: Record<string, string> = {};
  const meta: Record<string, CollectionMeta> = {};
  for (const node of nodes) {
    names[node.id] = node.name;
    meta[node.id] = {
      name: node.name,
      nodeType: node.nodeType,
      connector: node.connector,
      extension: node.extension,
      mimeType: node.mimeType,
    };
  }
  return { names, meta };
}
