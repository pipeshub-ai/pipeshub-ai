import { createHash } from 'node:crypto';
import type { HistoryTurnEntry } from './session-audience';

/**
 * Where a run's history lives (QA22-F2): the conversation as admission read
 * it, sealed as content of the run and kept beside the journal's payloads.
 * The run document names it (in its stored input) and never holds it, so
 * nothing a commit reads, rewrites or copies grows with the session.
 */
export interface RunHistoryRef {
  /** Content refs (`sealed:`) of the history's JSON, in order. */
  parts: string[];
  /** Of the whole JSON, before it was split and sealed. */
  hash: string;
  size: number;
  turns: number;
}

/**
 * A part stays well inside what one payload document or one sealed content
 * object may be, whatever the history's size.
 */
export const HISTORY_PART_BYTES = 4 * 1024 * 1024;

const sha256 = (bytes: Buffer): string =>
  `sha256:${createHash('sha256').update(bytes).digest('hex')}`;

/**
 * The history as it is stored: plain JSON, so each entry's keys reach the
 * worker in the order the conversation gave them.
 */
export const historyBytes = (turns: readonly HistoryTurnEntry[]): Buffer =>
  Buffer.from(JSON.stringify(turns), 'utf8');

export function historyParts(bytes: Buffer, partBytes: number): Buffer[] {
  if (!Number.isSafeInteger(partBytes) || partBytes <= 0) {
    throw new RangeError('A history part needs a positive size');
  }
  const parts: Buffer[] = [];
  for (let at = 0; at < bytes.length; at += partBytes) {
    parts.push(bytes.subarray(at, at + partBytes));
  }
  return parts;
}

export const describeHistory = (
  bytes: Buffer,
  turns: number,
): Pick<RunHistoryRef, 'hash' | 'size' | 'turns'> => ({
  hash: sha256(bytes),
  size: bytes.length,
  turns,
});

/** The entries the parts hold; null when they are not what `ref` describes. */
export function joinHistory(
  parts: readonly Buffer[],
  ref: Pick<RunHistoryRef, 'hash' | 'size' | 'turns'>,
): HistoryTurnEntry[] | null {
  const bytes = Buffer.concat(parts);
  if (bytes.length !== ref.size || sha256(bytes) !== ref.hash) return null;
  let turns: unknown;
  try {
    turns = JSON.parse(bytes.toString('utf8'));
  } catch {
    return null;
  }
  return Array.isArray(turns) && turns.length === ref.turns
    ? (turns as HistoryTurnEntry[])
    : null;
}

/** A stored reference as one; null for anything else. */
export function parseRunHistoryRef(stored: unknown): RunHistoryRef | null {
  if (stored === null || typeof stored !== 'object') return null;
  const { parts, hash, size, turns } = stored as Record<string, unknown>;
  if (
    !Array.isArray(parts) ||
    !parts.every((part) => typeof part === 'string') ||
    typeof hash !== 'string' ||
    typeof size !== 'number' ||
    typeof turns !== 'number'
  ) {
    return null;
  }
  return { parts, hash, size, turns };
}
