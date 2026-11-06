/**
 * Text a run is still writing can turn out to be narration. Before a tool call the agent says in a short sentence what
 * it is about to do, and once the call arrives that sentence belongs in the run's box, not in the answer. Nothing in
 * the stream tells the two apart before then, so while the text still reads like such a sentence it is kept out of the
 * answer: narration is first drawn in its box, never drawn as the answer and then moved there.
 *
 * Text reads as the answer once it opens with a block (a heading, list, quote, table or code), breaks a line, starts a
 * third sentence, or runs past `ANSWER_LENGTH` characters. Narration in recorded runs is one or two sentences of 56 to
 * 253 characters; their answers pass this point a median of about 0.2 s after their first word.
 */

/** Longer than any sentence the agent says before a tool call. Link targets do not count: they are not read. */
const ANSWER_LENGTH = 280;
const BLOCK_START = /^(?:#{1,6}\s|[-*+]\s|\d+[.)]\s|>|\||```|~~~)/;
/**
 * The end of a sentence with more text after it: `. ! ? …` and a space, or Chinese and Japanese `。！？`, which need no
 * space. Closing quotes or brackets after the mark belong to it. A colon or semicolon only joins clauses of one sentence.
 */
const SENTENCE_END = /(?:[.!?…]["'”’)\]]*\s+|[。！？][」』）】〕〗〙〛〉》”’"')\]］]*\s*)(?=\S)/g;
const LINK_TARGET = /\]\([^)]*\)/g;

export function mayBeNarration(text: string): boolean {
  const read = text.trim();
  if (!read || read.includes('\n') || BLOCK_START.test(read)) return false;
  if (read.replace(LINK_TARGET, ']').length > ANSWER_LENGTH) return false;
  return (read.match(SENTENCE_END) ?? []).length < 2;
}
