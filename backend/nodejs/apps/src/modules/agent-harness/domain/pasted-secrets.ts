import {
  PASTED_INPUT_RULES,
  REDACT_ONLY_PATTERN_SOURCES,
  SECRET_MASK,
  VENDOR_PATTERN_SOURCES,
} from '../contracts/vectors/token-patterns';

/**
 * Secrets a person pastes into a message (SEC-CR-07, G-37), found by the
 * shared vector's pasted-input rules. Node removes them before a message is
 * kept anywhere: the conversation, the run input, the journal, the title,
 * logs and so every export. The frontend runs the same rules to warn first;
 * this is the enforcement. Python's twin is harness_core.security.pasted_secrets.
 */
export interface PastedSecret {
  readonly kind: string;
  readonly start: number;
  readonly end: number;
}

/**
 * A scan that ran past its caller's time budget (SEC-C-01). Every vector pattern is linear-time (the
 * pattern-budget cohesion test holds them to it), so this is the safety net for a pattern that regresses,
 * not a path a person's message takes.
 */
export class PastedSecretScanTimeout extends Error {
  constructor() {
    super('The pasted-secret scan ran past its time budget');
    this.name = 'PastedSecretScanTimeout';
  }
}

export interface ScanBudget {
  /** `performance.now()` past which the scan stops with `PastedSecretScanTimeout`. */
  readonly deadline: number;
}

const checkBudget = (budget: ScanBudget | undefined): void => {
  if (budget !== undefined && performance.now() > budget.deadline) {
    throw new PastedSecretScanTimeout();
  }
};

/** Lines of the contextual pass between two budget checks. */
const LINES_PER_CHECK = 256;

interface Shape {
  readonly kind: string;
  readonly pattern: RegExp;
  readonly group: number;
  readonly checkValue: boolean;
}

// The compile target's lib predates `RegExpExecArray.indices`; Node 22 fills it.
type IndexedMatch = RegExpExecArray & {
  indices?: ReadonlyArray<readonly [number, number] | undefined>;
};

// `d` gives each group's indices, so a secret inside a longer match is cut exactly.
const flagsOf = (flags: string): string =>
  `gd${flags.includes('i') ? 'i' : ''}`;

const RULES = PASTED_INPUT_RULES;
const SHAPES: readonly Shape[] = [
  ...[...VENDOR_PATTERN_SOURCES, ...REDACT_ONLY_PATTERN_SOURCES].map((s) => ({
    kind: s.kind,
    pattern: new RegExp(s.source, 'gd'),
    group: 0,
    checkValue: false,
  })),
  ...RULES.detectors.map((d) => ({
    kind: d.kind,
    pattern: new RegExp(d.source, 'gd'),
    group: d.secretGroup,
    checkValue: d.checkValue,
  })),
  {
    kind: RULES.assigned.kind,
    pattern: new RegExp(RULES.assigned.source, flagsOf(RULES.assigned.flags)),
    group: RULES.assigned.secretGroup,
    checkValue: true,
  },
];
const PLACEHOLDER = new RegExp(
  RULES.placeholders,
  RULES.placeholderFlags.includes('i') ? 'i' : '',
);
const IDENTIFIER_PATH = new RegExp(RULES.identifierPath);
const CONTEXT = RULES.contextual;
const KEYWORD = new RegExp(
  CONTEXT.keyword,
  CONTEXT.keywordFlags.includes('i') ? 'i' : '',
);
const WORD = new RegExp(CONTEXT.word);
const SEPARATORS = new RegExp(CONTEXT.separators);
const TRAILING = new RegExp(CONTEXT.trailing);
const HEX_ONLY = /^[0-9A-Fa-f-]+$/;
const IDENTIFIER_SYMBOLS = new Set(['_', '-', '.']);

function classes(value: string): number {
  let lower = 0;
  let upper = 0;
  let digit = 0;
  let symbol = 0;
  for (let i = 0; i < value.length; i += 1) {
    const ch = value.charAt(i);
    if (ch >= 'a' && ch <= 'z') lower = 1;
    else if (ch >= 'A' && ch <= 'Z') upper = 1;
    else if (ch >= '0' && ch <= '9') digit = 1;
    else if (!IDENTIFIER_SYMBOLS.has(ch)) symbol = 1;
  }
  return lower + upper + digit + symbol;
}

