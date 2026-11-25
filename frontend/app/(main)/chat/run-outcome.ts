import { CUT_SHORT_REASONS, type CutShortReason } from '@/lib/contracts/harness/generated/core/enums';
import type { MessagePart } from './types';

/**
 * WP-89 (ADR-RUN-01 D1): a run's settled outcome as Node sends it (`runOutcome`, `projection/outcome-view.ts`). The
 * answer then holds only the model's prose, and the chat words this outcome in the reader's language. Its `closing`
 * is English for surfaces with no UI; the web app never shows it.
 */
export interface RunOutcome {
  kind: 'completed' | 'partial' | 'failed';
  reason?: string;
  action: 'continue' | 'retry' | 'none';
  file?: { name: string; by: 'engine' | 'wrap_up' };
  missing?: string;
  sources: number;
  kept: boolean;
  opening: boolean;
  servers?: string[];
  stop?: { scope?: string; reason?: string };
  /** A failed `out_of_memory` run: the sandbox's memory limit in MB (R31GVISO-FU-3). */
  memoryLimitMb?: number;
}

const KINDS: readonly RunOutcome['kind'][] = ['completed', 'partial', 'failed'];
const ACTIONS: readonly RunOutcome['action'][] = ['continue', 'retry', 'none'];

function text(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim() !== '' ? value : undefined;
}

/** The outcome a value carries, when it is one this build reads; anything else it carries is dropped. */
export function runOutcome(value: unknown): RunOutcome | null {
  if (value === null || typeof value !== 'object') return null;
  const v = value as Record<string, unknown>;
  const kind = KINDS.find((k) => k === v.kind);
  const action = ACTIONS.find((a) => a === v.action);
  if (kind === undefined || action === undefined) return null;
  const file = v.file as { name?: unknown; by?: unknown } | undefined;
  const name = text(file?.name);
  const by = file?.by === 'engine' || file?.by === 'wrap_up' ? file.by : undefined;
  const servers = Array.isArray(v.servers) ? v.servers.filter((s): s is string => text(s) !== undefined) : [];
  const reason = text(v.reason);
  const missing = text(v.missing);
  return {
    kind,
    action,
    ...(reason ? { reason } : {}),
    ...(name && by ? { file: { name, by } } : {}),
    ...(missing ? { missing } : {}),
    sources: typeof v.sources === 'number' && Number.isInteger(v.sources) && v.sources > 0 ? v.sources : 0,
    kept: v.kept === true,
    opening: v.opening === true,
    ...(servers.length > 0 ? { servers } : {}),
    ...(v.stop !== null && typeof v.stop === 'object' ? { stop: v.stop as RunOutcome['stop'] } : {}),
    ...(typeof v.memoryLimitMb === 'number' && Number.isInteger(v.memoryLimitMb) && v.memoryLimitMb > 0
      ? { memoryLimitMb: v.memoryLimitMb }
      : {}),
  };
}

/** The message's own outcome first, else its final text part's (a shared viewer keeps only that part). */
export function runOutcomeOf(own: unknown, parts: MessagePart[] | undefined): RunOutcome | null {
  return runOutcome(own) ?? runOutcome(parts?.find((part) => part.type === 'text' && part.isFinal === true)?.runOutcome);
}

/** A limit the outcome names, for the shared cut-short wording; `null` for `unfinished` or a failure's code. */
export function outcomeLimit(outcome: RunOutcome): CutShortReason | null {
  const reason = outcome.reason;
  return reason !== undefined && (CUT_SHORT_REASONS as readonly string[]).includes(reason) ? (reason as CutShortReason) : null;
}

/**
 * An answer saved before WP-89 holds the outcome presenter's English words itself (its closing block, or the whole
 * answer for a file it did not write): it renders as saved, and the chat does not add its own note on top.
 */
const LEGACY_MARKS = ['\n**This result is partial.** ', " isn't written yet.**", '**Written from what was gathered.**'];
const LEGACY_LEADS = ['This reply stopped early because '];

export function hasLegacyOutcomeWords(answer: string): boolean {
  const lead = answer.trimStart();
  return LEGACY_MARKS.some((mark) => answer.includes(mark)) || LEGACY_LEADS.some((start) => lead.startsWith(start));
}
