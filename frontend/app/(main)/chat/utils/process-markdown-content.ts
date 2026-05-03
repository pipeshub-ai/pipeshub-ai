/**
 * Normalizes assistant markdown before `ReactMarkdown` runs.
 *
 * Backend citation links use `[N](…/record/…/preview#blockIndex=…)` for internal
 * citations and `[N](https://…)` for web search citations.
 * If left as-is, remark parses them as normal links, so inline citation chips never run.
 * Stripping the URL leaves `[N]` (or `[[N]]`) for `AnswerContent` to resolve.
 *
 * @param citationWebUrls — when provided, `[N](url)` links whose URL matches a
 *   known citation webUrl are also stripped (covers web search citations).
 */
export function processMarkdownContent(
  content: string,
  citationWebUrls?: Set<string>,
): string {
  if (!content) return '';

  return (
    content
      // Fix escaped newlines
      .replace(/\\n/g, '\n')
      // Strip citation markdown links [N](url) / [[N]](url) → [N] / [[N]]
      .replace(
        /(\[{1,2})(\d+)(\]{1,2})\s*\(([^)]+)\)/g,
        (match, open, num, close, url) => {
          // Always strip internal citation links (legacy block-preview URLs)
          if (/\/record\/.*[Pp]review.*(?:#|%23|\?)blockIndex=\d+/.test(url)) {
            return `${open}${num}${close}`;
          }
          // Strip web citation links whose URL matches a known citation
          if (citationWebUrls?.has(url.trim())) {
            return `${open}${num}${close}`;
          }
          return match;
        },
      )
      // Clean up trailing whitespace but preserve structure
      .trim()
  );
}