function plausible(value: string): boolean {
  return (
    !PLACEHOLDER.test(value) &&
    !IDENTIFIER_PATH.test(value) &&
    classes(value) >= RULES.minClasses
  );
}

function bitsPerChar(value: string): number {
  const counts = new Map<string, number>();
  for (let i = 0; i < value.length; i += 1) {
    const ch = value.charAt(i);
    counts.set(ch, (counts.get(ch) ?? 0) + 1);
  }
  let bits = 0;
  counts.forEach((count) => {
    const p = count / value.length;
    bits -= p * Math.log2(p);
  });
  return bits;
}

function contextual(text: string, budget?: ScanBudget): PastedSecret[] {
  const found: PastedSecret[] = [];
  let offset = 0;
  let lines = 0;
  for (const line of text.split('\n')) {
    lines += 1;
    if (lines % LINES_PER_CHECK === 0) checkBudget(budget);
    if (KEYWORD.test(line)) {
      let cursor = 0;
      for (const raw of line.split(SEPARATORS)) {
        const start = raw === '' ? cursor : line.indexOf(raw, cursor);
        cursor = start + raw.length;
        const word = raw.replace(TRAILING, '');
        if (word === '' || word.includes('://') || HEX_ONLY.test(word))
          continue;
        if (!WORD.test(word)) continue;
        if (plausible(word) && bitsPerChar(word) >= CONTEXT.minBits) {
          found.push({
            kind: CONTEXT.kind,
            start: offset + start,
            end: offset + start + word.length,
          });
        }
      }
    }
    offset += line.length + 1;
  }
  return found;
}

/** Every secret in `text`, overlapping finds merged, in order of position. */
export function findPastedSecrets(
  text: string,
  budget?: ScanBudget,
): readonly PastedSecret[] {
  const found: PastedSecret[] = [];
  for (const shape of SHAPES) {
    checkBudget(budget);
    shape.pattern.lastIndex = 0;
    let match: IndexedMatch | null;
    while ((match = shape.pattern.exec(text))) {
      if (match[0] === '') shape.pattern.lastIndex += 1;
      const value = match[shape.group];
      const span = match.indices?.[shape.group];
      if (value === undefined || value === '' || span === undefined) continue;
      if (shape.checkValue && !plausible(value)) continue;
      found.push({ kind: shape.kind, start: span[0], end: span[1] });
    }
  }
  checkBudget(budget);
  found.push(...contextual(text, budget));
  found.sort((a, b) =>
    a.start !== b.start ? a.start - b.start : b.end - a.end,
  );
  const merged: PastedSecret[] = [];
  for (const item of found) {
    const last = merged[merged.length - 1];
    if (last && item.start < last.end) {
      if (item.end > last.end) {
        merged[merged.length - 1] = { ...last, end: item.end };
      }
      continue;
    }
    merged.push(item);
  }
  return merged;
}

/**
 * The kinds of every secret in a JSON value's texts, keys included, in order;
 * never a value. Edited approval arguments are refused when it finds any
 * (AP-06, WP-39): masking them would change what the call sends unasked.
 */
export function pastedSecretKindsIn(value: unknown): string[] {
  if (typeof value === 'string')
    return findPastedSecrets(value).map((f) => f.kind);
  if (Array.isArray(value))
    return value.flatMap((item) => pastedSecretKindsIn(item));
  if (value === null || typeof value !== 'object') return [];
  return Object.entries(value as Record<string, unknown>).flatMap(
    ([key, item]) => [
      ...pastedSecretKindsIn(key),
      ...pastedSecretKindsIn(item),
    ],
  );
}

export interface PastedRedaction {
  readonly text: string;
  /** The kinds removed, in order, one per secret; never a value. */
  readonly kinds: readonly string[];
}

/** `text` with every pasted secret replaced by the shared mask. */
export function redactPastedSecrets(
  text: string,
  mask: string = SECRET_MASK,
  budget?: ScanBudget,
): PastedRedaction {
  const found = findPastedSecrets(text, budget);
  if (found.length === 0) return { text, kinds: [] };
  let out = '';
  let cursor = 0;
  for (const item of found) {
    out += text.slice(cursor, item.start) + mask;
    cursor = item.end;
  }
  out += text.slice(cursor);
  return { text: out, kinds: found.map((f) => f.kind) };
}
