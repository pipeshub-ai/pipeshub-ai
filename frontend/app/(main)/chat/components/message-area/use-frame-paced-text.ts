'use client';

import { useEffect, useRef, useState } from 'react';
import { advanceFenceState, createFenceState } from '../../utils/repair-streaming-markdown';

/**
 * The longest the reveal holds text that has arrived. Drawing it takes a frame more, so what the reader sees trails the
 * text that arrived by about 100 ms at most.
 */
export const REVEAL_LAG_MS = 50;

let reducedQuery: MediaQueryList | null = null;
const reducedMotion = () => {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return false;
  reducedQuery ??= window.matchMedia('(prefers-reduced-motion: reduce)');
  return reducedQuery.matches;
};

const isSpace = (c: string | undefined): boolean => c === undefined || /\s/.test(c);
/** Punctuation or a symbol, as emphasis rules count it: ASCII marks, and the common quotes, dashes and the like. */
const isMark = (c: string | undefined): boolean => c !== undefined && /[!-/:-@[-`{-~\u00a1-\u00bf\u2010-\u2027\u2030-\u205e]/.test(c);

/** A run of `*`, `_` or `~~` on a line: emphasis, bold or strikethrough when it opens or closes one. */
interface DelimiterRun {
  at: number;
  end: number;
  ch: string;
  open: boolean;
  close: boolean;
}

/** An opener left open this long on its line is plain text (a lone `*`, a `_name`), so it holds nothing back. */
const EMPHASIS_REACH = 200;

/**
 * The emphasis runs of `line`, outside code spans, each paired with the run that closes it (by index), or -1. With
 * `more`, `line` stops where text is still to arrive, so a run at its end may yet open.
 */
function emphasisRuns(line: string, more = false): { runs: DelimiterRun[]; closedBy: number[] } {
  const runs: DelimiterRun[] = [];
  let code = false;
  for (let i = 0; i < line.length; ) {
    const c = line[i];
    if (c === '`') code = !code;
    if (code || (c !== '*' && c !== '_' && c !== '~') || line[i - 1] === '\\') {
      i += 1;
      continue;
    }
    let j = i;
    while (line[j] === c) j += 1;
    // A single `~` is "about" (~2 hours) far more often than strikethrough.
    if (c === '~' && j - i !== 2) {
      i = j;
      continue;
    }
    const before = line[i - 1];
    const after = more && j === line.length ? 'a' : line[j];
    const left = !isSpace(after) && (!isMark(after) || isSpace(before) || isMark(before));
    const right = !isSpace(before) && (!isMark(before) || isSpace(after) || isMark(after));
    // `_` never opens or closes inside a word (snake_case).
    const open = c === '_' ? left && (!right || isMark(before)) : left;
    const close = c === '_' ? right && (!left || isMark(after)) : right;
    runs.push({ at: i, end: j, ch: c, open, close });
    i = j;
  }
  // Each closing mark closes one of an opener's marks: `***a*` leaves `**` open.
  const closedBy = runs.map(() => -1);
  const left = runs.map((run) => run.end - run.at);
  const stack: number[] = [];
  runs.forEach((run, i) => {
    let marks = left[i];
    if (run.close) {
      for (let k = stack.findLastIndex((o) => runs[o].ch === run.ch); marks > 0 && k !== -1; k = stack.findLastIndex((o) => runs[o].ch === run.ch)) {
        const o = stack[k];
        const used = Math.min(left[o], marks);
        left[o] -= used;
        marks -= used;
        if (left[o] > 0) break;
        closedBy[o] = i;
        stack.splice(k);
      }
      // A run that closes one opens nothing.
      if (marks < left[i]) run.open = false;
    }
    if (run.open) stack.push(i);
  });
  return { runs, closedBy };
}

/**
 * The first markup opener on `line` still open at its end (a citation chip `[3]`, a link, emphasis, bold or
 * strikethrough, a code span, an HTML tag), whose markup would draw as text until it closes; -1 when none is.
 */
