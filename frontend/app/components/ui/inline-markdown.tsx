import React from 'react';

/**
 * Matches inline markdown spans plus ATX headings (`# `..`###### `) that show
 * up in cited source excerpts. Headings are rendered as bold text rather than
 * an actual `<h1>`-`<h6>` — this is meant for short snippets rendered inside
 * line-clamped containers (e.g. citation blockquotes), where a block-level
 * heading element would break `-webkit-line-clamp` and inline flow.
 *
 * Capture groups (positional, since named groups require ES2018 which this
 * project's `tsconfig` target predates):
 *   1: heading text        2: ***bold italic***     3: **bold**
 *   4: __bold__             5: ~~strikethrough~~     6: `code`
 *   7: *italic*             8: _italic_
 *
 * Kept as a source string (not a shared `RegExp` instance) because
 * `renderInlineMarkdown` recurses into heading text — a single shared global
 * `RegExp` would have its `lastIndex` clobbered by the inner call, corrupting
 * the outer loop's iteration and causing it to spin forever.
 */
const INLINE_MARKDOWN_SOURCE =
  '^#{1,6}[ \\t]+([^\\n]+)$|\\*{3}([^*]+?)\\*{3}|\\*{2}([^*]+?)\\*{2}|__([^_]+?)__|~~([^~]+?)~~|`([^`]+?)`|\\*([^*]+?)\\*|_([^_]+?)_';

/**
 * Renders a plain string containing inline markdown (plus ATX headings) into
 * React nodes made up only of inline elements (`strong`, `em`, `code`, `del`,
 * text). Safe to use anywhere plain text was previously interpolated
 * directly, including inside line-clamped blockquotes/text.
 */
export function renderInlineMarkdown(text: string): React.ReactNode {
  if (!text) return text;

  const nodes: React.ReactNode[] = [];
  let cursor = 0;
  let key = 0;
  let match: RegExpExecArray | null;

  // Fresh instance per call — see note on INLINE_MARKDOWN_SOURCE above.
  const regex = new RegExp(INLINE_MARKDOWN_SOURCE, 'gm');
  while ((match = regex.exec(text)) !== null) {
    if (match.index > cursor) {
      nodes.push(text.slice(cursor, match.index));
    }

    const [, heading, boldItalic, bold1, bold2, strike, code, italic1, italic2] = match;
    if (heading !== undefined) {
      // Recurse so inline markdown nested inside the heading text (e.g. a
      // heading containing `code` or **bold**) still renders correctly.
      nodes.push(<strong key={key++}>{renderInlineMarkdown(heading)}</strong>);
    } else if (boldItalic !== undefined) {
      nodes.push(<strong key={key++}><em>{renderInlineMarkdown(boldItalic)}</em></strong>);
    } else if (bold1 !== undefined || bold2 !== undefined) {
      nodes.push(<strong key={key++}>{renderInlineMarkdown(bold1 ?? bold2)}</strong>);
    } else if (strike !== undefined) {
      nodes.push(<del key={key++}>{renderInlineMarkdown(strike)}</del>);
    } else if (code !== undefined) {
      // Not recursed into — code span contents render literally (no nested
      // markdown), matching CommonMark's rule that inline code is verbatim.
      nodes.push(
        <code
          key={key++}
          style={{
            background: 'var(--slate-4)',
            border: '1px solid var(--slate-6)',
            color: 'inherit',
            padding: '1px 4px',
            borderRadius: '4px',
            fontFamily: '"Fira Code", "Cascadia Code", Consolas, "Courier New", monospace',
            fontSize: '0.9em',
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word',
          }}
        >
          {code}
        </code>
      );
    } else if (italic1 !== undefined || italic2 !== undefined) {
      nodes.push(<em key={key++}>{renderInlineMarkdown(italic1 ?? italic2)}</em>);
    }

    cursor = match.index + match[0].length;
  }

  if (cursor < text.length) {
    nodes.push(text.slice(cursor));
  }

  return nodes.length > 0 ? <>{nodes}</> : text;
}
