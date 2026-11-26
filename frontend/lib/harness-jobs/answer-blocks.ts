import { processMarkdownContent } from '@/chat/utils/process-markdown-content';
import { stripAnswerMarkers } from '@/chat/utils/parse-download-markers';
import type { AnswerBlock, AnswerSegment } from './view-models';

/**
 * A reply's markdown as the design's `AnswerBlock[]` (`AnswerBody`, design.md §6.3): headings no larger than the
 * body, paragraphs, bullets and numbered items, code, tables, and each `[n]` as a citation segment.
 *
 * Citation links are normalised first by the classic chat's own reader (`processMarkdownContent`), and download
 * markers are stripped (`stripAnswerMarkers`), so both interfaces read an answer the same way. Pure: no React.
 */

// Sticky (`y`): each matches only at the index it is asked about. Built with RegExp, which the target allows.
const CITATION = new RegExp(String.raw`\[\[?(\d+)\]?\]`, 'y');
const LINK = new RegExp(String.raw`\[([^\]\n]+)\]\(([^)\s]+)\)`, 'y');
const CODE = new RegExp('`([^`\\n]+)`', 'y');
const STRONG = new RegExp(String.raw`\*\*([^*\n]+)\*\*|__([^_\n]+)__`, 'y');
const EMPHASIS = new RegExp(String.raw`\*([^*\n]+)\*|_([^_\n]+)_`, 'y');