function openAt(line: string, more = false): number {
  const open: number[] = [];
  if (line.lastIndexOf('[') > line.lastIndexOf(']')) open.push(line.lastIndexOf('['));
  const target = line.lastIndexOf('](');
  if (target >= 0 && target > line.lastIndexOf(')')) open.push(line.lastIndexOf('[', target));
  if ((line.split('`').length - 1) % 2 === 1) open.push(line.lastIndexOf('`'));
  if (line.lastIndexOf('<') > line.lastIndexOf('>')) open.push(line.lastIndexOf('<'));
  const { runs, closedBy } = emphasisRuns(line, more);
  const unclosed = runs.findIndex((run, i) => run.open && closedBy[i] === -1 && line.length - run.at <= EMPHASIS_REACH);
  if (unclosed !== -1) open.push(runs[unclosed].at);
  const found = open.filter((at) => at >= 0);
  return found.length ? Math.min(...found) : -1;
}

/** Where the construct opening at `at` in `line` ends (exclusive), or -1 when `line` does not close it. */
function closeAfter(line: string, at: number): number {
  const rest = line.slice(at);
  const after = (closer: string, from: number) => {
    const c = rest.indexOf(closer, from);
    return c === -1 ? -1 : at + c + closer.length;
  };
  if (rest.startsWith('`')) return after('`', 1);
  if (rest.startsWith('<')) return after('>', 1);
  if (rest[0] === '*' || rest[0] === '_' || rest[0] === '~') {
    const { runs, closedBy } = emphasisRuns(line);
    const i = runs.findIndex((run) => run.at === at);
    return i !== -1 && closedBy[i] !== -1 ? runs[closedBy[i]].end : -1;
  }
  let depth = 0;
  for (let i = 0; i < rest.length; i += 1) {
    if (rest[i] === '[') depth += 1;
    else if (rest[i] === ']' && --depth === 0) return rest[i + 1] === '(' ? after(')', i + 2) : at + i + 1;
  }
  return -1;
}

/** A formula the text is inside of at some point: where it opens, the mark that closes it, and whether it is a block. */
interface Formula {
  at: number;
  closer: string;
  /** A `$$` on a line of its own: the formula runs to the next such line, blank lines and all. */
  block: boolean;
}

/**
 * The formula still open at `end`: `$$ … $$`, `\( … \)` or `\[ … \]`, or a block between two `$$` lines. Until it
 * closes, its LaTeX would draw as text. One not closed in its paragraph never was a formula. Code is skipped.
 */
function openFormula(text: string, end: number): Formula | null {
  let open: Formula | null = null;
  let fence = createFenceState();
  for (let lineStart = 0; lineStart <= end; ) {
    const newline = text.indexOf('\n', lineStart);
    const lineEnd = newline === -1 ? text.length : newline;
    const line = text.slice(lineStart, Math.min(lineEnd, end));
    const whole = text.slice(lineStart, lineEnd);
    const inCode = fence.inFence;
    fence = advanceFenceState(fence, whole);
    if (!inCode && !fence.inFence && !open?.block) {
      if (!line.trim()) {
        open = null;
      } else if (whole.trim() === '$$' && !open) {
        open = { at: lineStart + line.indexOf('$$'), closer: '$$', block: true };
      } else {
        let code = false;
        for (let i = 0; i < line.length; i += 1) {
          if (line[i] === '`') code = !code;
          if (code || (line[i - 1] === '\\' && line[i] !== '(' && line[i] !== '[' && line[i] !== ')' && line[i] !== ']')) continue;
          if (open) {
            if (line.startsWith(open.closer, i)) {
              i += open.closer.length - 1;
              open = null;
            }
          } else if (line.startsWith('$$', i) || line.startsWith('\\(', i) || line.startsWith('\\[', i)) {
            const closer = line[i] === '$' ? '$$' : line[i + 1] === '(' ? '\\)' : '\\]';
            open = { at: lineStart + i, closer, block: false };
            i += 1;
          }
        }
      }
    } else if (open?.block && !inCode && whole.trim() === '$$' && lineEnd <= end) {
      open = null;
    }
    if (newline === -1 || newline >= end) break;
    lineStart = newline + 1;
  }
  return open;
}

