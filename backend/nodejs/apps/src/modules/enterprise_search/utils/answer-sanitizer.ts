/**
 * Answer text as it may leave the server: nothing in it loads from another host until someone clicks.
 *
 * An answer is model output, and the model can be steered by what it reads, so an answer can carry a URL
 * holding the data it was given. The app's renderer gates outside images behind a click, but an API or
 * MCP client draws the body itself, so the body is made safe here, before any client sees it:
 *
 * - an outside image (Markdown or `<img>`) becomes `held-image:` and its percent-encoded URL, which no client
 *   loads; the app shows it as a click-to-load placeholder naming the host;
 * - raw HTML keeps only the tags and attributes the app's renderer allows, so only `<img src>` and `<a href>`
 *   carry a URL, and no inline style can fetch;
 * - every fenced mermaid block, however it is fenced and nested, goes through the mermaid source policy the
 *   app's renderer also runs (`mermaid-source-policy.ts`), and raw HTML can no longer mark an element as a
 *   diagram;
 * - U+E003, the mark the app's renderer deletes before it parses, is removed first, so a mark between two
 *   characters cannot hide what they spell from these checks.
 *
 * What counts as code is decided cautiously: when a renderer might read text as Markdown or HTML, it is made
 * safe here, so a doubtful case changes how code looks rather than what loads. Links stay links: they need a
 * click.
 *
 * `sanitizeAnswerMarkdown` takes the whole text. `sanitizeAnswerDelta` takes it in pieces, as a stream
 * arrives, and holds back only what it cannot judge yet; what it sends adds up to exactly what
 * `sanitizeAnswerMarkdown` gives for the whole text once `flushAnswerDelta` sends the rest.
 */
import { neutralizeMermaidSource } from './mermaid-source-policy';

export const HELD_IMAGE_SCHEME = 'held-image';

