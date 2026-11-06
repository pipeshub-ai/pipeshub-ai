import { createFenceState, advanceFenceState } from './repair-streaming-markdown';

/**
 * Splits answer markdown into top-level blocks so `AnswerContent` can give
 * each block its own memoized `ReactMarkdown` instance — only the block that
 * is still growing needs to re-parse on each SSE flush; every earlier block
 * (already fully formed) gets a cache hit.
 *
 * The finished answer is split the same way, so the hand-off from the
 * streamed text to the saved one keeps every block (and its chips) in place
 * instead of redrawing the whole answer. A split must therefore never change
 * how the markdown renders: blocks render on their own, so the rule is to
 * split only where nothing can carry across.
 *
 * Splitting rule: break on a blank line, EXCEPT:
 *   - never inside a ``` / ~~~ fence or a `$$` maths block (blank lines are
 *     their content there)
 *   - never inside an HTML element still open from an earlier line
 *   - never when the next non-blank line looks like a continuation of the
 *     same construct — a blockquote (`>`), a list item marker, an indented
 *     line (a list item's own paragraph, or code), a table row (`|`), or a
 *     closing HTML tag.
 *   - when the answer defines a link reference or a footnote, which can be
 *     used from any block: the blocks from the first one that defines or uses
 *     a defined label to the end render as one (a footnote's notes are drawn
 *     at the end). The blocks before it stay on their own, so a definition
 *     that arrives late does not make the whole answer parse again.
 * This favors under-splitting (bigger blocks, still correct) over the
 * reverse: a false split could sever a table's header from its delimiter row
 * and make remark-gfm fall back to plain text for that block.
 */
export function splitMarkdownBlocks(content: string): string[] {
  if (!content) return [];

  const lines = content.split('\n');
  const blocks: string[][] = [];
  let current: string[] = [];
  let fenceState = createFenceState();
  let inMath = false;
  let openHtml = 0;
  // Set when a blank line was seen (outside a fence) since the last
  // non-blank line — i.e. the next non-blank line is a candidate boundary.
  let pendingBoundary = false;

  for (const line of lines) {
    const wasInFence = fenceState.inFence;
    fenceState = advanceFenceState(fenceState, line);
    const isBlank = line.trim().length === 0;

    if (isBlank) {
      if (!wasInFence && !inMath && openHtml === 0) pendingBoundary = true;
      current.push(line);
      continue;
    }

    if (pendingBoundary && !wasInFence && !startsWithContinuationMarker(line)) {
      if (current.length > 0) blocks.push(current);
      current = [];
    }
    pendingBoundary = false;

    current.push(line);
    if (wasInFence || fenceState.inFence) continue;
    if ((line.match(/\$\$/g)?.length ?? 0) % 2 === 1) inMath = !inMath;
    if (!inMath) openHtml = Math.max(0, openHtml + htmlDepthChange(line));
  }

  if (current.length > 0) blocks.push(current);

  const split = blocks.map((b) => b.join('\n')).filter((b) => b.trim().length > 0);
  if (!DEFINITION_RE.test(content)) return split;
  const first = firstBlockWithDefinitions(split, content);
  if (first <= 0) return content.trim() ? [content] : [];
  return [...split.slice(0, first), split.slice(first).join('\n')];
}

/** The defined labels (`[1]: https://…` → `1`, `[^1]: …` → `^1`), as patterns that find a use of each in a block. */
function definedLabels(content: string): RegExp[] {
  const labels = new Set<string>();
  for (const m of content.matchAll(DEFINITIONS_RE)) labels.add(m[1].trim().toLowerCase().replace(/\s+/g, ' '));
  // Labels match case-insensitively, with any run of spaces as one.
  return [...labels].map((label) => new RegExp(`\\[${label.replace(/[.*+?^${}()|[\]\\]/g, '\\$&').replace(/ /g, '\\s+')}\\]`, 'i'));
}

/** The first block that defines a label or uses one (`[^1]`, `[text][1]`, `[1]`); -1 when none does. */
function firstBlockWithDefinitions(blocks: string[], content: string): number {
  const uses = definedLabels(content);
  return blocks.findIndex((block) => DEFINITION_RE.test(block) || uses.some((use) => use.test(block)));
}

const LIST_MARKER_RE = /^\s*([-*+]\s|\d+[.)]\s)/;
const INDENTED_RE = /^[ \t]+\S/;
const CLOSING_HTML_TAG_RE = /^\s*<\/[a-zA-Z][a-zA-Z0-9-]*>/;
/** A link reference definition (`[1]: https://…`) or a footnote (`[^1]: …`) on a line of its own. */
const DEFINITION_RE = /^ {0,3}\[[^\]\n]+\]:[ \t]*\S/m;
/** Every such definition, its label captured. */
const DEFINITIONS_RE = /^ {0,3}\[([^\]\n]+)\]:[ \t]*\S/gm;
/** HTML elements with no closing tag. */
const VOID_TAGS = new Set(['area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'source', 'track', 'wbr']);

/** Opening tags minus closing tags on a line: above zero, the element runs on past it. */
function htmlDepthChange(line: string): number {
  let change = 0;
  // Not in code spans (`List<String>`), and not autolinks (`<https://…>`): the name must end the tag or start its attributes.
  for (const m of line.replace(/`[^`]*`/g, '').matchAll(/<(\/?)([a-zA-Z][a-zA-Z0-9-]*)(?=[\s/>])[^<>]*?(\/?)>/g)) {
    if (m[3] === '/' || VOID_TAGS.has(m[2].toLowerCase())) continue;
    change += m[1] === '/' ? -1 : 1;
  }
  return change;
}

function startsWithContinuationMarker(line: string): boolean {
  const trimmed = line.trimStart();
  if (trimmed.startsWith('>')) return true;
  if (trimmed.startsWith('|')) return true;
  if (LIST_MARKER_RE.test(line)) return true;
  if (INDENTED_RE.test(line)) return true;
  if (CLOSING_HTML_TAG_RE.test(line)) return true;
  return false;
}
