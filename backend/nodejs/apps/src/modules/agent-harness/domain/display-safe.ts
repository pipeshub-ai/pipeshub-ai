/**
 * Untrusted text as a decision surface shows it (SEC-B-04, ADR-TEXT-01). The approval card's every string may be
 * the injected model's: a recipient, a channel, a URL, a tool's display name, a message. Characters a reader cannot
 * see or that reorder what follows become a visible marker (`⟨U+202E⟩`); a host that mixes scripts, is a
 * whole-script lookalike or holds a letter that passes for an ASCII one is shown in punycode, any other host in
 * Unicode; in an identifier, a word that mixes scripts has its non-ASCII letters marked.
 *
 * The tables are `contracts/harness/vectors/display-safe.json`'s, as Python's `harness_core.security.display_safe`
 * carries them. Node (`src/modules/agent-harness/domain/display-safe.ts`) and the web client
 * (`frontend/lib/utils/display-safe.ts`) hold the same code, each formatted by its own tree, and each test suite
 * replays the vector and compares these tables with it. Normal text passes unchanged and
 * the result is stable when applied again: the worker, Node and the client each apply it. ES5-safe: the client's
 * target rejects the `u` flag on a regex literal, so those are built at runtime.
 */

import { trimRun } from './trim-run';

export type DisplayMode = 'identifier' | 'prose';

export const MARKER_OPEN = '⟨U+';
export const MARKER_CLOSE = '⟩';

type Ranges = ReadonlyArray<readonly [number, number]>;

export const INVISIBLE_RANGES: Ranges = [
  [0x00ad, 0x00ad],
  [0x034f, 0x034f],
  [0x061c, 0x061c],
  [0x115f, 0x1160],
  [0x17b4, 0x17b5],
  [0x180b, 0x180f],
  [0x200b, 0x200f],
  [0x2028, 0x202e],
  [0x2060, 0x206f],
  [0x3164, 0x3164],
  [0xfe00, 0xfe0f],
  [0xfeff, 0xfeff],
  [0xffa0, 0xffa0],
  [0xfff9, 0xfffb],
  [0xe0000, 0xe007f],
  [0xe0100, 0xe01ef],
];
export const PROSE_CONTROLS: readonly number[] = [0x09, 0x0a, 0x0d];
export const PROSE_JOINERS: readonly number[] = [0x200c, 0x200d];
export const PROSE_VARIATION_SELECTORS: readonly number[] = [0xfe0e, 0xfe0f];

