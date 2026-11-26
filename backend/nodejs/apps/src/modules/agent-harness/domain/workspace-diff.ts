/**
 * A session workspace's changes between two revisions as the chat shows them
 * (SLASH2 C3 `/diff`): one entry per changed path, with a unified diff of a
 * text file. Bounded at every step, so a large workspace answers a short list
 * rather than a slow or huge one.
 */

export type WorkspaceFileChange = 'added' | 'modified' | 'deleted';

/** One changed path; the frontend's `ChangedFile` shape. */
export interface WorkspaceDiffFile {
  path: string;
  change: WorkspaceFileChange;
  /** The file's size now; its size before when it was deleted. */
  sizeBytes: number;
  additions: number;
  deletions: number;
  diff?: string;
  diffTruncated?: boolean;
  binary?: boolean;
  tooLarge?: boolean;
}

export const DIFF_CONTEXT_LINES = 3;
/** A side larger than this is listed, never read. */
export const DIFF_MAX_FILE_BYTES = 256 * 1024;
/** Lines one file's diff keeps; the rest is cut and marked. */
export const DIFF_MAX_LINES = 400;
/** The line-matching table's size; above it the changed middle reads as removed then added. */
const LCS_MAX_CELLS = 1_000_000;

type Op = { kind: ' ' | '-' | '+'; text: string; a: number; b: number };

const linesOf = (text: string): string[] => {
  if (text === '') return [];
  const lines = text.split('\n');
  if (lines[lines.length - 1] === '') lines.pop();
  return lines;
};

/** The edit script of the changed middle: an LCS table when it is small enough. */
function middleOps(a: string[], b: string[], a0: number, b0: number): Op[] {
  const n = a.length;
  const m = b.length;
  if (n * m > LCS_MAX_CELLS) {
    return [
      ...a.map((text, i) => ({ kind: '-' as const, text, a: a0 + i, b: b0 })),
      ...b.map((text, j) => ({
        kind: '+' as const,
        text,
        a: a0 + n,
        b: b0 + j,
      })),
    ];
  }
  // table[i][j]: the LCS length of a[i:] and b[j:].
  const width = m + 1;
  const table = new Uint32Array((n + 1) * width);
  for (let i = n - 1; i >= 0; i -= 1) {
    for (let j = m - 1; j >= 0; j -= 1) {
      table[i * width + j] =
        a[i] === b[j]
          ? (table[(i + 1) * width + j + 1] as number) + 1
          : Math.max(
              table[(i + 1) * width + j] as number,
              table[i * width + j + 1] as number,
            );
    }
  }
  const ops: Op[] = [];
  let i = 0;
  let j = 0;
  while (i < n || j < m) {
    if (i < n && j < m && a[i] === b[j]) {
      ops.push({ kind: ' ', text: a[i] as string, a: a0 + i, b: b0 + j });
      i += 1;
      j += 1;
    } else if (
      i < n &&
      (j === m ||
        (table[(i + 1) * width + j] as number) >=
          (table[i * width + j + 1] as number))
    ) {
      // Removals before additions, as `diff -u` orders them.
      ops.push({ kind: '-', text: a[i] as string, a: a0 + i, b: b0 + j });
      i += 1;
    } else {
      ops.push({ kind: '+', text: b[j] as string, a: a0 + i, b: b0 + j });
      j += 1;
    }
  }
  return ops;
}

const range = (start: number, length: number): string =>
  length === 1 ? String(start) : `${String(start)},${String(length)}`;

/**
 * `diff -u` of two texts (null: the file is absent on that side), with
 * `a/` and `b/` headers. Deterministic, so every executor's turn reads the
 * same text for the same revisions.
 */
export function unifiedDiff(
  path: string,
  before: string | null,
  after: string | null,
): { diff: string; additions: number; deletions: number; truncated: boolean } {
  const a = linesOf(before ?? '');
  const b = linesOf(after ?? '');
  let prefix = 0;
  while (prefix < a.length && prefix < b.length && a[prefix] === b[prefix])
    prefix += 1;
  let suffix = 0;
  while (
    suffix < a.length - prefix &&
    suffix < b.length - prefix &&
    a[a.length - 1 - suffix] === b[b.length - 1 - suffix]
  ) {
    suffix += 1;
  }
  const ops: Op[] = [
    ...a
      .slice(0, prefix)
      .map((text, i) => ({ kind: ' ' as const, text, a: i, b: i })),
    ...middleOps(
      a.slice(prefix, a.length - suffix),
      b.slice(prefix, b.length - suffix),
      prefix,
      prefix,
    ),
    ...a.slice(a.length - suffix).map((text, k) => ({
      kind: ' ' as const,
      text,
      a: a.length - suffix + k,
      b: b.length - suffix + k,
    })),
  ];
  const changed = ops.flatMap((op, index) => (op.kind === ' ' ? [] : [index]));
  const additions = ops.filter((op) => op.kind === '+').length;
  const deletions = ops.filter((op) => op.kind === '-').length;
  const out: string[] = [
    before === null ? '--- /dev/null' : `--- a/${path}`,
    after === null ? '+++ /dev/null' : `+++ b/${path}`,
  ];
  let k = 0;
  while (k < changed.length) {
    const start = Math.max(0, (changed[k] as number) - DIFF_CONTEXT_LINES);
    let end = (changed[k] as number) + DIFF_CONTEXT_LINES;
    while (
      k + 1 < changed.length &&
      (changed[k + 1] as number) - DIFF_CONTEXT_LINES <= end + 1
    ) {
      k += 1;
      end = (changed[k] as number) + DIFF_CONTEXT_LINES;
    }
    k += 1;
    const hunk = ops.slice(start, Math.min(ops.length, end + 1));
    const aLen = hunk.filter((op) => op.kind !== '+').length;
    const bLen = hunk.filter((op) => op.kind !== '-').length;
    const first = hunk[0] as Op;
    // An empty side starts at line 0, as `diff -u` writes it.
    out.push(
      `@@ -${range(aLen === 0 ? first.a : first.a + 1, aLen)} +${range(bLen === 0 ? first.b : first.b + 1, bLen)} @@`,
      ...hunk.map((op) => `${op.kind}${op.text}`),
    );
  }
  const truncated = out.length > DIFF_MAX_LINES;
  return {
    diff: `${(truncated ? out.slice(0, DIFF_MAX_LINES) : out).join('\n')}\n`,
    additions,
    deletions,
    truncated,
  };
}

const UTF8 = new TextDecoder('utf-8', { fatal: true });

/** The file as text; null when it is not (a NUL byte, or not UTF-8). */
export function textOf(bytes: Buffer): string | null {
  if (bytes.subarray(0, 8000).includes(0)) return null;
  try {
    return UTF8.decode(bytes);
  } catch {
    return null;
  }
}
