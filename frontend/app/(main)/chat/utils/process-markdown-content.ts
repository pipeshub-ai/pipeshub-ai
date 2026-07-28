/**
 * Normalizes assistant markdown before `ReactMarkdown` runs.
 *
 * Backend citation links use `[N](…/record/…/preview#blockIndex=…)` for internal
 * citations and `[N](https://…)` for web search citations.
 * If left as-is, remark parses them as normal links, so inline citation chips never run.
 * Stripping the URL leaves `[N]` (or `[[N]]`) for `AnswerContent` to resolve.
 *
 * During streaming the LLM may emit `[source](refN)` or `[source](https://refN.xyz)`
 * instead of `[N](url)`.  The backend normalises these in persisted parts, but
 * narration text flushed mid-stream retains the raw form.  Additional passes
 * convert those to `[N]` with provisional numbering so the citation-badge
 * pipeline can render them immediately.
 */
export function processMarkdownContent(content: string): string {
  if (!content) return '';

  const seen = new Map<string, number>();

  return (
    content
      // Fix escaped newlines
      .replace(/\\n/g, '\n')
      // Strip ALL numbered citation links [N](url) / [[N]](url) → [N] / [[N]]
      .replace(/(\[{1,2})(\d+)(\]{1,2})\s*\([^)]+\)/g, '$1$2$3')
      // Convert citation links using tiny-ref URLs [source](refN) or
      // [source](https://refN.xyz) → [N].  The LLM always receives tiny refs
      // (TINY_URL_THRESHOLD=0), so this is the primary streaming-narration path.
      .replace(
        /\[[^\]]*\]\s*\((?:https?:\/\/)?(ref(\d+))(?:\.xyz\/?)?[^)]*\)/g,
        (_match: string, _refFull: string, refNum: string) => {
          const key = `ref${refNum}`;
          if (!seen.has(key)) {
            seen.set(key, seen.size + 1);
          }
          return `[${seen.get(key)}]`;
        },
      )
      // Convert citation links using full record URLs [source](/record/…) → [N].
      // Fallback for when TINY_URL_THRESHOLD is raised above 0.
      .replace(
        /\[[^\]]*\]\s*\([^)]*\/record\/([a-f0-9-]+)[^)]*\)/g,
        (_match: string, recordId: string) => {
          if (!seen.has(recordId)) {
            seen.set(recordId, seen.size + 1);
          }
          return `[${seen.get(recordId)}]`;
        },
      )
      // Clean up trailing whitespace but preserve structure
      .trim()
  );
}