export const SCRIPTS: ReadonlyArray<{
  readonly name: string;
  readonly ranges: Ranges;
}> = [
  {
    name: 'Latin',
    ranges: [
      [0x41, 0x5a],
      [0x61, 0x7a],
      [0xaa, 0xaa],
      [0xba, 0xba],
      [0xc0, 0xd6],
      [0xd8, 0xf6],
      [0xf8, 0x2af],
      [0x1d00, 0x1d7f],
      [0x1e00, 0x1eff],
      [0x2c60, 0x2c7f],
      [0xa720, 0xa7ff],
      [0xab30, 0xab6f],
    ],
  },
  {
    name: 'Fullwidth',
    ranges: [
      [0xff21, 0xff3a],
      [0xff41, 0xff5a],
    ],
  },
  {
    name: 'Greek',
    ranges: [
      [0x370, 0x3ff],
      [0x1f00, 0x1fff],
    ],
  },
  {
    name: 'Cyrillic',
    ranges: [
      [0x400, 0x52f],
      [0x1c80, 0x1c8f],
      [0x2de0, 0x2dff],
      [0xa640, 0xa69f],
    ],
  },
  { name: 'Armenian', ranges: [[0x530, 0x58f]] },
  { name: 'Hebrew', ranges: [[0x590, 0x5ff]] },
  {
    name: 'Arabic',
    ranges: [
      [0x600, 0x6ff],
      [0x750, 0x77f],
      [0x8a0, 0x8ff],
      [0xfb50, 0xfdff],
      [0xfe70, 0xfefe],
    ],
  },
  { name: 'Devanagari', ranges: [[0x900, 0x97f]] },
  { name: 'Bengali', ranges: [[0x980, 0x9ff]] },
  { name: 'Gurmukhi', ranges: [[0xa00, 0xa7f]] },
  { name: 'Gujarati', ranges: [[0xa80, 0xaff]] },
  { name: 'Tamil', ranges: [[0xb80, 0xbff]] },
  { name: 'Telugu', ranges: [[0xc00, 0xc7f]] },
  { name: 'Kannada', ranges: [[0xc80, 0xcff]] },
  { name: 'Malayalam', ranges: [[0xd00, 0xd7f]] },
  { name: 'Thai', ranges: [[0xe00, 0xe7f]] },
  { name: 'Lao', ranges: [[0xe80, 0xeff]] },
  { name: 'Georgian', ranges: [[0x10a0, 0x10ff]] },
  {
    name: 'Cherokee',
    ranges: [
      [0x13a0, 0x13ff],
      [0xab70, 0xabbf],
    ],
  },
  {
    name: 'Hangul',
    ranges: [
      [0x1100, 0x11ff],
      [0x3130, 0x318f],
      [0xac00, 0xd7af],
    ],
  },
  { name: 'Hiragana', ranges: [[0x3040, 0x309f]] },
  {
    name: 'Katakana',
    ranges: [
      [0x30a0, 0x30ff],
      [0x31f0, 0x31ff],
      [0xff66, 0xff9f],
    ],
  },
  {
    name: 'Bopomofo',
    ranges: [
      [0x3100, 0x312f],
      [0x31a0, 0x31bf],
    ],
  },
  {
    name: 'Han',
    ranges: [
      [0x2e80, 0x2fdf],
      [0x3005, 0x3005],
      [0x3007, 0x3007],
      [0x3021, 0x3029],
      [0x3038, 0x303b],
      [0x3400, 0x4dbf],
      [0x4e00, 0x9fff],
      [0xf900, 0xfaff],
      [0x20000, 0x2fa1f],
    ],
  },
];
export const ALLOWED_MIXES: ReadonlyArray<readonly string[]> = [
  ['Latin', 'Han', 'Hiragana', 'Katakana'],
  ['Latin', 'Han', 'Bopomofo'],
  ['Latin', 'Han', 'Hangul'],
];
export const LOOKALIKES: Readonly<Record<string, string>> = {
  Cyrillic: 'аысԁеԍһіюјӏорԛѕԝхуъьҽпгѵѡ',
  Greek: 'αικνορτυχω',
};
export const LATIN_PASSES_FOR_ASCII = 'ıɑɡɩɪʟʀȷᴀᴄᴅᴇᴊᴋᴍᴏᴘᴛᴜᴠᴡᴢ';
export const HIDDEN_MARKS: readonly number[] = [
  0x323, 0x324, 0x325, 0x326, 0x327, 0x328, 0x331, 0x332, 0x335, 0x336, 0x337,
  0x338,
];

