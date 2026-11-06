/**
 * Repairs incomplete / unterminated markdown content that arrives mid-stream
 * via SSE so that `ReactMarkdown + remark-gfm` always receives valid input.
 *
 * This is intentionally only applied to the in-progress streaming content.
 * The final message content (from the `complete` SSE event) is fully formed
 * by the server and never needs patching.
 *
 * Repairs applied, in order:
 *
 * 1. **Escaped newlines** — Some SSE payloads encode newlines as the two-char
 *    sequence `\n` (backslash + n). These must become real newlines before any
 *    structural analysis is done, otherwise the entire table/code block appears
 *    as a single paragraph of text.
 *
 * 2. **Unclosed code fences** — If an opening ``` (or ~~~) has no matching
 *    closing fence yet, every subsequent line is swallowed into the code block.
 *    We close the fence so text that arrives after the code block renders normally.
 *
 * 3. **Incomplete table rows** — `remark-gfm` requires each table row to be
 *    delimited by a trailing `|`. During streaming the last token is often a
 *    partial cell value without the closing pipe. Rather than force-closing
 *    it (which used to make the cell's text grow one token at a time and
 *    re-trigger `table-layout: auto` column-width resolution on every SSE
 *    chunk — the main source of table flicker), the partial row is dropped
 *    until a chunk arrives that completes it. The row then appears once,
 *    fully formed.
 *
 * 4. **Citations not numbered yet** — the model cites as `[label](ref3)`; the
 *    server swaps that for a numbered chip. A raw citation is left out rather
 *    than drawn as a provisional chip whose number and name change later, and
 *    so is a link still being typed and a last line that may become the
 *    answer's confidence trailer. (A tab streaming its own answer never gets
 *    here with one: `settledAnswerText` holds the text back at the first one.)
 */

/**
 * Running state of a ``` / ~~~ fence scan, threaded line-by-line so callers
 * (this file's own repair pass, and `split-streaming-markdown.ts`) can share
 * one fence-tracking implementation instead of re-deriving CommonMark §4.5
 * open/close matching twice.
 */
export interface FenceState {
  inFence: boolean;
  fenceChar: string;
  fenceLen: number;
}

export function createFenceState(): FenceState {
  return { inFence: false, fenceChar: '`', fenceLen: 3 };
}

/** Feed one line into the scan, returning the (possibly updated) state. */
export function advanceFenceState(state: FenceState, line: string): FenceState {
  const trimmed = line.trimStart();
  if (!state.inFence) {
    const m = trimmed.match(/^(`{3,}|~{3,})/);
    if (m) {
      return { inFence: true, fenceChar: m[1][0], fenceLen: m[1].length };
    }
    return state;
  }
  // A valid closing fence: same char, ≥ fenceLen repetitions, optional trailing whitespace
  const closeRe = new RegExp(`^\\${state.fenceChar}{${state.fenceLen},}\\s*$`);
  if (closeRe.test(trimmed)) {
    return { ...state, inFence: false };
  }
  return state;
}

export function repairStreamingMarkdown(content: string): string {
  if (!content) return content;

  // ── 1. Convert escaped newlines; leave out citations not numbered yet ─────
  const result = withoutRawCitations(content.replace(/\\n/g, '\n'));

  const lines = result.split('\n');

  // ── 2. Detect and close unclosed code fences ─────────────────────────────
  // Both backtick fences (```) and tilde fences (~~~) are supported.
  let fenceState = createFenceState();
  for (const line of lines) {
    fenceState = advanceFenceState(fenceState, line);
  }

  if (fenceState.inFence) {
    // Unclosed fence — append the closing marker.
    // Return immediately; the partial content *inside* the fence is already
    // being rendered as a code block, which is the correct intermediate state.
    return result + '\n' + fenceState.fenceChar.repeat(fenceState.fenceLen);
  }

  // ── 3. Drop the last line if it is a partial table row ───────────────────
  // A GFM table row must start AND end with `|`. During streaming the very
  // last chunk is often a cell value still being typed, e.g.:
  //   | Organization Name | PipesHub
  // Dropping it (instead of force-closing with a trailing `|`) means the
  // table only re-renders once per completed row instead of once per chunk.
  const lastLine = lines[lines.length - 1];
  if (lastLine !== undefined) {
    const trimmedLast = lastLine.trimStart();
    if (trimmedLast.startsWith('|') && !lastLine.trimEnd().endsWith('|')) {
      lines.pop();
      return lines.join('\n');
    }
  }

  return result;
}

/**
 * `text` without the footnote references (`[^1]`) whose definition (`[^1]: …`) it does not hold: until it does, a
 * reference draws as its bare markup. Code is left as it is. It runs on the text as drawn, which a reveal may end
 * between a reference and its definition, not on all that has arrived.
 */
export function withoutUndefinedFootnotes(text: string): string {
  if (!text.includes('[^')) return text;
  const lines = text.split('\n');
  const defined = new Set<string>();
  let fence = createFenceState();
  const inCode = lines.map((line) => {
    const was = fence.inFence;
    fence = advanceFenceState(fence, line);
    const code = was || fence.inFence;
    const definition = code ? null : /^ {0,3}\[\^([^\]\s]+)\]:/.exec(line);
    if (definition) defined.add(definition[1]);
    return code;
  });
  return lines
    .map((line, i) => (inCode[i] ? line : line.replace(/\[\^([^\]\s]+)\](?!:)/g, (ref, label: string) => (defined.has(label) ? ref : ''))))
    .join('\n');
}

