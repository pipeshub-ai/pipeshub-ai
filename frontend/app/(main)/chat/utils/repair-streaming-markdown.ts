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
 *    partial cell value without the closing pipe, causing the whole table to
 *    fall back to plain-text rendering. Appending ` |` makes it a valid row.
 */
export function repairStreamingMarkdown(content: string): string {
  if (!content) return content;

  // ── 1. Convert escaped newlines ──────────────────────────────────────────
  const result = content.replace(/\\n/g, '\n');

  const lines = result.split('\n');

  // ── 2. Detect and close unclosed code fences ─────────────────────────────
  // Walk every line, toggling `inFence` when we spot opening / closing markers.
  // Both backtick fences (```) and tilde fences (~~~) are supported.
  // The closing fence must use the same character and have at least as many
  // repetitions as the opening fence (CommonMark spec §4.5).
  let inFence = false;
  let fenceChar = '`';
  let fenceLen = 3;

  for (const line of lines) {
    const trimmed = line.trimStart();
    if (!inFence) {
      const m = trimmed.match(/^(`{3,}|~{3,})/);
      if (m) {
        inFence = true;
        fenceChar = m[1][0];      // '`' or '~'
        fenceLen = m[1].length;   // 3, 4, …
      }
    } else {
      // A valid closing fence: same char, ≥ fenceLen repetitions, optional trailing whitespace
      const closeRe = new RegExp(`^\\${fenceChar}{${fenceLen},}\\s*$`);
      if (closeRe.test(trimmed)) {
        inFence = false;
      }
    }
  }

  if (inFence) {
    // Unclosed fence — append the closing marker.
    // Return immediately; the partial content *inside* the fence is already
    // being rendered as a code block, which is the correct intermediate state.
    return result + '\n' + fenceChar.repeat(fenceLen);
  }

  // ── 3. Repair the last line if it is a partial table row ─────────────────
  // A GFM table row must start AND end with `|`. During streaming the very
  // last chunk is often a cell value still being typed, e.g.:
  //   | Organization Name | PipesHub
  // Appending ` |` makes it a syntactically complete row so the table renders.
  const lastLine = lines[lines.length - 1];
  if (lastLine !== undefined) {
    const trimmedLast = lastLine.trimStart();
    if (trimmedLast.startsWith('|') && !lastLine.trimEnd().endsWith('|')) {
      lines[lines.length - 1] = lastLine.trimEnd() + ' |';
      return lines.join('\n');
    }
  }

  return result;
}