const LETTER = new RegExp('^\\p{L}$', 'u');
const MARK = new RegExp('^\\p{M}$', 'u');
const FORMAT = new RegExp('^\\p{Cf}$', 'u');
const SPACE = new RegExp('^\\p{White_Space}$', 'u');
const ALNUM = new RegExp('^[\\p{L}\\p{N}]$', 'u');
const PRINTABLE_ASCII = /^[\x20-\x7e]*$/;
// ASCII punctuation and whitespace end a word; '⟨' and '⟩' do not, so a marked word stays one Latin word.
const WORD_SEPARATORS = ' \t\r\n!"#$%&\'()*+,-./:;<=>?@[\\]^_`{|}~';
// What ends an address or a host inside identifier text.
const TOKEN = /[^\s,;<>()"']+/g;
const URL_HOST =
  /([A-Za-z][A-Za-z0-9+.-]{0,31}:\/\/)([^\s/?#@]{0,256}@)?([^\s/?#:@[\]]{1,253})/g;

const inRanges = (cp: number, ranges: Ranges): boolean => {
  return ranges.some(([start, end]) => cp >= start && cp <= end);
};
const cpOf = (ch: string): number => ch.codePointAt(0) ?? 0;
const isAscii = (ch: string): boolean => cpOf(ch) < 0x80;
const allAscii = (text: string): boolean => {
  for (let i = 0; i < text.length; i += 1)
    if (text.charCodeAt(i) >= 0x80) return false;
  return true;
};

export function isInvisible(ch: string): boolean {
  const cp = cpOf(ch);
  return (
    cp < 0x20 ||
    (cp >= 0x7f && cp <= 0x9f) ||
    inRanges(cp, INVISIBLE_RANGES) ||
    FORMAT.test(ch)
  );
}

export function marker(ch: string): string {
  let hex = cpOf(ch).toString(16).toUpperCase();
  while (hex.length < 4) hex = `0${hex}`;
  return `${MARKER_OPEN}${hex}${MARKER_CLOSE}`;
}

const visibleNeighbour = (ch: string | undefined): boolean =>
  ch !== undefined && !isAscii(ch) && !SPACE.test(ch);

function keptInProse(chars: readonly string[], i: number): boolean {
  const cp = cpOf(chars[i] ?? '');
  if (PROSE_CONTROLS.indexOf(cp) !== -1) return true;
  if (PROSE_JOINERS.indexOf(cp) !== -1)
    return visibleNeighbour(chars[i - 1]) && visibleNeighbour(chars[i + 1]);
  if (PROSE_VARIATION_SELECTORS.indexOf(cp) !== -1)
    return visibleNeighbour(chars[i - 1]);
  return false;
}

/** `text` with every character a reader cannot see as a marker (in prose, but a joiner inside a word). */
export function markInvisible(text: string, prose = false): string {
  if (allAscii(text) && PRINTABLE_ASCII.test(text)) return text;
  const chars = Array.from(text);
  return chars
    .map((ch, i) =>
      isInvisible(ch) && !(prose && keptInProse(chars, i)) ? marker(ch) : ch,
    )
    .join('');
}

/** The script a letter counts for; null for anything not a letter. */
export function scriptOf(ch: string): string | null {
  if (!LETTER.test(ch)) return null;
  const cp = cpOf(ch);
  const found = SCRIPTS.find((script) => inRanges(cp, script.ranges));
  return found === undefined ? 'Other' : found.name;
}

function scriptsOf(text: string): string[] {
  const found: string[] = [];
  Array.from(text).forEach((ch) => {
    const script = scriptOf(ch);
    if (script !== null && found.indexOf(script) === -1) found.push(script);
  });
  return found;
}

export function mixesScripts(text: string): boolean {
  const found = scriptsOf(text);
  return (
    found.length > 1 &&
    !ALLOWED_MIXES.some((mix) => found.every((s) => mix.indexOf(s) !== -1))
  );
}

// RFC 3492, as Python's `punycode` codec reads and writes it.
const BASE = 36;
const TMIN = 1;
const TMAX = 26;
const SKEW = 38;
const DAMP = 700;

function adapt(delta: number, first: boolean, numPoints: number): number {
  let d = first ? Math.floor(delta / DAMP) : Math.floor(delta / 2);
  d += Math.floor(d / numPoints);
  let k = 0;
  while (d > Math.floor(((BASE - TMIN) * TMAX) / 2)) {
    d = Math.floor(d / (BASE - TMIN));
    k += BASE;
  }
  return k + Math.floor(((BASE - TMIN + 1) * d) / (d + SKEW));
}

const threshold = (j: number, bias: number): number =>
  Math.min(TMAX, Math.max(TMIN, BASE * (j + 1) - bias));

/** A punycode label's body (after `xn--`) decoded; null when it does not decode. */
export function punycodeDecode(body: string): string | null {
  if (!allAscii(body)) return null;
  const cut = body.lastIndexOf('-');
  const out = cut === -1 ? [] : Array.from(body.slice(0, cut));
  const extended = (cut === -1 ? body : body.slice(cut + 1)).toUpperCase();
  let n = 0x80;
  let i = -1;
  let bias = 72;
  let pos = 0;
  while (pos < extended.length) {
    const start = pos;
    let delta = 0;
    let w = 1;
    for (let j = 0; ; j += 1) {
      if (pos >= extended.length) return null;
      const c = extended.charCodeAt(pos);
      pos += 1;
      let digit: number;
      if (c >= 0x41 && c <= 0x5a) digit = c - 0x41;
      else if (c >= 0x30 && c <= 0x39) digit = c - 22;
      else return null;
      const t = threshold(j, bias);
      delta += digit * w;
      if (delta > Number.MAX_SAFE_INTEGER) return null;
      if (digit < t) break;
      w *= BASE - t;
    }
    i += delta + 1;
    n += Math.floor(i / (out.length + 1));
    if (n > 0x10ffff) return null;
    i %= out.length + 1;
    out.splice(i, 0, String.fromCodePoint(n));
    bias = adapt(delta, start === 0, out.length);
  }
  return out.join('');
}

const DIGITS = 'abcdefghijklmnopqrstuvwxyz0123456789';

/** A label encoded as punycode's body (without `xn--`). */
export function punycodeEncode(label: string): string {
  const points = Array.from(label).map(cpOf);
  const basic = points.filter((cp) => cp < 0x80);
  let output = basic.map((cp) => String.fromCharCode(cp)).join('');
  const b = basic.length;
  let h = b;
  if (b > 0) output += '-';
  let n = 0x80;
  let delta = 0;
  let bias = 72;
  while (h < points.length) {
    let m = Infinity;
    points.forEach((cp) => {
      if (cp >= n && cp < m) m = cp;
    });
    delta += (m - n) * (h + 1);
    n = m;
    points.forEach((cp) => {
      if (cp < n) delta += 1;
      if (cp === n) {
        let q = delta;
        for (let k = BASE; ; k += BASE) {
          const t = k <= bias ? TMIN : k >= bias + TMAX ? TMAX : k - bias;
          if (q < t) break;
          output += DIGITS.charAt(t + ((q - t) % (BASE - t)));
          q = Math.floor((q - t) / (BASE - t));
        }
        output += DIGITS.charAt(q);
        bias = adapt(delta, h === b, h + 1);
        delta = 0;
        h += 1;
      }
    });
    delta += 1;
    n += 1;
  }
  return output;
}

function latinPassesForAscii(ch: string): boolean {
  if (LATIN_PASSES_FOR_ASCII.indexOf(ch) !== -1) return true;
  return Array.from(ch.normalize('NFD')).some(
    (c) => HIDDEN_MARKS.indexOf(cpOf(c)) !== -1,
  );
}

function labelSafe(label: string, tldScripts: readonly string[]): boolean {
  const chars = Array.from(label);
  const allowed = chars.every((ch) =>
    isAscii(ch)
      ? /^[A-Za-z0-9-]$/.test(ch)
      : (LETTER.test(ch) || MARK.test(ch)) &&
        !isInvisible(ch) &&
        !(scriptOf(ch) === 'Latin' && latinPassesForAscii(ch)),
  );
  if (!allowed || mixesScripts(label)) return false;
  const found = scriptsOf(label);
  const only = found.length === 1 ? found[0] : undefined;
  if (only === undefined || tldScripts.indexOf(only) !== -1) return true;
  const lookalikes: string | undefined = LOOKALIKES[only];
  const letters = chars.filter((ch) => scriptOf(ch) !== null);
  return (
    lookalikes === undefined ||
    !letters.every((ch) => lookalikes.indexOf(ch) !== -1)
  );
}

/**
 * `host` as a person should read it: as written when ASCII, in Unicode when nothing in it can pass for another
 * site, else each non-ASCII label in punycode.
 */
export function safeHost(host: string): string {
  if (host === '') return host;
  const decoded = host
    .split('.')
    .map((label) =>
      label.slice(0, 4).toLowerCase() === 'xn--'
        ? punycodeDecode(label.slice(4))
        : label,
    );
  if (decoded.some((label) => label === null)) return host;
  const labels = decoded as string[];
  if (labels.every(allAscii)) return host;
  const folded = labels.map((label) => label.normalize('NFKC').toLowerCase());
  const tldScripts = scriptsOf(folded[folded.length - 1] ?? '');
  if (folded.every((label) => labelSafe(label, tldScripts)))
    return folded.join('.');
  return folded
    .map((label) => (allAscii(label) ? label : `xn--${punycodeEncode(label)}`))
    .join('.');
}

const hostLike = (text: string): boolean => {
  const trimmed = trimRun(text, '.');
  return (
    trimmed.indexOf('.') !== -1 &&
    Array.from(text).every(
      (ch) => ALNUM.test(ch) || ch === '-' || ch === '.' || MARK.test(ch),
    )
  );
};

const safeUrlHosts = (text: string): string =>
  text.replace(
    URL_HOST,
    (_m, scheme: string, userinfo: string | undefined, host: string) =>
      `${scheme}${userinfo ?? ''}${safeHost(host)}`,
  );

function safeToken(token: string): string {
  if (token.indexOf('://') !== -1) return safeUrlHosts(token);
  const at = token.lastIndexOf('@');
  if (at !== -1 && hostLike(token.slice(at + 1)))
    return `${token.slice(0, at)}@${safeHost(token.slice(at + 1))}`;
  return hostLike(token) ? safeHost(token) : token;
}

function markMixedWords(text: string): string {
  const out: string[] = [];
  let word: string[] = [];
  const flush = (): void => {
    if (word.length > 0 && mixesScripts(word.join(''))) {
      word.forEach((ch) =>
        out.push(!isAscii(ch) && scriptOf(ch) !== null ? marker(ch) : ch),
      );
    } else {
      word.forEach((ch) => out.push(ch));
    }
    word = [];
  };
  Array.from(text).forEach((ch) => {
    if (WORD_SEPARATORS.indexOf(ch) !== -1) {
      flush();
      out.push(ch);
    } else {
      word.push(ch);
    }
  });
  flush();
  return out.join('');
}

/** `text` as a decision surface shows it (see the file's comment). */
export function displaySafe(
  text: string,
  mode: DisplayMode = 'identifier',
): string {
  if (
    text === '' ||
    (allAscii(text) &&
      PRINTABLE_ASCII.test(text) &&
      text.toLowerCase().indexOf('xn--') === -1)
  )
    return text;
  if (mode === 'prose') return safeUrlHosts(markInvisible(text, true));
  return markMixedWords(markInvisible(text).replace(TOKEN, safeToken));
}

/** `text` cut to `limit` code points with an ellipsis, never inside a marker: a cut one would read as text. */
export function clipDisplay(text: string, limit: number): string {
  const chars = Array.from(text);
  if (chars.length <= limit) return text;
  let head = chars.slice(0, limit - 1).join('');
  const opened = head.lastIndexOf(MARKER_OPEN.charAt(0));
  if (opened !== -1 && head.indexOf(MARKER_CLOSE, opened) === -1)
    head = head.slice(0, opened);
  return `${head}…`;
}

const SPACE_RUN = new RegExp('\\p{White_Space}+', 'gu');
const collapseSpace = (text: string): string =>
  text.replace(SPACE_RUN, ' ').replace(/^ | $/g, '');

/** What a client splits a display name at ("Server · tool"). */
export const NAME_SEPARATOR = ' · ';

/**
 * `Owner · title` for a tool a third party names: both display-safe and the title without the separator, so the
 * owner a client reads is always the configured one.
 */
export function toolDisplayName(owner: string, title: string): string {
  const shown = displaySafe(collapseSpace(title).replace(/\u00b7/g, '-'));
  return `${displaySafe(collapseSpace(owner))}${NAME_SEPARATOR}${shown}`;
}

/** Whether `text` (already display-safe) shows a marker for a character a reader could not see. */
export const hasMarker = (text: string): boolean =>
  text.indexOf(MARKER_OPEN) !== -1;

/** Each punycode host in `text` (already display-safe) with the Unicode form it decodes to, for "looks like". */
export function punycodeHosts(
  text: string,
): Array<{ ascii: string; unicode: string }> {
  const found: Array<{ ascii: string; unicode: string }> = [];
  text.split(/[^A-Za-z0-9.-]+/).forEach((token) => {
    const ascii = token.replace(/^[.-]+|[.-]+$/g, '');
    if (ascii.toLowerCase().indexOf('xn--') === -1) return;
    const unicode = ascii
      .split('.')
      .map((label) =>
        label.slice(0, 4).toLowerCase() === 'xn--'
          ? (punycodeDecode(label.slice(4)) ?? label)
          : label,
      )
      .join('.');
    if (unicode !== ascii && !found.some((f) => f.ascii === ascii))
      found.push({ ascii, unicode });
  });
  return found;
}