/** `closeFormula`'s answer for an opener whose paragraph ended without closing it. */
const NOT_A_FORMULA = -2;

/**
 * Where `formula`, open at `end`, closes (exclusive): -1 while it is still being written, and `NOT_A_FORMULA` when its
 * paragraph ended without closing it, so its opener is plain text.
 */
function closeFormula(text: string, formula: Formula, end: number): number {
  if (formula.block) {
    const closing = /\n[ \t]*\$\$[ \t]*(?=\n|$)/g;
    closing.lastIndex = Math.max(formula.at + 2, end - 1);
    const m = closing.exec(text);
    return m ? m.index + m[0].length : -1;
  }
  const close = text.indexOf(formula.closer, end - (formula.closer.length - 1));
  const paragraphEnd = text.slice(end).search(/\n[ \t]*\n/);
  if (paragraphEnd !== -1 && (close === -1 || close > end + paragraphEnd)) return NOT_A_FORMULA;
  return close === -1 ? -1 : close + formula.closer.length;
}

/** A table's line under its first row (`| --- | :-: |`), without which the first row draws as text. */
const TABLE_RULE = /^[ \t]*\|?[ \t]*:?-+:?[ \t]*(\|[ \t]*:?-+:?[ \t]*)*\|?[ \t]*$/;

/** The line of `text` that starts at `start`. */
const lineAt = (text: string, start: number): string => {
  const newline = text.indexOf('\n', start);
  return text.slice(start, newline === -1 ? text.length : newline);
};

/** A table row's cells. */
const cells = (row: string): number => row.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').length;

/** Where the table row at `lineStart` starts its table: this line or the one above (when this is its rule); else null. */
function tableHead(text: string, lineStart: number): number | null {
  const startOf = (at: number) => (at === 0 ? -1 : text.lastIndexOf('\n', at - 2) + 1);
  const isRow = (at: number) => at >= 0 && lineAt(text, at).trimStart().startsWith('|');
  const above = startOf(lineStart);
  if (!isRow(above)) return lineStart;
  return isRow(startOf(above)) ? null : above;
}
/**
 * A marker at a line's start, its spaces included: a list item's, which alone under a paragraph draws that paragraph as
 * a heading; a heading's, which alone draws an empty heading; a quote's, which alone draws an empty quote; or a
 * footnote's definition (`[^1]:`), which before its first word defines nothing the answer's blocks can share. Quote
 * marks may come before any of them (`> - `).
 */
const MARKER = String.raw`^[ \t]*(?:>[ \t]*)*(?:>|[-*+]|\d{1,9}[.)]|#{1,6}|\[\^[^\]\s]+\]:)[ \t]*`;

/** HTML start tags (`<b>`, `<sup>`), which draw nothing before the text they hold. */
const START_TAGS = String.raw`(?:<[A-Za-z][^<>]*>[ \t]*)*`;

/** A marker alone, or with only start tags after it. */
const LONE_MARKER = new RegExp(`${MARKER}${START_TAGS}$`);

/**
 * A marker, then the first mark of markup that the next character may open: `$` of `$$`, `\` of `\(` or `\[`, `~` of
 * `~~`, `!` of `![`, or an entity before its `;` (`&am`). Until then it is no markup, so it would draw as text: a
 * heading reading "$" for a frame.
 */
const MARKER_THEN_OPENING = new RegExp(`${MARKER}${START_TAGS}(?:[$\\\\~!]|&[#A-Za-z0-9]*)$`);

/**
 * `cut`, or the start of its line when all the line has before `cut` is a marker, or a marker and the first mark of
 * markup: a reveal never ends on a marker alone.
 */
