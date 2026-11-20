/**
 * Mermaid source with every construct that makes a drawing load something by itself taken out.
 *
 * A diagram in an answer is model output, and mermaid fetches on draw, with no click and at any security
 * level: an `img` in a node's `@{ ... }` data, an actor's `icon` (sequence `properties`, or `details`
 * read from the page), an `<img>`, `<picture>` or inline `style` in a label, and theme CSS set by a
 * directive or front matter. Those values are decided after mermaid has parsed them (YAML escapes, JSON
 * escapes, character references), so a pattern over the raw text cannot judge them: node data is parsed
 * here and written back in one plain form, so mermaid reads exactly what was judged.
 *
 * The app's renderer and the server both run this file, and the two copies must stay byte for byte the
 * same (the server's tests compare them): `frontend/lib/utils/mermaid-source-policy.ts` and
 * `backend/nodejs/apps/src/modules/enterprise_search/utils/mermaid-source-policy.ts`. It imports nothing.
 */

type Scalar = string | number | boolean | null;

/** Words that, in a directive or front matter, can reach CSS, label HTML or the sanitiser settings. */
const RISKY_CONFIG =
  /css|font|url|import|image|purify|html|secur|saniti|script|expression|\\|<|>|@|\/\//i;

/** Any of these in CSS can fetch, or can hide a word that does (a comment or an escape). */
const CSS_FETCH = /url|image|cross-fade|element\s*\(|@import|\/\*|\\/i;

/** Formatting tags a label may keep; they take no attributes, so they cannot load anything. */
const KEPT_TAG =
  /^<(?:br\s*\/?|\/?(?:b|i|u|em|strong|sub|sup|small|s|del|ins|mark|code|p|span|div))\s*>/i;

/** A class or state annotation such as `<<interface>>`, which is diagram syntax, not HTML. */
const ANNOTATION = /^<<[A-Za-z][\w -]{0,63}>>/;

/** Statements that load or link out: dropped whole. */
const DROPPED_STATEMENT =
  /^\s*(?:(?:details|links?)[ \t]+[^/\\+()<>:\n,;\s-]|(?:click|callback)[ \t]+[^\s-])/i;
const PROPERTIES_STATEMENT = /^(\s*properties[ \t]+)([^:\n]+):(.*)$/i;
const STYLE_STATEMENT = /^\s*(?:style|classDef|linkStyle)\b/i;

/** An icon reference (`fa:user`, `logos:aws`), never a URL. */
const ICON_REFERENCE = /^[\w-]+(?::[\w-]+)?$/;
const DATA_IMAGE = /^data:image\//i;

export function neutralizeMermaidSource(source: string): string {
  let text = source.replace(/\r\n?/g, '\n');
  text = withoutRiskyFrontMatter(text);
  text = withoutRiskyDirectives(text);
  text = withPlainNodeData(text);
  text = text.split('\n').map(withoutRiskyStatements).join('\n');
  return withInertTags(text);
}

/** Front matter as mermaid finds it: `---` first (after spaces), up to the last line that is `---` again. */
function withoutRiskyFrontMatter(text: string): string {
  const open = /^([ \t]*)---[ \t]*\n/.exec(text);
  if (!open) return text;
  const indent = open[1] ?? '';
  const closing = new RegExp(
    `\\n${indent.replace(/\t/g, '\\t')}---[ \\t]*(?:\\n|$)`,
    'g',
  );
  let end = -1;
  for (let m = closing.exec(text); m; m = closing.exec(text)) {
    if (m.index >= open[0].length - 1) end = m.index + m[0].length;
    closing.lastIndex = m.index + 1;
  }
  if (end === -1) return text;
  return RISKY_CONFIG.test(text.slice(0, end)) ? text.slice(end) : text;
}

/** `%%{ ... }%%` runs to its `}%%`, or to the end of the text when there is none. */
function withoutRiskyDirectives(text: string): string {
  let out = '';
  let from = 0;
  for (
    let start = text.indexOf('%%{');
    start !== -1;
    start = text.indexOf('%%{', from)
  ) {
    const close = text.indexOf('}%%', start + 3);
    const end = close === -1 ? text.length : close + 3;
    const directive = text.slice(start, end);
    out +=
      text.slice(from, start) + (RISKY_CONFIG.test(directive) ? '' : directive);
    from = end;
  }
  return out + text.slice(from);
}

/**
 * Every `@{ ... }`, wherever it appears, is read as mermaid's lexer reads node data: up to the first `}`
 * outside double quotes, with a line break inside quotes read as `<br/>`. Data that parses is written back
 * as one flow mapping without `img` (unless it is a `data:` image) and with label HTML made inert. Data
 * that does not parse is dropped whole; an opening `@{` that never closes is dropped on its own.
 */
function withPlainNodeData(text: string): string {
  let out = '';
  let from = 0;
  for (let at = text.indexOf('@{'); at !== -1; at = text.indexOf('@{', from)) {
    out += text.slice(from, at);
    const group = readNodeData(text, at + 2);
    if (!group) {
      from = at + 2;
      continue;
    }
    const parsed = parseNodeData(group.data);
    if (parsed) out += `@{ ${writeNodeData(parsed)} }`;
    from = group.end;
  }
  return out + text.slice(from);
}

function readNodeData(
  text: string,
  start: number,
): { data: string; end: number } | null {
  let data = '';
  let i = start;
  while (i < text.length) {
    const c = text.charAt(i);
    if (c === '}') return { data, end: i + 1 };
    if (c === '^') return null;
    if (c === '"') {
      const close = text.indexOf('"', i + 1);
      if (close === -1) return null;
      data += `"${text.slice(i + 1, close).replace(/\n\s*/g, '<br/>')}"`;
      i = close + 1;
      continue;
    }
    data += c;
    i += 1;
  }
  return null;
}

function parseNodeData(data: string): Map<string, Scalar> | null {
  if (!data.includes('\n')) return parseFlowMapping(`{${data}}`);
  const body = data.trim();
  if (body === '') return new Map();
  return body.startsWith('{')
    ? parseFlowMapping(body)
    : parseBlockMapping(data);
}

/** A flow mapping of scalars, as YAML (JSON schema) reads one; anything else is refused. */
function parseFlowMapping(text: string): Map<string, Scalar> | null {
  const map = new Map<string, Scalar>();
  let i = skipSpace(text, 0);
  if (text[i] !== '{') return null;
  i = skipSpace(text, i + 1);
  if (text[i] === '}')
    return skipSpace(text, i + 1) === text.length ? map : null;
  for (;;) {
    const key = readScalar(text, i, true);
    if (!key) return null;
    i = skipSpace(text, key.end);
    let value: Scalar = null;
    if (text[i] === ':') {
      i = skipSpace(text, i + 1);
      if (text[i] === ',' || text[i] === '}') {
        value = null;
      } else {
        const read = readScalar(text, i, false);
        if (!read) return null;
        value = read.value;
        i = skipSpace(text, read.end);
      }
    }
    const name = String(key.value);
    if (map.has(name)) return null;
    map.set(name, value);
    if (text[i] === ',') {
      i = skipSpace(text, i + 1);
      if (text[i] === '}')
        return skipSpace(text, i + 1) === text.length ? map : null;
      continue;
    }
    if (text[i] === '}')
      return skipSpace(text, i + 1) === text.length ? map : null;
    return null;
  }
}

/** `key: value` lines at one indent; nesting, lists, block scalars, anchors and comments are refused. */
function parseBlockMapping(text: string): Map<string, Scalar> | null {
  const map = new Map<string, Scalar>();
  let indent: string | null = null;
  for (const line of text.split('\n')) {
    if (line.trim() === '') continue;
    const lead = /^[ \t]*/.exec(line)?.[0] ?? '';
    indent ??= lead;
    if (lead !== indent) return null;
    const body = line.slice(lead.length);
    const key = readScalar(body, 0, true);
    if (!key) return null;
    let i = key.end;
    if (body[i] !== ':') return null;
    i = skipSpace(body, i + 1);
    let value: Scalar = null;
    if (i < body.length) {
      const read = readScalar(body, i, false, true);
      if (!read || skipSpace(body, read.end) !== body.length) return null;
      value = read.value;
    }
    const name = String(key.value);
    if (map.has(name)) return null;
    map.set(name, value);
  }
  return map;
}

function skipSpace(text: string, i: number): number {
  while (
    i < text.length &&
    (text[i] === ' ' || text[i] === '\t' || text[i] === '\n')
  )
    i += 1;
  return i;
}

function readScalar(
  text: string,
  i: number,
  isKey: boolean,
  block = false,
): { value: Scalar; end: number } | null {
  const c = text.charAt(i);
  if (c === '"') return readDoubleQuoted(text, i);
  if (c === "'") return readSingleQuoted(text, i);
  if (
    c === '' ||
    ('[]{},&*!|>%@`#?-'.includes(c) && !/^-[\d.]/.test(text.slice(i, i + 2)))
  )
    return null;
  let end = i;
  while (end < text.length) {
    const ch = text[end];
    if (ch === '\n') break;
    if (
      !block &&
      (ch === ',' || ch === '}' || ch === '{' || ch === '[' || ch === ']')
    )
      break;
    if (ch === ':' && (isKey || block) && /[\s,}]/.test(text[end + 1] ?? ' '))
      break;
    if (ch === '#' && /\s/.test(text[end - 1] ?? '')) return null;
    end += 1;
  }
  const raw = text.slice(i, end).trim();
  if (raw === '') return null;
  return { value: isKey ? raw : plainScalar(raw), end };
}

