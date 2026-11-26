/**
 * WP-89 (ADR-RUN-01 D1): a run's settled outcome as the chat words it. The worker journals
 * `x.pipeshub.outcome.settled` with `presented` only when this Node advertises `outcome_words`; its answer then
 * carries only the model's prose, and the web app words the status, the file lines and the action in the reader's
 * language from this view. `closing` is the presenter's English closing, for a surface with no UI (the Slack bot,
 * an API text consumer), which appends it to the prose. An outcome journaled without `presented` (an older worker)
 * is not a view: its answer already holds its words.
 */

export const OUTCOME_KINDS = ['completed', 'partial', 'failed'] as const;
export const OUTCOME_ACTIONS = ['continue', 'retry', 'none'] as const;

export interface OutcomeView {
  kind: (typeof OUTCOME_KINDS)[number];
  /** Why a partial or failed run ended: a limit (`time`, `budget`, …), `unfinished`, or a failure's code. */
  reason?: string;
  action: (typeof OUTCOME_ACTIONS)[number];
  /** The deliverable's file as delivered: its name and who wrote it (the engine, or the wrap-up in its place). */
  file?: { name: string; by: 'engine' | 'wrap_up' };
  /** The file the run still owes, when nothing stands in for it. */
  missing?: string;
  /** How many sources the run gathered. */
  sources: number;
  /** Its work was saved for the run that continues it. */
  kept: boolean;
  /** The answer is the opening of the file, which holds the whole result. */
  opening: boolean;
  /** The presenter's English closing, for surfaces with no UI; a failed run's English message. */
  closing: string;
  /** A failed `mcp_tool_withdrawn` run: the labels of the servers that withdrew a tool (WP-84), never ids. */
  servers?: string[];
  /** A failed `operator_stopped` run: the control's scope and reason (ADR-OPS-01 D5), never its note. */
  stop?: { scope: string; reason: string };
  /** A failed `out_of_memory` run: the sandbox's memory limit in MB (R31GVISO-FU-3). */
  memoryLimitMb?: number;
}

function record(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function oneOf<T extends string>(
  value: unknown,
  known: readonly T[],
): T | undefined {
  return typeof value === 'string' &&
    (known as readonly string[]).includes(value)
    ? (value as T)
    : undefined;
}

/** The file name of a workspace path (`out/report.md` → `report.md`). */
function fileName(path: string): string {
  const parts = path.split('/');
  return parts[parts.length - 1] ?? path;
}

/** The view of one `x.pipeshub.outcome.settled`'s data; `null` when it carries no `presented` or is malformed. */
export function outcomeView(data: Record<string, unknown>): OutcomeView | null {
  const presented = record(data['presented']);
  const closing = presented['closing'];
  if (typeof closing !== 'string') return null;
  const outcome = record(data['outcome']);
  const kind = oneOf(outcome['kind'], OUTCOME_KINDS);
  const action = oneOf(outcome['action'], OUTCOME_ACTIONS);
  if (kind === undefined || action === undefined) return null;
  const delivered = record(outcome['delivered']);
  const file = record(delivered['file']);
  const path = file['path'];
  const by = oneOf(file['by'], ['engine', 'wrap_up'] as const);
  const missing: unknown = Array.isArray(outcome['missing'])
    ? outcome['missing'][0]
    : undefined;
  const sources = delivered['sources'];
  const reason = outcome['reason'];
  return {
    kind,
    ...(typeof reason === 'string' && reason.length > 0 ? { reason } : {}),
    action,
    ...(typeof path === 'string' && path.length > 0 && by !== undefined
      ? { file: { name: fileName(path), by } }
      : {}),
    ...(typeof missing === 'string' && missing.length > 0 ? { missing } : {}),
    sources:
      typeof sources === 'number' && Number.isInteger(sources) && sources >= 0
        ? sources
        : 0,
    kept: delivered['kept'] === true,
    opening: presented['opening'] === true,
    closing,
  };
}

/**
 * A run that ended without an answer, as the same typed outcome (WP-89): its code is the reason, its English
 * message the closing, and what the web app words it with (the servers, the control's scope) rides along. Built
 * from `runFailure`, the one reading of a run's failure (WP-84's servers, WP-86's `OperatorStopOutcome`).
 */
export function failedOutcome(failure: {
  code: string;
  message: string;
  servers?: string[];
  stop?: { scope: string; reason: string };
  memoryLimitMb?: number;
}): OutcomeView {
  return {
    kind: 'failed',
    reason: failure.code,
    action: 'retry',
    sources: 0,
    kept: false,
    opening: false,
    closing: failure.message,
    ...(failure.servers !== undefined && failure.servers.length > 0
      ? { servers: [...failure.servers] }
      : {}),
    ...(failure.stop !== undefined
      ? { stop: { scope: failure.stop.scope, reason: failure.stop.reason } }
      : {}),
    ...(failure.memoryLimitMb !== undefined
      ? { memoryLimitMb: failure.memoryLimitMb }
      : {}),
  };
}
