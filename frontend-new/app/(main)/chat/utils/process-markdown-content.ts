/**
 * Normalizes assistant markdown before `ReactMarkdown` runs.
 *
 * Backend citation links use `[N](…/record/…/preview#blockIndex=…)` (or `?blockIndex=`).
 * If left as-is, remark parses them as normal links, so inline citation chips never run.
 * Stripping the URL leaves `[N]` (or `[[N]]`) for `AnswerContent` to resolve.
 */
export function processMarkdownContent(content: string): string {
  if (!content) return '';

  return (
    content
      // Fix escaped newlines
      .replace(/\\n/g, '\n')
      // Strip citation markdown links [N](url) / [[N]](url) → [N] / [[N]]
      .replace(
        /(\[{1,2})(\d+)(\]{1,2})\s*\([^)]*?\/record\/[^)]*?[Pp]review[^)]*?(?:#|%23|\?)blockIndex=\d+[^)]*?\)/g,
        '$1$2$3',
      )
      // Clean up trailing whitespace but preserve structure
      .trim()
  );
}