/** JSON-schema typing of a plain scalar: `null`, `true`, `false` and JSON numbers; the rest is text. */
function plainScalar(raw: string): Scalar {
  if (raw === 'null') return null;
  if (raw === 'true') return true;
  if (raw === 'false') return false;
  if (/^-?(?:0|[1-9]\d*)(?:\.\d*)?(?:[eE][-+]?\d+)?$/.test(raw))
    return Number(raw);
  return raw;
}

const ESCAPES: Record<string, string> = {
  '0': '\0',
  a: '\x07',
  b: '\b',
  t: '\t',
  '\t': '\t',
  n: '\n',
  v: '\v',
  f: '\f',
  r: '\r',
  e: '\x1b',
  ' ': ' ',
  '"': '"',
  '/': '/',
  '\\': '\\',
  N: '\u0085',
  _: ' ',
  L: ' ',
  P: ' ',
};

function readDoubleQuoted(
  text: string,
  start: number,
): { value: string; end: number } | null {
  let value = '';
  for (let i = start + 1; i < text.length; i += 1) {
    const c = text.charAt(i);
    if (c === '"') return { value, end: i + 1 };
    if (c === '\n') return null;
    if (c !== '\\') {
      value += c;
      continue;
    }
    const e = text.charAt(i + 1);
    const escaped = ESCAPES[e];
    if (escaped !== undefined) {
      value += escaped;
      i += 1;
      continue;
    }
    const width = e === 'x' ? 2 : e === 'u' ? 4 : e === 'U' ? 8 : 0;
    const hex = text.slice(i + 2, i + 2 + width);
    if (width === 0 || !new RegExp(`^[0-9A-Fa-f]{${String(width)}}$`).test(hex))
      return null;
    const code = parseInt(hex, 16);
    if (code > 0x10ffff) return null;
    value += String.fromCodePoint(code);
    i += 1 + width;
  }
  return null;
}