/** A markdown link (or a bracket that may still become one) outside code. */
interface LinkSpan {
  start: number;
  end: number;
  label: string;
  target: string | null;
  /** False for one still being typed at the very end: more text may make it a link. */
  complete: boolean;
}

/** The links of `text` outside code, in order. Only the last line can hold an incomplete one. */
function scanLinks(text: string): LinkSpan[] {
  const links: LinkSpan[] = [];
  const lines = text.split('\n');
  let fence = createFenceState();
  let lineStart = 0;
  for (let li = 0; li < lines.length; li += 1) {
    const line = lines[li];
    const start = lineStart;
    lineStart += line.length + 1;
    const wasInFence = fence.inFence;
    fence = advanceFenceState(fence, line);
    if (wasInFence || fence.inFence) continue;
    // Only the last line is still being written: elsewhere a bracket that never closes is just a bracket.
    const isLast = li === lines.length - 1;
    let inCode = false;
    for (let i = 0; i < line.length; i += 1) {
      if (line[i] === '`') inCode = !inCode;
      if (inCode || line[i] !== '[' || line[i - 1] === '\\') continue;
      let j = i + 1;
      for (let depth = 1; depth > 0; j += 1) {
        if (j === line.length) {
          j = -1;
          break;
        }
        if (line[j] === '[') depth += 1;
        else if (line[j] === ']') depth -= 1;
      }
      if (j === -1) {
        if (!isLast) continue;
        links.push({ start: start + i, end: text.length, label: line.slice(i + 1), target: null, complete: false });
        break;
      }
      const label = line.slice(i + 1, j - 1);
      let k = j;
      while (line[k] === ' ') k += 1;
      if (k === line.length && isLast) {
        links.push({ start: start + i, end: text.length, label, target: null, complete: false });
        break;
      }
      if (line[k] !== '(') {
        i = j - 1;
        continue;
      }
      let m = k + 1;
      for (let depth = 1; depth > 0; m += 1) {
        if (m === line.length) {
          m = -1;
          break;
        }
        if (line[m] === '(') depth += 1;
        else if (line[m] === ')') depth -= 1;
      }
      if (m === -1) {
        if (!isLast) {
          i = j - 1;
          continue;
        }
        links.push({ start: start + i, end: text.length, label, target: line.slice(k + 1), complete: false });
        break;
      }
      links.push({ start: start + i, end: start + m, label, target: line.slice(k + 1, m - 1), complete: true });
      i = m - 1;
    }
  }
  return links;
}

/** The model's own citation, before the server numbers it: to a short ref (`ref3`), or a record page by name. */
function isRawCitation(link: LinkSpan): boolean {
  const target = link.target?.trim() ?? '';
  if (/^(?:https?:\/\/)?ref\d+/i.test(target)) return true;
  return /\/record\//.test(target) && !/^\[?\d+\]?$/.test(link.label.trim());
}

/** Every shape of the answer's closing "Confidence: High" line, squashed, so a line still being typed can be matched. */
const TRAILER_FORMS = [':', '-', '–', 'level:', 'score:'].flatMap((separator) =>
  ['veryhigh', 'high', 'medium', 'low'].map((level) => `confidence${separator}${level}`),
);

const isRule = (line: string): boolean => /^[-*_ \t]+$/.test(line.trim()) && line.trim() !== '';

/** `text` without a last line that may still become the confidence trailer, nor the rule and blank lines above it. */
function withoutTrailerStart(text: string): string {
  const lines = text.split('\n');
  let cut = lines.length - 1;
  const squashed = lines[cut].replace(/[*_\s]+/g, '').toLowerCase().replace(/[.!]+$/, '');
  if (!squashed || TRAILER_FORMS.some((form) => form.startsWith(squashed))) {
    while (cut > 0 && !lines[cut - 1].trim()) cut -= 1;
    if (cut > 0 && isRule(lines[cut - 1])) cut -= 1;
  } else if (!isRule(lines[cut])) {
    return text;
  }
  if (lines.slice(cut).every((line) => !line.trim())) return text;
  return lines.slice(0, cut).join('\n').trimEnd();
}

/** `text` with the raw citations taken out (and the space before each), cut at a link still being typed. */
function withoutRawCitations(text: string): string {
  let out = '';
  let pos = 0;
  for (const link of scanLinks(text)) {
    if (!link.complete) {
      out = (out + text.slice(pos, link.start)).replace(/[ \t]+$/, '');
      pos = text.length;
      break;
    }
    if (!isRawCitation(link)) continue;
    out = (out + text.slice(pos, link.start)).replace(/[ \t]+$/, '');
    pos = link.end;
  }
  return withoutTrailerStart(out + text.slice(pos));
}

