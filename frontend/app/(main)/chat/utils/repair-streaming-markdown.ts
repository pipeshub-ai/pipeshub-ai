/**
 * Repairs incomplete / unterminated markdown content that arrives mid-stream
 * via SSE so that `ReactMarkdown + remark-gfm` always receives valid input.
 *
 * This is intentionally only applied to the in-progress streaming content.
 * The final message content (from the `complete` SSE event) is fully formed
 * by the server and never needs patching.
 *
 * Repairs applied, in order:
 *
 * 1. **Escaped newlines** — Some SSE payloads encode newlines as the two-char
 *    sequence `\n` (backslash + n). These must become real newlines before any
 *    structural analysis is done, otherwise the entire table/code block appears
 *    as a single paragraph of text.
 *
 * 2. **Unclosed code fences** — If an opening ``` (or ~~~) has no matching
 *    closing fence yet, every subsequent line is swallowed into the code block.
 *    We close the fence so text that arrives after the code block renders normally.
 *
 * 3. **Incomplete table rows** — `remark-gfm` requires each table row to be
 *    delimited by a trailing `|`. During streaming the last token is often a
 *    partial cell value without the closing pipe. Rather than force-closing
 *    it (which used to make the cell's text grow one token at a time and
 *    re-trigger `table-layout: auto` column-width resolution on every SSE
 *    chunk — the main source of table flicker), the partial row is dropped
 *    until a chunk arrives that completes it. The row then appears once,
 *    fully formed.
 */

/**
 * Running state of a ``` / ~~~ fence scan, threaded line-by-line so callers
 * (this file's own repair pass, and `split-streaming-markdown.ts`) can share
 * one fence-tracking implementation instead of re-deriving CommonMark §4.5
 * open/close matching twice.
 */
export interface FenceState {
  inFence: boolean;
  fenceChar: string;
  fenceLen: number;
}

export function createFenceState(): FenceState {
  return { inFence: false, fenceChar: '`', fenceLen: 3 };
}

/** Feed one line into the scan, returning the (possibly updated) state. */
export function advanceFenceState(state: FenceState, line: string): FenceState {
  const trimmed = line.trimStart();
  if (!state.inFence) {
    const m = trimmed.match(/^(`{3,}|~{3,})/);
    if (m) {
      return { inFence: true, fenceChar: m[1][0], fenceLen: m[1].length };
    }
    return state;
  }
  // A valid closing fence: same char, ≥ fenceLen repetitions, optional trailing whitespace
  const closeRe = new RegExp(`^\\${state.fenceChar}{${state.fenceLen},}\\s*$`);
  if (closeRe.test(trimmed)) {
    return { ...state, inFence: false };
  }
  return state;
}

export function repairStreamingMarkdown(content: string): string {
  if (!content) return content;

  // ── 1. Convert escaped newlines ──────────────────────────────────────────
  const result = content.replace(/\\n/g, '\n');

  const lines = result.split('\n');

  // ── 2. Detect and close unclosed code fences ─────────────────────────────
  // Both backtick fences (```) and tilde fences (~~~) are supported.
  let fenceState = createFenceState();
  for (const line of lines) {
    fenceState = advanceFenceState(fenceState, line);
  }

  if (fenceState.inFence) {
    // Unclosed fence — append the closing marker.
    // Return immediately; the partial content *inside* the fence is already
    // being rendered as a code block, which is the correct intermediate state.
    return result + '\n' + fenceState.fenceChar.repeat(fenceState.fenceLen);
  }

  // ── 3. Drop the last line if it is a partial table row ───────────────────
  // A GFM table row must start AND end with `|`. During streaming the very
  // last chunk is often a cell value still being typed, e.g.:
  //   | Organization Name | PipesHub
  // Dropping it (instead of force-closing with a trailing `|`) means the
  // table only re-renders once per completed row instead of once per chunk.
  const lastLine = lines[lines.length - 1];
  if (lastLine !== undefined) {
    const trimmedLast = lastLine.trimStart();
    if (trimmedLast.startsWith('|') && !lastLine.trimEnd().endsWith('|')) {
      lines.pop();
      return lines.join('\n');
    }
  }

  return result;
}