const INLINE_MARK = /\uE003/g;
/** Text with none of these leaves unchanged. */
const NEEDS_A_PASS = /[<\r]|!\[|```|~~~/;

interface Fence {
  readonly char: string;
  readonly length: number;
  /** Column of the fence after any quote markers: a less indented line may have left its list item. */
  readonly indent: number;
  readonly quotes: number;
  readonly mermaid: boolean;
  /** A mermaid block is held whole, from its opening line, until it closes. */
  readonly open: string;
  readonly lines: ReadonlyArray<{
    readonly text: string;
    readonly ending: string;
  }>;
}

interface HtmlBlock {
  /** The line that ends it; none means the next blank line does. */
  readonly end: RegExp | null;
}

interface Machine {
  readonly fence: Fence | null;
  readonly html: HtmlBlock | null;
}

export interface AnswerDeltaState {
  /** Text received after the last line break. */
  readonly pending: string;
  /** How much of that line's output has been sent. */
  readonly sent: number;
  readonly fence: Fence | null;
  readonly html: HtmlBlock | null;
}

const START: AnswerDeltaState = {
  pending: '',
  sent: 0,
  fence: null,
  html: null,
};

const memo = new Map<string, { out: string; bytes: number }>();
/** What the memo may hold, inputs and outputs together, as V8 keeps the strings. */
export const ANSWER_MEMO_BYTES = 16 * 2 ** 20;
const MEMO_ENTRIES = 1000;
const MEMO_MAX_LENGTH = 200_000;
let memoBytes = 0;

/** A UTF-16 code unit past Latin-1; a surrogate pair's halves are past it too. */
const BEYOND_LATIN1 = /[Ā-￿]/;

/** What V8 keeps a string in: a byte a character while every one is Latin-1, two once any is not. */
const stringBytes = (text: string): number =>
  (BEYOND_LATIN1.test(text) ? 2 : 1) * text.length;

/** How much the memo holds now (tests). */
export const answerMemoStats = (): { entries: number; bytes: number } => ({
  entries: memo.size,
  bytes: memoBytes,
});

/** Kept, newest last; the oldest go first once the memo holds more than its budget or its entries. */
const remember = (input: string, out: string): void => {
  const bytes = stringBytes(input) + stringBytes(out);
  if (bytes > ANSWER_MEMO_BYTES) return;
  memo.set(input, { out, bytes });
  memoBytes += bytes;
  while (memoBytes > ANSWER_MEMO_BYTES || memo.size > MEMO_ENTRIES) {
    const oldest = memo.keys().next().value as string;
    memoBytes -= (memo.get(oldest) as { bytes: number }).bytes;
    memo.delete(oldest);
  }
};

const sanitize = (text: string, keep: boolean): string => {
  if (typeof text !== 'string' || text === '') return text;
  const input = text.replace(INLINE_MARK, '');
  if (!NEEDS_A_PASS.test(input)) return input;
  const known = memo.get(input);
  if (known !== undefined) {
    memo.delete(input);
    memo.set(input, known);
    return known.out;
  }
  const first = sanitizeAnswerDelta(undefined, input);
  const out = first.out + flushAnswerDelta(first.state).out;
  if (keep && input.length <= MEMO_MAX_LENGTH) remember(input, out);
  return out;
};

/** The whole answer text, made safe to send. Anything that is not a string is returned as it is. */
export function sanitizeAnswerMarkdown(text: string): string {
  return sanitize(text, true);
}

/**
 * The same for a text that is only part of an answer (a live answer so far): it is never asked for again, so
 * nothing of it is kept.
 */
export function sanitizePartialAnswerMarkdown(text: string): string {
  return sanitize(text, false);
}

/** The next piece of a streamed answer: what may be sent now, and the state to pass with the next piece. */
export function sanitizeAnswerDelta(
  state: AnswerDeltaState | undefined,
  delta: string,
): { state: AnswerDeltaState; out: string } {
  const from = state ?? START;
  const added = typeof delta === 'string' ? delta.replace(INLINE_MARK, '') : '';
  return advance(from, from.pending + added, false);
}

/** The end of a streamed answer: everything still held back, and a state ready for a new answer. */
export function flushAnswerDelta(state: AnswerDeltaState | undefined): {
  state: AnswerDeltaState;
  out: string;
} {
  const from = state ?? START;
  return advance(from, from.pending, true);
}

function advance(
  state: AnswerDeltaState,
  text: string,
  final: boolean,
): { state: AnswerDeltaState; out: string } {
  let machine: Machine = { fence: state.fence, html: state.html };
  let sent = state.sent;
  let rest = text;
  let out = '';
  for (let next = nextLine(rest, final); next; next = nextLine(rest, final)) {
    const done = completeLine(machine, next.line, '\n');
    out += done.text.slice(sent);
    sent = 0;
    machine = done.machine;
    rest = next.rest;
  }
  if (final) {
    if (rest !== '') {
      const done = completeLine(machine, rest, '');
      out += done.text.slice(sent);
      machine = done.machine;
    }
    if (machine.fence?.mermaid) out += mermaidBlock(machine.fence);
    return { state: START, out };
  }
  // A trailing `\r` is the line's end, not its text.
  const preview = partialLine(
    machine,
    rest.endsWith('\r') ? rest.slice(0, -1) : rest,
  );
  if (preview.length > sent) {
    out += preview.slice(sent);
    sent = preview.length;
  }
  return {
    state: { pending: rest, sent, fence: machine.fence, html: machine.html },
    out,
  };
}

/** `\r\n`, `\r` and `\n` all end a line; a `\r` at the very end waits to see whether `\n` follows. */
function nextLine(
  text: string,
  final: boolean,
): { line: string; rest: string } | null {
  const at = text.search(/[\r\n]/);
  if (at === -1) return null;
  if (text[at] === '\r') {
    if (at + 1 === text.length && !final) return null;
    return {
      line: text.slice(0, at),
      rest: text.slice(at + (text[at + 1] === '\n' ? 2 : 1)),
    };
  }
  return { line: text.slice(0, at), rest: text.slice(at + 1) };
}

// ─── Blocks ───────────────────────────────────────────────────────────────────

interface Container {
  readonly quotes: number;
  /** Width of list markers and spaces after the last quote marker. */
  readonly indent: number;
  readonly rest: string;
}

/** Quote markers, list markers and indentation, read generously: a renderer may nest more than it shows. */
function splitContainer(line: string): Container {
  let quotes = 0;
  let indent = 0;
  let rest = line;
  for (;;) {
    const quote = /^[ \t]*>[ \t]?/.exec(rest);
    if (quote) {
      quotes += 1;
      indent = 0;
      rest = rest.slice(quote[0].length);
      continue;
    }
    const marker = /^[ \t]*(?:[-*+]|\d{1,9}[.)])(?=[ \t]|$)[ \t]*/.exec(rest);
    if (marker) {
      indent += marker[0].length;
      rest = rest.slice(marker[0].length);
      continue;
    }
    const space = /^[ \t]+/.exec(rest);
    if (space) {
      indent += space[0].length;
      rest = rest.slice(space[0].length);
      continue;
    }
    return { quotes, indent, rest };
  }
}

function fenceOpener(line: string): Omit<Fence, 'open' | 'lines'> | null {
  const container = splitContainer(line);
  const fence = /^(`{3,}|~{3,})(.*)$/.exec(container.rest);
  if (!fence) return null;
  const marks = fence[1] ?? '';
  const info = fence[2] ?? '';
  if (marks.startsWith('`') && info.includes('`')) return null;
  return {
    char: marks.charAt(0),
    length: marks.length,
    indent: container.indent,
    quotes: container.quotes,
    // The app draws any block whose language starts with `mermaid`; any mention is taken as one here.
    mermaid: /mermaid/i.test(info),
  };
}

