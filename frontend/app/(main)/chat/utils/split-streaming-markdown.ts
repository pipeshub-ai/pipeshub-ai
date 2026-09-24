import { createFenceState, advanceFenceState } from './repair-streaming-markdown';

/**
 * Splits streamed markdown into top-level blocks so `AnswerContent` can give
 * each block its own memoized `ReactMarkdown` instance — only the block that
 * is still growing needs to re-parse on each SSE flush; every earlier block
 * (already fully formed) gets a cache hit.
 *
 * This is only ever used on `streamingContent` while `isStreaming` is true.
 * The persisted / final answer always renders through a single `ReactMarkdown`
 * instance, so a splitting mistake here can only affect the in-progress
 * visual, never the saved message.
 *
 * Splitting rule: break on a blank line, EXCEPT:
 *   - never inside a ``` / ~~~ fence (blank lines are fence content there)
 *   - never when the next non-blank line looks like a continuation of the
 *     same construct — a blockquote (`>`), a list item marker, a 4-space
 *     indented continuation, a table row (`|`), or a closing HTML tag. This
 *     favors under-splitting (bigger blocks, still correct) over the reverse:
 *     a false split could sever a table's header from its delimiter row and
 *     make remark-gfm fall back to plain text for that block.
 */
export function splitMarkdownBlocks(content: string): string[] {
  if (!content) return [];

  const lines = content.split('\n');
  const blocks: string[][] = [];
  let current: string[] = [];
  let fenceState = createFenceState();
  // Set when a blank line was seen (outside a fence) since the last
  // non-blank line — i.e. the next non-blank line is a candidate boundary.
  let pendingBoundary = false;

  for (const line of lines) {
    const wasInFence = fenceState.inFence;
    fenceState = advanceFenceState(fenceState, line);
    const isBlank = line.trim().length === 0;

    if (isBlank) {
      if (!wasInFence) pendingBoundary = true;
      current.push(line);
      continue;
    }

    if (pendingBoundary && !wasInFence && !startsWithContinuationMarker(line)) {
      if (current.length > 0) blocks.push(current);
      current = [];
    }
    pendingBoundary = false;

    current.push(line);
  }

  if (current.length > 0) blocks.push(current);

  return blocks.map((b) => b.join('\n')).filter((b) => b.trim().length > 0);
}

const LIST_MARKER_RE = /^\s*([-*+]\s|\d+[.)]\s)/;
const INDENTED_RE = /^ {4,}\S/;
const CLOSING_HTML_TAG_RE = /^\s*<\/[a-zA-Z][a-zA-Z0-9-]*>/;

function startsWithContinuationMarker(line: string): boolean {
  const trimmed = line.trimStart();
  if (trimmed.startsWith('>')) return true;
  if (trimmed.startsWith('|')) return true;
  if (LIST_MARKER_RE.test(line)) return true;
  if (INDENTED_RE.test(line)) return true;
  if (CLOSING_HTML_TAG_RE.test(line)) return true;
  return false;
}
