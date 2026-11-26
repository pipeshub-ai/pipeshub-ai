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
 *
 * A link text that is part of the sentence (a ticket id, a document's name) stays
 * before its number, as the backend keeps it (`app/utils/citations.py`
 * `citation_link_label`): `[NW-118](ref3)` → `NW-118 [1]`.
 */

/** Link text that only marks a citation: the number replaces it. Mirrors the backend's rule. */
const GENERIC_LINK_TEXT = /^\s*(?:sources?|src|citations?|cite|references?|ref\s*\d*|link|\[?\^?\d+\]?|https?:\/\/\S*)?\s*$/i;

/** The words of a citation's link text the reader keeps: none when it only marks the citation. */
export function citationLinkLabel(linkText: string): string {
  const text = linkText.trim();
  return GENERIC_LINK_TEXT.test(text) ? '' : text;
}

function labelled(label: string, badge: string): string {
  return label ? `${label} ${badge}` : badge;
}

/**
 * A markdown link target `(…)`. One level of balanced parentheses is allowed, as in
 * `(https://en.wikipedia.org/wiki/Python_(programming_language))`; stopping at the first `)`
 * would leave the rest of the URL in the answer as text.
 */
const TARGET_CHAR = String.raw`(?:[^()\n]|\([^()\n]*\))`;
export const LINK_TARGET = String.raw`\(` + TARGET_CHAR + String.raw`*\)`;

const NUMBERED_CITATION_LINK = new RegExp(String.raw`(\[{1,2})(\d+)(\]{1,2})\s*` + LINK_TARGET, 'g');
const TINY_REF_LINK = new RegExp(
  String.raw`\[([^\]]*)\]\s*\((?:https?:\/\/)?(ref(\d+))(?:\.xyz\/?)?` + TARGET_CHAR + String.raw`*\)`,
  'g',
);
const RECORD_LINK = new RegExp(
  String.raw`\[([^\]]*)\]\s*\(` + TARGET_CHAR + String.raw`*?\/record\/([a-f0-9-]+)` + TARGET_CHAR + String.raw`*\)`,
  'g',
);

export function processMarkdownContent(content: string): string {
  if (!content) return '';

  const seen = new Map<string, number>();

  return (
    content
      // Fix escaped newlines
      .replace(/\\n/g, '\n')
      // Strip ALL numbered citation links [N](url) / [[N]](url) → [N] / [[N]]
      .replace(NUMBERED_CITATION_LINK, '$1$2$3')
      // Convert citation links using tiny-ref URLs [source](refN) or
      // [source](https://refN.xyz) → [N].  The LLM always receives tiny refs
      // (TINY_URL_THRESHOLD=0), so this is the primary streaming-narration path.
      .replace(
        TINY_REF_LINK,
        (_match: string, linkText: string, _refFull: string, refNum: string) => {
          const key = `ref${refNum}`;
          if (!seen.has(key)) {
            seen.set(key, seen.size + 1);
          }
          return labelled(citationLinkLabel(linkText), `[${seen.get(key)}]`);
        },
      )
      // Convert citation links using full record URLs [source](/record/…) → [N].
      // Fallback for when TINY_URL_THRESHOLD is raised above 0.
      .replace(
        RECORD_LINK,
        (_match: string, linkText: string, recordId: string) => {
          if (!seen.has(recordId)) {
            seen.set(recordId, seen.size + 1);
          }
          return labelled(citationLinkLabel(linkText), `[${seen.get(recordId)}]`);
        },
      )
      // Clean up trailing whitespace but preserve structure
      .trim()
  );
}