const notAfterLoneMarker = (text: string, cut: number): number => {
  const lineStart = text.lastIndexOf('\n', cut - 1) + 1;
  const before = text.slice(lineStart, cut);
  return LONE_MARKER.test(before) || MARKER_THEN_OPENING.test(before) ? lineStart : cut;
};

/** A citation chip's marker, as the answer draws it (`[3]`), at the start of what follows a reveal's end. */
const NEXT_MARKER = /^[ \t]*\[{1,2}\d+\]{1,2}/;

/**
 * `end`, moved past the rest of a run of citation markers it stops inside of, and the one punctuation mark after the
 * run that the answer draws in front of it: a run is one chip or one group, which would otherwise show its numbers one
 * at a time, its "+N" count changing as they come.
 */
function pastCitationRun(text: string, end: number): number {
  if (!/\[{1,2}\d+\]{1,2}[ \t]*$/.test(text.slice(Math.max(0, end - 12), end))) return end;
  let at = end;
  for (let next = NEXT_MARKER.exec(text.slice(at)); next; next = NEXT_MARKER.exec(text.slice(at))) at += next[0].length;
  return /^[.!?;:,]/.test(text.slice(at)) ? at + 1 : at;
}

/**
 * Where a reveal of `text` that wants to reach `want` may end: at the end of a word, and never inside a citation chip,
 * a link, emphasis, a code span, an HTML tag, a formula or a table row (or a table's first row without the line that
 * makes it a table), which would draw their markup as text for a frame, nor inside a run of citation chips, which would
 * draw a group a number at a time. It goes on past the construct when `text` already holds all of it, and stops before
 * it while it is still being written, at the end of `text` too; never below `floor`. Inside a code block only words
 * count.
 */
export function revealEnd(text: string, want: number, floor: number): number {
  let end = Math.min(text.length, Math.max(want, floor));
  for (let guard = 0; guard < 16; guard += 1) {
    while (end < text.length && !/\s/.test(text[end])) end += 1;
    const lineStart = text.lastIndexOf('\n', end - 1) + 1;
    let fence = createFenceState();
    for (const line of text.slice(0, lineStart).split('\n')) fence = advanceFenceState(fence, line);
    if (fence.inFence) return end;
    // A formula draws only whole.
    const formula = openFormula(text, end);
    const close = formula ? closeFormula(text, formula, end) : NOT_A_FORMULA;
    if (formula && close === -1) return Math.max(floor, notAfterLoneMarker(text, formula.at));
    if (close !== NOT_A_FORMULA) {
      end = close;
      continue;
    }
    const newline = text.indexOf('\n', end);
    const lineEnd = newline === -1 ? text.length : newline;
    const lineDone = newline !== -1 || text.trimEnd().endsWith('|');
    if (text.slice(lineStart, end).trimStart().startsWith('|')) {
      const head = tableHead(text, lineStart);
      if (head !== null) {
        // A table's first row draws only with the rule under it, which makes it a table.
        const headLine = lineAt(text, head);
        const ruleStart = head + headLine.length + 1;
        if (ruleStart > text.length) return Math.max(floor, head);
        const rule = lineAt(text, ruleStart);
        const ruleEnd = ruleStart + rule.length;
        if (ruleEnd >= text.length) {
          // Still being written: it may yet be the rule.
          if (/^[ \t|:-]*$/.test(rule)) return Math.max(floor, head);
        } else if (TABLE_RULE.test(rule) && rule.includes('|') && cells(rule) === cells(headLine)) {
          return ruleEnd;
        }
      }
      // A table row draws only whole.
      return lineDone ? lineEnd : Math.max(floor, lineStart);
    }
    // A marker alone shows with its first word.
    if (LONE_MARKER.test(text.slice(lineStart, end))) {
      if (end + 1 >= text.length) return Math.max(floor, lineStart);
      end += 1;
      continue;
    }
    // So does one whose first word so far is the first mark of markup: what comes next says whether it opens any.
    if (end === text.length && MARKER_THEN_OPENING.test(text.slice(lineStart, end))) return Math.max(floor, lineStart);
    const open = openAt(text.slice(lineStart, end), end === text.length);
    if (open === -1) return pastCitationRun(text, end);
    const closed = closeAfter(text.slice(lineStart, lineEnd), open);
    if (closed === -1) {
      // A finished line that never closed it: a plain bracket or asterisk, shown as it is.
      if (newline !== -1) return lineEnd;
      // Before markup still open on its first word, a heading's or list item's marker would show alone.
      return Math.max(floor, notAfterLoneMarker(text, lineStart + open));
    }
    if (lineStart + closed <= end) return pastCitationRun(text, end);
    end = lineStart + closed;
  }
  return pastCitationRun(text, Math.min(end, text.length));
}

