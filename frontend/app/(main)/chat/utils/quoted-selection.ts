/**
 * Quote-selected-text: the excerpt is stored inside the user `content` string
 * so history replay and reload keep it without a new Mongo field.
 *
 * Stored shape (parseable, readable if a client does not split it):
 *
 *   Quoted excerpt:
 *   """
 *   <excerpt>
 *   """
 *
 *   <question>
 */

// Keep equal to backend/nodejs/apps/src/modules/enterprise_search/utils/quoted-selection.ts
export const QUOTED_SELECTION_HEADER = 'Quoted excerpt:\n"""\n';
export const QUOTED_SELECTION_FOOTER = '\n"""\n\n';
export const QUOTED_SELECTION_MAX_CHARS = 4000;
export const QUOTED_SELECTION_PREVIEW_CHARS = 96;

export function normalizeQuotedSelection(text: string): string {
  return text
    .replace(/\u00a0/g, ' ')
    .replace(/[ \t]+\n/g, '\n')
    .replace(/\n[ \t]+/g, '\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}

export function clipQuotedSelection(text: string): { text: string; truncated: boolean } {
  const normalized = normalizeQuotedSelection(text);
  if (normalized.length <= QUOTED_SELECTION_MAX_CHARS) {
    return { text: normalized, truncated: false };
  }
  return {
    text: normalized.slice(0, QUOTED_SELECTION_MAX_CHARS).trimEnd(),
    truncated: true,
  };
}

export function previewQuotedSelection(
  text: string,
  maxChars = QUOTED_SELECTION_PREVIEW_CHARS,
): string {
  const oneLine = normalizeQuotedSelection(text).replace(/\s+/g, ' ');
  if (oneLine.length <= maxChars) return oneLine;
  return `${oneLine.slice(0, maxChars).trimEnd()}…`;
}

export function formatQuotedSelection(excerpt: string, question: string): string {
  const { text } = clipQuotedSelection(excerpt);
  const asked = question.trim();
  if (!text) return asked;
  return `${QUOTED_SELECTION_HEADER}${text}${QUOTED_SELECTION_FOOTER}${asked}`;
}

export function parseQuotedSelection(content: string): {
  excerpt: string | null;
  question: string;
} {
  if (!content.startsWith(QUOTED_SELECTION_HEADER)) {
    return { excerpt: null, question: content };
  }
  const close = content.indexOf(QUOTED_SELECTION_FOOTER, QUOTED_SELECTION_HEADER.length);
  if (close < 0) {
    return { excerpt: null, question: content };
  }
  return {
    excerpt: content.slice(QUOTED_SELECTION_HEADER.length, close),
    question: content.slice(close + QUOTED_SELECTION_FOOTER.length),
  };
}

/** Copy / title text: question first, excerpt as a readable fallback. */
export function readableQuotedSelection(content: string): string {
  const { excerpt, question } = parseQuotedSelection(content);
  if (!excerpt) return content;
  const quoted = excerpt.replace(/\n/g, '\n> ');
  return question.trim() ? `> ${quoted}\n\n${question}` : `> ${quoted}`;
}

export function conversationTitleFromQuery(query: string): string {
  const { excerpt, question } = parseQuotedSelection(query);
  const text = (question.trim() || excerpt || query).replace(/\s+/g, ' ').trim();
  return text.slice(0, 100);
}