/** A closing fence, at any indent: closing early only makes more text count as Markdown. */
function closesFence(fence: Fence, line: string): boolean {
  const container = splitContainer(line);
  if (container.quotes !== fence.quotes) return false;
  const marks = /^(`{3,}|~{3,})[ \t]*$/.exec(container.rest)?.[1] ?? '';
  return (
    marks !== '' &&
    marks.charAt(0) === fence.char &&
    marks.length >= fence.length
  );
}

/** A line that ends the quote or list item holding the fence ends the fence with it. */
function leavesFence(fence: Fence, line: string): boolean {
  const container = splitContainer(line);
  if (container.quotes < fence.quotes) return true;
  return (
    fence.indent > 0 && container.rest !== '' && container.indent < fence.indent
  );
}

const HTML_BLOCK_ENDS: ReadonlyArray<[RegExp, RegExp]> = [
  [
    /^<(?:script|pre|style|textarea)(?:[\s>]|$)/i,
    /<\/(?:script|pre|style|textarea)>/i,
  ],
  [/^<!--/, /-->/],
  [/^<\?/, /\?>/],
  [/^<![A-Za-z]/, />/],
  [/^<!\[CDATA\[/, /\]\]>/],
];

/**
 * Whether a line is inside raw HTML, where Markdown code spans and fences do not apply, and the state after it.
 * Any line that opens with a tag starts a block, which is more than CommonMark counts.
 */
function htmlStateFor(
  open: HtmlBlock | null,
  line: string,
): { inBlock: boolean; next: HtmlBlock | null } {
  if (open) {
    if (open.end === null)
      return line.trim() === ''
        ? { inBlock: false, next: null }
        : { inBlock: true, next: open };
    return { inBlock: true, next: open.end.test(line) ? null : open };
  }
  const content = splitContainer(line).rest;
  if (!/^<[A-Za-z/!?]/.test(content)) return { inBlock: false, next: null };
  const end =
    HTML_BLOCK_ENDS.find(([start]) => start.test(content))?.[1] ?? null;
  if (end?.test(content.slice(2)) === true) {
    return { inBlock: true, next: null };
  }
  return { inBlock: true, next: { end } };
}

function completeLine(
  machine: Machine,
  line: string,
  ending: string,
): { text: string; machine: Machine } {
  const { fence } = machine;
  if (fence) {
    if (closesFence(fence, line)) {
      const block = fence.mermaid ? mermaidBlock(fence) : '';
      return {
        text: block + line + ending,
        machine: { fence: null, html: null },
      };
    }
    if (leavesFence(fence, line)) {
      const block = fence.mermaid ? mermaidBlock(fence) : '';
      const after = outsideLine({ fence: null, html: null }, line, ending);
      return { text: block + after.text, machine: after.machine };
    }
    if (fence.mermaid) {
      return {
        text: '',
        machine: {
          fence: { ...fence, lines: [...fence.lines, { text: line, ending }] },
          html: null,
        },
      };
    }
    return { text: line + ending, machine };
  }
  return outsideLine(machine, line, ending);
}

function outsideLine(
  machine: Machine,
  line: string,
  ending: string,
): { text: string; machine: Machine } {
  const html = htmlStateFor(machine.html, line);
  if (!html.inBlock) {
    const opener = fenceOpener(line);
    if (opener) {
      const open = line + ending;
      return {
        text: opener.mermaid ? '' : open,
        machine: { fence: { ...opener, open, lines: [] }, html: null },
      };
    }
  }
  return {
    text: inlineLine(line, !html.inBlock, true) + ending,
    machine: { fence: null, html: html.next },
  };
}

/** What of a line still being written can be sent: nothing in a fence, or where it may open one or raw HTML. */
function partialLine(machine: Machine, line: string): string {
  if (machine.fence || line === '') return '';
  const content = splitContainer(line).rest;
  if (content === '' || /^[`~<]/.test(content)) return '';
  return inlineLine(line, machine.html === null, false);
}

