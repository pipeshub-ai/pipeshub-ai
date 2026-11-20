// Keep equal to frontend/app/(main)/chat/utils/quoted-selection.ts
export const QUOTED_SELECTION_HEADER = 'Quoted excerpt:\n"""\n';
export const QUOTED_SELECTION_FOOTER = '\n"""\n\n';

/**
 * A message that opens with a quoted excerpt, split as anyone but its author may read it: its own words are what
 * follows the last closing mark. An excerpt can hold the mark itself (a selected code block whose docstring closes
 * with it), so the first mark is not where the excerpt ends; a question that types the mark loses its start instead,
 * never the reverse. A quote that never closes has no words of its own. `quoted` is false for a message with no quote.
 */
export const splitQuotedSelection = (
  text: string,
): { quoted: boolean; question: string } => {
  const raw = typeof text === 'string' ? text : '';
  if (!raw.startsWith(QUOTED_SELECTION_HEADER)) {
    return { quoted: false, question: raw };
  }
  const body = raw.slice(QUOTED_SELECTION_HEADER.length);
  const close = body.lastIndexOf(QUOTED_SELECTION_FOOTER);
  return {
    quoted: true,
    question:
      close < 0 ? '' : body.slice(close + QUOTED_SELECTION_FOOTER.length),
  };
};

/** Title from the follow-up when the user quoted an excerpt. */
export const conversationTitleFromQuery = (query: string): string => {
  const raw = typeof query === 'string' ? query : '';
  if (raw.startsWith(QUOTED_SELECTION_HEADER)) {
    const close = raw.indexOf(
      QUOTED_SELECTION_FOOTER,
      QUOTED_SELECTION_HEADER.length,
    );
    if (close >= 0) {
      const excerpt = raw.slice(QUOTED_SELECTION_HEADER.length, close);
      const question = raw.slice(close + QUOTED_SELECTION_FOOTER.length);
      const text = (question.trim() || excerpt || raw)
        .replace(/\s+/g, ' ')
        .trim();
      return text.slice(0, 100);
    }
  }
  return raw.replace(/\s+/g, ' ').trim().slice(0, 100);
};
