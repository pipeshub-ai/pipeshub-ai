/**
 * How a sent question is drawn: a short heading, and any long or fenced material
 * lifted out of it into "pasted" chips under the heading.
 *
 * Display only. What was sent, what Copy copies and what Edit or Regenerate
 * reuse is always the stored question; nothing here changes it.
 */

/** A question at least this long, or with at least this many lines, reads as pasted material. */
export const QUESTION_PASTE_MIN_CHARS = 480;
export const QUESTION_PASTE_MIN_LINES = 8;
/** The heading taken from long material: its first line when it is this short and more follows. */
const HEAD_LINE_MAX_CHARS = 160;
/** Otherwise its first sentence when it ends by here, or a clip at this length. */
const LEAD_MAX_CHARS = 140;

export type QuestionPasteKind = 'text' | 'code';

export interface QuestionPaste {
  kind: QuestionPasteKind;
  text: string;
}

export interface QuestionDisplay {
  /** The words drawn as the heading. */
  heading: string;
  /** Material lifted out of the question, in the order it appeared. */
  pastes: QuestionPaste[];
  /**
   * The question was nothing but pasted code: the heading stays in the page (so
   * the turn keeps its landmark) but is only read by assistive technology.
   */
  headingHidden: boolean;
}

const FENCE = /```[^\n`]*\n?([\s\S]*?)```/g;

function lineCount(text: string): number {
  return text.split('\n').length;
}

function readsAsPaste(text: string): boolean {
  return text.length >= QUESTION_PASTE_MIN_CHARS || lineCount(text) >= QUESTION_PASTE_MIN_LINES;
}

/**
 * Splits long material into a heading and the paste under it: the first line
 * leads when it is short and more follows (the rest is the paste); otherwise the
 * first sentence or a clip leads and the whole text is the paste, so nothing the
 * person wrote is lost from it.
 */
function splitLong(text: string, clip: (text: string, limit: number) => string): { heading: string; paste: string } {
  const newlineAt = text.indexOf('\n');
  if (newlineAt > 0 && newlineAt <= HEAD_LINE_MAX_CHARS) {
    const head = text.slice(0, newlineAt).trim();
    const body = text.slice(newlineAt + 1).trim();
    if (head && body) return { heading: head, paste: body };
  }
  const sentenceEnd = text.search(/[.?!]\s/);
  const heading =
    sentenceEnd > 0 && sentenceEnd <= LEAD_MAX_CHARS
      ? text.slice(0, sentenceEnd + 1).trim()
      : `${clip(text, LEAD_MAX_CHARS).trimEnd()}…`;
  return { heading, paste: text };
}

/**
 * @param clip cuts the text to a length without breaking a mention token in it.
 */
export function questionDisplay(
  question: string,
  clip: (text: string, limit: number) => string = (text, limit) => text.slice(0, limit),
): QuestionDisplay {
  const pastes: QuestionPaste[] = [];
  let rest = question;

  if (question.includes('```')) {
    const outside: string[] = [];
    let cursor = 0;
    for (const match of question.matchAll(FENCE)) {
      const start = match.index ?? 0;
      outside.push(question.slice(cursor, start));
      const code = match[1].replace(/\n$/, '');
      if (code.trim()) pastes.push({ kind: 'code', text: code });
      cursor = start + match[0].length;
    }
    if (pastes.length > 0) {
      outside.push(question.slice(cursor));
      rest = outside
        .map((piece) => piece.trim())
        .filter(Boolean)
        .join(' ');
      if (!rest) {
        return {
          heading: pastes.map((paste) => paste.text).join('\n'),
          pastes,
          headingHidden: true,
        };
      }
    }
  }

  if (readsAsPaste(rest)) {
    const { heading, paste } = splitLong(rest, clip);
    return { heading, pastes: [{ kind: 'text', text: paste }, ...pastes], headingHidden: false };
  }
  return { heading: rest, pastes, headingHidden: false };
}

export function pasteLineCount(text: string): number {
  return lineCount(text);
}