function mermaidBlock(fence: Fence): string {
  const strip = (text: string): string => {
    let rest = text;
    for (let q = 0; q < fence.quotes; q += 1)
      rest = rest.replace(/^[ \t]*>[ \t]?/, '');
    return rest.replace(new RegExp(`^[ \\t]{0,${String(fence.indent)}}`), '');
  };
  const source = fence.lines.map((l) => strip(l.text)).join('\n');
  const safe = neutralizeMermaidSource(source);
  if (safe === source)
    return fence.open + fence.lines.map((l) => l.text + l.ending).join('');
  const prefix = '> '.repeat(fence.quotes) + ' '.repeat(fence.indent);
  const lastEnding = fence.lines[fence.lines.length - 1]?.ending ?? '';
  const lines = safe.split('\n');
  return (
    fence.open +
    lines
      .map((l, i) => prefix + l + (i < lines.length - 1 ? '\n' : lastEnding))
      .join('')
  );
}

// ─── Inline ───────────────────────────────────────────────────────────────────

const UNSETTLED = Symbol('unsettled');

/**
 * One line, left to right. With `final` false the line is still being written, and the scan stops at the
 * first construct it cannot judge yet, so what it returns is a prefix of the line's final output.
 */
function inlineLine(line: string, codeSpans: boolean, final: boolean): string {
  let out = '';
  let i = 0;
  while (i < line.length) {
    const c = line.charAt(i);
    if (c === '\\') {
      if (i + 1 >= line.length) {
        if (!final) return out;
        out += c;
        i += 1;
      } else if (line[i + 1] === '<') {
        // `\<` hides a tag from Markdown, but not from an HTML block: judged as a tag either way.
        out += c;
        i += 1;
      } else {
        out += line.slice(i, i + 2);
        i += 2;
      }
      continue;
    }
    if (c === '`' && codeSpans) {
      const run = /^`+/.exec(line.slice(i))?.[0] ?? '`';
      const close = codeSpanEnd(line, i + run.length, run.length, final);
      if (close === UNSETTLED) return out;
      const end = close ?? i + run.length;
      out += line.slice(i, end);
      i = end;
      continue;
    }
    if (c === '<') {
      const done = angle(line, i, final);
      if (done === UNSETTLED) return out;
      out += done.text;
      i = done.end;
      continue;
    }
    if (c === '!' && (line[i + 1] === '[' || i + 1 >= line.length)) {
      if (i + 1 >= line.length) {
        if (!final) return out;
        out += c;
        i += 1;
        continue;
      }
      const done = image(line, i, final);
      if (done === UNSETTLED) return out;
      out += done.text;
      i = done.end;
      continue;
    }
    out += c;
    i += 1;
  }
  return out;
}

/** The end of a code span opened by `width` backticks, null when the line has none, or unsettled. */
function codeSpanEnd(
  line: string,
  from: number,
  width: number,
  final: boolean,
): number | null | typeof UNSETTLED {
  for (let i = line.indexOf('`', from); i !== -1; i = line.indexOf('`', i)) {
    let end = i;
    while (line[end] === '`') end += 1;
    if (end >= line.length && !final) return UNSETTLED;
    if (end - i === width) return end;
    i = end;
  }
  return final ? null : UNSETTLED;
}

type Piece = { text: string; end: number };