/**
 * The answer's text as it is drawn while it streams: what arrived, revealed a little every frame instead of in the
 * bursts it comes in (a batch of tokens, or text held back until the server numbered a citation), and never more than
 * `REVEAL_LAG_MS` behind. A reveal ends where `revealEnd` allows. Text that does not continue what is shown (a new
 * turn, a correction) is shown at once, and so is everything with reduced motion, up to markup still being written.
 * When the answer is done, a reveal under way still finishes, so its last words do not land in one frame; markup it
 * still holds back then can never close, so it shows as it is.
 */
export function useFramePacedText(text: string, active: boolean): string {
  // A streaming answer's first text, too, stops short of markup still being written.
  const [shown, setShown] = useState(() => (active ? text.slice(0, revealEnd(text, text.length, 0)) : text));
  const state = useRef({ shown, text, active, arrivals: [] as { at: number; length: number }[], frame: 0, last: 0 });

  useEffect(() => {
    const s = state.current;
    s.text = text;
    s.active = active;
    // Once the answer is done, a reveal already under way finishes (within the lag); nothing new starts.
    if (reducedMotion() || !text.startsWith(s.shown) || (!active && !s.frame)) {
      cancelAnimationFrame(s.frame);
      s.frame = 0;
      s.arrivals = [];
      // Shown at once, but while it streams not past markup still being written.
      s.shown = active ? text.slice(0, revealEnd(text, text.length, 0)) : text;
      setShown(s.shown);
      return;
    }
    if (text.length === s.shown.length) return;
    s.arrivals.push({ at: performance.now(), length: text.length });
    if (s.frame) return;
    s.last = 0;
    const step = (now: number) => {
      const current = state.current;
      const backlog = current.text.length - current.shown.length;
      if (backlog <= 0) {
        current.frame = 0;
        return;
      }
      // Everything that arrived a full lag ago is due now; the rest is spread over the frames left.
      const due = current.arrivals.reduce((max, a) => (now - a.at >= REVEAL_LAG_MS ? Math.max(max, a.length) : max), 0);
      // The first frame of a reveal counts as one frame's time.
      const share = Math.min(1, (current.last ? now - current.last : 1000 / 60) / REVEAL_LAG_MS);
      const glide = current.shown.length + Math.max(1, Math.ceil(backlog * share));
      current.last = now;
      const allowed = revealEnd(current.text, Math.max(due, glide), current.shown.length);
      // Once the answer is done, nothing more comes to close what the reveal holds back (a run stopped mid-word, a
      // stray `**`): the rest shows as it is, as the saved answer draws it, rather than never.
      const end = allowed > current.shown.length || current.active ? allowed : current.text.length;
      if (end > current.shown.length) {
        current.shown = current.text.slice(0, end);
        current.arrivals = current.arrivals.filter((a) => a.length > end);
        setShown(current.shown);
      }
      current.frame = requestAnimationFrame(step);
    };
    s.frame = requestAnimationFrame(step);
  }, [text, active]);

  useEffect(
    () => () => {
      cancelAnimationFrame(state.current.frame);
      state.current.frame = 0;
    },
    [],
  );

  // eslint-disable-next-line react-hooks/refs -- intentional: a reveal still finishing after the answer is done
  return active || state.current.frame !== 0 ? shown : text;
}