function readSingleQuoted(
  text: string,
  start: number,
): { value: string; end: number } | null {
  let value = '';
  for (let i = start + 1; i < text.length; i += 1) {
    const c = text.charAt(i);
    if (c === '\n') return null;
    if (c !== "'") {
      value += c;
      continue;
    }
    if (text[i + 1] === "'") {
      value += "'";
      i += 1;
      continue;
    }
    return { value, end: i + 1 };
  }
  return null;
}

function writeNodeData(data: Map<string, Scalar>): string {
  const pairs: string[] = [];
  for (const [key, value] of data) {
    const name = key.toLowerCase();
    if (
      name === 'img' &&
      !(typeof value === 'string' && DATA_IMAGE.test(value.trim()))
    )
      continue;
    if (
      name === 'icon' &&
      !(typeof value === 'string' && ICON_REFERENCE.test(value.trim()))
    )
      continue;
    if (/^(?:image|src|srcset|href|url|link|style|css)$/.test(name)) continue;
    const shown = typeof value === 'string' ? withInertTags(value) : value;
    pairs.push(
      `${writeKey(key)}: ${typeof shown === 'string' ? quote(shown) : String(shown)}`,
    );
  }
  return pairs.join(', ');
}

function writeKey(key: string): string {
  return /^[A-Za-z_][\w-]*$/.test(key) ? key : quote(key);
}