const AUTOLINK = /^<[A-Za-z][A-Za-z0-9+.-]{1,31}:[^\s<>]*>/;
const EMAIL_AUTOLINK =
  /^<[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*>/;

function angle(
  line: string,
  at: number,
  final: boolean,
): Piece | typeof UNSETTLED {
  const ahead = line.slice(at);
  const link = AUTOLINK.exec(ahead) ?? EMAIL_AUTOLINK.exec(ahead);
  if (link) return { text: link[0], end: at + link[0].length };
  const next = line[at + 1];
  if (next === undefined) return final ? { text: '<', end: at + 1 } : UNSETTLED;
  const opensTag =
    /[A-Za-z]/.test(next) ||
    (next === '/' && /[A-Za-z]/.test(line[at + 2] ?? ''));
  if (next === '/' && line[at + 2] === undefined && !final) return UNSETTLED;
  if (!opensTag) return { text: '<', end: at + 1 };
  const tag = readTag(line, at);
  if (!tag) return final ? { text: '&lt;', end: at + 1 } : UNSETTLED;
  return { text: writeTag(tag, line.slice(at, tag.end)), end: tag.end };
}

interface Tag {
  name: string;
  closing: boolean;
  attributes: Array<[string, string | null]>;
  selfClosing: boolean;
  end: number;
}

/** A start or end tag read as a browser's HTML tokenizer reads it, ending on this line or not at all. */
function readTag(line: string, start: number): Tag | null {
  let i = start + 1;
  const closing = line[i] === '/';
  if (closing) i += 1;
  const nameStart = i;
  while (i < line.length && !/[\t\f />]/.test(line.charAt(i))) i += 1;
  const name = line.slice(nameStart, i).toLowerCase();
  const attributes: Array<[string, string | null]> = [];
  for (;;) {
    while (i < line.length && /[\t\f ]/.test(line.charAt(i))) i += 1;
    if (i >= line.length) return null;
    if (line[i] === '>')
      return { name, closing, attributes, selfClosing: false, end: i + 1 };
    if (line[i] === '/') {
      if (line[i + 1] === '>')
        return { name, closing, attributes, selfClosing: true, end: i + 2 };
      i += 1;
      continue;
    }
    const attrStart = i;
    i += 1;
    while (i < line.length && !/[\t\f />=]/.test(line.charAt(i))) i += 1;
    const attribute = line.slice(attrStart, i).toLowerCase();
    while (i < line.length && /[\t\f ]/.test(line.charAt(i))) i += 1;
    if (line[i] !== '=') {
      attributes.push([attribute, null]);
      continue;
    }
    i += 1;
    while (i < line.length && /[\t\f ]/.test(line.charAt(i))) i += 1;
    if (i >= line.length) return null;
    const quote = line[i];
    if (quote === '"' || quote === "'") {
      const close = line.indexOf(quote, i + 1);
      if (close === -1) return null;
      attributes.push([attribute, line.slice(i + 1, close)]);
      i = close + 1;
    } else {
      const valueStart = i;
      while (i < line.length && !/[\t\f >]/.test(line.charAt(i))) i += 1;
      attributes.push([attribute, line.slice(valueStart, i)]);
    }
  }
}

/** The tags the app's renderer keeps (`ANSWER_SANITIZE_SCHEMA` in the frontend); any other tag is dropped. */
const ALLOWED_TAGS = new Set([
  'a',
  'b',
  'blockquote',
  'br',
  'code',
  'dd',
  'del',
  'details',
  'div',
  'dl',
  'dt',
  'em',
  'h1',
  'h2',
  'h3',
  'h4',
  'h5',
  'h6',
  'hr',
  'i',
  'img',
  'input',
  'ins',
  'kbd',
  'li',
  'ol',
  'p',
  'pre',
  'q',
  'rp',
  'rt',
  'ruby',
  's',
  'samp',
  'section',
  'span',
  'strike',
  'strong',
  'sub',
  'summary',
  'sup',
  'table',
  'tbody',
  'td',
  'tfoot',
  'th',
  'thead',
  'tr',
  'tt',
  'ul',
  'var',
]);

/** Attributes that never hold a URL. */
const PLAIN_ATTRIBUTES = new Set([
  'alt',
  'title',
  'id',
  'lang',
  'dir',
  'align',
  'valign',
  'width',
  'height',
  'colspan',
  'rowspan',
  'scope',
  'headers',
  'abbr',
  'axis',
  'border',
  'cellpadding',
  'cellspacing',
  'char',
  'charoff',
  'clear',
  'color',
  'cols',
  'compact',
  'datetime',
  'frame',
  'hspace',
  'label',
  'name',
  'nowrap',
  'open',
  'rel',
  'rev',
  'rows',
  'rules',
  'span',
  'start',
  'summary',
  'tabindex',
  'target',
  'value',
  'vspace',
  'reversed',
  'role',
  'type',
  'aria-label',
  'aria-describedby',
  'aria-labelledby',
  'aria-hidden',
  'data-footnote-ref',
  'data-footnote-backref',
  'data-footnotes',
]);

/** The elements whose inline `style` the app's renderer keeps. */
const STYLED_TAGS = new Set([
  'table',
  'thead',
  'tbody',
  'tfoot',
  'tr',
  'th',
  'td',
  'span',
  'pre',
  'code',
  'img',
  'a',
]);

/** As the app's renderer: any word here can fetch, or hide a word that does. */
const CSS_FETCH = /url|image|cross-fade|element\s*\(|@import|\/\*|\\/i;

const LINK_SCHEMES = new Set([
  'http',
  'https',
  'mailto',
  'irc',
  'ircs',
  'xmpp',
  'tel',
]);

function writeTag(tag: Tag, raw: string): string {
  if (!ALLOWED_TAGS.has(tag.name)) return '';
  if (tag.closing) return /^<\/[a-z0-9]+>$/i.test(raw) ? raw : `</${tag.name}>`;
  if (tag.attributes.length === 0) return raw;
  const seen = new Set<string>();
  let out = `<${tag.name}`;
  for (const [name, value] of tag.attributes) {
    if (seen.has(name)) continue;
    seen.add(name);
    const kept = keptValue(tag.name, name, value);
    if (kept === undefined) continue;
    out += kept === null ? ` ${name}` : ` ${name}="${escapeAttribute(kept)}"`;
  }
  return `${out}${tag.selfClosing ? ' />' : '>'}`;
}

/** The value to write for an attribute, null for a bare one, or undefined to drop it. */
function keptValue(
  tag: string,
  name: string,
  value: string | null,
): string | null | undefined {
  const decoded = value === null ? null : decodeReferences(value);
  if (name === 'src') {
    if (tag !== 'img' || decoded === null) return undefined;
    return imageVerdict(decoded) === 'keep'
      ? decoded
      : heldImageUrl(heldTarget(decoded));
  }
  if (name === 'href')
    return tag === 'a' && decoded !== null && linkAllowed(decoded)
      ? decoded
      : undefined;
  if (name === 'style')
    return STYLED_TAGS.has(tag) && decoded !== null && !CSS_FETCH.test(decoded)
      ? decoded
      : undefined;
  if (name === 'class') {
    // An element marked as a diagram would be drawn by a client that runs mermaid over the page.
    const classes = (decoded ?? '')
      .split(/\s+/)
      .filter((c) => c !== '' && !/mermaid/i.test(c));
    return classes.length > 0 ? classes.join(' ') : undefined;
  }
  if (tag === 'input') {
    if (name === 'type')
      return decoded?.trim().toLowerCase() === 'checkbox'
        ? 'checkbox'
        : undefined;
    return name === 'checked' || name === 'disabled' ? decoded : undefined;
  }
  return PLAIN_ATTRIBUTES.has(name) ? decoded : undefined;
}

const escapeAttribute = (value: string): string =>
  value
    .replace(/&/g, '&amp;')
    .replace(/"/g, '&quot;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');

/** A Markdown image on one line: kept, its destination held, or its `!` escaped when it cannot be read whole. */
function image(
  line: string,
  at: number,
  final: boolean,
): Piece | typeof UNSETTLED {
  const parsed = readImage(line, at);
  if (parsed === UNSETTLED)
    return final ? { text: '\\!', end: at + 1 } : UNSETTLED;
  if (parsed === null) return { text: '\\!', end: at + 1 };
  const destination = decodeReferences(
    unescapeMarkdown(line.slice(parsed.destStart, parsed.destEnd)),
  );
  if (imageVerdict(destination) === 'keep')
    return { text: line.slice(at, parsed.end), end: parsed.end };
  return {
    text:
      line.slice(at, parsed.destStart) +
      heldImageUrl(heldTarget(destination)) +
      line.slice(parsed.destEnd, parsed.end),
    end: parsed.end,
  };
}

/**
 * `![alt](destination "title")` per CommonMark, on one line, with plain alt text (no brackets or code); null
 * when it can never be one, unsettled when the line may still complete it.
 */
function readImage(
  line: string,
  at: number,
):
  | { destStart: number; destEnd: number; end: number }
  | null
  | typeof UNSETTLED {
  let i = at + 2;
  for (;;) {
    if (i >= line.length) return UNSETTLED;
    const c = line[i];
    if (c === '\\') {
      if (i + 1 >= line.length) return UNSETTLED;
      i += 2;
      continue;
    }
    if (c === '[' || c === '`') return null;
    if (c === ']') break;
    i += 1;
  }
  i += 1;
  if (i >= line.length) return UNSETTLED;
  if (line[i] !== '(') return null;
  i = skipBlanks(line, i + 1);
  if (i >= line.length) return UNSETTLED;
  let destStart: number;
  let destEnd: number;
  if (line[i] === '<') {
    destStart = i + 1;
    for (i += 1; ; i += 1) {
      if (i >= line.length) return UNSETTLED;
      if (line[i] === '\\') {
        i += 1;
        continue;
      }
      if (line[i] === '<') return null;
      if (line[i] === '>') break;
    }
    destEnd = i;
    i += 1;
  } else {
    destStart = i;
    let depth = 0;
    for (;;) {
      if (i >= line.length) return UNSETTLED;
      const c = line.charAt(i);
      if (
        c === '\\' &&
        i + 1 < line.length &&
        /[!-/:-@[-`{-~]/.test(line.charAt(i + 1))
      ) {
        i += 2;
        continue;
      }
      if (c === ' ' || c === '\t') break;
      if (c.charCodeAt(0) < 0x20 || c === '\x7f') return null;
      if (c === '(') {
        depth += 1;
        if (depth > 32) return null;
      } else if (c === ')') {
        if (depth === 0) break;
        depth -= 1;
      }
      i += 1;
    }
    destEnd = i;
  }
  const afterDestination = i;
  i = skipBlanks(line, i);
  if (i >= line.length) return UNSETTLED;
  if (line[i] === ')') return { destStart, destEnd, end: i + 1 };
  // A title is set off from its destination by a space.
  if (i === afterDestination) return null;
  const opener = line[i];
  const closer =
    opener === '"' ? '"' : opener === "'" ? "'" : opener === '(' ? ')' : '';
  if (closer === '') return null;
  for (i += 1; ; i += 1) {
    if (i >= line.length) return UNSETTLED;
    if (line[i] === '\\') {
      i += 1;
      continue;
    }
    if (opener === '(' && line[i] === '(') return null;
    if (line[i] === closer) break;
  }
  i = skipBlanks(line, i + 1);
  if (i >= line.length) return UNSETTLED;
  return line[i] === ')' ? { destStart, destEnd, end: i + 1 } : null;
}

const skipBlanks = (line: string, from: number): number => {
  let i = from;
  while (i < line.length && (line[i] === ' ' || line[i] === '\t')) i += 1;
  return i;
};

const unescapeMarkdown = (text: string): string =>
  text.replace(/\\([!-/:-@[-`{-~])/g, '$1');

// ─── URLs ─────────────────────────────────────────────────────────────────────

/** Every named reference a browser turns into an ASCII character; any other one gives a character no URL syntax uses. */
const NAMED_REFERENCES: Record<string, string> = {
  amp: '&',
  lt: '<',
  gt: '>',
  quot: '"',
  apos: "'",
  nbsp: ' ',
  NonBreakingSpace: ' ',
  Tab: '\t',
  NewLine: '\n',
  excl: '!',
  num: '#',
  dollar: '$',
  percnt: '%',
  lpar: '(',
  rpar: ')',
  ast: '*',
  midast: '*',
  plus: '+',
  comma: ',',
  period: '.',
  sol: '/',
  colon: ':',
  semi: ';',
  equals: '=',
  quest: '?',
  commat: '@',
  lsqb: '[',
  lbrack: '[',
  bsol: '\\',
  rsqb: ']',
  rbrack: ']',
  Hat: '^',
  lowbar: '_',
  UnderBar: '_',
  grave: '`',
  DiacriticalGrave: '`',
  lcub: '{',
  lbrace: '{',
  verbar: '|',
  vert: '|',
  VerticalLine: '|',
  rcub: '}',
  rbrace: '}',
  AMP: '&',
  LT: '<',
  GT: '>',
  QUOT: '"',
};
const LEGACY_REFERENCES = new Set([
  'amp',
  'lt',
  'gt',
  'quot',
  'nbsp',
  'AMP',
  'LT',
  'GT',
  'QUOT',
]);

function decodeReferences(text: string): string {
  return text.replace(
    /&(?:#[xX]([0-9a-fA-F]+)|#([0-9]+)|([A-Za-z][A-Za-z0-9]{1,31}))(;?)/g,
    (whole, hex, dec, name, semi) => {
      if (hex !== undefined || dec !== undefined) {
        const digits = String(hex ?? dec).replace(/^0+(?=.)/, '');
        const code =
          digits.length > 8
            ? Infinity
            : parseInt(digits, hex !== undefined ? 16 : 10);
        return code > 0 && code <= 0x10ffff && (code < 0xd800 || code > 0xdfff)
          ? String.fromCodePoint(code)
          : '�';
      }
      const value = NAMED_REFERENCES[name as string];
      return value !== undefined &&
        (semi === ';' || LEGACY_REFERENCES.has(name as string))
        ? value
        : whole;
    },
  );
}

/** As a browser reads a URL attribute: control characters and spaces at either end do not count. */
function withoutEdgeControls(value: string): string {
  let start = 0;
  let end = value.length;
  while (start < end && value.charCodeAt(start) <= 0x20) start += 1;
  while (end > start && value.charCodeAt(end - 1) <= 0x20) end -= 1;
  return value.slice(start, end);
}

const BASES = ['https://app.invalid/a/b', 'http://app.invalid/a/b'];

/**
 * An image may keep its URL when it cannot reach another host from any page: a relative path (the app's
 * renderer then decides which of its own paths load), a `data:` image, a `blob:` URL, or a held image.
 */
function imageVerdict(url: string): 'keep' | 'hold' {
  const value = withoutEdgeControls(url.replace(/[\t\n\r]/g, ''));
  if (value === '') return 'keep';
  if (
    /^held-image:/i.test(value) ||
    /^data:image\//i.test(value) ||
    /^blob:/i.test(value)
  )
    return 'keep';
  if (/^[A-Za-z][A-Za-z0-9+.-]*:/.test(value) || /^[\\/]{2}/.test(value))
    return 'hold';
  for (const base of BASES) {
    try {
      if (new URL(value, base).origin !== new URL(base).origin) return 'hold';
    } catch {
      return 'hold';
    }
  }
  return 'keep';
}

/** The absolute URL a held image stands for, so the app can load it on a click. */
function heldTarget(url: string): string {
  const value = url.replace(/[\t\n\r]/g, '').trim();
  try {
    if (/^[\\/]{2}/.test(value))
      return new URL(`https://${value.replace(/^[\\/]+/, '')}`).href;
    if (/^https?:/i.test(value)) return new URL(value).href;
  } catch {
    return value;
  }
  return value;
}

/** `held-image:` and the percent-encoded URL: no client loads it, and it is safe in Markdown and in an attribute. */
export function heldImageUrl(url: string): string {
  const encoded = encodeURIComponent(url).replace(
    /[!'()*~]/g,
    (c) => `%${c.charCodeAt(0).toString(16).toUpperCase()}`,
  );
  return `${HELD_IMAGE_SCHEME}:${encoded}`;
}

function linkAllowed(url: string): boolean {
  const value = withoutEdgeControls(url.replace(/[\t\n\r]/g, ''));
  const scheme = /^([A-Za-z][A-Za-z0-9+.-]*):/.exec(value);
  return scheme === null || LINK_SCHEMES.has((scheme[1] ?? '').toLowerCase());
}

// ─── Stored rows ──────────────────────────────────────────────────────────────

const ANSWER_TYPES = new Set(['bot_response', 'error', 'shared_answer']);

/**
 * A stored message as it may be sent: an answer's text and the text of its steps made safe, and so is a
 * reply's quote of an answer. Questions and notes are the people's own words and stay as written. Returns
 * the same object when nothing changes.
 */
export function sanitizeAnswerMessage<T>(message: T): T {
  if (message === null || typeof message !== 'object') return message;
  const row = message as Record<string, unknown>;
  const changes: Record<string, unknown> = {};
  if (ANSWER_TYPES.has(String(row.messageType))) {
    if (typeof row.content === 'string') {
      const content = sanitizeAnswerMarkdown(row.content);
      if (content !== row.content) changes.content = content;
    }
    if (Array.isArray(row.parts)) {
      const parts = sanitizeParts(row.parts);
      if (parts !== row.parts) changes.parts = parts;
    }
  }
  const reply = row.replyTo;
  if (reply !== null && typeof reply === 'object') {
    const quote = reply as Record<string, unknown>;
    if (typeof quote.excerpt === 'string') {
      const excerpt = sanitizeAnswerMarkdown(quote.excerpt);
      if (excerpt !== quote.excerpt) changes.replyTo = { ...quote, excerpt };
    }
  }
  if (Object.keys(changes).length === 0) return message;
  const copy: Record<string, unknown> = { ...row, ...changes };
  return copy as T;
}

export const sanitizeAnswerMessages = <T>(messages: readonly T[]): T[] =>
  messages.map(sanitizeAnswerMessage);

function sanitizeParts(parts: unknown[]): unknown[] {
  const out: unknown[] = [];
  let changed = false;
  for (const part of parts) {
    const safe = sanitizePart(part);
    if (safe !== part) changed = true;
    out.push(safe);
  }
  return changed ? out : parts;
}

function sanitizePart(part: unknown): unknown {
  if (part === null || typeof part !== 'object') return part;
  const p = part as Record<string, unknown>;
  if (p.type === 'text' && typeof p.content === 'string') {
    const content = sanitizeAnswerMarkdown(p.content);
    return content === p.content ? part : { ...p, content };
  }
  if (p.type === 'sub_agent' && Array.isArray(p.parts)) {
    const inner = sanitizeParts(p.parts);
    return inner === p.parts ? part : { ...p, parts: inner };
  }
  return part;
}