/**
 * A citation link's label that the server's badge replaces outright: a generic word ("source", "ref 2"), numbers, or
 * the target itself (a URL, a ref, a record id). Any other label is prose the server keeps in front of the badge.
 * Mirrors `_is_citation_label` in the backend's `utils/citations.py`.
 */
function isGenericCitationLabel(label: string): boolean {
  if (/^\s*(?:(?:sources?|refs?|links?|citations?)\s*#?\s*\d*|\d+(?:\s*[,;]\s*\d+)*)?\s*$/i.test(label)) return true;
  const bare = label.replace(/^[\s`'"[\]()<>]+|[\s`'"[\]()<>]+$/g, '');
  if (/^(?:https?:\/\/ref\d+\.xyz\/?|ref[0-9a-z-]+|[0-9a-f]{8}(?:-?[0-9a-f]{4}){3}-?[0-9a-f]{12}|[0-9a-f]{24})$/i.test(bare)) {
    return true;
  }
  return /^<?(?:https?:\/\/|www\.)\S*>?$/.test(bare);
}

const escapeRegExp = (text: string): string => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

/**
 * The words a citation link leaves in the sentence once the server makes it a badge: its label, unless that is
 * generic or repeats what was just written before it (and any badges after that). Mirrors `kept_citation_link_text`.
 */
function keptLabel(label: string, written: string): string {
  if (isGenericCitationLabel(label)) return '';
  const kept = label.trim();
  const echo = new RegExp(`(?<!\\w)${escapeRegExp(kept)}(?:\\]\\([^)]*\\))?\\s*(?:\\[\\d+\\]\\([^)]*\\)\\s*)*$`, 'i');
  return echo.test(written.slice(-(kept.length + 2000))) ? '' : kept;
}

/** One citation in the streamed text: numbered by the server already, or still the model's own link. */
interface Marker {
  link: LinkSpan;
  numbered: boolean;
  /** What a link not numbered yet leaves in the sentence meanwhile (and after): its label when it is prose. */
  kept: string;
}

/**
 * What of the streamed answer to show now. The server numbers the answer's citations (`settledLength` characters of
 * `text` are its numbered copy) a few times a second; what streamed after that is the model's raw text. The words are
 * shown as they arrive. A citation is shown only when it is final: a run of citations side by side (one chip, or one
 * group of them) appears once all of it is numbered and the sentence goes on after it, so a chip never changes its
 * number, name or group, and a group never changes its count. Until then its markers are left out, and a citation's
 * descriptive label shows as the words the server keeps. Also left out: a link still being typed at the end, and a
 * closing line that may become the confidence trailer the server takes out.
 */
export function settledAnswerText(text: string, settledLength: number): string {
  const links = scanLinks(text);
  const markers: Marker[] = [];
  let cutAt = text.length;
  for (const link of links) {
    if (!link.complete) {
      cutAt = link.start;
      break;
    }
    const numberedLabel = /^\s*\d+\s*$/.test(link.label);
    if (link.end <= settledLength && numberedLabel) markers.push({ link, numbered: true, kept: '' });
    else if (link.end > settledLength && (numberedLabel || isRawCitation(link))) markers.push({ link, numbered: false, kept: '' });
  }
  // Runs of citations with only spaces (or one line break) between them: what the answer draws as one chip or group.
  // A label the server keeps is words between two badges, so it starts a run of its own.
  const runs: Marker[][] = [];
  let written = '';
  let pos = 0;
  for (const marker of markers) {
    written += text.slice(pos, marker.link.start);
    if (!marker.numbered) marker.kept = keptLabel(marker.link.label, written);
    const last = runs[runs.length - 1]?.at(-1);
    const gap = last ? text.slice(last.link.end, marker.link.start) : null;
    if (last && !marker.kept && gap !== null && /^[ \t]*(?:\n[ \t]*)?$/.test(gap)) runs[runs.length - 1].push(marker);
    else runs.push([marker]);
    written += marker.numbered ? text.slice(marker.link.start, marker.link.end) : marker.kept;
    pos = marker.link.end;
  }
  const hidden = new Set<Marker>();
  for (const run of runs) {
    const end = run[run.length - 1].link.end;
    // The sentence has gone on past the run: something other than space follows it in what has arrived.
    const after = text.slice(end, cutAt);
    const final = run.every((marker) => marker.numbered) && /\S/.test(after);
    if (!final) for (const marker of run) hidden.add(marker);
  }
  let out = '';
  pos = 0;
  for (const marker of markers) {
    if (marker.link.start >= cutAt) break;
    if (!hidden.has(marker)) continue;
    out += text.slice(pos, marker.link.start);
    // A marker left out takes the space before it along, so the words close up as they will around its chip.
    out = marker.kept ? out + marker.kept : out.replace(/[ \t]+$/, '');
    pos = marker.link.end;
  }
  out += text.slice(pos, cutAt);
  if (cutAt < text.length) out = out.replace(/[ \t]+$/, '');
  return settledLength < text.length ? withoutTrailerStart(out) : out;
}