/** A YAML double-quoted string mermaid's lexer keeps whole: no raw quote, brace, `@` or line break. */
function quote(value: string): string {
  let out = '"';
  for (const ch of value) {
    const code = ch.codePointAt(0) ?? 0;
    if (ch === '\\') out += '\\\\';
    else if (
      ch === '"' ||
      ch === '@' ||
      ch === '{' ||
      ch === '}' ||
      ch === '^' ||
      ch === '`' ||
      code < 0x20 ||
      code === 0x7f
    ) {
      out += `\\x${code.toString(16).padStart(2, '0')}`;
    } else if (code === 0x2028 || code === 0x2029 || code === 0x85)
      out += `\\u${code.toString(16).padStart(4, '0')}`;
    else out += ch;
  }
  return `${out}"`;
}

/** One line, split on `;` outside quotes, with the statements that load or link out dropped. */
function withoutRiskyStatements(line: string): string {
  const parts: string[] = [];
  let quoted = false;
  let from = 0;
  for (let i = 0; i < line.length; i += 1) {
    if (line[i] === '"') quoted = !quoted;
    else if (line[i] === ';' && !quoted) {
      parts.push(line.slice(from, i));
      from = i + 1;
    }
  }
  parts.push(line.slice(from));
  return parts
    .map(statementOf)
    .filter((part) => part !== null)
    .join(';');
}

function statementOf(part: string): string | null {
  if (DROPPED_STATEMENT.test(part)) return null;
  const properties = PROPERTIES_STATEMENT.exec(part);
  if (properties) {
    const [, keyword = '', actor = '', rest = ''] = properties;
    const json = (rest.split(/[#;]/, 1)[0] ?? '').trim();
    let cls: unknown;
    try {
      const parsed: unknown = JSON.parse(json);
      cls =
        parsed !== null && typeof parsed === 'object'
          ? (parsed as Record<string, unknown>).class
          : undefined;
    } catch {
      return null;
    }
    return typeof cls === 'string' && /^[\w-]+$/.test(cls)
      ? `${keyword}${actor}: {"class": "${cls}"}`
      : null;
  }
  if (STYLE_STATEMENT.test(part) && CSS_FETCH.test(part)) return null;
  return part;
}

/** Every `<` that would open a tag mermaid draws as HTML, other than the kept formatting tags, is written as mermaid's `#lt;`. */
function withInertTags(text: string): string {
  let out = '';
  for (let i = 0; i < text.length; i += 1) {
    if (text[i] !== '<') {
      out += text.charAt(i);
      continue;
    }
    const ahead = text.slice(i, i + 72);
    const kept = (text[i + 1] === '<' ? ANNOTATION : KEPT_TAG).exec(ahead);
    if (kept) {
      out += kept[0];
      i += kept[0].length - 1;
    } else if (/^<[A-Za-z/!?]/.test(ahead)) {
      out += '#lt;';
    } else {
      out += '<';
    }
  }
  return out;
}
