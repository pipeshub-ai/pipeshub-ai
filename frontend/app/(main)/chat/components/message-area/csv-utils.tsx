import React from 'react';
import { InlineCitationBadge } from './response-tabs/citations';
import type { CitationMaps, CitationCallbacks } from './response-tabs/citations';

/**
 * Parse CSV text into a 2-D array of strings.
 * Handles:
 *   - Quoted fields containing commas or escaped "" quotes.
 *   - Markdown links [N](url) where the URL may contain commas (e.g. #:~:text= fragments).
 *     Parenthesis depth is tracked so commas inside (...) are never treated as
 *     field separators.
 *
 * Rows are returned exactly as parsed — no reordering, merging, or removal of cells.
 * Short rows are padded with empty strings so every row has the same column count
 * as the header (prevents rendering crashes).
 */
export function parseCsvContent(content: string): string[][] {
  const lines = content.trim().split(/\r?\n/);
  const rows = lines
    .filter((line) => line.trim().length > 0)
    .map((line) => {
      const cells: string[] = [];
      let current = '';
      let inQuotes = false;
      let parenDepth = 0;
      for (let i = 0; i < line.length; i++) {
        const char = line[i];
        if (char === '"') {
          if (inQuotes && line[i + 1] === '"') {
            current += '"';
            i++;
          } else {
            inQuotes = !inQuotes;
          }
        } else if (char === '(' && !inQuotes) {
          parenDepth++;
          current += char;
        } else if (char === ')' && !inQuotes) {
          parenDepth = Math.max(0, parenDepth - 1);
          current += char;
        } else if (char === ',' && !inQuotes && parenDepth === 0) {
          cells.push(current.trim());
          current = '';
        } else if (char === '\\' && line[i + 1] === '"') {
          // JSON-style \" escaping: drop the backslash so the following '"'
          // is processed as a normal CSV quote toggle.  This prevents cells
          // from showing literal \value\ during streaming before the JSON
          // response has been fully decoded.
        } else {
          current += char;
        }
      }
      cells.push(current.trim());
      return cells;
    });

  if (rows.length < 2) return rows;

  const headerLen = rows[0].length;
  return rows.map((row, idx) => {
    if (idx === 0) return row;
    if (row.length < headerLen) return [...row, ...Array(headerLen - row.length).fill('')];
    return row;
  });
}

/**
 * Render a single CSV cell value, converting any [N](url) markdown links or
 * bare [N] citation markers into InlineCitationBadge components.
 * Plain-text cells (no markers) are returned unchanged.
 *
 * LLMs often put source citations in a "source_links" column using either:
 *   [N](https://...)          — full markdown link
 *   [N](url1)";"[N](url2)   — semicolon-separated with surrounding quotes
 * The regex strips the surrounding quote/semicolon artifacts from the
 * inter-badge separators so the column renders as a clean badge row.
 */
export function parseCsvCellContent(
  text: string,
  citationMaps?: CitationMaps,
  citationCallbacks?: CitationCallbacks,
): React.ReactNode[] {
  const tokenRegex = /\[(\d+)\](?:\([^)]*\))?/g;
  const parts: React.ReactNode[] = [];
  let last = 0;
  let hasMarkers = false;
  let match: RegExpExecArray | null;

  while ((match = tokenRegex.exec(text)) !== null) {
    hasMarkers = true;

    if (match.index > last) {
      const between = text.slice(last, match.index).replace(/^[;"]+|[;"]+$/g, '');
      if (between) parts.push(between);
    }

    const chunkIndex = parseInt(match[1], 10);
    const citationId = citationMaps?.citationsOrder[chunkIndex];
    const citation = citationId ? citationMaps?.citations[citationId] : undefined;

    parts.push(
      <InlineCitationBadge
        key={`csv-cite-${match.index}-${chunkIndex}`}
        chunkIndex={chunkIndex}
        occurrenceKey={`csv-${match.index}-${chunkIndex}`}
        citation={citation}
        callbacks={citationCallbacks}
      />,
    );

    last = match.index + match[0].length;
  }

  if (last < text.length) {
    const rest = hasMarkers
      ? text.slice(last).replace(/^[;"]+|[;"]+$/g, '')
      : text.slice(last);
    if (rest) parts.push(rest);
  }

  return parts.length > 0 ? parts : [text];
}
