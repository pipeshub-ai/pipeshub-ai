// Keep equal to frontend/app/(main)/chat/utils/quoted-selection.ts
export const QUOTED_SELECTION_HEADER = 'Quoted excerpt:\n"""\n';
export const QUOTED_SELECTION_FOOTER = '\n"""\n\n';

/** Title from the follow-up when the user quoted an excerpt. */
export const conversationTitleFromQuery = (query: string): string => {
  const raw = typeof query === 'string' ? query : '';
  if (raw.startsWith(QUOTED_SELECTION_HEADER)) {
    const close = raw.indexOf(QUOTED_SELECTION_FOOTER, QUOTED_SELECTION_HEADER.length);
    if (close >= 0) {
      const excerpt = raw.slice(QUOTED_SELECTION_HEADER.length, close);
      const question = raw.slice(close + QUOTED_SELECTION_FOOTER.length);
      const text = (question.trim() || excerpt || raw).replace(/\s+/g, ' ').trim();
      return text.slice(0, 100);
    }
  }
  return raw.replace(/\s+/g, ' ').trim().slice(0, 100);
};