/** Inline markdown into segments: text, strong, links, inline code, and `[n]` / `[[n]]` citations. */
export function inlineSegments(text: string): AnswerSegment[] {
  const out: AnswerSegment[] = [];
  let buffer = '';
  const flush = () => {
    if (buffer) out.push({ kind: 'text', text: buffer });
    buffer = '';
  };
  const at = (re: RegExp, i: number): RegExpExecArray | null => {
    re.lastIndex = i;
    return re.exec(text);
  };
  let i = 0;
  while (i < text.length) {
    const ch = text[i];
    let m: RegExpExecArray | null;
    if (ch === '[' && (m = at(CITATION, i))) {
      flush();
      out.push({ kind: 'citation', label: m[1] });
      i += m[0].length;
      continue;
    }
    if (ch === '!' && text[i + 1] === '[' && (m = at(LINK, i + 1))) {
      // An image is a link to it: the answer never loads a picture from an address the model wrote.
      flush();
      out.push({ kind: 'link', text: m[1].trim() || m[2], href: m[2] });
      i += 1 + m[0].length;
      continue;
    }
    if (ch === '[' && (m = at(LINK, i))) {
      flush();
      out.push({ kind: 'link', text: m[1], href: m[2] });
      i += m[0].length;
      continue;
    }
    if (ch === '`' && (m = at(CODE, i))) {
      flush();
      out.push({ kind: 'code', text: m[1] });
      i += m[0].length;
      continue;
    }
    if ((ch === '*' || ch === '_') && (m = at(STRONG, i))) {
      flush();
      out.push({ kind: 'text', text: m[1] ?? m[2], strong: true });
      i += m[0].length;
      continue;
    }
    if ((ch === '*' || ch === '_') && (m = at(EMPHASIS, i)) && (i === 0 || /\s|\(/.test(text[i - 1]))) {
      // Emphasis has no weight of its own in an answer: its words stay, its marks go.
      buffer += m[1] ?? m[2];
      i += m[0].length;
      continue;
    }
    buffer += ch;
    i += 1;
  }
  flush();
  return out;
}

const HEADING = /^(#{1,6})\s+(.*?)\s*#*\s*$/;
const BULLET = /^([ \t]*)[-*+]\s+(.*)$/;
const NUMBERED = /^([ \t]*)(\d{1,3})[.)]\s+(.*)$/;
const FENCE = /^\s{0,3}(```|~~~)\s*([\w+-]*)/;
const TABLE_RULE = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/;
const QUOTE = /^\s{0,3}>\s?(.*)$/;
const RULE = /^\s{0,3}([-*_])(\s*\1){2,}\s*$/;

/** How deep a list item sits from its indent: two spaces (or a tab) a level, three levels at most. */
const levelOf = (indent: string): number => Math.min(3, Math.floor(indent.replace(/\t/g, '  ').length / 2));
const withLevel = (level: number) => (level > 0 ? { level } : {});

const alignOf = (rule: string): Array<'left' | 'right' | 'center'> =>
  cells(rule).map((c) => (c.startsWith(':') && c.endsWith(':') ? 'center' : c.endsWith(':') ? 'right' : 'left'));

const cells = (row: string): string[] =>
  row
    .trim()
    .replace(/^\|/, '')
    .replace(/\|$/, '')
    .split('|')
    .map((c) => c.trim());

/** Markdown into answer blocks. Unclosed code at the end (a reply still streaming) is shown as code so far. */
export function answerBlocks(markdown: string): AnswerBlock[] {
  const text = processMarkdownContent(stripAnswerMarkers(markdown ?? ''));
  const lines = text.replace(/\r\n?/g, '\n').split('\n');
  const blocks: AnswerBlock[] = [];
  let paragraph: string[] = [];
  let sectionIndex = 0;
  const endParagraph = () => {
    const joined = paragraph.join('\n').trim();
    if (joined) blocks.push({ kind: 'paragraph', segments: inlineSegments(joined) });
    paragraph = [];
  };
  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i];
    const fence = FENCE.exec(line);
    if (fence) {
      endParagraph();
      const body: string[] = [];
      i += 1;
      while (i < lines.length && !lines[i].trimStart().startsWith(fence[1])) {
        body.push(lines[i]);
        i += 1;
      }
      blocks.push({ kind: 'code', language: fence[2] ?? '', text: body.join('\n') });
      continue;
    }
    if (!line.trim()) {
      endParagraph();
      continue;
    }
    if (RULE.test(line)) {
      endParagraph();
      continue;
    }
    const heading = HEADING.exec(line);
    if (heading) {
      endParagraph();
      const level = heading[1].length;
      const words = heading[2].replace(/\*\*/g, '');
      if (level <= 2) blocks.push({ kind: 'heading', text: words });
      else {
        sectionIndex += 1;
        blocks.push({ kind: 'section-header', index: sectionIndex, text: words });
      }
      continue;
    }
    if (line.includes('|') && i + 1 < lines.length && TABLE_RULE.test(lines[i + 1])) {
      endParagraph();
      const header = cells(line).map(inlineSegments);
      const align = alignOf(lines[i + 1]);
      const rows: AnswerSegment[][][] = [];
      i += 2;
      while (i < lines.length && lines[i].includes('|') && lines[i].trim()) {
        rows.push(cells(lines[i]).map(inlineSegments));
        i += 1;
      }
      i -= 1;
      blocks.push({ kind: 'table', header, rows, ...(align.some((a) => a !== 'left') ? { align } : {}) });
      continue;
    }
    const bullet = BULLET.exec(line);
    if (bullet) {
      endParagraph();
      blocks.push({ kind: 'bullet', segments: inlineSegments(bullet[2]), ...withLevel(levelOf(bullet[1])) });
      continue;
    }
    const numbered = NUMBERED.exec(line);
    if (numbered) {
      endParagraph();
      blocks.push({ kind: 'bullet', index: Number(numbered[2]), segments: inlineSegments(numbered[3]), ...withLevel(levelOf(numbered[1])) });
      continue;
    }
    const quote = QUOTE.exec(line);
    paragraph.push(quote ? quote[1] : line);
  }
  endParagraph();
  return blocks;
}

/** The answer as plain text, for Copy: citations as `[n]`, links as their words. */
export function answerPlainText(blocks: ReadonlyArray<AnswerBlock>): string {
  const segs = (segments: ReadonlyArray<AnswerSegment>) =>
    segments.map((s) => (s.kind === 'citation' ? `[${s.label}]` : s.text)).join('');
  return blocks
    .map((b) => {
      switch (b.kind) {
        case 'paragraph':
          return segs(b.segments);
        case 'bullet':
          return `${b.index !== undefined ? `${b.index}.` : '-'} ${segs(b.segments)}`;
        case 'code':
          return b.text;
        case 'table':
          return [b.header, ...b.rows].map((row) => row.map(segs).join('\t')).join('\n');
        case 'section-header':
        case 'heading':
        case 'title':
          return b.text;
        default:
          return '';
      }
    })
    .join('\n\n');
}

/** The words of the blocks alone: no citation numbers, no markup, whitespace collapsed. */
function proseWords(blocks: ReadonlyArray<AnswerBlock>): string {
  const segs = (segments: ReadonlyArray<AnswerSegment>) => segments.map((s) => (s.kind === 'citation' ? ' ' : s.text)).join('');
  return blocks
    .map((b) => {
      switch (b.kind) {
        case 'paragraph':
        case 'bullet':
          return segs(b.segments);
        case 'table':
          return [b.header, ...b.rows].map((row) => row.map(segs).join(' ')).join(' ');
        case 'code':
        case 'section-header':
        case 'heading':
        case 'title':
          return b.text;
        default:
          return '';
      }
    })
    .join(' ')
    .replace(/\s+/g, ' ')
    .trim();
}

/** A preview shorter than this could start an unrelated answer by chance. */
const MIN_PREVIEW = 160;

/**
 * A job's report says nothing its turn's answer does not: the same words whatever their citations (the report cites
 * `[source](ref3)`, the answer it became `[1](https://…)`), or the start of them, because the job's stream carries a
 * report cut to a preview. Drawing both shows the reader the same words twice (design.md §6B.21).
 */
export function repeatsProse(report: ReadonlyArray<AnswerBlock>, answer: ReadonlyArray<AnswerBlock>): boolean {
  const said = proseWords(report).replace(/(\.\.\.|…)$/, '').trim();
  const whole = proseWords(answer);
  if (!said) return false;
  return said === whole || (said.length >= MIN_PREVIEW && whole.startsWith(said));
}
